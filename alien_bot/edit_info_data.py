"""編集用プロフィールのバリデーションと更新状態の純粋関数。"""

import re
from copy import deepcopy
from urllib.parse import urlsplit


COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}
PLATFORMS = ("YouTube", "TikTok", "X")
HOSTS = {
    "YouTube": {"youtube.com", "youtu.be", "youtube-nocookie.com"},
    "TikTok": {"tiktok.com"},
    "X": {"x.com", "twitter.com"},
}
ALIASES = {
    "youtube": "YouTube", "yt": "YouTube", "ユーチューブ": "YouTube",
    "tiktok": "TikTok", "ティックトック": "TikTok",
    "x": "X", "twitter": "X", "ツイッター": "X",
}
SAME_RE = re.compile(r"^(.+?)\s*同上\s*$")


def valid_url(value: str, platform: str | None = None) -> str:
    value = value.strip()
    parts = urlsplit(value)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        raise ValueError("SNSには http(s) から始まる有効なURLを入力してください。")
    if len(value) > 500:
        raise ValueError("SNSのURLは500文字以内にしてください。")
    if platform is not None:
        if not any(host == domain or host.endswith("." + domain) for domain in HOSTS[platform]):
            raise ValueError(f"{platform}のURLには対応する公式ドメインを指定してください。")
    return value


def platform_name(value: str) -> str:
    platform = ALIASES.get(value.lower().replace(" ", ""))
    if not platform:
        raise ValueError("連携用SNSはYouTube、TikTok、Xの3種類のみ対応します。")
    return platform


def parse_linked(text: str, previous: dict | None = None) -> dict:
    result = deepcopy(previous or {})
    for line in text.splitlines():
        if not line.strip():
            continue
        if ":" not in line and "：" not in line:
            raise ValueError("連携用SNSは「YouTube: https://...」と入力してください。")
        key, url = re.split(r"[:：]", line.strip(), maxsplit=1)
        platform = platform_name(key.strip())
        if url.strip() in ("削除", "remove", "REMOVE"):
            result.pop(platform, None)
        else:
            result[platform] = valid_url(url, platform=platform)
    return result


def parse_description(text: str, linked: dict, previous: dict | None = None) -> dict:
    result = deepcopy(previous or {})
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # 「X: 同上」は下のキー/値形式で処理する。先に同上と誤認しない。
        match = SAME_RE.fullmatch(line) if ":" not in line and "：" not in line else None
        if match:
            platform = platform_name(match.group(1).strip())
            if platform not in linked:
                raise ValueError(f"「{platform}同上」に必要な連携用SNSが未登録です。")
            result[platform] = {"same_as": platform}
            continue
        if ":" not in line and "：" not in line:
            raise ValueError("概要欄用SNSは「pixiv: https://...」または「YouTube同上」で入力してください。")
        key, value = re.split(r"[:：]", line, maxsplit=1)
        key = key.strip()
        value = value.strip()
        if not key or len(key) > 40 or "." in key or "$" in key:
            raise ValueError("SNS名は1〜40文字で、ピリオドと$を含めないでください。")
        if value == "同上":
            platform = platform_name(key)
            if platform not in linked:
                raise ValueError(f"「{platform}同上」に必要な連携用SNSが未登録です。")
            result[platform] = {"same_as": platform}
        elif value in ("削除", "remove", "REMOVE"):
            result.pop(key, None)
        else:
            result[key] = {"url": valid_url(value)}
    if len(result) > 3:
        raise ValueError("概要欄用SNSは1人3つまでです。")
    for entry in result.values():
        if "same_as" in entry and entry["same_as"] not in linked:
            raise ValueError("概要欄の「同上」は、対応する連携用SNSが必要です。")
    return result


def parse_edit_fields(
    color: str, name: str, linked_text: str, description_text: str,
    previous: dict | None = None,
) -> dict:
    previous = previous or {}
    changed = {}
    if color.strip():
        value = color.strip().upper()
        if not COLOR_RE.fullmatch(value):
            raise ValueError("Colorは #FF88CC のような6桁のカラーコードを入力してください。")
        changed["color"] = value
    if name.strip():
        value = name.strip()
        if len(value) > 100 or "@" in value:
            raise ValueError("活動名は100文字以内、@を含まない形式にしてください。")
        changed["name"] = value
    if linked_text.strip():
        changed["linked_sns"] = parse_linked(linked_text, previous.get("linked_sns"))
    linked = changed.get("linked_sns", previous.get("linked_sns", {}))
    if description_text.strip():
        changed["description_sns"] = parse_description(
            description_text, linked, previous.get("description_sns")
        )
    # 連携用SNSを削除すると既存の「同上」も使えなくなる。
    if "linked_sns" in changed:
        for entry in previous.get("description_sns", {}).values():
            if "same_as" in entry and entry["same_as"] not in linked and "description_sns" not in changed:
                raise ValueError("連携用SNSを削除する場合、概要欄側の「同上」も削除してください。")
    return changed


def complete(profile: dict | None) -> bool:
    profile = profile or {}
    return bool(profile.get("color") and profile.get("name") and profile.get("pictures"))


def due_notification(profile: dict | None) -> str | None:
    """管理者から依頼済みの人への17時通知種別。"""
    if not profile or not profile.get("watch"):
        return None
    if profile.get("error"):
        return "error"
    if not complete(profile):
        return "missing"
    return None


def description_resolved(description_sns: dict, linked_sns: dict) -> dict:
    return {
        name: linked_sns.get(value["same_as"], "")
        if "same_as" in value else value.get("url", "")
        for name, value in (description_sns or {}).items()
    }
