"""撮影協力者の作業場・通知ロール・案内メッセージの管理。"""

import asyncio
from datetime import datetime, timedelta, timezone
import os
import random

import discord
from discord import app_commands
from discord.ext import commands, tasks
from pymongo import MongoClient


TASK_CATEGORY_ID = 1561752871530270840
ADMIN_ROLE_ID = 1541554412126994594
NOTIFICATION_ROLES = {
    "配信募集通知": 1561755518878617630,
    "業務連絡通知": 1561755552198037564,
    "その他通知": 1561755578303254680,
    "急募通知": 1561755615821303979,
}
NOTIFICATION_ROLE_IDS = set(NOTIFICATION_ROLES.values())
NOTICE_GUIDE = "/noticeを各自の作業場で利用することで、通知の設定を変更できます。"


class ServerManagement(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.guild_id = bot.target_guild_id
        self.client = MongoClient(
            os.environ["MONGO_URL"], serverSelectionTimeoutMS=8000
        )
        db = self.client["nkii_nkii_alien"]
        self.workspaces = db["workspaces"]
        self.preferences = db["notification_preferences"]
        self.announcements = db["temporary_announcements"]
        self.member_locks = {}
        self.bootstrap_started = False
        self.cleanup_expired.start()

    def cog_unload(self):
        self.cleanup_expired.cancel()
        self.client.close()

    @staticmethod
    def is_worker(member: discord.Member) -> bool:
        return not member.bot and all(role.id != ADMIN_ROLE_ID for role in member.roles)

    def member_lock(self, member_id: int):
        return self.member_locks.setdefault(member_id, asyncio.Lock())

    def workspace_key(self, member_id: int):
        return f"{self.guild_id}:{member_id}"

    async def find_workspace(self, member: discord.Member):
        guild = member.guild
        key = self.workspace_key(member.id)
        record = await asyncio.to_thread(self.workspaces.find_one, {"_id": key})
        category = guild.get_channel(TASK_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            raise RuntimeError("タスクカテゴリが見つかりません")

        channel = guild.get_channel(record.get("channel_id")) if record else None
        if isinstance(channel, discord.TextChannel) and channel.category_id == TASK_CATEGORY_ID:
            return channel

        marker = f"alien_workspace_owner:{member.id}"
        for candidate in category.text_channels:
            if candidate.topic == marker:
                await asyncio.to_thread(
                    self.workspaces.update_one,
                    {"_id": key},
                    {"$set": {"channel_id": candidate.id}},
                    True,
                )
                return candidate
        return None

    async def ensure_workspace(self, member: discord.Member):
        guild = member.guild
        channel = await self.find_workspace(member)
        if channel:
            # サーバーに再参加した際、以前の作業場を再び使えるようにする。
            overwrite = channel.overwrites_for(member)
            if overwrite.view_channel is not True:
                await channel.set_permissions(
                    member,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    reason="撮影協力者の作業場を再開",
                )
            return channel

        category = guild.get_channel(TASK_CATEGORY_ID)
        admin_role = guild.get_role(ADMIN_ROLE_ID)
        bot_member = guild.me or await guild.fetch_member(self.bot.user.id)
        if not isinstance(category, discord.CategoryChannel) or admin_role is None:
            raise RuntimeError("タスクカテゴリまたは管理者ロールが見つかりません")

        # Discordのカテゴリ内チャンネル数の上限を超えて作らない。
        if len(category.channels) >= 50:
            raise RuntimeError("タスクカテゴリが50チャンネルの上限に達しました")

        channel = await guild.create_text_channel(
            name=f"{member.display_name}の作業場"[:100],
            category=category,
            topic=f"alien_workspace_owner:{member.id}",
            overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                admin_role: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                ),
                member: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                ),
                bot_member: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True,
                    manage_messages=True
                ),
            },
            reason="撮影協力者専用の作業場を作成",
        )
        await asyncio.to_thread(
            self.workspaces.update_one,
            {"_id": self.workspace_key(member.id)},
            {"$set": {"channel_id": channel.id}},
            True,
        )
        print(f"[Alien/Workspace] 作成: member={member.id} channel={channel.id}", flush=True)
        return channel

    async def apply_initial_roles(self, member: discord.Member, *, joined: bool = False):
        roles = {role_id: member.guild.get_role(role_id) for role_id in NOTIFICATION_ROLE_IDS}
        if any(role is None for role in roles.values()):
            raise RuntimeError("指定された通知ロールの一部が見つかりません")

        key = self.workspace_key(member.id)
        prefs = await asyncio.to_thread(self.preferences.find_one, {"_id": key})
        old_flags = prefs.get("role_flags", {}) if prefs else {}
        initial = prefs is None or not prefs.get("initialized", False)

        if initial:
            # 手動で/noticeが先に使われた場合は、その選択だけ引き継ぐ。
            desired = {
                str(role_id): bool(old_flags.get(str(role_id), True))
                for role_id in NOTIFICATION_ROLE_IDS
            }
        elif joined:
            # 再入室時は以前の通知設定を復元する。
            desired = {
                str(role_id): bool(old_flags.get(str(role_id), True))
                for role_id in NOTIFICATION_ROLE_IDS
            }
        else:
            # 再起動時は既存ユーザーの設定を上書きしない。
            return

        to_add = [
            role for role_id, role in roles.items()
            if desired[str(role_id)] and role not in member.roles
        ]
        to_remove = [
            role for role_id, role in roles.items()
            if not desired[str(role_id)] and role in member.roles
        ]
        if to_add:
            await member.add_roles(*to_add, reason="通知ロール初期設定/再参加時の復元")
        if to_remove:
            await member.remove_roles(*to_remove, reason="通知設定の復元")
        await asyncio.to_thread(
            self.preferences.update_one,
            {"_id": key},
            {"$set": {"initialized": True, "role_flags": desired}},
            True,
        )

    async def synchronize_member(self, member: discord.Member, *, joined: bool = False):
        if member.guild.id != self.guild_id or not self.is_worker(member):
            return
        async with self.member_lock(member.id):
            try:
                await self.ensure_workspace(member)
                await self.apply_initial_roles(member, joined=joined)
            except (discord.Forbidden, discord.HTTPException, RuntimeError, OSError) as exc:
                print(f"[Alien/Workspace] member={member.id} error: {exc}", flush=True)
            except Exception as exc:
                print(f"[Alien/Workspace] member={member.id} unexpected: {type(exc).__name__}: {exc}", flush=True)

    async def bootstrap(self):
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            print("[Alien/Workspace] 対象サーバーが見つかりません", flush=True)
            return
        print("[Alien/Workspace] 既存メンバーの初回同期を開始", flush=True)
        count = 0
        try:
            async for member in guild.fetch_members(limit=None):
                if self.is_worker(member):
                    await self.synchronize_member(member)
                    count += 1
            print(f"[Alien/Workspace] 同期終了: 対象 {count} 人", flush=True)
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[Alien/Workspace] メンバー取得エラー: {exc}", flush=True)

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.bootstrap_started:
            self.bootstrap_started = True
            asyncio.create_task(self.bootstrap())

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.guild.id == self.guild_id:
            await self.synchronize_member(member, joined=True)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.guild.id != self.guild_id:
            return
        if not self.is_worker(before) and self.is_worker(after):
            await self.synchronize_member(after, joined=True)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        # 記録や作業場の誤削除を防止。再参加したら同じ作業場を再利用する。
        if member.guild.id == self.guild_id and not member.bot:
            print(f"[Alien/Workspace] 退出: member={member.id} (作業場は保持)", flush=True)

    @app_commands.command(name="notice", description="自分の通知ロールを付け外しします")
    @app_commands.describe(what="変更したい通知の種類")
    @app_commands.choices(what=[
        app_commands.Choice(name="配信募集通知", value="1561755518878617630"),
        app_commands.Choice(name="業務連絡通知", value="1561755552198037564"),
        app_commands.Choice(name="その他通知", value="1561755578303254680"),
        app_commands.Choice(name="急募通知", value="1561755615821303979"),
    ])
    async def notice(self, interaction: discord.Interaction, what: app_commands.Choice[str]):
        guild = interaction.guild
        member = interaction.user
        if (
            guild is None or guild.id != self.guild_id
            or not isinstance(member, discord.Member)
            or not self.is_worker(member)
        ):
            await interaction.response.send_message("撮影協力者専用のコマンドです。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            workspace = await self.find_workspace(member)
            if workspace is None or workspace.id != interaction.channel_id:
                await interaction.followup.send(
                    "このコマンドは自分の作業場チャンネルで利用してください。",
                    ephemeral=True,
                )
                return
            role_id = int(what.value)
            if role_id not in NOTIFICATION_ROLE_IDS:
                await interaction.followup.send("指定された通知は変更できません。", ephemeral=True)
                return
            role = guild.get_role(role_id)
            if role is None:
                await interaction.followup.send("通知ロールが見つかりません。", ephemeral=True)
                return
            async with self.member_lock(member.id):
                fresh = await guild.fetch_member(member.id)
                currently_enabled = role in fresh.roles
                if currently_enabled:
                    await fresh.remove_roles(role, reason="本人による/notice設定")
                else:
                    await fresh.add_roles(role, reason="本人による/notice設定")
                flags = {str(role_id): role_obj in fresh.roles for role_id, role_obj in [
                    (rid, guild.get_role(rid)) for rid in NOTIFICATION_ROLE_IDS
                ]}
                flags[str(role_id)] = not currently_enabled
                await asyncio.to_thread(
                    self.preferences.update_one,
                    {"_id": self.workspace_key(member.id)},
                    {"$set": {"initialized": True, "role_flags": flags}},
                    True,
                )
            state = "OFF（ロールを外しました）" if currently_enabled else "ON（ロールを付けました）"
            await interaction.followup.send(
                f"「{role.name}」の通知を **{state}**", ephemeral=True
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[Alien/Notice] Discord操作エラー: {exc}", flush=True)
            await interaction.followup.send(
                "権限不足などで変更できませんでした。管理者にお問い合わせください。",
                ephemeral=True,
            )
        except Exception as exc:
            print(f"[Alien/Notice] 保存エラー: {type(exc).__name__}: {exc}", flush=True)
            await interaction.followup.send(
                "通知設定を保存できませんでした。しばらくしてから再試行してください。",
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.guild.id != self.guild_id or message.author.bot:
            return
        if not (NOTIFICATION_ROLE_IDS & {role.id for role in message.role_mentions}):
            return
        if random.random() >= 0.10:
            return
        try:
            sent = await message.channel.send(
                NOTICE_GUIDE,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            # Discord側の自動削除タイマーに加え、再起動後の削除もDBで管理する。
            await asyncio.to_thread(
                self.announcements.insert_one,
                {
                    "channel_id": sent.channel.id,
                    "message_id": sent.id,
                    "delete_at": datetime.now(timezone.utc) + timedelta(hours=1),
                },
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            print(f"[Alien/Notice] 案内送信エラー: {exc}", flush=True)
        except Exception as exc:
            print(f"[Alien/Notice] 案内保存エラー: {type(exc).__name__}: {exc}", flush=True)

    @tasks.loop(minutes=1)
    async def cleanup_expired(self):
        now = datetime.now(timezone.utc)
        try:
            records = await asyncio.to_thread(
                lambda: list(self.announcements.find(
                    {"delete_at": {"$lte": now}}
                ).limit(100))
            )
        except Exception as exc:
            print(f"[Alien/Notice] 削除予約の取得失敗: {exc}", flush=True)
            return
        for doc in records:
            try:
                channel = self.bot.get_channel(doc["channel_id"])
                if channel is None:
                    channel = await self.bot.fetch_channel(doc["channel_id"])
                message = channel.get_partial_message(doc["message_id"])
                await message.delete()
            except discord.NotFound:
                pass
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"[Alien/Notice] 自動削除失敗: {exc}", flush=True)
                continue
            except Exception as exc:
                print(f"[Alien/Notice] 自動削除エラー: {exc}", flush=True)
                continue
            await asyncio.to_thread(self.announcements.delete_one, {"_id": doc["_id"]})

    @cleanup_expired.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    if not os.environ.get("MONGO_URL"):
        raise RuntimeError("サーバー管理には既存のMONGO_URLが必要です")
    await bot.add_cog(ServerManagement(bot))
