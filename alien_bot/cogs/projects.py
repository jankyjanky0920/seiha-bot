"""企画募集の確認投稿・リアクション記録・未回答者の作業場への18時通知。"""

import asyncio
import os
from datetime import datetime, time as dt_time, timedelta, timezone
from uuid import uuid4

import discord
from discord import app_commands
from discord.ext import commands, tasks
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from zoneinfo import ZoneInfo

from alien_bot.project_format import (
    EMOJIS, NO_ATTEND, JST, parse_project_form, build_recruitment_message,
    overdue_unanswered_by_member,
)
from alien_bot.score_store import is_admin, utcnow


PROJECT_CHANNEL_ID = 1544209717218508882

VALID_REACTIONS = set(EMOJIS) | {NO_ATTEND}
REMINDER_TEXT = (
    "@everyone\n"
    "まだ回答していない企画募集があります！\n"
    f"（企画募集チャンネル <#{PROJECT_CHANNEL_ID}>）にて、"
    "まだ確認していない募集メッセージにリアクションしてください。\n"
    "募集メッセージになんらかのリアクションを行うことでこの通知は停止します。"
)


def is_notification_target(member) -> bool:
    """一般の撮影協力者のみ作業場での通知対象とする。"""
    return not member.bot and not is_admin(member)


class ProjectConfirmation(discord.ui.View):
    def __init__(self, cog, author_id: int, form: dict, message: str):
        super().__init__(timeout=600)
        self.cog = cog
        self.author_id = author_id
        self.form = form
        self.message = message
        self.finished = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.user.id != self.author_id
            or not self.cog.admin_permitted(interaction)
        ):
            await interaction.response.send_message(
                "この確認操作は募集を作成した管理者のみが実行できます。",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="この内容で募集する", style=discord.ButtonStyle.success)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.cog.publish_lock:
            if self.finished:
                await interaction.followup.send("この確認は処理済みです。", ephemeral=True)
                return
            if utcnow() >= self.form["deadline_at"].astimezone(timezone.utc):
                await interaction.followup.send(
                    "この募集は締め切りを過ぎたため投稿できません。", ephemeral=True
                )
                return
            try:
                sent = await self.cog.publish(interaction, self.form, self.message)
                self.finished = True
                for item in self.children:
                    item.disabled = True
                try:
                    await interaction.message.edit(view=self)
                except discord.HTTPException:
                    pass
                await interaction.followup.send(
                    f"募集を投稿しました：{sent.jump_url}", ephemeral=True
                )
            except Exception as exc:
                print(f"[Alien/Project] 投稿エラー: {type(exc).__name__}: {exc}", flush=True)
                await interaction.followup.send(
                    "投稿中にエラーが発生しました。Renderのログを確認してください。",
                    ephemeral=True,
                )

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.finished = True
        await interaction.response.edit_message(
            content="企画募集をキャンセルしました。", view=None
        )


