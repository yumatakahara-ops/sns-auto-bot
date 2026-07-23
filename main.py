"""
X と Threads に自動投稿するメインスクリプト。
GitHub Actions から定期実行される想定。

処理の流れ:
1. 過去の投稿履歴（posted_history.json）を読み込む
2. Claude API でテーマに沿った投稿ネタを生成（X用・Threads用の2パターン）
3. X (Twitter) API v2 に投稿
4. Threads API に投稿
5. 投稿履歴を更新してコミット（GitHub Actions側で実施）
"""

import os
import sys
import json
import time
from datetime import datetime

import requests
from requests_oauthlib import OAuth1
from anthropic import Anthropic

import config


# ------------------------------------------------------------
# 環境変数（GitHub Secretsから渡される）
# ------------------------------------------------------------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

X_API_KEY = os.environ.get("X_API_KEY")
X_API_SECRET = os.environ.get("X_API_SECRET")
X_ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN")
X_ACCESS_SECRET = os.environ.get("X_ACCESS_SECRET")

THREADS_ACCESS_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN")
THREADS_USER_ID = os.environ.get("THREADS_USER_ID")

# どちらか片方だけ使いたい場合はこのフラグで制御可能
ENABLE_X = os.environ.get("ENABLE_X", "true").lower() == "true"
ENABLE_THREADS = os.environ.get("ENABLE_THREADS", "true").lower() == "true"

DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"  # trueならAPIに投稿せず内容だけ表示

# GitHub Actions側のcronスケジュールに応じて "morning"(8:00) "noon"(12:00) "evening"(19:00) が渡される
POST_TIME_SLOT = os.environ.get("POST_TIME_SLOT", "morning")


