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

    def test_mismatched_platform_label_is_not_an_error(self):
        # SNS名とURLの種類が違っていても、URLを優先して保存する。
        parsed = parse_linked("YouTube: https://x.com/wrong")
        self.assertEqual(parsed["X"], "https://x.com/wrong")

    def test_sns_links_without_required_format(self):
        data = parse_edit_fields(
            "", "",
            "https://www.youtube.com/@someone\n"
            "x.com/someone\n"
            "www.tiktok.com/@someone",
            "",
        )
        self.assertEqual(data["linked_sns"]["YouTube"], "https://www.youtube.com/@someone")
        self.assertEqual(data["linked_sns"]["X"], "https://x.com/someone")
        self.assertEqual(data["linked_sns"]["TikTok"], "https://www.tiktok.com/@someone")
        self.assertIn("x.com/someone", data["linked_sns_raw"])

    def test_flexible_sns_text_does_not_block_submission(self):
        # URLでなくても拒否せず、原文を管理者に渡す。
        example = "このアカウントでお願いします：@testuser"
        data = parse_edit_fields("", "", example, "")
        self.assertEqual(data["linked_sns_raw"], example)
        self.assertEqual(data["linked_sns"], {})

        odd_url = "https://xn--26jzf6ayai9cybo9117eusc./"
        result = parse_edit_fields("", "", odd_url, "")
        self.assertEqual(result["linked_sns_raw"], odd_url)

        named = parse_linked("Youtube @someone\nX @someone_else")
        self.assertEqual(named["YouTube"], "@someone")
        self.assertEqual(named["X"], "@someone_else")

    def test_description_url_only_and_same_as_without_link(self):
        desc = parse_description(
            "https://www.pixiv.net/users/123\nYouTube同上", {}
        )
        self.assertEqual(len(desc), 2)
        self.assertEqual(desc["YouTube"], {"same_as": "YouTube"})
        self.assertEqual(
            description_resolved(desc, {})["YouTube"], "YouTube同上"
        )
        self.assertEqual(
            description_resolved(desc, {})["pixiv"],
            "https://www.pixiv.net/users/123",
        )

    def test_description_freeform_notes_and_whitespace_label(self):
        data = parse_edit_fields(
            "", "", "", "pixiv https://www.pixiv.net/users/10\nX: 同上\n自作ホームページ",
        )
        self.assertEqual(len(data["description_sns"]), 3)
        self.assertEqual(
            data["description_sns"]["pixiv"],
            {"url": "https://www.pixiv.net/users/10"},
        )
        self.assertEqual(data["description_sns"]["X"], {"same_as": "X"})
        self.assertEqual(data["description_sns"]["メモ1"]["url"], "自作ホームページ")
        self.assertEqual(data["description_sns_raw"].count("\n"), 2)

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
        self.assertIsInstance(modal.linked_sns, discord.ui.Label)
        self.assertIsInstance(modal.description_sns, discord.ui.Label)
        self.assertIsInstance(modal.linked_sns.component, discord.ui.TextInput)
        self.assertIsInstance(modal.description_sns.component, discord.ui.TextInput)
        self.assertIn("コラボレーション依頼", modal.linked_sns.description)
        self.assertIn("URLだけでも", modal.linked_sns.description)
        self.assertIn("3件まで", modal.description_sns.description)
        self.assertIn("「同上」", modal.description_sns.description)
        self.assertLessEqual(len(modal.linked_sns.description), 100)
        self.assertLessEqual(len(modal.description_sns.description), 100)
        self.assertEqual(str(modal.linked_sns.component), "")
        self.assertEqual(str(modal.description_sns.component), "")
        view = MemberSelectView(cog=SimpleNamespace(), mode="report")
        self.assertEqual(len(view.children), 1)


if __name__ == "__main__":
    unittest.main()
