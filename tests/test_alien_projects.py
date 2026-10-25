"""企画募集: 形式・日跨ぎ・人数範囲・複数募集の未回答判定。"""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from importlib import import_module

from alien_bot.project_format import (
    parse_project_form, build_recruitment_message, unanswered_by_member,
    overdue_unanswered_by_member, JST,
)

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
DATES = "11/14 13:00 21:00\n11/15 13:00 21:00"
PLANS = "AI vs 人間 | 2\n素人ギャンブラー | 3-7\n人狼 | 8-"


class RecruitmentTests(unittest.TestCase):
    def test_example(self):
        form = parse_project_form(DATES, PLANS, now=NOW)
        self.assertEqual(len(form["slots"]), 4)
        self.assertEqual(form["deadline_at"].date().isoformat(), "2026-11-13")
        msg = build_recruitment_message(form)
        self.assertIn("1️⃣11/14(土)13:00~16:00", msg)
        self.assertIn("2️⃣11/14(土)21:00~24:00", msg)
        self.assertIn("3️⃣11/15(日)13:00~16:00", msg)
        self.assertIn("4️⃣11/15(日)21:00~24:00", msg)
        self.assertIn("3~7人の場合", msg)
        self.assertIn("8人以上の場合", msg)
        self.assertIn("1人の場合\n中止", msg)
        self.assertNotIn("<@&", msg)

    def test_overnight_time(self):
        form = parse_project_form("11/14 25:00", "テスト | 2-", now=NOW)
        msg = build_recruitment_message(form)
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

    def test_72hour_delay_and_multiple_campaigns(self):
        cutoff = datetime(2026, 10, 26, 18, 0, tzinfo=JST)
        old = cutoff - timedelta(hours=72)
        recent = cutoff - timedelta(hours=71, minutes=59)
        records = [
            {"created_at": old, "responses": {"10": ["1️⃣"]}},
            {"created_at": recent, "responses": {}},
        ]
        self.assertFalse(overdue_unanswered_by_member(10, records, cutoff))
        self.assertTrue(overdue_unanswered_by_member(20, records, cutoff))
        self.assertFalse(overdue_unanswered_by_member(
            20, records, cutoff - timedelta(seconds=1)
        ))
        self.assertFalse(overdue_unanswered_by_member(
            20, [{"responses": {}}], cutoff
        ))
        older_unanswered = {"created_at": old - timedelta(days=1), "responses": {}}
        self.assertTrue(overdue_unanswered_by_member(
            10, records + [older_unanswered], cutoff
        ))

    def test_workspace_reminder_content(self):
        from alien_bot.cogs.projects import REMINDER_TEXT
        self.assertEqual(REMINDER_TEXT.splitlines()[0], "@everyone")
        self.assertIn("まだ回答していない企画募集があります！", REMINDER_TEXT)
        self.assertIn("<#1544209717218508882>", REMINDER_TEXT)
        self.assertIn("なんらかのリアクションを行うことで", REMINDER_TEXT)
        self.assertNotIn("<@&", REMINDER_TEXT)

    def test_per_workspace_once_per_day_no_admins(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from pymongo.errors import DuplicateKeyError
        from alien_bot.cogs.projects import ProjectsCog

        when = datetime(2026, 10, 26, 18, tzinfo=JST)
        admin = SimpleNamespace(
            id=10, bot=False,
            roles=[SimpleNamespace(id=1541554412126994594)]
        )
        regular = SimpleNamespace(id=20, bot=False, roles=[])
        answered = SimpleNamespace(id=30, bot=False, roles=[])
        bot_member = SimpleNamespace(id=40, bot=True, roles=[])

        class FakeChannel:
            def __init__(self, guild):
                self.guild = guild
                self.id = 999
                self.send = AsyncMock(
                    return_value=SimpleNamespace(id=123)
                )

        async def fetch_members(limit=None):
            for member in (admin, regular, answered, bot_member):
                yield member

        guild = SimpleNamespace(id=111, fetch_members=fetch_members)
        channel = FakeChannel(guild)
        storage = {}
        def insert_one(value):
            if value["_id"] in storage:
                raise DuplicateKeyError("Already sent today")
            storage[value["_id"]] = value
        def update_one(query, update):
            storage[query["_id"]].update(update["$set"])
        def find_one(query):
            if query["_id"] == "111:20":
                return {"channel_id": 999}
            return None

        cog = object.__new__(ProjectsCog)
        cog.bot = SimpleNamespace(
            target_guild_id=111,
            get_guild=lambda guild_id: guild,
            get_channel=lambda channel_id: channel,
        )
        cog.workspaces = SimpleNamespace(find_one=find_one)
        cog.reminders = SimpleNamespace(
            insert_one=insert_one, update_one=update_one,
        )
        eligible = {
            "created_at": when - timedelta(days=4),
            "responses": {"30": ["❌"]},
        }
        recent = {
            "created_at": when - timedelta(days=1),
            "responses": {},
        }
        with patch.object(ProjectsCog, "current_projects", return_value=[eligible, recent]):
            with patch("alien_bot.cogs.projects.discord.TextChannel", FakeChannel):
                self.assertEqual(asyncio.run(cog.send_workspace_reminders(when)), 1)
                self.assertEqual(asyncio.run(cog.send_workspace_reminders(when)), 0)
        channel.send.assert_awaited_once()
        self.assertIn("workspace:2026-10-26:111:20", storage)
        self.assertEqual(storage["workspace:2026-10-26:111:20"]["state"], "posted")

    def test_discord_cog_import(self):
        module = import_module("alien_bot.cogs.projects")
        self.assertEqual(module.ProjectsCog.create.name, "zzzadmin_project")


if __name__ == "__main__":
    unittest.main()
