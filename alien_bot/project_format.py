"""企画募集フォームの入力チェックと日本時間のメッセージ生成。"""

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")
EMOJIS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")
NO_ATTEND = "❌"
WEEKDAYS = "月火水木金土日"
DATE_RE = re.compile(r"^(?:(\d{4})/)?(\d{1,2})/(\d{1,2})$")
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
RANGE_RE = re.compile(r"^(\d+)(?:-(\d*))?$")


def parse_date(value: str, today: date) -> date:
    match = DATE_RE.fullmatch(value)
    if not match:
        raise ValueError(f"日付「{value}」は 11/14 または 2026/11/14 の形式にしてください。")
    year, month, day = match.groups()
    try:
        if year is not None:
            result = date(int(year), int(month), int(day))
        else:
            result = date(today.year, int(month), int(day))
            if result < today:
                result = date(today.year + 1, int(month), int(day))
    except ValueError as exc:
        raise ValueError(f"存在しない日付です: {value}") from exc
    return result


def parse_time(value: str) -> int:
    match = TIME_RE.fullmatch(value)
    if not match:
        raise ValueError(f"時刻「{value}」は 13:00 または 25:00 の形式にしてください。")
    hour, minute = (int(p) for p in match.groups())
    if not 0 <= hour <= 47 or not 0 <= minute <= 59:
        raise ValueError(f"時刻が範囲外です: {value}")
    return hour * 60 + minute


def time_label(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_project_form(dates_text: str, projects_text: str, now: datetime | None = None):
    now = now or datetime.now(JST)
    if now.tzinfo is None:
        raise ValueError("現在日時にはタイムゾーンが必要です。")
    today = now.astimezone(JST).date()
    slots = []
    for line in dates_text.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        if len(parts) < 2:
            raise ValueError(f"日付の後には1つ以上の時刻が必要です: {line}")
        base = parse_date(parts[0], today)
        for item in parts[1:]:
            minutes = parse_time(item)
            actual = datetime.combine(base, time.min, JST) + timedelta(minutes=minutes)
            if actual <= now.astimezone(JST):
                raise ValueError(f"過去の撮影日時は指定できません: {line}")
            if actual > now.astimezone(JST) + timedelta(days=366):
                raise ValueError("撮影日時は366日以内にしてください。")
            slots.append({
                "base_date": base.isoformat(),
                "start_minutes": minutes,
                "start_at": actual,
            })

    if not 1 <= len(slots) <= len(EMOJIS):
        raise ValueError("撮影日時は1〜10枠指定してください。")
    slots.sort(key=lambda item: item["start_at"])
    if len({item["start_at"] for item in slots}) != len(slots):
        raise ValueError("同じ撮影日時が重複しています。")
    deadline_day = min(date.fromisoformat(item["base_date"]) for item in slots) - timedelta(days=1)
    deadline = datetime.combine(deadline_day, time(23, 59, 59), JST)
    if now.astimezone(JST) > deadline:
        raise ValueError("募集期限を過ぎています。最初の撮影日の前日までに募集してください。")

    projects = []
    for line in projects_text.splitlines():
        if not line.strip():
            continue
        if "|" not in line:
            raise ValueError(f"企画は「企画名 | 2」「企画名 | 3-7」の形式で指定してください: {line}")
        name, raw_range = (part.strip() for part in line.rsplit("|", 1))
        if not name or len(name) > 100 or "\n" in name:
            raise ValueError("企画名は1〜100文字にしてください。")
        match = RANGE_RE.fullmatch(raw_range)
        if not match:
            raise ValueError(f"人数範囲が正しくありません: {raw_range}")
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) else None
        if first < 2 or (last is not None and last < first):
            raise ValueError(f"人数範囲を確認してください: {raw_range}")
        if "-" not in raw_range:
            last = first
        projects.append({"name": name, "minimum": first, "maximum": last})
    if not projects:
        raise ValueError("企画を1つ以上指定してください。")
    projects.sort(key=lambda p: p["minimum"])
    # 参加人数2人から上限なしまで、漏れ・重複なく一意に決定できること。
    expected = 2
    for project in projects:
        if project["minimum"] != expected:
            raise ValueError(f"{expected}人を含む人数範囲が未指定、または重複しています。")
        if project["maximum"] is None:
            expected = None
            break
        expected = project["maximum"] + 1
    if expected is not None or projects[-1]["maximum"] is not None:
        raise ValueError("最後の人数範囲を「8-」のように上限なしにしてください。")
    if projects[-1]["minimum"] != projects[-1]["minimum"] or len(
        [p for p in projects if p["maximum"] is None]
    ) != 1:
        raise ValueError("上限なしの人数範囲は最後に1つだけ指定してください。")

    return {"slots": slots, "projects": projects, "deadline_at": deadline}


def build_recruitment_message(data: dict, role_id: int) -> str:
    lines = []
    for index, slot in enumerate(data["slots"]):
        d = date.fromisoformat(slot["base_date"])
        st = slot["start_minutes"]
        lines.append(
            f"{EMOJIS[index]}{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"
            f"{time_label(st)}~{time_label(st + 180)}"
        )
    lines.extend([
        "上記の時間帯で撮影します",
        "企画に参加できない人は❌を押してください",
        "",
        "企画内容",
        "1人の場合",
        "中止",
    ])
    for project in data["projects"]:
        low, high = project["minimum"], project["maximum"]
        range_text = (
            f"{low}人の場合" if high == low
            else f"{low}人以上の場合" if high is None
            else f"{low}~{high}人の場合"
        )
        lines.extend([range_text, f"【{project['name']}】"])
    lines.extend(["", f"<@&{role_id}>"])
    content = "\n".join(lines)
    if len(content) > 1900:
        raise ValueError("募集メッセージが長すぎます。日時または企画名を減らしてください。")
    return content
