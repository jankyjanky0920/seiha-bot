"""編集情報フォーム・入力変更・SNS同上・管理者通知の自動テスト。"""

import unittest
from types import SimpleNamespace

from alien_bot.edit_info_data import (
    complete, due_notification, parse_edit_fields, parse_linked,
    parse_description, description_resolved,
)


class TestEditInfo(unittest.TestCase):
    def test_partial_updates(self):
        old = {
            "color": "#112233", "name": "旧活動名",
            "pictures": [{"filename": "a.png", "message_id": 1}],
            "linked_sns": {"YouTube": "https://youtube.com/@a"},
            "description_sns": {"YouTube": {"same_as": "YouTube"}},
        }
        changed = parse_edit_fields("", "新活動名", "", "", old)
        self.assertEqual(changed, {"name": "新活動名"})
        self.assertTrue(complete({**old, **changed}))
        self.assertEqual(old["name"], "旧活動名")

    def test_completion_requires_three(self):
        profile = {"color": "#123456", "name": "ABC"}
        self.assertFalse(complete(profile))
        profile["pictures"] = [{"message_id": 1}]
        self.assertTrue(complete(profile))
        self.assertFalse(complete({**profile, "color": ""}))

    def test_error_and_incomplete_reminders(self):
        self.assertIsNone(due_notification({"watch": False}))
        self.assertEqual(due_notification({"watch": True}), "missing")
        self.assertEqual(due_notification({"watch": True, "error": "写真が不鮮明"}), "error")
        self.assertIsNone(due_notification({
            "watch": True, "color": "#123456", "name": "A",
            "pictures": [{"message_id": 1}], "error": None,
        }))

    def test_color(self):
        self.assertEqual(parse_edit_fields("#aabbcc", "", "", "")["color"], "#AABBCC")
        for color in ("red", "#123", "#12345678", "<script>"):
            with self.subTest(color=color):
                with self.assertRaises(ValueError):
                    parse_edit_fields(color, "", "", "")

    def test_two_sns_groups_and_reference(self):
        linked = parse_linked(
            "YouTube: https://youtube.com/@someone\n"
            "TikTok: https://www.tiktok.com/@someone\n"
            "X: https://x.com/someone"
        )
        desc = parse_description(
            "YouTube同上\npixiv: https://www.pixiv.net/users/123\nX: 同上",
            linked,
        )
        self.assertEqual(len(desc), 3)
        self.assertEqual(
            description_resolved(desc, linked)["YouTube"],
            "https://youtube.com/@someone",
        )
        self.assertEqual(
            description_resolved(desc, linked)["pixiv"],
            "https://www.pixiv.net/users/123",
        )

    def test_description_limit(self):
        with self.assertRaises(ValueError):
            parse_description(
                "a: https://example.com\nb: https://example.com\n"
                "c: https://example.com\nd: https://example.com", {},
            )

    def test_linked_url_wrong_domain(self):
        with self.assertRaises(ValueError):
            parse_linked("YouTube: https://x.com/wrong")

    def test_empty_fields_do_not_erase_social_accounts(self):
        previous = {
            "linked_sns": {"X": "https://x.com/me"},
            "description_sns": {"X": {"same_as": "X"}},
        }
        self.assertEqual(parse_edit_fields("", "", "", "", previous), {})

    def test_discord_cog_registration(self):
        import discord
        from alien_bot.cogs.edit_info import (
            EditInfoCog, EditInfoModal, MemberSelectView,
            ACK, REMIND_MISSING, reminder_error,
        )
        self.assertTrue(hasattr(discord.ui, "FileUpload"))
        self.assertEqual(EditInfoCog.edit_info.name, "edit_info")
        self.assertEqual(EditInfoCog.admin_edit.name, "zzzadmin_edit")
        self.assertEqual(EditInfoCog.admin_edit_info.name, "zzzadmin_edit_info")
        self.assertIn("提出ありがとうございます！", ACK)
        self.assertIn("@everyone", REMIND_MISSING)
        self.assertIn("@everyone", reminder_error("あいうえお"))
        self.assertIn("あいうえお", reminder_error("あいうえお"))
        modal = EditInfoModal(cog=SimpleNamespace())
        self.assertEqual(len(modal.children), 5)
        view = MemberSelectView(cog=SimpleNamespace(), mode="report")
        self.assertEqual(len(view.children), 1)


if __name__ == "__main__":
    unittest.main()
