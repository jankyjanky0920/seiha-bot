"""Google Drive（専用アカウント）との共有機能。管理者が確認したGoogleメールだけACL登録。"""

import asyncio
import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
from pymongo import MongoClient

from alien_bot.drive_storage import (
    DriveClient, FOLDER_NAMES, ROOT_NAME, folder_url,
    normalized_email, safe_name,
)
from alien_bot.score_store import is_admin, utcnow

DB_NAME = "nkii_nkii_alien"
MAX_DISCORD_UPLOAD = 20 * 1024 * 1024


class DriveCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.client = MongoClient(
            os.environ["MONGO_URL"], serverSelectionTimeoutMS=8000,
            tz_aware=True,
        )
        db = self.client[DB_NAME]
        self.state = db["drive_state"]
        self.members = db["drive_members"]
        self.projects = db["drive_projects"]
        self.jobs = db["drive_upload_jobs"]
        self.drive = DriveClient()
        self.provision_lock = asyncio.Lock()
        self.import_lock = asyncio.Lock()
        self.import_loop.start()

    def cog_unload(self):
        self.import_loop.cancel()
        self.client.close()

    def has_access(self, interaction) -> bool:
        return (
            interaction.guild is not None
            and interaction.guild.id == self.bot.target_guild_id
            and isinstance(interaction.user, discord.Member)
            and not interaction.user.bot
        )

    def admin(self, interaction) -> bool:
        return self.has_access(interaction) and is_admin(interaction.user)

    def state_key(self):
        return f"drive:{self.bot.target_guild_id}"

    def member_key(self, user_id):
        return f"{self.bot.target_guild_id}:{user_id}"

    def project_key(self, project_id):
        return f"{self.bot.target_guild_id}:project:{project_id}"

    async def configured(self):
        return await asyncio.to_thread(self.state.find_one, {"_id": self.state_key()})

    async def require_setup(self):
        settings = await self.configured()
        if not settings:
            raise RuntimeError("管理者が /zzzadmin_drive_setup で初期設定を行ってください。")
        return settings

    async def ensure_member_folder(self, user_id: int, cfg=None):
        cfg = cfg or await self.require_setup()
        name = str(user_id)
        return await asyncio.to_thread(
            self.drive.ensure_folder, name, cfg["members"]
        )

    @app_commands.command(
        name="zzzadmin_drive_setup",
        description="Google Driveの制作フォルダを初期作成【管理者】",
    )
    async def setup_drive(self, interaction: discord.Interaction):
        if not self.admin(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        async with self.provision_lock:
            try:
                existing = await self.configured()
                if existing:
                    await interaction.followup.send(
                        "既に初期設定されています。\n"
                        + folder_url(existing["root"]), ephemeral=True
                    )
                    return
                # root には招待を行わない。子フォルダ単位でアクセスを制限する。
                root = await asyncio.to_thread(
                    self.drive.ensure_folder, ROOT_NAME, "root"
                )
                cfg = {"_id": self.state_key(), "root": root}
                for key, name in FOLDER_NAMES.items():
                    cfg[key] = await asyncio.to_thread(
                        self.drive.ensure_folder, name, root
                    )
                cfg["created_at"] = utcnow()
                await asyncio.to_thread(
                    self.state.update_one,
                    {"_id": self.state_key()},
                    {"$setOnInsert": cfg},
                    True,
                )
                await interaction.followup.send(
                    "制作フォルダを作成しました。管理者用リンク：\n"
                    + folder_url(root)
                    + "\n共有はGoogleアカウントを登録した人にのみ行います。",
                    ephemeral=True,
                )
            except Exception as exc:
                print(f"[Alien/Drive] setup error: {type(exc).__name__}", flush=True)
                await interaction.followup.send(
                    "Drive初期設定に失敗しました。RenderのログとOAuth設定を確認してください。",
                    ephemeral=True,
                )

    @app_commands.command(
        name="zzzadmin_drive_member",
        description="確認済みGoogleメールを登録し共通・本人素材を共有【管理者】",
    )
    @app_commands.describe(
        member="共有先のDiscordメンバー",
        email="本人確認済みのGoogleメールアドレス（登録時に招待メール送信）",
    )
    async def register_member(
        self, interaction: discord.Interaction,
        member: discord.Member, email: str,
    ):
        if not self.admin(interaction) or member.bot or is_admin(member):
            await interaction.response.send_message(
                "管理者のみ、一般メンバーのGoogleメールを登録できます。",
                ephemeral=True,
            )
            return
        try:
            email = normalized_email(email)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            cfg = await self.require_setup()
            previous = await asyncio.to_thread(
                self.members.find_one, {"_id": self.member_key(member.id)}
            )
            if previous and previous.get("email") != email:
                await interaction.followup.send(
                    "登録済みメールを変更する場合は、先に /zzzadmin_drive_revoke を実行してください。",
                    ephemeral=True,
                )
                return
            duplicate = await asyncio.to_thread(
                self.members.find_one,
                {"email": email, "_id": {"$ne": self.member_key(member.id)}},
            )
            if duplicate:
                await interaction.followup.send(
                    "このメールは別のDiscordメンバーに登録されています。",
                    ephemeral=True,
                )
                return
            member_folder = await self.ensure_member_folder(member.id, cfg)
            await asyncio.to_thread(self.drive.grant, cfg["common"], email, "reader")
            await asyncio.to_thread(self.drive.grant, cfg["rules"], email, "reader")
            await asyncio.to_thread(self.drive.grant, member_folder, email, "reader")
            await asyncio.to_thread(
                self.members.update_one,
                {"_id": self.member_key(member.id)},
                {"$set": {
                    "email": email,
                    "user_id": str(member.id),
                    "folder_id": member_folder,
                    "updated_at": utcnow(),
                }},
                True,
            )
            await interaction.followup.send(
                f"{member.display_name}さんの共通素材・利用規約・本人素材フォルダを"
                "Googleアカウント宛てに共有しました。", ephemeral=True,
            )
        except Exception as exc:
            print(f"[Alien/Drive] member grant error: {type(exc).__name__}", flush=True)
            await interaction.followup.send(
                "共有に失敗しました。アカウント・容量・権限を確認してください。",
                ephemeral=True,
            )

    async def revoke_member(self, user_id: int) -> bool:
        record = await asyncio.to_thread(
            self.members.find_one, {"_id": self.member_key(user_id)}
        )
        if not record:
            return False
        cfg = await self.require_setup()
        email = record["email"]
        folders = [cfg["common"], cfg["rules"], record["folder_id"]]
        project_entries = await asyncio.to_thread(
            lambda: list(self.projects.find(
                {"member_ids": str(user_id), "guild_id": self.bot.target_guild_id}
            ))
        )
        folders.extend(p["folder_id"] for p in project_entries)
        for folder_id in dict.fromkeys(folders):
            await asyncio.to_thread(self.drive.revoke, folder_id, email)
        await asyncio.to_thread(
            self.members.delete_one, {"_id": self.member_key(user_id)}
        )
        return True

    @app_commands.command(
        name="zzzadmin_drive_revoke",
        description="メンバーのGoogle Drive共有権限をすべて解除【管理者】",
    )
    async def revoke_member_command(
        self, interaction: discord.Interaction, member: discord.Member,
    ):
        if not self.admin(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            removed = await self.revoke_member(member.id)
            await interaction.followup.send(
                "Google Driveの共有を解除しました。" if removed else "登録がありません。",
                ephemeral=True,
            )
        except Exception as exc:
            print(f"[Alien/Drive] revoke error: {type(exc).__name__}", flush=True)
            await interaction.followup.send(
                "権限解除に失敗しました。Renderのログをご確認ください。",
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if member.guild.id != self.bot.target_guild_id:
            return
        try:
            await self.revoke_member(member.id)
        except Exception as exc:
            print(
                f"[Alien/Drive] 退室者のGoogle権限解除失敗 member={member.id}: "
                f"{type(exc).__name__}", flush=True
            )

    @app_commands.command(name="files", description="共通素材と利用規約のGoogle Driveリンク")
    async def common_files(self, interaction: discord.Interaction):
        if not self.has_access(interaction):
            await interaction.response.send_message("このサーバー専用です。", ephemeral=True)
            return
        try:
            cfg = await self.require_setup()
            record = await asyncio.to_thread(
                self.members.find_one,
                {"_id": self.member_key(interaction.user.id)},
            )
            if not record and not self.admin(interaction):
                await interaction.response.send_message(
                    "まだDrive共有が設定されていません。管理者にGoogleアカウントの登録を依頼してください。",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                "**共通素材** " + folder_url(cfg["common"]) + "\n"
                "**利用規約** " + folder_url(cfg["rules"]) + "\n"
                "Google Driveには登録されたGoogleアカウントでログインしてください。",
                ephemeral=True,
            )
        except RuntimeError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)

    @app_commands.command(name="my_files", description="自分の提出素材と参加企画フォルダ")
    async def my_files(self, interaction: discord.Interaction):
        if not self.has_access(interaction):
            await interaction.response.send_message("このサーバー専用です。", ephemeral=True)
            return
        profile = await asyncio.to_thread(
            self.members.find_one,
            {"_id": self.member_key(interaction.user.id)},
        )
        if not profile:
            await interaction.response.send_message(
                "Google Driveアカウントが登録されていません。",
                ephemeral=True,
            )
            return
        projects = await asyncio.to_thread(
            lambda: list(self.projects.find({
                "guild_id": self.bot.target_guild_id,
                "member_ids": str(interaction.user.id),
            }).limit(15))
        )
        lines = ["**個人素材** " + folder_url(profile["folder_id"])]
        for p in projects:
            lines.append(f"**{discord.utils.escape_markdown(p['name'])}** "
                         + folder_url(p["folder_id"]))
        await interaction.response.send_message(
            "\n".join(lines), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="zzzadmin_drive_project",
        description="関係者限定の企画別フォルダを作成【管理者】",
    )
    async def create_project(self, interaction: discord.Interaction, name: str):
        if not self.admin(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            name = safe_name(name)
            cfg = await self.require_setup()
            import uuid
            project_id = uuid.uuid4().hex[:12]
            folder_id = await asyncio.to_thread(
                self.drive.create_folder, project_id + "_" + name, cfg["projects"]
            )
            await asyncio.to_thread(
                self.projects.insert_one, {
                    "_id": self.project_key(project_id),
                    "guild_id": self.bot.target_guild_id,
                    "project_id": project_id,
                    "name": name, "folder_id": folder_id,
                    "member_ids": [], "created_at": utcnow(),
                }
            )
            await interaction.followup.send(
                f"関係者限定フォルダを作成しました。\n"
                f"企画ID: `{project_id}`\n{folder_url(folder_id)}\n"
                "関係者は /zzzadmin_drive_project_access で追加してください。",
                ephemeral=True,
            )
        except (ValueError, RuntimeError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except Exception as exc:
            print(f"[Alien/Drive] project error: {type(exc).__name__}", flush=True)
            await interaction.followup.send("企画フォルダを作成できませんでした。", ephemeral=True)

    @app_commands.command(
        name="zzzadmin_drive_project_access",
        description="企画への関係者のアクセス権を追加【管理者】",
    )
    @app_commands.choices(role=[
        app_commands.Choice(name="閲覧", value="reader"),
        app_commands.Choice(name="編集", value="writer"),
    ])
    async def project_access(
        self, interaction: discord.Interaction, project_id: str,
        member: discord.Member, role: app_commands.Choice[str],
    ):
        if not self.admin(interaction) or member.bot:
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            project = await asyncio.to_thread(
                self.projects.find_one, {"_id": self.project_key(project_id)}
            )
            profile = await asyncio.to_thread(
                self.members.find_one, {"_id": self.member_key(member.id)}
            )
            if not project or not profile:
                await interaction.followup.send(
                    "企画IDまたは対象メンバーのGoogle登録が見つかりません。",
                    ephemeral=True,
                )
                return
            await asyncio.to_thread(
                self.drive.grant, project["folder_id"], profile["email"], role.value
            )
            await asyncio.to_thread(
                self.projects.update_one,
                {"_id": self.project_key(project_id)},
                {"$addToSet": {"member_ids": str(member.id)}},
            )
            await interaction.followup.send(
                f"{member.display_name}さんへ企画の"
                f"{'編集' if role.value == 'writer' else '閲覧'}権限を付与しました。",
                ephemeral=True,
            )
        except Exception as exc:
            print(f"[Alien/Drive] project grant: {type(exc).__name__}", flush=True)
            await interaction.followup.send("共有に失敗しました。", ephemeral=True)

    @app_commands.command(
        name="zzzadmin_drive_projects",
        description="作成済み企画IDとフォルダを一覧【管理者】",
    )
    async def list_projects(self, interaction: discord.Interaction):
        if not self.admin(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        records = await asyncio.to_thread(
            lambda: list(self.projects.find(
                {"guild_id": self.bot.target_guild_id}
            ).limit(30))
        )
        lines = [
            f"`{p['project_id']}` {discord.utils.escape_markdown(p['name'])} "
            + folder_url(p["folder_id"]) for p in records
        ]
        await interaction.response.send_message(
            "\n".join(lines)[:1900] if lines else "企画フォルダは未作成です。",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="zzzadmin_drive_upload",
        description="共通素材・利用規約・完成動画をDriveに保存【管理者】",
    )
    @app_commands.choices(area=[
        app_commands.Choice(name="共通素材", value="common"),
        app_commands.Choice(name="利用規約", value="rules"),
        app_commands.Choice(name="完成動画", value="completed"),
    ])
    async def upload(
        self, interaction: discord.Interaction, area: app_commands.Choice[str],
        attachment: discord.Attachment,
    ):
        if not self.admin(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        if attachment.size > MAX_DISCORD_UPLOAD:
            await interaction.response.send_message(
                "20MBを超える動画はGoogle DriveのWeb画面から直接アップロードしてください。",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            cfg = await self.require_setup()
            payload = await attachment.read()
            mime = attachment.content_type or "application/octet-stream"
            result = await asyncio.to_thread(
                self.drive.upload_bytes,
                cfg[area.value], attachment.filename, payload, mime
            )
            await interaction.followup.send(
                "Driveへの保存が完了しました。\n"
                + folder_url(cfg[area.value])
                + "\n※動画は通常、DriveのWeb画面からの直接アップロードが便利です。",
                ephemeral=True,
            )
        except Exception as exc:
            print(f"[Alien/Drive] upload error: {type(exc).__name__}", flush=True)
            await interaction.followup.send("Driveへの保存に失敗しました。", ephemeral=True)

    async def queue_edit_images(self, member_id: int, attachments):
        """編集情報の提出がDB保存されたあとに、Drive転送ジョブを永続記録。"""
        for a in attachments:
            job_id = f"edit:{self.bot.target_guild_id}:{a.id}"
            await asyncio.to_thread(
                self.jobs.update_one, {"_id": job_id},
                {"$setOnInsert": {
                    "guild_id": self.bot.target_guild_id,
                    "user_id": str(member_id),
                    "channel_id": a.message.channel.id if getattr(a, "message", None) else None,
                    "attachment_id": str(a.id),
                    "filename": a.filename,
                    "created_at": utcnow(),
                    "state": "queued",
                }}, True,
            )
        # 画像メッセージIDは呼び出し元が渡すこと（Attachmentからは取得できない）。
        return len(attachments)

    async def enqueue_archived_pictures(self, member_id: int, archive_message):
        for a in archive_message.attachments:
            job_id = f"edit:{self.bot.target_guild_id}:{a.id}"
            await asyncio.to_thread(
                self.jobs.update_one, {"_id": job_id},
                {"$setOnInsert": {
                    "guild_id": self.bot.target_guild_id,
                    "user_id": str(member_id),
                    "channel_id": archive_message.channel.id,
                    "message_id": archive_message.id,
                    "attachment_id": str(a.id),
                    "filename": a.filename,
                    "created_at": utcnow(),
                    "state": "queued",
                }}, True,
            )

    async def process_import_jobs(self, *, limit: int = 8) -> int:
        if self.import_lock.locked():
            return 0
        async with self.import_lock:
            jobs = await asyncio.to_thread(
                lambda: list(self.jobs.find(
                    {"state": "queued", "guild_id": self.bot.target_guild_id}
                ).limit(limit))
            )
            if not jobs:
                return 0
            cfg = await self.configured()
            if not cfg:
                return 0
            count = 0
            for job in jobs:
                try:
                    folder_id = await self.ensure_member_folder(
                        int(job["user_id"]), cfg
                    )
                    channel = (
                        self.bot.get_channel(job["channel_id"])
                        or await self.bot.fetch_channel(job["channel_id"])
                    )
                    archive = await channel.fetch_message(job["message_id"])
                    asset = next(
                        (a for a in archive.attachments
                         if str(a.id) == job["attachment_id"]), None
                    )
                    if asset is None:
                        raise RuntimeError("Discord側の画像保管ファイルが見つかりません")
                    filename = job["attachment_id"] + "_" + asset.filename
                    existing = await asyncio.to_thread(
                        self.drive.find_file, folder_id, filename
                    )
                    if existing:
                        item = {"id": existing}
                    else:
                        data = await asset.read()
                        item = await asyncio.to_thread(
                            self.drive.upload_bytes,
                            folder_id, filename, data,
                            asset.content_type or "application/octet-stream",
                        )
                    await asyncio.to_thread(
                        self.jobs.update_one, {"_id": job["_id"]},
                        {"$set": {
                            "state": "uploaded", "drive_file_id": item["id"],
                            "folder_id": folder_id, "uploaded_at": utcnow(),
                        }},
                    )
                    count += 1
                except Exception as exc:
                    # 失敗してもDiscordへの元の提出画像はそのまま保持。
                    print(
                        f"[Alien/Drive] 画像転送失敗 job={job['_id']}: "
                        f"{type(exc).__name__}", flush=True
                    )
            return count

    @tasks.loop(minutes=15)
    async def import_loop(self):
        await self.process_import_jobs()

    @import_loop.before_loop
    async def before_import(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    needed = (
        "MONGO_URL", "GOOGLE_DRIVE_CLIENT_ID",
        "GOOGLE_DRIVE_CLIENT_SECRET", "GOOGLE_DRIVE_REFRESH_TOKEN",
    )
    missing = [key for key in needed if not os.getenv(key)]
    if missing:
        print(
            "[Alien/Drive] 環境変数未設定のためDrive機能は起動しません: "
            + ", ".join(missing), flush=True,
        )
        return
    await bot.add_cog(DriveCog(bot))
