"""サーバー専用GoogleアカウントのDrive用OAuthリフレッシュトークンをローカルで取得。

実行場所: 自分のPCのみ。GitHub / Discord / ChatGPT にトークンを送らないこと。
利用前に GOOGLE_DRIVE_CLIENT_ID と GOOGLE_DRIVE_CLIENT_SECRET を設定。
"""

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPE = "https://www.googleapis.com/auth/drive.file"


def main():
    client_id = os.getenv("GOOGLE_DRIVE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_DRIVE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        sys.exit(
            "GOOGLE_DRIVE_CLIENT_ID と GOOGLE_DRIVE_CLIENT_SECRET を"
            "ローカルPCの環境変数に設定してください。"
        )

    config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(config, scopes=[SCOPE])
    print(
        "サーバー専用Googleアカウントでログインし、"
        "Botが作成するファイルへのアクセスを許可してください。"
    )
    credentials = flow.run_local_server(
        port=0, access_type="offline", prompt="consent",
        open_browser=True,
    )
    if not credentials.refresh_token:
        sys.exit(
            "リフレッシュトークンを取得できませんでした。"
            "Google側でアプリへの許可を解除して再実行してください。"
        )
    print("\n以下をRenderのEnvironmentに設定します。秘密情報として扱ってください。")
    print("GOOGLE_DRIVE_REFRESH_TOKEN=" + credentials.refresh_token)
    print("\nこの値はGitHubのコードやDiscordには貼り付けないでください。")


if __name__ == "__main__":
    main()
