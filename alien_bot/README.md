# んきー。んきー。星人

このフォルダは **seiha-bot とは独立したDiscord Bot** のコードです。Renderの同じWeb Serviceで動作しますが、Discordアプリ・トークン・スラッシュコマンドは別です。

## Renderの環境変数

- `ALIEN_DISCORD_TOKEN` : 「んきー。んきー。星人」のBotトークン（既存の `DISCORD_TOKEN` は変更しない）
- `ALIEN_GUILD_ID` : 「んきー。んきー。帝国」のサーバーID

**この2つを両方設定するまで、星人Botは起動しません。既存seiha-botの起動はそのままです。** これらの値はRenderの Environment に入力し、GitHubには絶対に書かないでください。

Discord Developer Portalで星人Botを対象サーバーにインストールし、`applications.commands` スコープを有効にしてください。

## 機能追加

`alien_bot/cogs/` に `setup(bot)` を備えたCogモジュールを追加すると、次回起動時に自動読込されます。既存の `cogs/` フォルダは **seiha-bot専用** なので編集不要です。

## 動作確認

RenderのLogsで `[Alien] ログイン成功` と `[Alien] スラッシュコマンドの同期完了` を確認し、帝国サーバーで `/test` を実行すると `test` と返信します。

既存cron-job.orgの定期アクセス先URLおよびRender Start Command（`python main.py`）は変更不要です。
