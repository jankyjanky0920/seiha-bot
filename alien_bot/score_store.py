"""星人Bot専用のMongoDBスコア台帳。値はすべて整数(1/1000スコア)。"""

import math
import os
from datetime import datetime, timezone
from uuid import uuid4

from pymongo import MongoClient


DB_NAME = "nkii_nkii_alien"
ADMIN_ROLE_ID = 1541554412126994594
ADMIN_CHANNEL_ID = 1561759852731175053
TASK_CATEGORY_ID = 1561752871530270840

PRODUCTS = {
    "shorts": ("shorts編集", 2000, "あなたの活動で投稿する動画のshortsを編集します。"),
    "commission": ("依頼強制購入", 3000, "あなたの何らかの依頼活動を、私が購入させていただきます。"),
    "puzzle": ("謎解き", 1000, "お好きなシチュエーションでご利用いただける謎解きを作成します。"),
    "discord_bot": ("あなた専用discord bot", 5000, "ChatGPT Plusを利用して開発します。開発期間は1週間。以降の開発は再度購入してください（軽いエラーは簡易修正します）。"),
    "website": ("あなた専用web site", 5000, "ChatGPT Plusを利用して開発します。開発期間は1週間。以降の開発は再度購入してください（軽いエラーは簡易修正します）。"),
}


def utcnow():
    return datetime.now(timezone.utc)


def score_text(milli: int) -> str:
    return f"{milli // 1000}.{milli % 1000:03d}"


def duration_reward(start: datetime, end: datetime) -> tuple[int, int]:
    """経過秒の端数は切り捨て、分単位で計算。千分位へ切り上げ。"""
    full_minutes = max(0, int((end - start).total_seconds() // 60))
    return full_minutes, (full_minutes * 1000 + 59) // 60


def is_admin(member) -> bool:
    return any(role.id == ADMIN_ROLE_ID for role in member.roles)


def is_worker(member) -> bool:
    return not member.bot and not is_admin(member)


class ScoreStore:
    def __init__(self):
        self.client = MongoClient(
            os.environ["MONGO_URL"], serverSelectionTimeoutMS=8000
        )
        db = self.client[DB_NAME]
        self.wallets = db["score_wallets"]
        self.ledger = db["score_ledger"]
        self.shoots = db["shoots"]
        self.shoot_history = db["shoot_history"]
        self.confirmations = db["shoot_confirmations"]
        self.shop_orders = db["shop_orders"]
        self.workspaces = db["workspaces"]

    def close(self):
        self.client.close()

    def wallet(self, user_id: int):
        key = str(user_id)
        self.wallets.update_one(
            {"_id": key},
            {"$setOnInsert": {
                "balance_milli": 0, "held_milli": 0, "spent_milli": 0,
                "earned_milli": 0, "credited_shoot_ids": [], "reservations": {},
            }},
            upsert=True,
        )
        return self.wallets.find_one({"_id": key})

    def grant_shoot(self, user_id: int, shoot_id: str, start: datetime, end: datetime):
        minutes, amount = duration_reward(start, end)
        self.wallet(user_id)
        result = self.wallets.update_one(
            {"_id": str(user_id), "credited_shoot_ids": {"$ne": shoot_id}},
            {"$inc": {"balance_milli": amount, "earned_milli": amount},
             "$addToSet": {"credited_shoot_ids": shoot_id}},
        )
        self.ledger.update_one(
            {"_id": f"shoot:{shoot_id}:{user_id}"},
            {"$setOnInsert": {
                "user_id": str(user_id), "shoot_id": shoot_id,
                "amount_milli": amount, "minutes": minutes,
                "start": start, "end": end, "created_at": utcnow(),
                "kind": "shoot",
            }},
            upsert=True,
        )
        return minutes, amount, result.modified_count == 1

    def reserve(self, user_id: int, product_key: str, quantity: int):
        name, price, _ = PRODUCTS[product_key]
        cost = price * quantity
        uid = str(user_id)
        self.wallet(user_id)
        order_id = uuid4().hex[:16]
        reservation_path = f"reservations.{order_id}"
        reservation = {
            "state": "held", "product_key": product_key, "quantity": quantity,
            "amount_milli": cost,
        }
        result = self.wallets.update_one(
            {"_id": uid, "balance_milli": {"$gte": cost}, reservation_path: {"$exists": False}},
            {"$inc": {"balance_milli": -cost, "held_milli": cost},
             "$set": {reservation_path: reservation}},
        )
        if result.modified_count != 1:
            return None
        order = {
            "_id": order_id, "user_id": uid, "product_key": product_key,
            "product_name": name, "quantity": quantity, "cost_milli": cost,
            "state": "pending", "created_at": utcnow(), "message_id": None,
        }
        # crash時の復旧を可能にするため、予約の元情報は財布側にも保存する。
        self.shop_orders.update_one({"_id": order_id}, {"$setOnInsert": order}, upsert=True)
        return order

    def settle_order(self, order_id: str, approved: bool, actor_id: int):
        order = self.shop_orders.find_one({"_id": order_id})
        if not order:
            raise ValueError("購入申請が見つかりません")
        uid = order["user_id"]
        path = f"reservations.{order_id}.state"
        wallet = self.wallet(int(uid))
        reservation = wallet.get("reservations", {}).get(order_id)
        if not reservation:
            raise RuntimeError("スコアの確保情報が見つかりません")
        target_state = "approved" if approved else "rejected"
        if reservation["state"] == "held":
            cost = reservation["amount_milli"]
            inc = {"held_milli": -cost}
            if approved:
                inc["spent_milli"] = cost
            else:
                inc["balance_milli"] = cost
            updated = self.wallets.update_one(
                {"_id": uid, path: "held"},
                {"$inc": inc, "$set": {path: "spent" if approved else "refunded"}},
            )
            if updated.modified_count != 1:
                # ほかの承認処理が先に完了した場合は結果を読み直す。
                wallet = self.wallet(int(uid))
                reservation = wallet["reservations"].get(order_id)
        else:
            reservation = self.wallet(int(uid))["reservations"].get(order_id)
        actual_state = reservation["state"]
        if actual_state not in ("spent", "refunded"):
            raise RuntimeError("申請の確定が完了しませんでした")
        target_state = "approved" if actual_state == "spent" else "rejected"
        self.shop_orders.update_one(
            {"_id": order_id},
            {"$set": {"state": target_state, "decided_by": str(actor_id), "decided_at": utcnow()}},
        )
        return self.shop_orders.find_one({"_id": order_id}), target_state

    def workspace_channel_id(self, guild_id: int, user_id: int):
        doc = self.workspaces.find_one({"_id": f"{guild_id}:{user_id}"})
        return doc.get("channel_id") if doc else None
