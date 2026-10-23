"""編集用データの部分更新、画像アーカイブ、管理者確認、毎日17時リマインド。"""

import asyncio
import io
import os
from datetime import datetime, time as clock_time
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from alien_bot.edit_info_data import (
    IMAGE_EXTENSIONS, complete, description_resolved,
    due_notification, parse_edit_fields,
)
from alien_bot.score_store import ADMIN_CHANNEL_ID, is_admin, utcnow


ACK = (
    "提出ありがとうございます！\n"
    "もし情報の変更・追加などがありましたら、再度/edit_infoで必要項目のみ記入してください。\n"
    "また、入力内容に問題があった場合はこちらから連絡します。"
)
REMIND_MISSING = (
    "@everyone\n"
    "動画編集に使用するため、色、立ち絵などの各種情報を提出して下さい！\n"
    "【提出方法】\n"
    "この作業場チャンネルで\n"
    "/edit_info\n"
    "と打ち込み、表示される画面で提出してください。"
)


def reminder_error(error: str) -> str:
    return (
        "@everyone\n"
        "動画編集に使用する情報に、以下のようなミスがあったのでお知らせします！"
        "再度提出をお願いします。\n"
        "【エラー内容】\n"
        f"{error}\n"
        "【提出方法】\n"
        "この作業場チャンネルで\n"
        "/edit_info\n"
        "と打ち込み、表示される画面で提出してください。"
    )


def image_valid(attachment: discord.Attachment) -> bool:
    import os.path
    ext = os.path.splitext(attachment.filename.lower())[1]
    return ext in IMAGE_EXTENSIONS and (
        not attachment.content_type
        or attachment.content_type.lower().startswith("image/")
    )


class EditInfoModal(discord.ui.Modal, title="動画編集に使用する情報の提出"):
    color = discord.ui.TextInput(
        label="① Color（必須）", placeholder="#FF88CC / 更新時は省略可能",
        max_length=7, required=False,
    )
    name = discord.ui.TextInput(
        label="③ Name（必須）", placeholder="正式な活動名 / 更新時は省略可能",
        max_length=100, required=False,
    )
    picture = discord.ui.Label(
        text="② Picture（立ち絵・表情差分）",
        description="一度に最大5枚・省略可（既存画像は残します）",
        component=discord.ui.FileUpload(
            required=False, min_values=0, max_values=5,
        ),
    )
    linked_sns = discord.ui.TextInput(
        label="④ 連携用SNS（任意）", required=False,
        style=discord.TextStyle.paragraph,
        placeholder="YouTube: https://youtube.com/@...\nTikTok: https://www.tiktok.com/@...\nX: https://x.com/...",
        max_length=1700,
    )
    description_sns = discord.ui.TextInput(
        label="⑤ 概要欄用SNS（最大3件）", required=False,
        style=discord.TextStyle.paragraph,
        placeholder="YouTube同上\npixiv: https://www.pixiv.net/users/...",
        max_length=1700,
    )

    def __init__(self, cog):
        super().__init__(timeout=600)
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.handle_submission(interaction, self)


