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
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    recent_topics = "\n".join(f"- {h['x_text']}" for h in history[-10:]) or "(まだ投稿履歴なし)"

    prompt = f"""あなたはSNS運用担当です。以下の条件でX(Twitter)とThreads向けの投稿文をそれぞれ作成してください。

# テーマ・ターゲット
{config.THEME}

# トーン
{config.TONE}

# ガードレール（厳守）
{config.GUARDRAILS}

# 直近の投稿ネタ（このネタとは違う切り口・話題にすること）
{recent_topics}

# 出力ルール
- X用は{config.X_MAX_CHARS}文字以内、Threads用は{config.THREADS_MAX_CHARS}文字以内
- X用は簡潔に。Threads用はX用と同じ話題でもう少し詳しく・会話的に書いてよい
- 必ず以下のJSON形式のみで出力すること。前置きや説明文、コードブロック記号（```）は一切つけないこと。

{{"x_text": "Xに投稿する文章", "threads_text": "Threadsに投稿する文章"}}
"""

    response = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = "".join(block.text for block in response.content if block.type == "text").strip()

    # 万が一コードブロックで返ってきた場合の保険
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
        "timestamp": datetime.utcnow().isoformat(),
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
