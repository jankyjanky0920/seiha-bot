"""撮影企画の永続データ、ステータス、出演者素材の判定。

企画IDは一意。企画名は重複可能なので、曖昧な場合はIDを使用する。
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from alien_bot.edit_info_data import complete
from alien_bot.score_store import score_text, utcnow

JST = ZoneInfo("Asia/Tokyo")
STATUS_WAIT = "waiting_assets"
STATUS_EDIT = "editing"
STATUS_REVIEW = "review"
STATUS_POSTED = "posted"
STATUS_LABELS = {
    STATUS_WAIT: "一部出演者の素材提出待機",
    STATUS_EDIT: "編集中",
    STATUS_REVIEW: "書き出し、出演者へチェック",
    STATUS_POSTED: "投稿完了",
}
MAX_NAME = 100


def normalize_name(name: str) -> str:
    value = name.strip()
    if not value or len(value) > MAX_NAME or "\n" in value or "\r" in value:
        raise ValueError("企画名は改行なしの1〜100文字で入力してください。")
    return value


def validate_duration(hours: int, minutes: int) -> int:
    if not 0 <= hours <= 72 or not 0 <= minutes <= 59:
        raise ValueError("撮影時間は0〜72時間、分は0〜59で入力してください。")
    total = hours * 60 + minutes
    if total == 0:
        raise ValueError("撮影時間は1分以上を指定してください。")
    return total


def parse_shoot_date(date_text: str):
    try:
        result = datetime.strptime(date_text.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("撮影日は YYYY-MM-DD（例：2026-11-04）で入力してください。") from exc
    return result


def reward_for_minutes(minutes: int) -> int:
    return (minutes * 1000 + 59) // 60


def thank_message(name: str, minutes: int, score_milli: int | None) -> str:
    from discord.utils import escape_markdown, escape_mentions
    display = escape_mentions(escape_markdown(name))
    if score_milli is None:
        return f"🎬【{display}】への撮影協力ありがとうございました！"
    return (
        f"🎬【{display}】への撮影協力ありがとうございました！ "
        f"{minutes}分 → **{score_text(score_milli)} スコア** 獲得しました。"
    )


def voice_plan(shoot: dict, name: str) -> dict:
    name = normalize_name(name)
    if "ended_at" not in shoot:
        raise ValueError("撮影終了日時が確定していません。")
    members = shoot.get("participants", {})
    durations = {}
    for uid, item in members.items():
        elapsed = (item.get("ended_at") or shoot["ended_at"]) - item["joined_at"]
        durations[str(uid)] = max(0, int(elapsed.total_seconds() // 60))
    started = shoot["started_at"]
    return {
        "_id": shoot["shoot_id"],
        "guild_id": shoot["guild_id"],
        "name": name,
        "member_ids": list(durations),
        "participant_minutes": durations,
        "shoot_date": started.astimezone(JST).date().isoformat(),
        "shoot_started_at": started,
        "shoot_ended_at": shoot["ended_at"],
        "duration_minutes": max(0, int((shoot["ended_at"] - started).total_seconds() // 60)),
        "origin": "voice",
        "award_score": True,
        "status": STATUS_WAIT,
        "missing_member_ids": list(durations),
        "review_round": 0,
        "review_votes": {},
        "collab_urls": {},
        "created_at": utcnow(),
    }


def manual_plan(guild_id: int, name: str, date_text: str, hours: int,
                minutes: int, member_ids: list[int], award_score: bool) -> dict:
    name = normalize_name(name)
    total = validate_duration(hours, minutes)
    day = parse_shoot_date(date_text)
    unique = list(dict.fromkeys(str(uid) for uid in member_ids))
    if not unique or len(unique) > 25:
        raise ValueError("出演者を1〜25人指定してください。")
    beginning = datetime.combine(day, datetime.min.time(), JST).astimezone(timezone.utc)
    return {
        "_id": "manual_" + uuid4().hex[:20],
        "guild_id": guild_id,
        "name": name,
        "member_ids": unique,
        "participant_minutes": {uid: total for uid in unique},
        "shoot_date": day.isoformat(),
        "shoot_started_at": beginning,
        "shoot_ended_at": beginning + timedelta(minutes=total),
        "duration_minutes": total,
        "origin": "manual",
        "award_score": bool(award_score),
        "status": STATUS_WAIT,
        "missing_member_ids": unique,
        "review_round": 0,
        "review_votes": {},
        "collab_urls": {},
        "created_at": utcnow(),
    }


def missing_assets(member_ids: list[str], profile_documents: list[dict]) -> list[str]:
    profiles = {str(p.get("user_id") or p.get("_id", "").split(":")[-1]): p
                for p in profile_documents}
    return [uid for uid in member_ids if not complete(profiles.get(uid))]


class ProductionStore:
    def __init__(self, score_store):
        db = score_store.client["nkii_nkii_alien"]
        self.plans = db["production_plans"]
        self.profiles = db["edit_profiles"]
        self.review_messages = db["production_review_messages"]
        self.notices = db["production_notices"]

    def add(self, record: dict) -> dict:
        """idempotent: 同じ撮影IDの別名登録を拒否。"""
        self.plans.update_one(
            {"_id": record["_id"]},
            {"$setOnInsert": record},
            upsert=True,
        )
        existing = self.plans.find_one({"_id": record["_id"]})
        if existing["name"] != record["name"]:
            raise ValueError(
                f"この撮影はすでに「{existing['name']}」として登録済みです。"
            )
        return self.refresh_assets(existing["_id"])

    def get(self, query: str, guild_id: int) -> dict:
        query = query.strip()
        if not query:
            raise ValueError("企画名または企画IDを入力してください。")
        exact = self.plans.find_one({"_id": query, "guild_id": guild_id})
        if exact:
            return exact
        matched = list(self.plans.find({
            "guild_id": guild_id, "name": query,
        }).limit(3))
        if not matched:
            raise ValueError("指定された企画は見つかりません。")
        if len(matched) > 1:
            raise ValueError(
                "同名の企画が複数あります。/zzzadmin_plans で企画IDを確認してください。"
            )
        return matched[0]

    def refresh_assets(self, plan_id: str) -> dict:
        plan = self.plans.find_one({"_id": plan_id})
        if not plan:
            raise ValueError("企画が見つかりません。")
        if plan["status"] != STATUS_WAIT:
            return plan
        keys = [f"{plan['guild_id']}:{uid}" for uid in plan["member_ids"]]
        docs = list(self.profiles.find({"_id": {"$in": keys}}))
        missing = missing_assets(plan["member_ids"], docs)
        update = {"missing_member_ids": missing, "updated_at": utcnow()}
        if not missing:
            update["status"] = STATUS_EDIT
        self.plans.update_one(
            {"_id": plan_id, "status": STATUS_WAIT}, {"$set": update},
        )
        return self.plans.find_one({"_id": plan_id})

    def refresh_member(self, guild_id: int, user_id: int) -> list[dict]:
        updated = []
        for plan in self.plans.find({
            "guild_id": guild_id, "member_ids": str(user_id),
            "status": STATUS_WAIT,
        }):
            updated.append(self.refresh_assets(plan["_id"]))
        return updated
