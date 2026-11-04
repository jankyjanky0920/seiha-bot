"""Google Driveの限定アクセスAPI。サーバー専用GoogleアカウントのOAuth2を使用。

drive.file スコープでBotが作成したフォルダとファイルだけを管理する。
Google Driveの「リンクを知っている全員」は決して有効化しない。
"""

import os
import re
from urllib.parse import urlparse

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
DRIVE_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"

EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")

ROOT_NAME = "んきー。んきー。帝国"
FOLDER_NAMES = {
    "rules": "00_利用規約",
    "common": "01_共通素材",
    "members": "02_メンバー素材",
    "projects": "03_企画別素材",
    "completed": "04_完成動画",
}


def normalized_email(email: str) -> str:
    email = email.strip().lower()
    if not 3 <= len(email) <= 254 or not EMAIL_RE.fullmatch(email):
        raise ValueError("Googleアカウントのメールアドレスを入力してください。")
    return email


def safe_name(name: str) -> str:
    name = name.strip().replace("/", "_").replace("\\", "_").replace("\n", " ")
    if not name or len(name) > 120 or name in (".", ".."):
        raise ValueError("フォルダ・ファイル名は1〜120文字にしてください。")
    return name


def folder_url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"


def is_private_scope(area: str) -> bool:
    return area in ("members", "projects")


class DriveClient:
    def __init__(self):
        # 依存パッケージはこの機能が有効な場合のみ読み込む。
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import AuthorizedSession

        keys = {
            "client_id": "GOOGLE_DRIVE_CLIENT_ID",
            "client_secret": "GOOGLE_DRIVE_CLIENT_SECRET",
            "refresh_token": "GOOGLE_DRIVE_REFRESH_TOKEN",
        }
        values = {name: os.getenv(var, "").strip() for name, var in keys.items()}
        missing = [keys[k] for k, v in values.items() if not v]
        if missing:
            raise RuntimeError("Google Drive認証情報が未設定です: " + ", ".join(missing))
        credentials = Credentials(
            token=None,
            refresh_token=values["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=values["client_id"],
            client_secret=values["client_secret"],
            scopes=[DRIVE_SCOPE],
        )
        self.session = AuthorizedSession(credentials)
        self.session.timeout = 40

    @staticmethod
    def _raise(response):
        # トークン・メールアドレス等を含む応答本文をログへ出さない。
        if not response.ok:
            raise RuntimeError(f"Google Drive API HTTP {response.status_code}")
        return response

    def _json(self, method: str, url: str, **kwargs):
        response = self.session.request(method, url, timeout=40, **kwargs)
        return self._raise(response).json()

    def find_folder(self, parent_id: str | None, name: str) -> str | None:
        escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        parts = [f"name = '{escaped}'", f"mimeType = '{FOLDER_MIME}'", "trashed = false"]
        if parent_id:
            parts.append(f"'{parent_id}' in parents")
        response = self._json(
            "GET", DRIVE_BASE + "/files",
            params={"q": " and ".join(parts), "fields": "files(id,name),nextPageToken",
                    "pageSize": 100},
        )
        files = response.get("files") or []
        return files[0]["id"] if files else None

    def find_file(self, parent_id: str, filename: str) -> str | None:
        """再試行時に同じ画像を二重保存しないための存在確認。"""
        escaped = filename.replace("\\", "\\\\").replace("'", "\\'")
        query = (
            f"name = '{escaped}' and '{parent_id}' in parents "
            "and trashed = false"
        )
        result = self._json(
            "GET", DRIVE_BASE + "/files",
            params={"q": query, "fields": "files(id),nextPageToken",
                    "pageSize": 100},
        )
        return (result.get("files") or [{}])[0].get("id")

    def file_info(self, file_id: str) -> dict:
        """完成動画の共有をそのフォルダ内のファイルだけに限定するための確認。"""
        return self._json(
            "GET", DRIVE_BASE + f"/files/{file_id}",
            params={"fields": "id,name,parents,mimeType,webViewLink"},
        )

    def create_folder(self, name: str, parent_id: str | None = None) -> str:
        metadata = {"name": safe_name(name), "mimeType": FOLDER_MIME}
        if parent_id:
            metadata["parents"] = [parent_id]
        response = self._json(
            "POST", DRIVE_BASE + "/files",
            params={"fields": "id"},
            json=metadata,
        )
        return response["id"]

    def ensure_folder(self, name: str, parent_id: str | None) -> str:
        name = safe_name(name)
        existing = self.find_folder(parent_id, name)
        return existing or self.create_folder(name, parent_id)

    def _permissions(self, folder_id: str) -> list[dict]:
        # ページングを処理する。全員公開の権限は作成しない。
        found, token = [], None
        while True:
            params = {
                "fields": "nextPageToken,permissions(id,type,emailAddress,role)",
                "pageSize": 100,
            }
            if token:
                params["pageToken"] = token
            result = self._json(
                "GET", DRIVE_BASE + f"/files/{folder_id}/permissions",
                params=params,
            )
            found.extend(result.get("permissions") or [])
            token = result.get("nextPageToken")
            if not token:
                return found

    def grant(self, folder_id: str, email: str, role: str = "reader") -> str:
        email = normalized_email(email)
        if role not in ("reader", "writer"):
            raise ValueError("共有権限はreaderかwriterのみです。")
        for permission in self._permissions(folder_id):
            if permission.get("type") == "user" and (
                permission.get("emailAddress") or ""
            ).lower() == email:
                if permission.get("role") != role:
                    self._json(
                        "PATCH", DRIVE_BASE + f"/files/{folder_id}/permissions/{permission['id']}",
                        json={"role": role},
                    )
                return permission["id"]
        item = self._json(
            "POST", DRIVE_BASE + f"/files/{folder_id}/permissions",
            params={"sendNotificationEmail": "true", "fields": "id"},
            json={"type": "user", "role": role, "emailAddress": email},
        )
        return item["id"]

    def revoke(self, folder_id: str, email: str) -> bool:
        email = normalized_email(email)
        for permission in self._permissions(folder_id):
            if permission.get("type") == "user" and (
                permission.get("emailAddress") or ""
            ).lower() == email:
                response = self.session.delete(
                    DRIVE_BASE + f"/files/{folder_id}/permissions/{permission['id']}",
                    timeout=40,
                )
                self._raise(response)
                return True
        return False

    def upload_bytes(
        self, parent_id: str, filename: str, payload: bytes, mime: str
    ) -> dict:
        if not isinstance(payload, bytes) or not payload:
            raise ValueError("空ファイルは保存できません")
        metadata = {"name": safe_name(filename), "parents": [parent_id]}
        init = self.session.post(
            UPLOAD_BASE + "/files",
            params={"uploadType": "resumable", "fields": "id,name,webViewLink"},
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": mime or "application/octet-stream",
                "X-Upload-Content-Length": str(len(payload)),
            },
            json=metadata, timeout=40,
        )
        self._raise(init)
        location = init.headers.get("Location") or ""
        parsed = urlparse(location)
        if parsed.scheme != "https" or parsed.hostname != "www.googleapis.com":
            raise RuntimeError("Google Driveのアップロード先が正しくありません")
        result = self.session.put(
            location, data=payload,
            headers={
                "Content-Type": mime or "application/octet-stream",
                "Content-Length": str(len(payload)),
            },
            timeout=180,
        )
        return self._raise(result).json()
