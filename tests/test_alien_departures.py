"""サーバー退室時の記録投稿と個人作業場削除を検証。"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from alien_bot.cogs.server_management import (
    ServerManagement, DEPARTURE_LOG_CHANNEL_ID, TASK_CATEGORY_ID,
)


class FakeChannel:
    def __init__(self, channel_id, *, topic=None, category_id=None):
        self.id = channel_id
        self.topic = topic
        self.category_id = category_id
        self.delete = AsyncMock()
        self.send = AsyncMock()


class FakeWorkspaceCollection:
    def __init__(self, record):
        self.record = record
        self.deleted = []

    def find_one(self, query):
        return self.record

    def delete_one(self, query):
        self.deleted.append(query)
        self.record = None


def setup_member(owned=True):
    user_id, guild_id, workspace_id = 42, 123, 777
    workspace = FakeChannel(
        workspace_id,
        topic=("alien_workspace_owner:42" if owned else "alien_workspace_owner:99"),
        category_id=TASK_CATEGORY_ID,
    )
    log = FakeChannel(DEPARTURE_LOG_CHANNEL_ID)
    channels = {
        DEPARTURE_LOG_CHANNEL_ID: log,
        workspace_id: workspace,
    }
    guild = SimpleNamespace(
        id=guild_id, get_member=lambda uid: None,
        get_channel=lambda cid: channels.get(cid),
    )
    member = SimpleNamespace(
        id=user_id, guild=guild, name="tester", bot=False,
    )
    store = FakeWorkspaceCollection(
        {"_id": "123:42", "channel_id": workspace_id}
    )
    cog = object.__new__(ServerManagement)
    cog.guild_id = guild_id
    cog.member_locks = {}
    cog.workspaces = store
    cog.bot = SimpleNamespace(
        fetch_channel=AsyncMock(side_effect=RuntimeError("Unexpected channel fetch"))
    )
    return cog, member, workspace, log, store


class TestDepartures(unittest.TestCase):
    def test_leave_logs_and_deletes_only_owned_workspace(self):
        cog, member, workspace, log, store = setup_member()
        with patch("alien_bot.cogs.server_management.discord.TextChannel", FakeChannel):
            asyncio.run(cog.on_member_remove(member))
        log.send.assert_awaited_once()
        args, kwargs = log.send.await_args
        self.assertEqual(args[0], "testerが退室しました。")
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        workspace.delete.assert_awaited_once()
        self.assertEqual(store.deleted, [{"_id": "123:42"}])

    def test_wrong_owner_marker_never_deletes_other_channel(self):
        cog, member, workspace, log, store = setup_member(owned=False)
        with patch("alien_bot.cogs.server_management.discord.TextChannel", FakeChannel):
            asyncio.run(cog.on_member_remove(member))
        workspace.delete.assert_not_awaited()
        self.assertEqual(store.deleted, [{"_id": "123:42"}])
        log.send.assert_awaited_once()

    def test_log_failure_does_not_stop_workspace_cleanup(self):
        cog, member, workspace, log, store = setup_member()
        log.send.side_effect = OSError("log unavailable")
        with patch("alien_bot.cogs.server_management.discord.TextChannel", FakeChannel):
            asyncio.run(cog.on_member_remove(member))
        workspace.delete.assert_awaited_once()
        self.assertEqual(len(store.deleted), 1)

    def test_channel_delete_failure_keeps_workspace_record(self):
        cog, member, workspace, log, store = setup_member()
        workspace.delete.side_effect = OSError("channel delete unavailable")
        with patch("alien_bot.cogs.server_management.discord.TextChannel", FakeChannel):
            asyncio.run(cog.on_member_remove(member))
        self.assertEqual(store.deleted, [])
        log.send.assert_awaited_once()

    def test_rejoined_member_is_not_deleted(self):
        cog, member, workspace, log, store = setup_member()
        member.guild.get_member = lambda uid: member
        with patch("alien_bot.cogs.server_management.discord.TextChannel", FakeChannel):
            asyncio.run(cog.on_member_remove(member))
        workspace.delete.assert_not_awaited()
        self.assertEqual(store.deleted, [])
        log.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
