"""Google Driveの限定共有・認証・画像転送キューの回帰テスト。"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from alien_bot.drive_storage import (
    DriveClient, DRIVE_SCOPE, FOLDER_NAMES, ROOT_NAME,
    folder_url, normalized_email, safe_name,
)


class DrivePolicyTests(unittest.TestCase):
    def test_scope_and_folder_layout(self):
        self.assertEqual(DRIVE_SCOPE, "https://www.googleapis.com/auth/drive.file")
        self.assertEqual(
            tuple(FOLDER_NAMES), ("rules", "common", "members", "projects", "completed")
        )
        self.assertTrue(ROOT_NAME)
        self.assertIn("drive.google.com/drive/folders/", folder_url("123"))

    def test_email_and_name_validation(self):
        self.assertEqual(normalized_email(" Member+ONE@Gmail.com "), "member+one@gmail.com")
        for bad in ("", "abc", "a@b", "a@gmail.com\\n@everyone", "https://drive.google.com"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalized_email(bad)
        self.assertEqual(safe_name("../common"), ".._common")
        for bad in ("", ".", ".."):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                safe_name(bad)

    def test_share_is_email_only_never_anyone(self):
        client = object.__new__(DriveClient)
        client._permissions = Mock(return_value=[])
        client._json = Mock(return_value={"id": "abc123"})
        permission_id = client.grant("folder123", "User@gmail.com")
        self.assertEqual(permission_id, "abc123")
        args, kwargs = client._json.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(kwargs["json"], {
            "type": "user", "role": "reader", "emailAddress": "user@gmail.com"
        })
        self.assertNotEqual(kwargs["json"]["type"], "anyone")

    def test_permission_grant_deduplicates_and_revoke(self):
        client = object.__new__(DriveClient)
        client._permissions = Mock(return_value=[
            {"id": "existing", "type": "user",
             "emailAddress": "u@gmail.com", "role": "reader"}
        ])
        client._json = Mock(return_value={"id": "existing"})
        self.assertEqual(client.grant("folder", "u@gmail.com"), "existing")
        client._json.assert_not_called()
        client.grant("folder", "u@gmail.com", "writer")
        self.assertEqual(client._json.call_args.args[0], "PATCH")
        self.assertEqual(client._json.call_args.kwargs["json"], {"role": "writer"})
        response = SimpleNamespace(ok=True, status_code=204)
        client.session = SimpleNamespace(delete=Mock(return_value=response))
        self.assertTrue(client.revoke("folder", "u@gmail.com"))
        client.session.delete.assert_called_once()

    def test_folder_lookup_escapes_name_and_filters_parent(self):
        client = object.__new__(DriveClient)
        client._json = Mock(return_value={"files": [{"id": "folder"}]})
        self.assertEqual(client.find_folder("root", "test's"), "folder")
        _, kwargs = client._json.call_args
        self.assertIn("'root' in parents", kwargs["params"]["q"])
        self.assertIn("trashed = false", kwargs["params"]["q"])

    def test_drive_cog_commands_and_auth_not_loaded_without_client(self):
        from alien_bot.cogs.drive import DriveCog
        names = (
            "zzzadmin_drive_setup", "zzzadmin_drive_member",
            "zzzadmin_drive_revoke", "zzzadmin_drive_project",
            "zzzadmin_drive_project_access", "zzzadmin_drive_projects",
            "zzzadmin_drive_upload", "zzzadmin_drive_finished_access",
            "files", "my_files",
        )
        actual = {cmd.name for cmd in DriveCog.__dict__.values()
                  if hasattr(cmd, "name") and hasattr(cmd, "callback")}
        for name in names:
            self.assertIn(name, actual)

    def test_drive_finished_sharing_checks_parent_before_grant(self):
        from alien_bot.cogs.drive import DriveCog

        async def check():
            cog = object.__new__(DriveCog)
            cog.bot = SimpleNamespace(target_guild_id=1)
            cog.members = SimpleNamespace(find_one=Mock(
                return_value={"email": "user@gmail.com"}
            ))
            cog.drive = SimpleNamespace(
                file_info=Mock(return_value={"parents": ["another_folder"]}),
                grant=Mock(),
            )
            cog.require_setup = AsyncMock(
                return_value={"completed": "completed_folder"}
            )
            member = SimpleNamespace(id=42, bot=False)
            interaction = SimpleNamespace(
                response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
                followup=SimpleNamespace(send=AsyncMock()),
            )
            cog.admin = Mock(return_value=True)
            await DriveCog.finished_access.callback(
                cog, interaction, "file", member
            )
            cog.drive.grant.assert_not_called()
            self.assertIn(
                "完成動画フォルダ", interaction.followup.send.call_args.args[0]
            )
        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
