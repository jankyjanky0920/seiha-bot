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

## サーバー管理（第1段階）

設定を有効にすると、このBotは対象Discordサーバーの **Bot・管理者ロール所持者以外** に対して次の処理を行います。

- タスクカテゴリ (ID: `1561752871530270840`) に本人＋管理者ロール＋Botだけが閲覧できる `〇〇の作業場` を作成します。既存メンバーも初回に処理します。
- 初回のみ4つの通知ロール（配信募集通知、業務連絡通知、その他通知、急募通知）を自動付与します。
- 作業場内で `/notice what:<通知ロール>` を実行すると、そのロールを付け外しします。本人にしか見えないメッセージで結果を通知します。
- ロール設定はMongoDBの `nkii_nkii_alien.notification_preferences` に記録され、再起動でリセットされません。退室時の作業場と設定は削除せず、再参加時に復元します。
- これら4種のロールがメンションされたメッセージの約10%に、利用案内を投稿。投稿した案内は1時間後（削除処理は1分間隔）に削除されます。削除予定はMongoDBに保存されます。
- 管理者ロールID: `1541554412126994594`。既存seiha-bot専用の `cogs/` は変更しません。

### 起動手順（必須）

1. Discord Developer Portal → **んきー。んきー。星人** → Bot → **Privileged Gateway Intents** で `SERVER MEMBERS INTENT` と `MESSAGE CONTENT INTENT` を有効にします。既存seiha-botのアプリではありません。
2. 星人Botに `Manage Roles`・`Manage Channels`・`View Channels`・`Send Messages`・`Read Message History` を許可します。
3. Discordのロール一覧で **星人Botの最高位ロール**を、上記4つの通知ロールより上へ移動します。管理者ロールを上回る必要はありません。
4. Renderの既存サービスの Environment に `ALIEN_SERVER_MANAGEMENT_ENABLED=1` を新しく追加し、保存・デプロイします。既存の `DISCORD_TOKEN`、`MONGO_URL`、`ALIEN_DISCORD_TOKEN`、`ALIEN_GUILD_ID` は変更しません。
5. RenderのLogsで `[Alien/Workspace] 同期終了` を確認し、各協力者の作業場で `/notice` をテストします。

**注意：** `ALIEN_SERVER_MANAGEMENT_ENABLED` を設定しない（または `0` にする）限り、サーバー管理Cogは起動せず、従来の `/test` のみが動作します。DiscordのIntentを有効化する前に `1` にすると星人Bot側の接続が失敗する可能性があります。

Discordカテゴリには最大50チャンネルという制限があります。カテゴリが満杯になった場合はログにエラーを出し、それ以上作成しません。ロール付与やチャンネル作成にはDiscord側の権限が必要です。

**今回のコードは第1段階のみ。** 撮影スコア・ショップ・企画募集は今後別Cogとして追加します。
