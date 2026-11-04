"""撮影企画の管理。手動追加／素材確認／出演者レビュー／投稿完了。"""

import asyncio
from datetime import timedelta
from urllib.parse import urlsplit

import discord
from discord import app_commands
from discord.ext import commands, tasks

from alien_bot.production_plans import (
    ProductionStore, STATUS_WAIT, STATUS_EDIT, STATUS_REVIEW, STATUS_POSTED,
    STATUS_LABELS, manual_plan, normalize_name, validate_duration,
    parse_shoot_date, thank_message, reward_for_minutes,
)
from alien_bot.score_store import (
    ScoreStore, ADMIN_CHANNEL_ID, is_admin, utcnow,
)


def https_url(value: str, *, drive: bool = False) -> str:
    value = value.strip()
    parts = urlsplit(value)
    if (
        parts.scheme != "https" or not parts.hostname or parts.username
        or parts.password or len(value) > 500
    ):
        raise ValueError("https:// で始まる有効なリンクを入力してください。")
    if drive and parts.hostname.lower() not in ("drive.google.com", "docs.google.com"):
        raise ValueError("確認用の動画にはGoogle Driveのリンクを指定してください。")
    return value


def member_ids_for_plan(plan) -> list[str]:
    return list(plan["member_ids"])


class PlanMemberSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(
            placeholder="撮影に参加したメンバーを選択（最大25人）",
            min_values=1, max_values=25,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.submit_manual(interaction, self.view, self.values)


class PlanManualView(discord.ui.View):
    def __init__(self, cog, author_id: int, data: dict):
        super().__init__(timeout=600)
        self.cog, self.author_id, self.data = cog, author_id, data
        self.complete = False
        self.add_item(PlanMemberSelect())

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id or not self.cog.admin(interaction):
            await interaction.response.send_message(
                "この撮影の登録は入力した管理者だけが操作できます。",
                ephemeral=True,
            )
            return False
        return True


class RevisionModal(discord.ui.Modal, title="修正してほしい箇所を入力"):
    comment = discord.ui.TextInput(
        label="どこを、どのように修正してほしいですか？",
        style=discord.TextStyle.paragraph, required=True,
        placeholder="例：1:25のテロップの名前を修正してください。",
        max_length=900,
    )

    def __init__(self, cog, plan_id, version, reviewer_id, message_id):
        super().__init__(timeout=600)
        self.cog = cog
        self.plan_id, self.version = plan_id, version
        self.reviewer_id, self.message_id = reviewer_id, message_id

    async def on_submit(self, interaction):
        await self.cog.submit_review(
            interaction, self.plan_id, self.version,
            self.reviewer_id, self.message_id, False, str(self.comment),
        )


class ReviewView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Yes（この動画でOK）", style=discord.ButtonStyle.success,
        custom_id="alien:plan:review:yes",
    )
    async def yes(self, interaction, button):
        await self.cog.vote_review(interaction, True)

    @discord.ui.button(
        label="No（修正依頼）", style=discord.ButtonStyle.danger,
        custom_id="alien:plan:review:no",
    )
    async def no(self, interaction, button):
        await self.cog.vote_review(interaction, False)


class PlanCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.store = ScoreStore()
        self.data = ProductionStore(self.store)
        self.operation_lock = asyncio.Lock()
        self.refresh_loop.start()
        self.bot.add_view(ReviewView(self))

    def cog_unload(self):
        self.refresh_loop.cancel()
        self.store.close()

    def admin(self, interaction):
        return (
            interaction.guild is not None
            and interaction.guild.id == self.bot.target_guild_id
            and isinstance(interaction.user, discord.Member)
            and is_admin(interaction.user)
        )

    async def find(self, query: str):
        return await asyncio.to_thread(
            self.data.get, query, self.bot.target_guild_id
        )

    async def send_member(self, user_id: int, message: str, *, view=None):
        cid = await asyncio.to_thread(
            self.store.workspace_channel_id,
            self.bot.target_guild_id, user_id,
        )
        if cid:
            channel = self.bot.get_channel(cid) or await self.bot.fetch_channel(cid)
            return await channel.send(
                message, view=view,
                allowed_mentions=discord.AllowedMentions(
                    everyone=True, users=False, roles=False
                ),
            )
        # 管理者に作業場はないため、管理者出演者だけDMで受け取れる。
        guild = self.bot.get_guild(self.bot.target_guild_id)
        if guild:
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.HTTPException:
                    return None
            if member and is_admin(member):
                return await member.send(
                    message, view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        return None

    async def send_once(self, kind: str, plan: dict, uid: str, text: str):
        key = f"{kind}:{plan['_id']}:{uid}"
        record = await asyncio.to_thread(
            self.data.notices.find_one, {"_id": key},
        )
        if record and record.get("state") == "sent":
            return False
        # 送信不可の場合も再試行できるよう状態を保持する。
        await asyncio.to_thread(
            self.data.notices.update_one,
            {"_id": key},
            {"$setOnInsert": {
                "kind": kind, "plan_id": plan["_id"],
                "member_id": uid, "state": "pending",
                "created_at": utcnow(),
            }}, True,
        )
        try:
            sent = await self.send_member(int(uid), text)
            if not sent:
                return False
            await asyncio.to_thread(
                self.data.notices.update_one, {"_id": key},
                {"$set": {
                    "state": "sent", "message_id": sent.id,
                    "sent_at": utcnow(),
                }},
            )
            return True
        except (discord.HTTPException, OSError) as exc:
            print(
                f"[Alien/Plan] 通知できません member={uid}: {type(exc).__name__}",
                flush=True,
            )
            return False

    async def settle(self, record: dict, shoot: dict | None = None):
        # grant_shootは撮影IDごとの重複付与を防止している。
        for uid in record["member_ids"]:
            minutes = record["participant_minutes"].get(uid, 0)
            if record["award_score"]:
                if shoot and uid in shoot.get("participants", {}):
                    item = shoot["participants"][uid]
                    beginning = item["joined_at"]
                    ending = item.get("ended_at") or shoot["ended_at"]
                else:
                    beginning = record["shoot_started_at"]
                    ending = beginning + timedelta(minutes=minutes)
                await asyncio.to_thread(
                    self.store.grant_shoot, int(uid), record["_id"],
                    beginning, ending,
                )
            await self.send_once(
                "thanks", record, uid,
                thank_message(
                    record["name"], minutes,
                    reward_for_minutes(minutes) if record["award_score"] else None,
                ),
            )

    async def new_plan(self, record: dict, shoot=None):
        async with self.operation_lock:
            saved = await asyncio.to_thread(self.data.add, record)
            await self.settle(saved, shoot=shoot)
            # Drive接続後は対応する専用企画フォルダを任意で生成する。
            await self.ensure_drive(saved)
            return await asyncio.to_thread(
                self.data.plans.find_one, {"_id": saved["_id"]}
            )

    async def ensure_drive(self, plan: dict):
        drive_cog = self.bot.get_cog("DriveCog")
        if drive_cog is None:
            return
        try:
            await drive_cog.ensure_plan_project(plan)
        except Exception as exc:
            print(
                f"[Alien/Plan] Drive企画フォルダ連携保留 {plan['_id']}: "
                f"{type(exc).__name__}", flush=True,
            )

    @app_commands.command(name="zzzadmin_planinfo", description="過去の撮影企画を手動登録【管理者】")
    @app_commands.describe(
        name="企画名", date="撮影日 YYYY-MM-DD",
        hours="撮影時間（時）", minutes="撮影時間（分）",
        award_score="撮影協力者に時間に応じたスコアを付与するか",
    )
    async def planinfo(
        self, interaction: discord.Interaction,
        name: str, date: str, hours: int, minutes: int,
        award_score: bool,
    ):
        if not self.admin(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        try:
            normalize_name(name)
            parse_shoot_date(date)
            validate_duration(hours, minutes)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(
            "**撮影協力者を選択してください。**\n"
            "手動追加では選択された全員に指定の撮影時間を記録します。"
            + ("スコアも付与します。" if award_score else "スコアは付与しません。"),
            view=PlanManualView(self, interaction.user.id, {
                "name": name, "date": date, "hours": hours,
                "minutes": minutes, "award_score": award_score,
            }),
            ephemeral=True,
        )

    # /zzzadin_planinfo は、ユーザーから指定された綴りもサポートする。
    @app_commands.command(name="zzzadin_planinfo", description="撮影企画の手動登録（略称）【管理者】")
    async def planinfo_alias(
        self, interaction: discord.Interaction, name: str, date: str,
        hours: int, minutes: int, award_score: bool,
    ):
        await self.planinfo.callback(
            self, interaction, name, date, hours, minutes, award_score
        )

    async def submit_manual(self, interaction, view, selected):
        if view.complete:
            await interaction.response.send_message(
                "すでに登録しています。", ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            participants = []
            for user in selected:
                member = interaction.guild.get_member(user.id)
                if not member:
                    member = await interaction.guild.fetch_member(user.id)
                if member.bot:
                    continue
                participants.append(member.id)
            entry = manual_plan(
                interaction.guild.id,
                view.data["name"], view.data["date"],
                view.data["hours"], view.data["minutes"],
                participants, view.data["award_score"],
            )
            # タップ連打による重複登録を防ぐためviewとLockを併用。
            if "plan_id" not in view.data:
                view.data["plan_id"] = entry["_id"]
            else:
                entry["_id"] = view.data["plan_id"]
            plan = await self.new_plan(entry)
            view.complete = True
            await interaction.followup.send(
                f"企画を登録しました：**{plan['name']}**\n"
                f"企画ID: `{plan['_id']}`\n"
                f"ステータス：{STATUS_LABELS[plan['status']]}\n"
                "撮影時間に応じたスコアは、指定がTrueの人にのみ付与します。",
                ephemeral=True,
            )
        except (ValueError, discord.HTTPException) as exc:
            await interaction.followup.send(
                f"登録できませんでした：{exc}", ephemeral=True,
            )
        except Exception as exc:
            print(f"[Alien/Plan] manual error: {type(exc).__name__}: {exc}", flush=True)
            await interaction.followup.send(
                "一部処理に失敗しました。企画IDやスコア履歴を確認してください。",
                ephemeral=True,
            )

    @app_commands.command(
        name="zzzadmin_plan_settle",
        description="登録済み企画のスコア・お礼通知を安全に再試行【管理者】",
    )
    async def settle_retry(self, interaction: discord.Interaction, plan: str):
        if not self.admin(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with self.operation_lock:
                saved = await self.find(plan)
                await self.settle(saved)
            await interaction.followup.send(
                "スコアとお礼メッセージの処理を確認・再試行しました。"
                "撮影IDによりスコアの二重付与は防止されます。",
                ephemeral=True,
            )
        except Exception as exc:
            print(
                f"[Alien/Plan] settle retry error: {type(exc).__name__}",
                flush=True,
            )
            await interaction.followup.send(
                "再処理に失敗しました。Renderのログを確認してください。",
                ephemeral=True,
            )

    @app_commands.command(name="zzzadmin_plans", description="撮影企画と制作ステータスの一覧【管理者】")
    async def plans(self, interaction: discord.Interaction):
        if not self.admin(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        docs = await asyncio.to_thread(
            lambda: list(self.data.plans.find({
                "guild_id": self.bot.target_guild_id,
            }).sort("created_at", -1).limit(25))
        )
        if not docs:
            await interaction.response.send_message("撮影企画はまだありません。", ephemeral=True)
            return
        lines = [
            f"`{doc['_id']}`：{discord.utils.escape_markdown(doc['name'])} "
            f"({doc['shoot_date']}) [{STATUS_LABELS[doc['status']]}]"
            for doc in docs
        ]
        await interaction.response.send_message(
            "\n".join(lines)[:1950], ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="zzzadmin_plan", description="撮影企画の詳細・確認結果を表示【管理者】")
    async def plan_detail(self, interaction: discord.Interaction, plan: str):
        if not self.admin(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        try:
            data = await self.find(plan)
            votes = data.get("review_votes", {})
            lines = [
                f"🎬 **{discord.utils.escape_markdown(data['name'])}**",
                f"企画ID: `{data['_id']}`",
                f"撮影日: {data['shoot_date']}",
                f"撮影時間: {data['duration_minutes']}分",
                f"ステータス: {STATUS_LABELS[data['status']]}",
                f"スコア付与: {'あり' if data['award_score'] else 'なし'}",
                "出演者:",
            ]
            for uid in data["member_ids"]:
                minutes = data["participant_minutes"].get(uid, 0)
                missing = "（素材未提出）" if uid in data.get("missing_member_ids", []) else ""
                vote = votes.get(uid) or {}
                response = vote.get("vote", "未回答")
                lines.append(
                    f"  <@{uid}> {minutes}分 {missing} / 確認: {response}"
                )
                if vote.get("comment"):
                    lines.append(
                        "    修正希望：" + discord.utils.escape_markdown(
                            discord.utils.escape_mentions(vote["comment"])
                        )[:220]
                    )
            if data.get("review_url"):
                lines.append("確認用動画: " + data["review_url"])
            if data.get("published_url"):
                lines.append("公開動画: " + data["published_url"])
            await interaction.response.send_message(
                "\n".join(lines)[:1970], ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)

    async def refresh_member(self, member_id):
        await asyncio.to_thread(
            self.data.refresh_member, self.bot.target_guild_id, member_id
        )

    @app_commands.command(name="zzzadmin_plan_refresh", description="出演者の素材提出状況を再確認【管理者】")
    async def plan_refresh(self, interaction: discord.Interaction, plan: str):
        if not self.admin(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        try:
            item = await self.find(plan)
            updated = await asyncio.to_thread(self.data.refresh_assets, item["_id"])
            await self.ensure_drive(updated)
            await interaction.response.send_message(
                f"ステータス：{STATUS_LABELS[updated['status']]}\n"
                f"未提出者：{', '.join('<@'+x+'>' for x in updated.get('missing_member_ids', [])) or 'なし'}",
                ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)

    @tasks.loop(minutes=30)
    async def refresh_loop(self):
        waiting = await asyncio.to_thread(
            lambda: list(self.data.plans.find({
                "guild_id": self.bot.target_guild_id, "status": STATUS_WAIT,
            }).limit(100))
        )
        for record in waiting:
            try:
                await asyncio.to_thread(self.data.refresh_assets, record["_id"])
            except Exception as exc:
                print(
                    f"[Alien/Plan] 素材判定失敗 {record['_id']}: {type(exc).__name__}",
                    flush=True,
                )

    @refresh_loop.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="zzzadmin_plan_review", description="書き出し後の動画確認を出演者へ依頼【管理者】")
    async def plan_review(self, interaction: discord.Interaction, plan: str, drive_url: str):
        if not self.admin(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        try:
            url = https_url(drive_url, drive=True)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            async with self.operation_lock:
                item = await self.find(plan)
                if item["status"] not in (STATUS_EDIT, STATUS_REVIEW):
                    raise ValueError(
                        "出演者全員の素材が揃って「編集中」になってから確認を依頼してください。"
                    )
                version = item.get("review_round", 0) + 1
                await asyncio.to_thread(
                    self.data.plans.update_one, {"_id": item["_id"]},
                    {"$set": {
                        "status": STATUS_REVIEW, "review_url": url,
                        "review_round": version, "review_votes": {},
                        "updated_at": utcnow(),
                    }},
                )
                drive_note = ""
                drive_cog = self.bot.get_cog("DriveCog")
                if drive_cog is not None:
                    try:
                        outcome = await drive_cog.grant_review_access(item, url)
                        if outcome and outcome["unshared"]:
                            drive_note = (
                                "\\nDriveの自動共有が未完了："
                                + ", ".join(outcome["unshared"])
                                + "。Drive側で個別に閲覧権限を設定してください。"
                            )
                    except Exception as exc:
                        print(
                            f"[Alien/Plan] Drive確認用動画共有失敗: {type(exc).__name__}",
                            flush=True,
                        )
                        drive_note = (
                            "\\nDrive側の閲覧権限を自動設定できませんでした。"
                            "手動で出演者を共有先に追加してください。"
                        )
                else:
                    drive_note = (
                        "\\nGoogle Driveが未接続のため、閲覧権限は"
                        "Google Drive側で手動設定してください。"
                    )
                failed = []
                for uid in item["member_ids"]:
                    try:
                        msg = await self.send_member(
                            int(uid),
                            f"@everyone\n🎬 **【{discord.utils.escape_markdown(item['name'])}】"
                            "の動画をご確認ください。**\n"
                            f"{url}\n"
                            "この動画で問題がなければ **Yes**、修正したい箇所があれば"
                            " **No** を押して内容を入力してください。",
                            view=ReviewView(self),
                        )
                        if msg is None:
                            failed.append(uid)
                            continue
                        await asyncio.to_thread(
                            self.data.review_messages.update_one,
                            {"_id": f"{item['_id']}:{version}:{uid}"},
                            {"$set": {
                                "plan_id": item["_id"],
                                "guild_id": self.bot.target_guild_id,
                                "member_id": uid,
                                "version": version,
                                "message_id": msg.id, "channel_id": msg.channel.id,
                                "created_at": utcnow(),
                            }}, True,
                        )
                    except (discord.HTTPException, OSError) as exc:
                        print(f"[Alien/Plan] 確認依頼送信失敗: {type(exc).__name__}", flush=True)
                        failed.append(uid)
            await interaction.followup.send(
                "動画確認を送信しました。"
                + (f"未送信: {', '.join(failed)}" if failed else " 全員に送信済みです。")
                + drive_note,
                ephemeral=True,
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except Exception as exc:
            print(f"[Alien/Plan] review error: {type(exc).__name__}", flush=True)
            await interaction.followup.send("動画確認の設定に失敗しました。", ephemeral=True)

    async def review_target(self, interaction):
        if not interaction.message:
            return None
        # DMの管理者出演者もクリックできるよう、guildは任意。
        record = await asyncio.to_thread(
            self.data.review_messages.find_one,
            {"message_id": interaction.message.id, "channel_id": interaction.channel_id},
        )
        if not record or str(interaction.user.id) != record["member_id"]:
            return None
        plan = await asyncio.to_thread(
            self.data.plans.find_one, {"_id": record["plan_id"]},
        )
        if not plan or plan["status"] != STATUS_REVIEW:
            return None
        if plan.get("review_round") != record["version"]:
            return None
        return record

    async def vote_review(self, interaction, approved: bool):
        record = await self.review_target(interaction)
        if record is None:
            await interaction.response.send_message(
                "この確認は対象外、またはすでに終了しています。",
                ephemeral=True,
            )
            return
        if not approved:
            await interaction.response.send_modal(
                RevisionModal(
                    self, record["plan_id"], record["version"],
                    record["member_id"], record["message_id"],
                )
            )
            return
        await self.submit_review(
            interaction, record["plan_id"], record["version"],
            record["member_id"], record["message_id"], True, "",
        )

    async def submit_review(self, interaction, plan_id, version, uid, message_id,
                            approved, comment):
        if str(interaction.user.id) != str(uid):
            await interaction.response.send_message("ご本人のみ回答できます。", ephemeral=True)
            return
        if not approved and not comment.strip():
            await interaction.response.send_message("修正内容を入力してください。", ephemeral=True)
            return
        record = await asyncio.to_thread(
            self.data.review_messages.find_one,
            {"plan_id": plan_id, "member_id": str(uid),
             "version": version, "message_id": message_id},
        )
        if not record:
            await interaction.response.send_message("有効な確認依頼がありません。", ephemeral=True)
            return
        async with self.operation_lock:
            plan = await asyncio.to_thread(
                self.data.plans.find_one, {"_id": plan_id},
            )
            if not plan or plan["status"] != STATUS_REVIEW or plan.get("review_round") != version:
                await interaction.response.send_message(
                    "この動画の確認受付は終了しました。", ephemeral=True,
                )
                return
            answer = {
                "vote": "yes" if approved else "no",
                "comment": comment.strip() if not approved else "",
                "at": utcnow(),
            }
            await asyncio.to_thread(
                self.data.plans.update_one,
                {"_id": plan_id, "status": STATUS_REVIEW, "review_round": version},
                {"$set": {f"review_votes.{uid}": answer, "updated_at": utcnow()}},
            )
        await interaction.response.send_message(
            "確認結果を保存しました。ありがとうございます。", ephemeral=True,
        )
        if not approved:
            try:
                ch = self.bot.get_channel(ADMIN_CHANNEL_ID) or await self.bot.fetch_channel(ADMIN_CHANNEL_ID)
                await ch.send(
                    f"🎞️【{discord.utils.escape_markdown(plan['name'])}】"
                    f" <@{uid}> から修正依頼が届きました。\n"
                    + discord.utils.escape_markdown(
                        discord.utils.escape_mentions(comment)
                    )[:900],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException as exc:
                print(f"[Alien/Plan] 修正依頼通知失敗: {exc}", flush=True)

    @app_commands.command(
        name="zzzadmin_plan_collab",
        description="出演者ごとのコラボ招待リンクを登録【管理者】",
    )
    async def plan_collab(
        self, interaction: discord.Interaction, plan: str, member: discord.Member,
        youtube_url: str = "", tiktok_url: str = "", x_post_url: str = "",
        no_invite: bool = False,
    ):
        if not self.admin(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        try:
            item = await self.find(plan)
            if str(member.id) not in item["member_ids"]:
                raise ValueError("この人は企画の出演者ではありません。")
            links = {}
            for k, value in (
                ("YouTube", youtube_url), ("TikTok", tiktok_url), ("X", x_post_url),
            ):
                if value.strip():
                    links[k] = https_url(value)
            if not links and not no_invite:
                raise ValueError(
                    "少なくとも1件の招待リンクを入力するか、no_inviteをTrueにしてください。"
                )
            await asyncio.to_thread(
                self.data.plans.update_one, {"_id": item["_id"]},
                {"$set": {
                    f"collab_urls.{member.id}": (
                        {"no_invite": True} if no_invite else links
                    ), "updated_at": utcnow(),
                }},
            )
            await interaction.response.send_message(
                "出演者ごとの投稿・コラボ招待先を記録しました。",
                ephemeral=True,
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)

    @app_commands.command(
        name="zzzadmin_plan_posted",
        description="企画を投稿完了にし、出演者へ招待リンク送信【管理者】",
    )
    async def plan_posted(
        self, interaction: discord.Interaction, plan: str,
        published_url: str = "", force: bool = False,
    ):
        if not self.admin(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with self.operation_lock:
                item = await self.find(plan)
                if item["status"] not in (STATUS_REVIEW, STATUS_POSTED):
                    raise ValueError("出演者チェックを行ってから投稿完了にしてください。")
                votes = item.get("review_votes", {})
                waiting = [
                    uid for uid in item["member_ids"]
                    if votes.get(uid, {}).get("vote") != "yes"
                ]
                if waiting and not force and item["status"] != STATUS_POSTED:
                    raise ValueError(
                        "出演者全員のYesが揃っていません。"
                        "管理者判断で投稿する場合だけ force=True を指定してください。"
                    )
                invites = item.get("collab_urls", {})
                missing = [uid for uid in item["member_ids"] if uid not in invites]
                if missing:
                    raise ValueError(
                        "コラボ依頼の設定がない出演者がいます："
                        + ", ".join(missing)
                        + "。/zzzadmin_plan_collab で登録してください。"
                    )
                link = https_url(published_url) if published_url.strip() else item.get("published_url", "")
                await asyncio.to_thread(
                    self.data.plans.update_one, {"_id": item["_id"]},
                    {"$set": {
                        "status": STATUS_POSTED,
                        "published_url": link,
                        "posted_at": item.get("posted_at") or utcnow(),
                        "updated_at": utcnow(),
                    }},
                )
                item["published_url"] = link
                sent_count = 0
                for uid in item["member_ids"]:
                    urls = invites[uid]
                    lines = [
                        f"🎬【{discord.utils.escape_markdown(item['name'])}】"
                        "の投稿が完了しました！",
                    ]
                    if link:
                        lines.append("公開動画：" + link)
                    if urls.get("no_invite"):
                        lines.append("コラボ招待の対象外です。")
                    else:
                        lines.append("コラボレーション依頼・タグ付けリンク：")
                        lines.extend(f"{key}: {val}" for key, val in urls.items())
                    if await self.send_once("posted", item, uid, "\n".join(lines)):
                        sent_count += 1
            await interaction.followup.send(
                f"ステータスを「投稿完了」に変更しました。"
                f" 作業場・管理者DMへ送信：{sent_count}/{len(item['member_ids'])}件。"
                " 未送信者への再送は同じコマンドを実行してください。",
                ephemeral=True,
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except Exception as exc:
            print(f"[Alien/Plan] posted error: {type(exc).__name__}", flush=True)
            await interaction.followup.send("投稿完了処理に失敗しました。", ephemeral=True)


async def setup(bot):
    await bot.add_cog(PlanCog(bot))