class ProjectModal(discord.ui.Modal, title="企画募集の作成"):
    dates = discord.ui.TextInput(
        label="撮影日時（日付 時刻 時刻...）",
        style=discord.TextStyle.paragraph,
        placeholder="11/14 13:00 21:00\n11/15 13:00 21:00",
        max_length=1700,
        required=True,
    )
    plans = discord.ui.TextInput(
        label="企画名 | 参加人数",
        style=discord.TextStyle.paragraph,
        placeholder="AI vs 人間 コードネーム対決 | 2\n素人ギャンブラー | 3-7\nシンプル会話型人狼 | 8-",
        max_length=1700,
        required=True,
    )

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        if not self.cog.admin_permitted(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        try:
            form = parse_project_form(str(self.dates), str(self.plans))
            message = build_recruitment_message(form)
        except ValueError as exc:
            await interaction.response.send_message(
                f"入力内容を確認してください：{exc}", ephemeral=True
            )
            return

        view = ProjectConfirmation(self.cog, interaction.user.id, form, message)
        await interaction.response.send_message(
            "**こういった内容で企画を募集します。よろしいですか？**\n\n"
            + message + "\n\n※募集を投稿してから72時間後以降、未回答者にのみ作業場へ通知します。",
            ephemeral=True,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class ProjectsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_client = MongoClient(
            os.environ["MONGO_URL"], serverSelectionTimeoutMS=8000, tz_aware=True
        )
        db = self.db_client["nkii_nkii_alien"]
        self.projects = db["project_recruitments"]
        self.reminders = db["project_reminders"]
        self.workspaces = db["workspaces"]
        self.publish_lock = asyncio.Lock()
        self.reaction_lock = asyncio.Lock()
        self.bootstrapped = False
        self.reconcile_loop.start()
        self.remind_at_18.start()
        self.cleanup_loop.start()

    def cog_unload(self):
        self.reconcile_loop.cancel()
        self.remind_at_18.cancel()
        self.cleanup_loop.cancel()
        self.db_client.close()

    def admin_permitted(self, interaction):
        return (
            interaction.guild is not None
            and interaction.guild.id == self.bot.target_guild_id
            and isinstance(interaction.user, discord.Member)
            and is_admin(interaction.user)
        )

    def current_projects(self):
        return list(self.projects.find({
            "state": "active", "deadline_at": {"$gte": utcnow()},
        }))

    @app_commands.command(
        name="zzzadmin_project", description="撮影日時・人数別企画を募集【管理者】"
    )
    async def create(self, interaction: discord.Interaction):
        if not self.admin_permitted(interaction):
            await interaction.response.send_message(
                "管理者専用コマンドです。", ephemeral=True
            )
            return
        await interaction.response.send_modal(ProjectModal(self))

    async def publish(self, interaction, form, message):
        guild = interaction.guild
        channel = guild.get_channel(PROJECT_CHANNEL_ID) or await self.bot.fetch_channel(
            PROJECT_CHANNEL_ID
        )
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError("企画募集チャンネルが見つかりません")
        # 旧通知ロールは削除済み。募集投稿時のメンションは行わない。
        sent = await channel.send(
            message, allowed_mentions=discord.AllowedMentions.none()
        )
        data = {
            "_id": uuid4().hex,
            "guild_id": guild.id,
            "channel_id": channel.id,
            "message_id": sent.id,
            "deadline_at": form["deadline_at"].astimezone(timezone.utc),
            "slots": form["slots"],
            "projects": form["projects"],
            "responses": {},
            "state": "active",
            "created_by": str(interaction.user.id),
            "created_at": utcnow(),
        }
        await asyncio.to_thread(self.projects.insert_one, data)
        for emoji in EMOJIS[:len(form["slots"])] + (NO_ATTEND,):
            await sent.add_reaction(emoji)
        print(f"[Alien/Project] 公開: {sent.id}", flush=True)
        return sent

    async def get_channel(self, guild_id=None):
        ch = self.bot.get_channel(PROJECT_CHANNEL_ID)
        if ch is None:
            ch = await self.bot.fetch_channel(PROJECT_CHANNEL_ID)
        return ch

    async def refresh_reactions(self):
        """Render停止中のリアクション変更をDiscordの現状から復元。"""
        async with self.reaction_lock:
            records = await asyncio.to_thread(self.current_projects)
            for item in records:
                try:
                    channel = await self.get_channel()
                    message = await channel.fetch_message(item["message_id"])
                except discord.NotFound:
                    await asyncio.to_thread(
                        self.projects.update_one,
                        {"_id": item["_id"]},
                        {"$set": {"state": "deleted"}},
                    )
                    continue
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"[Alien/Project] 回答の再読込失敗: {exc}", flush=True)
                    continue
                responses = {}
                allowed = set(EMOJIS[:len(item["slots"])]) | {NO_ATTEND}
                for reaction in message.reactions:
                    emoji = str(reaction.emoji)
                    if emoji not in allowed:
                        continue
                    try:
                        async for user in reaction.users(limit=None):
                            if not user.bot:
                                responses.setdefault(str(user.id), []).append(emoji)
                    except (discord.Forbidden, discord.HTTPException) as exc:
                        print(f"[Alien/Project] リアクション一覧取得失敗: {exc}", flush=True)
                        break
                else:
                    await asyncio.to_thread(
                        self.projects.update_one, {"_id": item["_id"]},
                        {"$set": {"responses": responses}},
                    )

    async def handle_raw_reaction(self, payload, *, adding: bool):
        if payload.guild_id != self.bot.target_guild_id:
            return
        emoji = str(payload.emoji)
        if emoji not in VALID_REACTIONS:
            return
        async with self.reaction_lock:
            record = await asyncio.to_thread(
                self.projects.find_one,
                {"message_id": payload.message_id, "state": "active",
                 "deadline_at": {"$gte": utcnow()}},
            )
            if record is None:
                return
            allowed = set(EMOJIS[:len(record["slots"])]) | {NO_ATTEND}
            if emoji not in allowed:
                return
            guild = self.bot.get_guild(self.bot.target_guild_id)
            if guild is None:
                return
            try:
                member = guild.get_member(payload.user_id) or await guild.fetch_member(
                    payload.user_id
                )
                if member.bot:
                    return
                update = (
                    {"$addToSet": {f"responses.{member.id}": emoji}}
                    if adding
                    else {"$pull": {f"responses.{member.id}": emoji}}
                )
                await asyncio.to_thread(
                    self.projects.update_one,
                    {"_id": record["_id"], "state": "active"}, update,
                )
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"[Alien/Project] リアクション処理失敗: {exc}", flush=True)
                return

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        await self.handle_raw_reaction(payload, adding=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        await self.handle_raw_reaction(payload, adding=False)

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.bootstrapped:
            self.bootstrapped = True
            asyncio.create_task(self.reconcile())

    async def reconcile(self):
        try:
            await self.refresh_reactions()
            print("[Alien/Project] 募集の回答状況を同期しました", flush=True)
        except Exception as exc:
            print(f"[Alien/Project] 募集同期エラー: {type(exc).__name__}: {exc}", flush=True)

    @tasks.loop(minutes=10)
    async def reconcile_loop(self):
        await self.reconcile()

    @reconcile_loop.before_loop
    async def before_reconcile(self):
        await self.bot.wait_until_ready()

    async def send_workspace_reminders(self, now: datetime) -> int:
        """公開から72時間経過した募集の未回答者に1日1回、作業場で通知。"""
        active = await asyncio.to_thread(self.current_projects)
        if not any(
            project.get("created_at")
            and project["created_at"] + timedelta(days=3) <= now
            for project in active
        ):
            return 0

        guild = self.bot.get_guild(self.bot.target_guild_id)
        if guild is None:
            print("[Alien/Project] サーバーがキャッシュにありません", flush=True)
            return 0

        today = now.astimezone(JST).date().isoformat()
        sent_count = 0
        async for member in guild.fetch_members(limit=None):
            if not is_notification_target(member):
                continue
            if not overdue_unanswered_by_member(member.id, active, now):
                continue
            user_key = f"{guild.id}:{member.id}"
            record = await asyncio.to_thread(
                self.workspaces.find_one, {"_id": user_key}
            )
            if not record or not record.get("channel_id"):
                print(f"[Alien/Project] 作業場なし member={member.id}", flush=True)
                continue

            reminder_id = f"workspace:{today}:{user_key}"
            try:
                await asyncio.to_thread(
                    self.reminders.insert_one,
                    {"_id": reminder_id, "state": "reserved",
                     "kind": "workspace", "created_at": utcnow()},
                )
            except DuplicateKeyError:
                continue

            try:
                channel = (
                    self.bot.get_channel(record["channel_id"])
                    or await self.bot.fetch_channel(record["channel_id"])
                )
                if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
                    raise RuntimeError("保存済み作業場が存在しません")

                posted = await channel.send(
                    REMINDER_TEXT,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=True, users=False, roles=False
                    ),
                )
                await asyncio.to_thread(
                    self.reminders.update_one,
                    {"_id": reminder_id},
                    {"$set": {
                        "state": "posted", "channel_id": channel.id,
                        "message_id": posted.id,
                        "delete_at": utcnow() + timedelta(hours=1),
                    }},
                )
                sent_count += 1
            except (discord.HTTPException, RuntimeError, OSError) as exc:
                print(f"[Alien/Project] 作業場通知失敗 member={member.id}: {exc}", flush=True)
                # 送れなかった場合は次回実行で再試行可能にする。
                await asyncio.to_thread(
                    self.reminders.delete_one, {"_id": reminder_id}
                )

        return sent_count

    @tasks.loop(time=dt_time(hour=18, minute=0, tzinfo=ZoneInfo("Asia/Tokyo")))
    async def remind_at_18(self):
        try:
            await self.refresh_reactions()
            sent = await self.send_workspace_reminders(utcnow())
            print(f"[Alien/Project] 18時・作業場通知: {sent}件", flush=True)
        except Exception as exc:
            print(f"[Alien/Project] 18時通知失敗: {type(exc).__name__}: {exc}", flush=True)

    @remind_at_18.before_loop
    async def before_reminder(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def cleanup_loop(self):
        expired = await asyncio.to_thread(
            lambda: list(self.reminders.find({
                "state": "posted", "delete_at": {"$lte": utcnow()},
            }).limit(20))
        )
        for item in expired:
            try:
                channel = self.bot.get_channel(item["channel_id"]) or await self.bot.fetch_channel(
                    item["channel_id"]
                )
                await channel.get_partial_message(item["message_id"]).delete()
            except discord.NotFound:
                pass
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"[Alien/Project] 1時間通知削除エラー: {exc}", flush=True)
                continue
            await asyncio.to_thread(
                self.reminders.update_one,
                {"_id": item["_id"]}, {"$set": {"state": "deleted"}},
            )

    @cleanup_loop.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    if not os.environ.get("MONGO_URL"):
        raise RuntimeError("企画募集にはMONGO_URLの設定が必要です。")
    await bot.add_cog(ProjectsCog(bot))
