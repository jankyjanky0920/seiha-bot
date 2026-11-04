"""撮影後の企画名登録・スコア遅延・編集ステータスの回帰テスト。"""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from alien_bot.production_plans import (
    JST, STATUS_WAIT, STATUS_EDIT, STATUS_REVIEW, STATUS_POSTED,
    STATUS_LABELS, manual_plan, voice_plan, missing_assets,
    validate_duration, parse_shoot_date, thank_message, reward_for_minutes,
    ProductionStore,
)


class TestProductionPlans(unittest.TestCase):
    def test_manual_plan_and_score_opt_out(self):
        p = manual_plan(
            123, "撮影A", "2026-10-20", 1, 9, [10, 20, 10], False
        )
        self.assertEqual(p["duration_minutes"], 69)
        self.assertEqual(p["participant_minutes"], {"10": 69, "20": 69})
        self.assertFalse(p["award_score"])
        self.assertEqual(p["status"], STATUS_WAIT)
        self.assertEqual(p["shoot_date"], "2026-10-20")
        self.assertTrue(p["_id"].startswith("manual_"))

        p2 = manual_plan(123, "撮影A", "2026-10-20", 1, 9, [10], True)
        self.assertTrue(p2["award_score"])
        self.assertNotEqual(p2["_id"], p["_id"])

    def test_manual_plan_validations(self):
        for h, m in ((-1, 0), (0, 0), (0, 60), (73, 0)):
            with self.subTest(h=h, m=m), self.assertRaises(ValueError):
                validate_duration(h, m)
        with self.assertRaises(ValueError):
            parse_shoot_date("2026/10/20")
        with self.assertRaises(ValueError):
            manual_plan(1, "", "2026-10-20", 1, 0, [10], True)
        with self.assertRaises(ValueError):
            manual_plan(1, "テスト", "2026-10-20", 1, 0, [], True)

    def test_voice_plan_and_early_leaver(self):
        start = datetime(2026, 11, 4, 10, tzinfo=timezone.utc)
        end = start + timedelta(minutes=69)
        shoot = {
            "shoot_id": "voice_123",
            "guild_id": 22,
            "started_at": start,
            "ended_at": end,
            "participants": {
                "10": {"joined_at": start, "ended_at": None},
                "20": {"joined_at": start + timedelta(minutes=20),
                       "ended_at": start + timedelta(minutes=50)},
            },
        }
        p = voice_plan(shoot, "AI vs 人間")
        self.assertEqual(p["_id"], "voice_123")
        self.assertEqual(p["participant_minutes"], {"10": 69, "20": 30})
        self.assertTrue(p["award_score"])
        self.assertEqual(p["duration_minutes"], 69)

    def test_score_notifications_are_named(self):
        self.assertEqual(reward_for_minutes(69), 1150)
        self.assertEqual(
            thank_message("企画A", 69, 1150),
            "🎬【企画A】への撮影協力ありがとうございました！ 69分 → "
            "**1.150 スコア** 獲得しました。",
        )
        self.assertNotIn("獲得", thank_message("企画A", 69, None))

    def test_all_three_fields_required(self):
        ids = ["10", "20"]
        docs = [
            {"_id": "1:10", "user_id": "10", "name": "A", "color": "#FFFFFF",
             "pictures": [{"message_id": 1}]},
            {"_id": "1:20", "user_id": "20", "name": "B", "color": "#FFFF00"},
        ]
        self.assertEqual(missing_assets(ids, docs), ["20"])
        docs[1]["pictures"] = [{"message_id": 2}]
        self.assertEqual(missing_assets(ids, docs), [])

    def test_status_names(self):
        self.assertEqual(STATUS_LABELS[STATUS_WAIT], "一部出演者の素材提出待機")
        self.assertEqual(STATUS_LABELS[STATUS_EDIT], "編集中")
        self.assertEqual(STATUS_LABELS[STATUS_REVIEW], "書き出し、出演者へチェック")
        self.assertEqual(STATUS_LABELS[STATUS_POSTED], "投稿完了")

    def test_production_store_auto_promotes_only_when_all_ready(self):
        profiles = [
            {"_id": "123:10", "user_id": "10", "name": "A",
             "color": "#FFFFFF", "pictures": [1]},
        ]
        plan = {
            "_id": "p", "guild_id": 123, "status": STATUS_WAIT,
            "member_ids": ["10", "20"],
        }
        class Plans:
            def find_one(self, query):
                return plan.copy()
            def update_one(self, query, update):
                plan.update(update["$set"])
        store = object.__new__(ProductionStore)
        store.plans = Plans()
        store.profiles = SimpleNamespace(find=Mock(return_value=profiles))
        after = store.refresh_assets("p")
        self.assertEqual(after["status"], STATUS_WAIT)
        self.assertEqual(after["missing_member_ids"], ["20"])
        profiles.append({
            "_id": "123:20", "user_id": "20", "name": "B",
            "color": "#EEEEEE", "pictures": [1],
        })
        store.profiles.find.return_value = profiles
        after = store.refresh_assets("p")
        self.assertEqual(after["status"], STATUS_EDIT)
        self.assertEqual(after["missing_member_ids"], [])

    def test_discord_commands_and_modal(self):
        from alien_bot.cogs.plan_management import (
            PlanCog, ReviewView, RevisionModal, PlanManualView,
        )
        from alien_bot.cogs.shooting import ShootingCog, ShootNameView, ShootNameModal

        expected = (
            "zzzadmin_planinfo", "zzzadin_planinfo",
            "zzzadmin_plans", "zzzadmin_plan",
            "zzzadmin_plan_refresh", "zzzadmin_plan_review",
            "zzzadmin_plan_collab", "zzzadmin_plan_posted",
        )
        names = {cmd.name for cmd in PlanCog.__dict__.values()
                 if hasattr(cmd, "name") and hasattr(cmd, "callback")}
        for name in expected:
            self.assertIn(name, names)
        self.assertEqual(ShootingCog.shootname.name, "zzzadmin_shootname")
        self.assertEqual(ShootingCog.shootfinish.name, "zzzadmin_shootfinish")
        self.assertTrue(ReviewView(SimpleNamespace()).is_persistent())
        self.assertEqual(len(ShootNameModal(SimpleNamespace()).children), 1)
        view = PlanManualView(SimpleNamespace(), 123, {})
        self.assertEqual(len(view.children), 1)
        self.assertEqual(view.children[0].max_values, 25)

    def test_shootfinish_does_not_grant_before_name(self):
        from alien_bot.cogs.shooting import ShootingCog
        class Store:
            def __init__(self):
                self.grant_shoot = Mock()
                self.shoots = SimpleNamespace(
                    find_one=Mock(return_value={
                        "_id": "current", "phase": "running", "shoot_id": "123",
                        "channel_id": 5,
                    }),
                    update_one=Mock(),
                )
                self.confirmations = SimpleNamespace(update_many=Mock())

        async def run():
            cog = object.__new__(ShootingCog)
            cog.store = Store()
            cog.lock = asyncio.Lock()
            cog.permitted = Mock(return_value=True)
            cog.set_status = AsyncMock(return_value=None)
            interaction = SimpleNamespace(
                response=SimpleNamespace(defer=AsyncMock()),
                guild=SimpleNamespace(voice_client=None),
                followup=SimpleNamespace(send=AsyncMock()),
            )
            await ShootingCog.shootfinish.callback(cog, interaction)
            cog.store.grant_shoot.assert_not_called()
            cog.store.shoots.update_one.assert_called_once()
            self.assertEqual(
                cog.store.shoots.update_one.call_args.args[1]["$set"]["phase"],
                "awaiting_name",
            )
            message = interaction.followup.send.call_args.args[0]
            self.assertIn("続いて企画名", message)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
