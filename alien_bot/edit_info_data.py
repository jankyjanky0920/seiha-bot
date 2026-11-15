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


# SNS欄は自由入力。分かるURLのみ構造化し、判断できない情報は原文で保持する。
# 「YouTube: ...」という書式や https:// の有無は必須ではない。
LINK_RE = re.compile(
    r"(?i)(?:https?://|www\.|"
    r"(?:youtube\.com|youtu\.be|tiktok\.com|x\.com|twitter\.com|"
    r"pixiv\.net|instagram\.com)/)"
    r"[^\s<>\u3000，、]+"
)
LABEL_RE = re.compile(r"^\s*([^\s:：=＝]{1,40})\s*[:：=＝]\s*(.*?)\s*$")
PLAIN_LABEL_RE = re.compile(r"^\s*([^\s:：=＝]{1,40})\s+(.+?)\s*$")


def _platform_from_host(host: str) -> str | None:
    host = host.lower().rstrip(".")
    for name, domains in HOSTS.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return name
    return None


def _links_in(text: str) -> list[tuple[str, str]]:
    found = []
    for match in LINK_RE.finditer(text):
        value = match.group().rstrip(".,。、，）)]}\"'")
        if not value:
            continue
        url = value if value.lower().startswith(("http://", "https://")) else "https://" + value
        try:
            parts = urlsplit(url)
            if not parts.hostname:
                continue
            found.append((url, parts.hostname.lower()))
        except ValueError:
            continue
    return found


def _label_and_value(line: str) -> tuple[str | None, str]:
    match = LABEL_RE.match(line)
    if match:
        return match.group(1), match.group(2)
    match = PLAIN_LABEL_RE.match(line)
    if match and not match.group(1).lower().startswith(("http", "www")):
        return match.group(1), match.group(2)
    return None, line.strip()


def parse_linked(text: str, previous: dict | None = None) -> dict:
    """自由入力を受け入れ、判別可能なSNSリンクだけを自動的に分類する。

    判別できない文・特殊URLも拒否せず、parse_edit_fields側で原文を保存する。
    """
    result = deepcopy(previous or {})
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        label, value = _label_and_value(line)
        explicit = ALIASES.get((label or "").lower().replace(" ", ""))
        if explicit and value.lower() in ("削除", "remove"):
            result.pop(explicit, None)
            continue
        links = _links_in(line)
        if links:
            for url, host in links:
                platform = _platform_from_host(host) or explicit
                if platform:
                    result[platform] = url
        elif explicit and value:
            # URL以外のアカウント名も受付可。管理者は原文を確認できる。
            result[explicit] = value.strip()
    return result


def _safe_title(label: str, fallback: str) -> str:
    label = label.strip().replace(".", "・").replace("$", "＄")
    return label[:40] if label else fallback


def parse_description(text: str, linked: dict, previous: dict | None = None) -> dict:
    """概要欄は形式自由。1行=1件を基本とし、同上・URLのみも受付可。"""
    result = deepcopy(previous or {})
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        label, value = _label_and_value(line)
        direct_same = SAME_RE.fullmatch(line) if not LABEL_RE.match(line) else None
        platform = ALIASES.get((label or "").lower().replace(" ", ""))
        if direct_same:
            name = ALIASES.get(direct_same.group(1).strip().lower().replace(" ", ""))
            if name:
                result[name] = {"same_as": name}
                continue
        if label and value.lower() in ("削除", "remove"):
            result.pop(platform or _safe_title(label, "その他"), None)
            continue
        if label and value.strip() == "同上" and platform:
            result[platform] = {"same_as": platform}
            continue
        links = _links_in(line)
        if links:
            url, host = links[0]
            title = platform or (
                _safe_title(label, "") if label and label.lower() not in ("http", "https")
                else ""
            )
            title = title or _platform_from_host(host) or host.removeprefix("www.").split(".")[0]
            title = _safe_title(title, "SNS")
            result[title] = {"url": url}
            # 1行に複数リンクが書かれていても内容を失わないよう原文も残す。
            if len(links) > 1:
                result[title] = {"url": line}
        elif label:
            title = _safe_title(label, "SNS")
            result[platform or title] = {"url": value or line}
        else:
            # URL以外のSNS名・アカウント名・メモもそのまま保存する。
            note_key = next(
                (key for key, item in result.items()
                 if item.get("url") == line), None
            )
            if note_key is None:
                number = 1
                while f"メモ{number}" in result:
                    number += 1
                result[f"メモ{number}"] = {"url": line}
    if len(result) > 3:
        raise ValueError("概要欄用SNSは3件までにしてください。")
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
        # 不明なアカウント・特殊なURLも取りこぼさないよう元入力を保存する。
        changed["linked_sns_raw"] = linked_text.strip()
    linked = changed.get("linked_sns", previous.get("linked_sns", {}))
    if description_text.strip():
        changed["description_sns"] = parse_description(
            description_text, linked, previous.get("description_sns")
        )
        changed["description_sns_raw"] = description_text.strip()
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
        name: (linked_sns or {}).get(value["same_as"]) or (value["same_as"] + "同上")
        if "same_as" in value else value.get("url", "")
        for name, value in (description_sns or {}).items()
    }
