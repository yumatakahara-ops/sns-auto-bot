# X & Threads 自動投稿ボット

Claude APIで投稿ネタを生成し、X（旧Twitter）とThreadsに1日4回自動投稿するツールです。
GitHub Actions上で動くので、自分のPCを起動しておく必要はありません（完全放置OK）。

---

## 0. 全体の流れ

1. このリポジトリをGitHubにアップロード
2. 3つのサービスでAPIキーを取得（Anthropic / X / Threads）
3. 取得したキーをGitHubの「Secrets」に登録
4. `config.py` にテーマを書く
5. あとは放っておけば1日4回、自動で投稿されます

所要時間の目安：初回セットアップ 30〜60分。以降は完全放置。

---

## 1. リポジトリをGitHubにアップロード

1. GitHubで新しいプライベートリポジトリを作成（例: `sns-auto-bot`）
2. このフォルダの中身を丸ごとアップロード（git push、またはGitHub Web UIでファイルをドラッグ&ドロップ）

---

## 2. Anthropic APIキーの取得

1. https://console.anthropic.com にアクセスしてログイン（なければアカウント作成）
2. 左メニューの「API Keys」から新しいキーを発行
3. 支払い方法を登録し、少額（$5〜10程度）をチャージ
   - 1日4投稿×生成コストは非常に小さいため、月数十円〜数百円程度で足ります

---

## 3. X（旧Twitter）APIキーの取得

1. https://developer.x.com にアクセスし、Xアカウントでログイン
2. Developer Portalで新しいプロジェクト・アプリを作成
3. アプリの設定で **User authentication settings** を有効化
   - App permissions: **Read and Write** を選択（これが無いと投稿できません）
   - Type of App: Web App / Automated App など（"Confidential client"を選択）
   - Callback URL / Website URLは仮の値でOK（例: `https://example.com`）
4. 以下の4つのキーを発行してメモ：
   - API Key
   - API Key Secret
   - Access Token（App permissionsをRead and Writeにした**後**に再生成すること）
   - Access Token Secret
5. 従量課金設定画面で最低 **$5** をチャージ（投稿1件あたり約$0.01〜0.015）

参考：1日4投稿×30日 ≈ 120投稿/月 ≈ 月$1.2〜1.8程度。$5あれば数ヶ月持ちます。

---

## 4. Threads APIキーの取得

Threads APIは現状**無料**です（Meta社提供）。

1. https://developers.facebook.com にアクセスし、Facebookアカウントでログイン
2. 「マイアプリ」→「アプリを作成」
3. ユースケースで **「Threads APIにアクセス」** を選択
4. 左メニュー「Threads API」→「カスタマイズ」から「Threadsテスター」に**自分のThreadsアカウント**を追加
5. Threadsアプリ側の設定（プロフィール→アプリとウェブサイトの権限、または招待通知）から、テスター招待を承認
6. Graph API Explorer、または OAuth フローで以下の権限を持つアクセストークンを発行：
   - `threads_basic`
   - `threads_content_publish`
7. 発行したアクセストークンを**長期トークン**に交換（60日間有効。期限が来たら再発行が必要）
8. 自分のThreadsユーザーID（USER_ID）も控えておく

※ Threadsは自分自身のアカウントへの投稿であれば、Meta側の「アプリ審査（App Review）」を通さなくても、テスターとして追加するだけで投稿できます。

---

## 5. GitHub Secretsへの登録

リポジトリの `Settings → Secrets and variables → Actions → New repository secret` から、以下をひとつずつ登録してください。

| Secret名 | 値 |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropicで発行したAPIキー |
| `X_API_KEY` | XのAPI Key |
| `X_API_SECRET` | XのAPI Key Secret |
| `X_ACCESS_TOKEN` | XのAccess Token |
| `X_ACCESS_SECRET` | XのAccess Token Secret |
| `THREADS_ACCESS_TOKEN` | Threadsの長期アクセストークン |
| `THREADS_USER_ID` | ThreadsのユーザーID |

---

## 6. テーマを設定する

`config.py` の `THEME` を、あなたが投稿したい内容に書き換えてください。
ターゲット層・ジャンル・「こういう切り口で書いてほしい」という指示を具体的に書くほど、生成される投稿の質が上がります。

---

## 7. 動作テスト

1. GitHubリポジトリの「Actions」タブ →「Auto Post to X and Threads」を選択
2. 「Run workflow」をクリックし、`dry_run` を `true` にして実行
   - この場合、実際には投稿されず、生成された文章がログに表示されるだけです
3. ログを確認して、生成内容やテーマの反映具合をチェック
4. 問題なければ `dry_run` を `false`（または未入力）で実行すると、実際に投稿されます

---

## 8. 自動実行のスケジュール

`.github/workflows/auto_post.yml` に以下の時刻で設定済みです（日本時間）：

- 9:00 / 13:00 / 18:00 / 21:00

頻度や時間を変えたい場合は、このファイル内の `cron` の値を編集してください（UTC基準で書く必要があります）。

---

## 9. 週次パフォーマンスレポート（新機能）

毎週月曜7:00（JST）に、直近7日間の投稿について自動で以下を行います。

1. X・Threadsそれぞれの投稿から「いいね・リツイート・引用・返信・インプレッション（Xのみ）・閲覧数（Threadsのみ）」を取得
2. Claudeが「伸びた投稿の傾向」「X/Threadsの反応の違い」「来週おすすめの切り口」を分析
3. `reports/2026-W29.md` のようなファイル名でMarkdownレポートを自動生成し、リポジトリにコミット

レポートは `reports/` フォルダに溜まっていくので、GitHub上でいつでも見返せます。

**手動で今すぐ実行したい場合**
Actionsタブ →「Weekly Performance Report」→「Run workflow」で即座に実行できます。

**費用について**
指標取得（読み取り）にもX APIの従量課金がかかります（1件あたり約$0.005〜0.01）。1日4投稿×週7日=28件の指標取得でも週あたり数十円程度です。

**注意点**
- 投稿してから間もない場合、インプレッション数などの指標がまだ確定していないことがあります（数時間〜半日程度のタイムラグが出ることがあります）
- Threadsの閲覧数（views）は投稿から少し時間が経たないと反映されない場合があります

---

## 10. 運用上の注意

- 投稿内容は生成AIによる自動作成のため、**最初の1〜2週間は毎日ログをチェック**し、意図しない内容が出ていないか確認することを推奨します
- X・Threadsともに、機械的な投稿の乱用はガイドライン違反になり得ます。今回の設定（1日4回程度）は一般的な範囲内です
- `posted_history.json` に過去の投稿内容が蓄積され、ネタの重複を防ぐのに使われます。消さないでください
- 何か投稿がおかしい場合は、Actionsタブから該当の実行ログを確認できます
