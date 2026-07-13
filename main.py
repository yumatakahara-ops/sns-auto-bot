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
    #
