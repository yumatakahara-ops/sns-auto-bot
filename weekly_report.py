"""
週次パフォーマンスレポート生成スクリプト。
GitHub Actionsで毎週1回実行される想定。

処理の流れ:
1. posted_history.json から直近N日分の投稿（x_id / threads_id を持つもの）を抽出
2. X API / Threads API から各投稿の指標（いいね・リポスト・引用・返信・インプレッション等）を取得
3. 取得した数値データをClaudeに渡し、傾向分析・改善提案を自然言語で生成
4. reports/YYYY-Wxx.md としてMarkdownレポートを保存
5. posted_history.json にも指標を書き戻す（後で振り返れるように）
"""

import os
import sys
import json
from datetime import datetime, timedelta, timezone

import requests
from requests_oauthlib import OAuth1
from anthropic import Anthropic

import config


ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

X_API_KEY = os.environ.get("X_API_KEY")
X_API_SECRET = os.environ.get("X_API_SECRET")
X_ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN")
X_ACCESS_SECRET = os.environ.get("X_ACCESS_SECRET")

THREADS_ACCESS_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN")


# ------------------------------------------------------------
# 履歴読み込み
# ------------------------------------------------------------
def load_history():
    if not os.path.exists(config.HISTORY_FILE):
        return []
    with open(config.HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_history(history):
    with open(config.HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def get_target_posts(history):
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.REPORT_LOOKBACK_DAYS)
    targets = []
    for entry in history:
        try:
            ts = datetime.fromisoformat(entry["timestamp"]).replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if ts >= cutoff and (entry.get("x_id") or entry.get("threads_id")):
            targets.append(entry)
    return targets


# ------------------------------------------------------------
# X の指標取得
# ------------------------------------------------------------
def fetch_x_metrics(tweet_id):
    if not tweet_id or tweet_id == "dry_run_id":
        return None
    if not all([X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET]):
        return None

    auth = OAuth1(X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET)
    url = f"https://api.x.com/2/tweets/{tweet_id}"
    resp = requests.get(url, auth=auth, params={"tweet.fields": "public_metrics"})

    if resp.status_code != 200:
        print(f"X指標取得失敗 (id={tweet_id}): {resp.status_code} {resp.text}")
        return None

    metrics = resp.json().get("data", {}).get("public_metrics", {})
    return {
        "likes": metrics.get("like_count", 0),
        "retweets": metrics.get("retweet_count", 0),
        "quotes": metrics.get("quote_count", 0),
        "replies": metrics.get("reply_count", 0),
        "impressions": metrics.get("impression_count", 0),
    }


# ------------------------------------------------------------
# Threads の指標取得
# ------------------------------------------------------------
def fetch_threads_metrics(media_id):
    if not media_id or media_id == "dry_run_id":
        return None
    if not THREADS_ACCESS_TOKEN:
        return None

    url = f"https://graph.threads.net/v1.0/{media_id}/insights"
    resp = requests.get(url, params={
        "metric": "views,likes,replies,reposts,quotes",
        "access_token": THREADS_ACCESS_TOKEN,
    })

    if resp.status_code != 200:
        print(f"Threads指標取得失敗 (id={media_id}): {resp.status_code} {resp.text}")
        return None

    data = resp.json().get("data", [])
    result = {item["name"]: item.get("values", [{}])[0].get("value", 0) for item in data}
    return {
        "views": result.get("views", 0),
        "likes": result.get("likes", 0),
        "replies": result.get("replies", 0),
        "reposts": result.get("reposts", 0),
        "quotes": result.get("quotes", 0),
    }


# ------------------------------------------------------------
# Claude による分析
# ------------------------------------------------------------
def analyze_with_claude(rows):
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    table_lines = []
    for r in rows:
        table_lines.append(
            f"- 投稿: \"{r['x_text'][:40]}...\" | "
            f"X(いいね{r['x_metrics']['likes'] if r['x_metrics'] else '-'}, "
            f"RT/引用{(r['x_metrics']['retweets'] + r['x_metrics']['quotes']) if r['x_metrics'] else '-'}, "
            f"インプレッション{r['x_metrics']['impressions'] if r['x_metrics'] else '-'}) | "
            f"Threads(いいね{r['threads_metrics']['likes'] if r['threads_metrics'] else '-'}, "
            f"閲覧{r['threads_metrics']['views'] if r['threads_metrics'] else '-'})"
        )
    table_text = "\n".join(table_lines) if table_lines else "(データなし)"

    prompt = f"""以下は直近{config.REPORT_LOOKBACK_DAYS}日間に投稿したSNS投稿とその反応データです。

# テーマ設定
{config.THEME}

# 投稿と反応データ
{table_text}

このデータを見て、SNS運用担当としてカジュアルな文体で簡潔に分析してください。含めてほしい内容：
1. 全体的な傾向（伸びた投稿の共通点、伸びなかった投稿の共通点）
2. X と Threads で反応の違いがあれば指摘
3. 来週以降、どんな切り口・テーマを増やすと良さそうかの具体的な提案（2〜3個）

前置きなしで、分析内容から直接書き始めてください。"""

    response = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    return "".join(block.text for block in response.content if block.type == "text").strip()


# ------------------------------------------------------------
# レポート生成
# ------------------------------------------------------------
def build_report_markdown(rows, analysis_text):
    now = datetime.now(timezone.utc)
    week_str = now.strftime("%Y-W%V")

    total_x_likes = sum(r["x_metrics"]["likes"] for r in rows if r["x_metrics"])
    total_x_impressions = sum(r["x_metrics"]["impressions"] for r in rows if r["x_metrics"])
    total_threads_likes = sum(r["threads_metrics"]["likes"] for r in rows if r["threads_metrics"])
    total_threads_views = sum(r["threads_metrics"]["views"] for r in rows if r["threads_metrics"])

    lines = [
        f"# 週次パフォーマンスレポート（{week_str}）",
        "",
        f"対象期間: 直近{config.REPORT_LOOKBACK_DAYS}日間 / 対象投稿数: {len(rows)}件",
        "",
        "## サマリー",
        "",
        f"- X 合計いいね数: {total_x_likes} / 合計インプレッション: {total_x_impressions}",
        f"- Threads 合計いいね数: {total_threads_likes} / 合計閲覧数: {total_threads_views}",
        "",
        "## 投稿別データ",
        "",
        "| 投稿(X) | Xいいね | Xリツイート | X引用 | X返信 | Xインプレッション | Threadsいいね | Threads閲覧 |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for r in rows:
        xm = r["x_metrics"] or {}
        tm = r["threads_metrics"] or {}
        text_preview = r["x_text"][:30].replace("|", " ").replace("\n", " ")
        lines.append(
            f"| {text_preview}... | {xm.get('likes', '-')} | {xm.get('retweets', '-')} | "
            f"{xm.get('quotes', '-')} | {xm.get('replies', '-')} | {xm.get('impressions', '-')} | "
            f"{tm.get('likes', '-')} | {tm.get('views', '-')} |"
        )

    lines += [
        "",
        "## Claudeによる分析・来週への提案",
        "",
        analysis_text,
    ]

    return "\n".join(lines), week_str


# ------------------------------------------------------------
# メイン処理
# ------------------------------------------------------------
def main():
    if not ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY が設定されていません。")
        sys.exit(1)

    history = load_history()
    targets = get_target_posts(history)

    if not targets:
        print("対象期間内に指標取得可能な投稿がありませんでした。レポート生成をスキップします。")
        return

    print(f"{len(targets)}件の投稿について指標を取得します...")

    rows = []
    for entry in targets:
        x_metrics = fetch_x_metrics(entry.get("x_id"))
        threads_metrics = fetch_threads_metrics(entry.get("threads_id"))

        # 履歴側にも書き戻しておく（後から振り返れるように）
        entry["x_metrics"] = x_metrics
        entry["threads_metrics"] = threads_metrics

        rows.append({
            "x_text": entry["x_text"],
            "x_metrics": x_metrics,
            "threads_metrics": threads_metrics,
        })

    print("Claudeで分析中...")
    analysis_text = analyze_with_claude(rows)

    report_md, week_str = build_report_markdown(rows, analysis_text)

    os.makedirs(config.REPORT_DIR, exist_ok=True)
    report_path = os.path.join(config.REPORT_DIR, f"{week_str}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"レポートを {report_path} に保存しました。")

    save_history(history)


if __name__ == "__main__":
    main()