# ------------------------------------------------------------
# 履歴管理
# ------------------------------------------------------------
def load_history():
    if not os.path.exists(config.HISTORY_FILE):
        return []
    try:
        with open(config.HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def save_history(history):
    # 直近N件だけ保持（ファイル肥大化防止）
    trimmed = history[-config.HISTORY_KEEP_LAST:]
    with open(config.HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------
# Claude で投稿文を生成
# ------------------------------------------------------------
def generate_post(history):
    """8:00 / 12:00 / 19:00 共通: 単発投稿を1つ生成する"""
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    recent_topics = "\n".join(f"- {h.get('x_text', '')}" for h in history[-10:]) or "(まだ投稿履歴なし)"

    post_number = len(history)
    is_experimental = (post_number % 5 == 4)

    hook_types = [
        "断言リスト型", "対比・逆説型", "絞り込み型", "裏技提示型", "再現エピソード型",
        "理不尽指摘型", "ランキング型", "警鐘型", "正論反論・共感型", "矛盾追及型",
    ]
    hook_type_instruction = f"今回のフックは特に「{hook_types[post_number % len(hook_types)]}」を使って書いてください。"

    x_max_chars = config.X_MAX_CHARS_BY_SLOT.get(POST_TIME_SLOT, config.X_MAX_CHARS_BY_SLOT["morning"])
    if POST_TIME_SLOT == "noon":
        length_instruction = (
            "短文モード（12:00投稿）：200文字以内に収まるよう、要点を1〜2個に絞って"
            "テンポよく読める短い投稿にしてください。箇条書きを使う場合も最小限に。"
        )
    else:
        length_instruction = (
            "長文モード（8:00/19:00投稿）：箇条書きや改行を使い、背景説明や具体例も交えて"
            "しっかり書き込む投稿にしてください（1000文字以内）。"
        )

    if is_experimental:
        mode_instruction = """
今回は「実験枠」の投稿です。以下を踏まえて、いつもの型とは少し違う切り口で書いてください。
- 直近話題になっていそうなキャリア・転職関連のトピックがあれば触れてよい（ただし断定的な最新情報は避け、
  一般的に言われていることの範囲で書く）
- 定番の型に縛られず、新しい切り口・フォーマットを試してよい
- いつもの投稿とは違うテイストにすることを意識する
"""
    else:
        mode_instruction = "今回は「定番枠」の投稿です。テーマ内で定義されている型（求人票の翻訳・エージェントの裏側・勘違いの訂正など）から1つ選んで書いてください。"

    # 8:00の投稿のみ、Web検索でトレンド分析してから書く（1日1回だけ、追加コストを抑えるため）
    use_trend_research = (POST_TIME_SLOT == "morning")
    if use_trend_research:
        trend_instruction = """
# トレンドリサーチの指示（今回は必須）
書く前に、「転職」「退職」「面接対策」などのキーワードでWeb検索し、
今よく読まれている・反応が良さそうな投稿にどんな傾向があるか調べてください。

重要：バズっている投稿の文章量、テーマ、内容、言い回しをなるべく取り入れ、少しだけ内容のアレンジを加えてください。
全く同じ文章はダメです。
"""
    else:
        trend_instruction = ""

    prompt = f"""あなたはSNS運用担当です。以下の条件でX(Twitter)とThreads向けの投稿文をそれぞれ作成してください。

# テーマ・ターゲット・裏コンセプト
{config.THEME}

# トーン
{config.TONE}

# ガードレール（厳守）
{config.GUARDRAILS}

# 今回の投稿タイプ
{mode_instruction}
{trend_instruction}
# 今回のフォーマット指示
{length_instruction}
{hook_type_instruction}

# 直近の投稿ネタ（このネタとは違う切り口・話題にすること）
{recent_topics}

# 出力ルール
- X用は{x_max_chars}文字以内、Threads用は{config.THREADS_MAX_CHARS}文字以内
- X用とThreads用は同じ話題・同じ切り口で、文章の書き方だけ少し変えてよい（Threadsの方がやや会話的でもよい）
- 最初の一文は必ず、読者の目を引く"短く簡潔な"フックにすること（長い前置きは避ける）
- 語尾に「〜よ」は使わないこと
- ハッシュタグは一切つけないこと
- 必ず以下のJSON形式のみで出力すること。前置きや説明文、コードブロック記号（```）は一切つけないこと。

{{"x_text": "Xに投稿する文章", "threads_text": "Threadsに投稿する文章"}}
"""

    create_kwargs = {
        "model": config.CLAUDE_MODEL,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    if use_trend_research:
        create_kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]

    response = client.messages.create(**create_kwargs)

    text_blocks = [block.text for block in response.content if block.type == "text"]
    raw_text = (text_blocks[-1] if text_blocks else "").strip()
    raw_text = raw_text.replace("```json", "").replace("```", "").strip()

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print("Claudeの出力がJSONとしてパースできませんでした:")
        print(raw_text)
        raise e

    return data["x_text"], data["threads_text"]


# ------------------------------------------------------------
# X (Twitter) への投稿
# ------------------------------------------------------------
def post_to_x(text):
    """成功時は tweet_id (str) を、失敗時は None を返す"""
    if DRY_RUN:
        print(f"[DRY_RUN] Xに投稿するはずだった内容:\n{text}")
        return "dry_run_id"

    auth = OAuth1(X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET)
    url = "https://api.x.com/2/tweets"
    resp = requests.post(url, auth=auth, json={"text": text})

    if resp.status_code not in (200, 201):
        print(f"X投稿失敗: {resp.status_code} {resp.text}")
        print(f"デバッグ用レスポンスヘッダー: {dict(resp.headers)}")
        return None

    tweet_id = resp.json().get("data", {}).get("id")
    print(f"X投稿成功: {resp.json()}")
    return tweet_id


# ------------------------------------------------------------
# Threads への投稿（コンテナ作成→公開の2ステップ）
# ------------------------------------------------------------
def post_to_threads(text):
    """成功時は media_id (str) を、失敗時は None を返す"""
    if DRY_RUN:
        print(f"[DRY_RUN] Threadsに投稿するはずだった内容:\n{text}")
        return "dry_run_id"

    base_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads"

    # 1. コンテナ作成
    create_resp = requests.post(base_url, params={
        "media_type": "TEXT",
        "text": text,
        "access_token": THREADS_ACCESS_TOKEN,
    })
    create_data = create_resp.json()
    container_id = create_data.get("id")

    if not container_id:
        print(f"Threadsコンテナ作成失敗: {create_resp.status_code} {create_resp.text}")
        return None

    # Threads側の推奨: 公開前に少し待つ
    time.sleep(5)

    # 2. 公開
    publish_url = f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish"
    publish_resp = requests.post(publish_url, params={
        "creation_id": container_id,
        "access_token": THREADS_ACCESS_TOKEN,
    })

    if publish_resp.status_code not in (200, 201):
        print(f"Threads公開失敗: {publish_resp.status_code} {publish_resp.text}")
        return None

    media_id = publish_resp.json().get("id")
    print(f"Threads投稿成功: {publish_resp.json()}")
    return media_id


# ------------------------------------------------------------
# メイン処理
# ------------------------------------------------------------
def main():
    now_utc = datetime.utcnow()
    print(f"[実行時刻デバッグ] UTC: {now_utc.isoformat()} / POST_TIME_SLOT: {POST_TIME_SLOT}")

    if not ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY が設定されていません。GitHub Secretsを確認してください。")
        sys.exit(1)

    history = load_history()

    print("Claudeで投稿文を生成中...")
    x_text, threads_text = generate_post(history)

    print("---- X用投稿文 ----")
    print(x_text)
    print("---- Threads用投稿文 ----")
    print(threads_text)

    x_id = None
    threads_id = None

    if ENABLE_X:
        if not all([X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET]):
            print("X用のSecretsが不足しています。Xへの投稿をスキップします。")
        else:
            x_id = post_to_x(x_text)

    if ENABLE_THREADS:
        if not all([THREADS_ACCESS_TOKEN, THREADS_USER_ID]):
            print("Threads用のSecretsが不足しています。Threadsへの投稿をスキップします。")
        else:
            threads_id = post_to_threads(threads_text)

    # 履歴に追加（投稿の成否に関わらずネタの重複防止のために記録。
    # x_id / threads_id は週次レポートで指標を取得する際のキーになる）
    history.append({
        "timestamp": now_utc.isoformat(),
        "post_time_slot": POST_TIME_SLOT,
        "x_text": x_text,
        "threads_text": threads_text,
        "x_posted": x_id is not None,
        "threads_posted": threads_id is not None,
        "x_id": x_id,
        "threads_id": threads_id,
    })
    save_history(history)

    if x_id is None and threads_id is None and not DRY_RUN:
        sys.exit(1)


if __name__ == "__main__":
    main()
