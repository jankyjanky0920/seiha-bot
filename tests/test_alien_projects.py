"""企画募集: 形式・日跨ぎ・人数範囲・複数募集の未回答判定。"""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from importlib import import_module

from alien_bot.project_format import (
    parse_project_form, build_recruitment_message, unanswered_by_member, JST,
)

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
DATES = "11/14 13:00 21:00\n11/15 13:00 21:00"
PLANS = "AI vs 人間 | 2\n素人ギャンブラー | 3-7\n人狼 | 8-"


class RecruitmentTests(unittest.TestCase):
    def test_example(self):
        form = parse_project_form(DATES, PLANS, now=NOW)
        self.assertEqual(len(form["slots"]), 4)
        self.assertEqual(form["deadline_at"].date().isoformat(), "2026-11-13")
        msg = build_recruitment_message(form, 1561755446304317532)
        self.assertIn("1️⃣11/14(土)13:00~16:00", msg)
        self.assertIn("2️⃣11/14(土)21:00~24:00", msg)
        self.assertIn("3️⃣11/15(日)13:00~16:00", msg)
        self.assertIn("4️⃣11/15(日)21:00~24:00", msg)
        self.assertIn("3~7人の場合", msg)
        self.assertIn("8人以上の場合", msg)
        self.assertIn("1人の場合\n中止", msg)
        self.assertIn("<@&1561755446304317532>", msg)

    def test_overnight_time(self):
        form = parse_project_form("11/14 25:00", "テスト | 2-", now=NOW)
        msg = build_recruitment_message(form, 1)
        self.assertIn("25:00~28:00", msg)
        self.assertEqual(form["slots"][0]["start_at"].day, 15)

    def test_year_rollover(self):
        form = parse_project_form(
            "1/3 10:00", "テスト | 2-",
            now=datetime(2026, 12, 20, tzinfo=JST),
        )
        self.assertEqual(form["slots"][0]["base_date"], "2027-01-03")

    def test_overlap_or_gap(self):
        for plans in (
            "A | 2-3\nB | 3-",
            "A | 2\nB | 4-",
            "A | 2\nB | 3-5",
        ):
            with self.subTest(plans=plans):
                with self.assertRaises(ValueError):
                    parse_project_form("11/14 13:00", plans, now=NOW)

    def test_expired_deadline(self):
        with self.assertRaises(ValueError):
            parse_project_form(
                "10/21 13:00", "テスト | 2-",
                now=datetime(2026, 10, 21, 0, 0, tzinfo=JST),
            )

    def test_answer_any_reaction_and_overlapping_campaigns(self):
        one = {"responses": {"10": ["1️⃣"], "20": []}}
        two = {"responses": {"20": ["❌"]}}
        self.assertTrue(unanswered_by_member(10, [one, two]))
        self.assertTrue(unanswered_by_member(20, [one, two]))
        self.assertFalse(unanswered_by_member(10, [one]))
        self.assertFalse(unanswered_by_member(20, [two]))
        self.assertFalse(unanswered_by_member(10, []))

    def test_notification_targets_exclude_admin_and_bots(self):
        from types import SimpleNamespace
        from alien_bot.cogs.projects import is_notification_target
        admin = SimpleNamespace(
            bot=False, roles=[SimpleNamespace(id=1541554412126994594)]
        )
        regular = SimpleNamespace(bot=False, roles=[])
        bot_user = SimpleNamespace(bot=True, roles=[])
        self.assertFalse(is_notification_target(admin))
        self.assertTrue(is_notification_target(regular))
        self.assertFalse(is_notification_target(bot_user))

    def test_admin_role_removed_during_reconciliation(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from alien_bot.cogs.projects import ProjectsCog, PROJECT_ROLE_ID

        project_role = SimpleNamespace(id=PROJECT_ROLE_ID)
        admin_role = SimpleNamespace(id=1541554412126994594)
        guild = SimpleNamespace(
            id=123, get_role=lambda role_id: project_role
        )
        admin = SimpleNamespace(
            id=10, bot=False, guild=guild, roles=[admin_role, project_role],
            add_roles=AsyncMock(), remove_roles=AsyncMock(),
        )
        worker = SimpleNamespace(
            id=20, bot=False, guild=guild, roles=[],
            add_roles=AsyncMock(), remove_roles=AsyncMock(),
        )
        cog = object.__new__(ProjectsCog)
        cog.bot = SimpleNamespace(target_guild_id=123)
        cog.member_locks = {}
        active = [{"responses": {}}]
        asyncio.run(cog.sync_member(admin, active))
        admin.remove_roles.assert_awaited_once()
        admin.add_roles.assert_not_awaited()
        asyncio.run(cog.sync_member(worker, active))
        worker.add_roles.assert_awaited_once()
        worker.remove_roles.assert_not_awaited()

    def test_initial_publishing_excludes_and_cleans_admin(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from alien_bot.cogs.projects import ProjectsCog, PROJECT_ROLE_ID

        project_role = SimpleNamespace(id=PROJECT_ROLE_ID)
        admin_role = SimpleNamespace(id=1541554412126994594)
        admin = SimpleNamespace(
            id=10, bot=False, roles=[admin_role, project_role],
            add_roles=AsyncMock(), remove_roles=AsyncMock(),
        )
        worker = SimpleNamespace(
            id=20, bot=False, roles=[],
            add_roles=AsyncMock(), remove_roles=AsyncMock(),
        )
        bot_user = SimpleNamespace(
            id=30, bot=True, roles=[],
            add_roles=AsyncMock(), remove_roles=AsyncMock(),
        )
        async def members():
            for member in (admin, worker, bot_user):
                yield member
        guild = SimpleNamespace(
            get_role=lambda role_id: project_role,
            fetch_members=lambda limit=None: members(),
        )
        cog = object.__new__(ProjectsCog)
        asyncio.run(cog.assign_initial_notification_roles(guild))
        admin.add_roles.assert_not_awaited()
        admin.remove_roles.assert_awaited_once()
        worker.add_roles.assert_awaited_once()
        bot_user.add_roles.assert_not_awaited()

    def test_discord_cog_import(self):
        module = import_module("alien_bot.cogs.projects")
        self.assertEqual(module.ProjectsCog.create.name, "zzzadmin_project")


if __name__ == "__main__":
    unittest.main()