class MemberSelector(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(
            placeholder="対象メンバーを選択（最大25人）",
            min_values=1, max_values=25,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.handle_selection(
            interaction, self.view.mode, self.view.error, self.values
        )


class MemberSelectView(discord.ui.View):
    def __init__(self, cog, mode: str, error: str | None = None):
        super().__init__(timeout=600)
        self.cog, self.mode, self.error = cog, mode, error
        self.add_item(MemberSelector())


class EditInfoCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client = MongoClient(
            os.environ["MONGO_URL"], serverSelectionTimeoutMS=8000, tz_aware=True
        )
        db = self.client["nkii_nkii_alien"]
        self.profiles = db["edit_profiles"]
        self.workspaces = db["workspaces"]
        self.reminders = db["edit_info_reminders"]
        self.locks = {}
        self.daily_17.start()

    def cog_unload(self):
        self.daily_17.cancel()
        self.client.close()

    def guild_member(self, interaction: discord.Interaction) -> bool:
        return (
            interaction.guild is not None
            and interaction.guild.id == self.bot.target_guild_id
            and isinstance(interaction.user, discord.Member)
            and not interaction.user.bot
        )

    def admin(self, interaction: discord.Interaction) -> bool:
        return self.guild_member(interaction) and is_admin(interaction.user)

    def member_lock(self, user_id: int) -> asyncio.Lock:
        return self.locks.setdefault(user_id, asyncio.Lock())

    def key(self, user_id: int) -> str:
        return f"{self.bot.target_guild_id}:{user_id}"

    async def workspace(self, user_id: int):
        record = await asyncio.to_thread(
            self.workspaces.find_one, {"_id": self.key(user_id)}
        )
        if not record or not record.get("channel_id"):
            return None
        return self.bot.get_channel(record["channel_id"]) or await self.bot.fetch_channel(
            record["channel_id"]
        )

    async def permitted_workspace(self, interaction: discord.Interaction) -> bool:
        if not self.guild_member(interaction):
            return False
        if self.admin(interaction):
            return True
        record = await asyncio.to_thread(
            self.workspaces.find_one, {"_id": self.key(interaction.user.id)}
        )
        return bool(record and record.get("channel_id") == interaction.channel_id)

    @app_commands.command(name="edit_info", description="色・画像・活動名・SNSを提出・部分更新します")
    async def edit_info(self, interaction: discord.Interaction):
        if not self.guild_member(interaction):
            await interaction.response.send_message("このサーバーでのみ利用できます。", ephemeral=True)
            return
        if not await self.permitted_workspace(interaction):
            await interaction.response.send_message(
                "自分の作業場で実行してください。管理者はどこでも実行できます。",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(EditInfoModal(self))

    async def handle_submission(self, interaction: discord.Interaction, modal: EditInfoModal):
        if not await self.permitted_workspace(interaction):
            await interaction.response.send_message("作業場から提出してください。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        member = interaction.user
        try:
            files = list(modal.picture.component.values)
            if not any((
                str(modal.color).strip(), str(modal.name).strip(),
                str(modal.linked_sns).strip(), str(modal.description_sns).strip(),
                files,
            )):
                await interaction.followup.send(
                    "少なくとも1項目は入力またはアップロードしてください。",
                    ephemeral=True,
                )
                return
            if len(files) > 5 or any(not image_valid(pic) for pic in files):
                await interaction.followup.send(
                    "画像はPNG/JPEG/WEBP/GIF/AVIFを最大5枚まで提出してください。",
                    ephemeral=True,
                )
                return
            if sum(pic.size for pic in files) > 35 * 1024 * 1024:
                await interaction.followup.send(
                    "画像は1回の提出につき合計35MB以内にしてください。",
                    ephemeral=True,
                )
                return
            async with self.member_lock(member.id):
                existing = await asyncio.to_thread(
                    self.profiles.find_one, {"_id": self.key(member.id)}
                )
                updates = parse_edit_fields(
                    str(modal.color), str(modal.name),
                    str(modal.linked_sns), str(modal.description_sns),
                    existing,
                )
                old_images = (existing or {}).get("pictures", [])
                if len(old_images) + len(files) > 50:
                    await interaction.followup.send(
                        "保存済み画像が50枚に達します。管理者に整理を依頼してください。",
                        ephemeral=True,
                    )
                    return
                archived = None
                if files:
                    channel = (
                        self.bot.get_channel(ADMIN_CHANNEL_ID)
                        or await self.bot.fetch_channel(ADMIN_CHANNEL_ID)
                    )
                    if not isinstance(channel, discord.TextChannel):
                        raise RuntimeError("管理者専用の画像保管先が見つかりません")
                    uploads = [await pic.to_file() for pic in files]
                    archived = await channel.send(
                        f"🖼️ 編集素材保管：{member.mention} ({member.id})",
                        files=uploads,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    new_images = [
                        {
                            "message_id": archived.id,
                            "channel_id": archived.channel.id,
                            "attachment_id": str(att.id),
                            "filename": att.filename,
                            "size": att.size,
                        }
                        for att in archived.attachments
                    ]
                    if len(new_images) != len(files):
                        raise RuntimeError("画像保管中にファイル数の不一致が発生しました")
                    updates["pictures"] = old_images + new_images
                updates["last_submission_at"] = utcnow()
                updates["error"] = None
                await asyncio.to_thread(
                    self.profiles.update_one,
                    {"_id": self.key(member.id)},
                    {
                        "$set": updates,
                        "$setOnInsert": {"user_id": str(member.id), "watch": False},
                        "$inc": {"version": 1},
                    },
                    True,
                )
            try:
                workspace = await self.workspace(member.id)
                if workspace:
                    await workspace.send(
                        ACK, allowed_mentions=discord.AllowedMentions.none()
                    )
                else:
                    print(
                        f"[Alien/EditInfo] 作業場なし: member={member.id}", flush=True
                    )
            except (discord.Forbidden, discord.HTTPException) as err:
                print(f"[Alien/EditInfo] お礼メッセージ失敗: {err}", flush=True)
            await interaction.followup.send(
                "編集情報を保存しました。画像は既存分に追加しています。"
                "（管理者には画像を確認できる保存先を用意しています）",
                ephemeral=True,
            )
        except ValueError as exc:
            await interaction.followup.send(f"入力内容を確認してください：{exc}", ephemeral=True)
        except Exception as exc:
            print(f"[Alien/EditInfo] 提出エラー: {type(exc).__name__}: {exc}", flush=True)
            await interaction.followup.send(
                "提出情報を保存できませんでした。管理者にご連絡ください。",
                ephemeral=True,
            )

    @app_commands.command(name="zzzadmin_edit", description="対象者の未提出・入力ミス通知を設定【管理者】")
    @app_commands.describe(error="提出済みの人へ送る共通のエラー内容（省略可）")
    async def admin_edit(self, interaction: discord.Interaction, error: str | None = None):
        if not self.admin(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        if error and len(error.strip()) > 900:
            await interaction.response.send_message(
                "エラー文は900文字以内で入力してください。", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "17時の未提出リマインド対象にする人を選択してください。"
            "入力済みの人には、指定したエラーがあれば即時通知し、再提出まで毎日17時に通知します。",
            view=MemberSelectView(self, "watch", (error or "").strip() or None),
            ephemeral=True,
        )

    @app_commands.command(name="zzzadmin_edit_info", description="選択した人の編集用情報を照会【管理者】")
    async def admin_edit_info(self, interaction: discord.Interaction):
        if not self.admin(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        await interaction.response.send_message(
            "編集情報を確認したいメンバーを選択してください（最大25人）。",
            view=MemberSelectView(self, "report"), ephemeral=True,
        )

    async def handle_selection(self, interaction, mode, error, selected):
        if not self.admin(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if mode == "report":
            reports = []
            for user in selected:
                member = guild.get_member(user.id)
                title = member.display_name if member else user.display_name
                profile = await asyncio.to_thread(
                    self.profiles.find_one, {"_id": self.key(user.id)}
                )
                if not profile:
                    reports.append(f"■ {title} ({user.id})\n未提出\n")
                    continue
                images = profile.get("pictures") or []
                linked = profile.get("linked_sns") or {}
                descriptions = profile.get("description_sns") or {}
                report = [
                    f"■ {title} ({user.id})",
                    f"提出状況: {'基本3項目すべて提出済み' if complete(profile) else '基本3項目未完了'}",
                    f"イメージカラー: {profile.get('color') or '未提出'}",
                    f"正式活動名: {profile.get('name') or '未提出'}",
                    f"画像: {len(images)}枚",
                ]
                for a in images:
                    report.append(
                        f"  - {a['filename']} (保管先: https://discord.com/channels/{guild.id}/{a['channel_id']}/{a['message_id']})"
                    )
                report.append("連携用SNS:")
                report.extend(
                    f"  {platform}: {url}" for platform, url in linked.items()
                )
                report.append("概要欄用SNS:")
                report.extend(
                    f"  {site}: {value}"
                    for site, value in description_resolved(descriptions, linked).items()
                )
                report.append(f"エラー連絡: {profile.get('error') or 'なし'}")
                report.append(f"バージョン: {profile.get('version', 0)}")
                reports.append("\n".join(report) + "\n")
            data = "\n".join(reports).encode("utf-8")
            await interaction.followup.send(
                f"選択した{len(selected)}人の編集情報をまとめました。"
                "画像は添付のレポート内にある保管先リンクから確認できます。",
                file=discord.File(io.BytesIO(data), filename="edit_info_report.txt"),
                ephemeral=True,
            )
            return

        processed, immediate, errors = 0, 0, []
        for user in selected:
            try:
                if user.bot:
                    continue
                async with self.member_lock(user.id):
                    profile = await asyncio.to_thread(
                        self.profiles.find_one, {"_id": self.key(user.id)}
                    )
                    had_submission = bool(profile and profile.get("version", 0) > 0)
                    fields = {"watch": True}
                    if error and had_submission:
                        fields["error"] = error
                    elif not had_submission:
                        fields["error"] = None
                    await asyncio.to_thread(
                        self.profiles.update_one,
                        {"_id": self.key(user.id)},
                        {"$set": fields,
                         "$setOnInsert": {"user_id": str(user.id), "version": 0}},
                        True,
                    )
                if error and had_submission:
                    channel = await self.workspace(user.id)
                    if channel:
                        await channel.send(
                            reminder_error(error),
                            allowed_mentions=discord.AllowedMentions(everyone=True),
                        )
                        immediate += 1
                    else:
                        errors.append(f"{user.display_name}: 作業場がありません")
                processed += 1
            except Exception as exc:
                print(f"[Alien/EditInfo] 管理者登録失敗 user={user.id}: {exc}", flush=True)
                errors.append(f"{user.display_name}: 設定できませんでした")
        await interaction.followup.send(
            f"{processed}人を17時の確認対象に設定しました。"
            f"エラー即時通知：{immediate}件。"
            + ("\n" + "\n".join(errors[:15]) if errors else ""),
            ephemeral=True,
        )

    @tasks.loop(time=clock_time(hour=17, minute=0, tzinfo=ZoneInfo("Asia/Tokyo")))
    async def daily_17(self):
        today = datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()
        try:
            records = await asyncio.to_thread(
                lambda: list(self.profiles.find({"watch": True}))
            )
            sent_count = 0
            for profile in records:
                kind = due_notification(profile)
                if kind is None:
                    continue
                user_id = int(profile["user_id"])
                claim_id = f"{today}:{self.key(user_id)}"
                try:
                    await asyncio.to_thread(
                        self.reminders.insert_one,
                        {"_id": claim_id, "created_at": utcnow(), "kind": kind},
                    )
                except DuplicateKeyError:
                    continue
                try:
                    channel = await self.workspace(user_id)
                    if not channel:
                        raise RuntimeError("対象者の作業場がありません")
                    message = (
                        reminder_error(profile["error"])
                        if kind == "error" else REMIND_MISSING
                    )
                    await channel.send(
                        message,
                        allowed_mentions=discord.AllowedMentions(everyone=True),
                    )
                    await asyncio.to_thread(
                        self.reminders.update_one,
                        {"_id": claim_id}, {"$set": {"sent_at": utcnow()}},
                    )
                    sent_count += 1
                except Exception as exc:
                    print(f"[Alien/EditInfo] 17時通知失敗 user={user_id}: {exc}", flush=True)
                    # 未送信の場合は同日再試行ができるように戻す。
                    await asyncio.to_thread(
                        self.reminders.delete_one, {"_id": claim_id}
                    )
            print(f"[Alien/EditInfo] 17時通知完了: {sent_count}件", flush=True)
        except Exception as exc:
            print(f"[Alien/EditInfo] 17時タスク失敗: {type(exc).__name__}: {exc}", flush=True)

    @daily_17.before_loop
    async def before_daily_17(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(EditInfoCog(bot))
