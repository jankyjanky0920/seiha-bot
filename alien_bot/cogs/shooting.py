"""撮影時間の記録、途中参加・早退確認、企画名確定後のスコア付与。"""

import asyncio
from uuid import uuid4

import discord
from discord import app_commands
from discord.ext import commands
from discord.http import Route

from alien_bot.score_store import (
    ScoreStore, ADMIN_CHANNEL_ID, is_admin, can_earn_shoot_score, score_text, utcnow,
)


class ShootingYesView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Yes（記録する）", style=discord.ButtonStyle.success,
        custom_id="alien:shoot:yes",
    )
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.confirm_shoot_change(interaction)


class ShootNameModal(discord.ui.Modal, title="撮影した企画の名前を入力"):
    name = discord.ui.TextInput(
        label="企画名", max_length=100,
        placeholder="例：AI vs 人間 コードネーム対決", required=True,
    )

    def __init__(self, cog):
        super().__init__(timeout=600)
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.finish_with_name(interaction, str(self.name))


class ShootNameView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=600)
        self.cog = cog

    @discord.ui.button(label="企画名を入力する", style=discord.ButtonStyle.success)
    async def name(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.cog.permitted(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        await interaction.response.send_modal(ShootNameModal(self.cog))


class ShootingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.store = ScoreStore()
        self.lock = asyncio.Lock()
        self.bot.add_view(ShootingYesView(self))

    def cog_unload(self):
        self.store.close()

    def permitted(self, interaction):
        return (
            interaction.guild is not None
            and interaction.guild.id == self.bot.target_guild_id
            and isinstance(interaction.user, discord.Member)
            and is_admin(interaction.user)
        )

    async def set_status(self, channel_id: int, status: str):
        """DiscordのVoice Channel Status REST APIを使用する。"""
        try:
            await self.bot.http.request(
                Route("PUT", "/channels/{channel_id}/voice-status", channel_id=channel_id),
                json={"status": status},
            )
            return None
        except (discord.Forbidden, discord.HTTPException) as err:
            print(f"[Alien/Shoot] VCステータス変更不可: {err}", flush=True)
            return str(err)

    @app_commands.command(name="zzzadmin_shootstart", description="撮影を開始して時間記録を行います【管理者】")
    async def shootstart(self, interaction: discord.Interaction):
        if not self.permitted(interaction):
            await interaction.response.send_message("管理者専用コマンドです。", ephemeral=True)
            return
        vc = interaction.user.voice.channel if interaction.user.voice else None
        if not isinstance(vc, discord.VoiceChannel):
            await interaction.response.send_message("先に撮影するボイスチャンネルへ参加してください。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.lock:
            try:
                prior = await asyncio.to_thread(
                    self.store.shoots.find_one, {"_id": "current"}
                )
                if prior and prior.get("phase") in ("running", "finalizing", "awaiting_name"):
                    await interaction.followup.send(
                        "すでに撮影中、または前回の撮影の企画名が未登録です。先に /zzzadmin_shootfinish で企画名を登録してください。",
                        ephemeral=True,
                    )
                    return
                if prior:
                    archive = {key: value for key, value in prior.items() if key != "_id"}
                    await asyncio.to_thread(
                        self.store.shoot_history.update_one,
                        {"_id": prior["shoot_id"]},
                        {"$setOnInsert": archive},
                        True,
                    )
                if interaction.guild.voice_client:
                    await interaction.followup.send("星人Botがすでに別のボイスチャンネルに参加しています。", ephemeral=True)
                    return

                await vc.connect(self_deaf=True, timeout=20, reconnect=True)
                started = utcnow()
                participants = {
                    str(member.id): {
                        "joined_at": started, "ended_at": None,
                        "display_name": member.display_name,
                    }
                    for member in vc.members if can_earn_shoot_score(member)
                }
                session = {
                    "_id": "current", "shoot_id": uuid4().hex[:16],
                    "phase": "running", "channel_id": vc.id,
                    "guild_id": interaction.guild.id, "started_at": started,
                    "started_by": str(interaction.user.id),
                    "participants": participants,
                }
                await asyncio.to_thread(
                    self.store.shoots.replace_one, {"_id": "current"}, session, True
                )
                error = await self.set_status(vc.id, "撮影中🔴")
                text = f"🔴 撮影を開始しました。記録対象：{len(participants)}人。"
                if error:
                    text += "（チャンネルステータス変更は失敗しました。権限を確認してください）"
                await interaction.followup.send(text, ephemeral=True)
                print(f"[Alien/Shoot] START {session['shoot_id']} VC={vc.id}", flush=True)
            except Exception as err:
                print(f"[Alien/Shoot] 開始失敗: {type(err).__name__}: {err}", flush=True)
                # DBに開始記録できなかった場合だけVCから退出して再試行できるようにする。
                current = await asyncio.to_thread(
                    self.store.shoots.find_one, {"_id": "current"}
                )
                if not current or current.get("phase") != "running":
                    if interaction.guild.voice_client:
                        await interaction.guild.voice_client.disconnect(force=True)
                await interaction.followup.send(
                    "撮影開始に失敗しました。RenderのログとBotの接続権限を確認してください。",
                    ephemeral=True,
                )

    @app_commands.command(
        name="zzzadmin_shootfinish",
        description="撮影を終了し、次に企画名を登録します【管理者】",
    )
    async def shootfinish(self, interaction: discord.Interaction):
        if not self.permitted(interaction):
            await interaction.response.send_message(
                "管理者専用コマンドです。", ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.lock:
            try:
                shoot = await asyncio.to_thread(
                    self.store.shoots.find_one, {"_id": "current"}
                )
                if not shoot or shoot.get("phase") == "finished":
                    await interaction.followup.send(
                        "記録中・企画名入力待ちの撮影はありません。",
                        ephemeral=True,
                    )
                    return
                if shoot["phase"] == "running":
                    end_at = utcnow()
                    await asyncio.to_thread(
                        self.store.shoots.update_one,
                        {"_id": "current", "phase": "running"},
                        {"$set": {
                            "phase": "awaiting_name", "ended_at": end_at,
                        }},
                    )
                    shoot["phase"] = "awaiting_name"
                    shoot["ended_at"] = end_at
                elif shoot["phase"] == "finalizing":
                    # 以前のバージョンで保存された精算途中も企画として回収する。
                    await asyncio.to_thread(
                        self.store.shoots.update_one,
                        {"_id": "current", "phase": "finalizing"},
                        {"$set": {"phase": "awaiting_name"}},
                    )
                    shoot["phase"] = "awaiting_name"
                elif shoot["phase"] != "awaiting_name":
                    raise RuntimeError("撮影の状態が不正です。")

                await asyncio.to_thread(
                    self.store.confirmations.update_many,
                    {"shoot_id": shoot["shoot_id"], "state": "pending"},
                    {"$set": {"state": "expired"}},
                )
                if interaction.guild.voice_client:
                    try:
                        await interaction.guild.voice_client.disconnect(force=True)
                    except discord.HTTPException as exc:
                        print(f"[Alien/Shoot] VC退出失敗: {exc}", flush=True)
                await self.set_status(shoot["channel_id"], "撮影終了")
                await interaction.followup.send(
                    f"撮影を終了しました。（撮影ID：`{shoot['shoot_id']}`）\n"
                    "**続いて企画名を入力してください。**\n"
                    "スコア付与と出演者へのお礼メッセージは、"
                    "企画名を登録した後に実行します。\n"
                    "画面を閉じた場合は /zzzadmin_shootname から再開できます。",
                    view=ShootNameView(self), ephemeral=True,
                )
            except Exception as exc:
                print(f"[Alien/Shoot] 終了処理失敗: {type(exc).__name__}: {exc}", flush=True)
                await interaction.followup.send(
                    "撮影終了処理に失敗しました。再度 /zzzadmin_shootfinish を実行してください。",
                    ephemeral=True,
                )

    @app_commands.command(
        name="zzzadmin_shootname",
        description="終了済み撮影の企画名を登録しスコアを付与【管理者】",
    )
    async def shootname(self, interaction: discord.Interaction, name: str):
        if not self.permitted(interaction):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.finish_with_name(interaction, name, deferred=True)

    async def finish_with_name(self, interaction, name: str, *, deferred: bool = False):
        from alien_bot.production_plans import normalize_name, voice_plan, STATUS_LABELS

        if not self.permitted(interaction):
            if deferred:
                await interaction.followup.send("管理者専用です。", ephemeral=True)
            else:
                await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        if not deferred:
            await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            name = normalize_name(name)
            async with self.lock:
                session = await asyncio.to_thread(
                    self.store.shoots.find_one,
                    {"_id": "current", "phase": "awaiting_name"},
                )
                if not session:
                    await interaction.followup.send(
                        "企画名を登録する撮影がありません。", ephemeral=True,
                    )
                    return
                manager = self.bot.get_cog("PlanCog")
                if manager is None:
                    raise RuntimeError("企画管理機能が有効になっていません")
                record = voice_plan(session, name)
                plan = await manager.new_plan(record, shoot=session)
                await asyncio.to_thread(
                    self.store.shoots.update_one,
                    {"_id": "current", "shoot_id": session["shoot_id"],
                     "phase": "awaiting_name"},
                    {"$set": {
                        "phase": "finished", "plan_id": plan["_id"],
                        "plan_name": plan["name"],
                    }},
                )
                await interaction.followup.send(
                    f"🎬【{plan['name']}】を登録しました！\n"
                    f"企画ID: `{plan['_id']}`\n"
                    f"撮影協力者：{len(plan['member_ids'])}人\n"
                    f"ステータス：{STATUS_LABELS[plan['status']]}\n"
                    "スコア付与と企画名入りのお礼メッセージを処理しました。",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except Exception as exc:
            print(f"[Alien/Shoot] 企画名確定失敗: {type(exc).__name__}: {exc}", flush=True)
            await interaction.followup.send(
                "企画名の確定が完了しませんでした。"
                "再度 /zzzadmin_shootname で再試行してください。"
                "撮影IDによる二重スコア付与は防止します。",
                ephemeral=True,
            )

    async def notify_worker(self, guild_id: int, user_id: int, text: str):
        try:
            cid = await asyncio.to_thread(
                self.store.workspace_channel_id, guild_id, user_id
            )
            if not cid:
                return
            ch = self.bot.get_channel(cid) or await self.bot.fetch_channel(cid)
            await ch.send(text, allowed_mentions=discord.AllowedMentions.none())
        except (discord.HTTPException, OSError) as err:
            print(f"[Alien/Shoot] 個人通知失敗 {user_id}: {err}", flush=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.guild.id != self.bot.target_guild_id or not can_earn_shoot_score(member):
            return
        old = before.channel.id if before.channel else None
        new = after.channel.id if after.channel else None
        if old == new:
            return
        async with self.lock:
            try:
                shoot = await asyncio.to_thread(
                    self.store.shoots.find_one, {"_id": "current", "phase": "running"}
                )
                if not shoot:
                    return
                target = shoot["channel_id"]
                uid = str(member.id)
                participants = shoot["participants"]
                if new == target and old != target:
                    # 再参加した人の早退確認は取り消す。
                    if uid in participants:
                        await asyncio.to_thread(
                            self.store.confirmations.update_many,
                            {"shoot_id": shoot["shoot_id"], "user_id": uid,
                             "kind": "leave", "state": "pending"},
                            {"$set": {"state": "cancelled"}},
                        )
                    elif not await asyncio.to_thread(
                        self.store.confirmations.count_documents,
                        {"shoot_id": shoot["shoot_id"], "user_id": uid,
                         "kind": "late", "state": "pending"},
                    ):
                        await self.ask_admin(
                            shoot, member, "late", utcnow(),
                            "途中参加者として記録しますか？ Yesを押した時点から撮影時間を記録します。",
                        )
                elif old == target and new != target:
                    # まだ途中参加を認めていなければ申請を無効化。
                    if uid not in participants:
                        await asyncio.to_thread(
                            self.store.confirmations.update_many,
                            {"shoot_id": shoot["shoot_id"], "user_id": uid,
                             "kind": "late", "state": "pending"},
                            {"$set": {"state": "cancelled"}},
                        )
                    elif not participants[uid].get("ended_at") and not await asyncio.to_thread(
                        self.store.confirmations.count_documents,
                        {"shoot_id": shoot["shoot_id"], "user_id": uid,
                         "kind": "leave", "state": "pending"},
                    ):
                        await self.ask_admin(
                            shoot, member, "leave", utcnow(),
                            "早退者として記録しますか？ Yesを押すと退室時点で撮影終了扱いになります。",
                        )
            except Exception as err:
                print(f"[Alien/Shoot] 入退室処理エラー: {type(err).__name__}: {err}", flush=True)

    async def ask_admin(self, shoot, member, kind, event_time, description):
        doc = {
            "_id": uuid4().hex[:20], "shoot_id": shoot["shoot_id"],
            "user_id": str(member.id), "kind": kind, "seen_at": event_time,
            "state": "pending", "message_id": None,
        }
        await asyncio.to_thread(self.store.confirmations.insert_one, doc)
        ch = self.bot.get_channel(ADMIN_CHANNEL_ID)
        if ch is None:
            ch = await self.bot.fetch_channel(ADMIN_CHANNEL_ID)
        msg = await ch.send(
            f"🎥 {member.mention} さん\n{description}",
            view=ShootingYesView(self),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await asyncio.to_thread(
            self.store.confirmations.update_one,
            {"_id": doc["_id"]}, {"$set": {"message_id": msg.id}},
        )

    async def confirm_shoot_change(self, interaction: discord.Interaction):
        if not self.permitted(interaction) or interaction.channel_id != ADMIN_CHANNEL_ID:
            await interaction.response.send_message("管理者専用チャンネルでのみ操作できます。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.lock:
            try:
                confirm = await asyncio.to_thread(
                    self.store.confirmations.find_one,
                    {"message_id": interaction.message.id},
                )
                if not confirm or confirm["state"] != "pending":
                    await interaction.followup.send("この確認はすでに終了しています。", ephemeral=True)
                    return
                shoot = await asyncio.to_thread(
                    self.store.shoots.find_one,
                    {"_id": "current", "phase": "running",
                     "shoot_id": confirm["shoot_id"]},
                )
                if not shoot:
                    await interaction.followup.send("この撮影は終了しています。", ephemeral=True)
                    return
                uid = confirm["user_id"]
                if confirm["kind"] == "late":
                    guild = interaction.guild
                    member = guild.get_member(int(uid))
                    if not member:
                        member = await guild.fetch_member(int(uid))
                    if not member.voice or member.voice.channel.id != shoot["channel_id"]:
                        await interaction.followup.send(
                            "対象者はすでに撮影チャンネルから退出しています。", ephemeral=True
                        )
                        return
                    started = utcnow()  # 退室時刻ではなく、Yesを押した時点から
                    await asyncio.to_thread(
                        self.store.shoots.update_one, {"_id": "current"},
                        {"$set": {f"participants.{uid}": {
                            "joined_at": started, "ended_at": None,
                            "display_name": member.display_name,
                        }}},
                    )
                    response_text = f"{member.display_name} さんを途中参加者として登録しました。"
                else:
                    person = shoot["participants"].get(uid)
                    if not person or person.get("ended_at"):
                        await interaction.followup.send("すでに精算済みです。", ephemeral=True)
                        return
                    end = confirm["seen_at"]  # Yesを押した時刻ではなく実際の退室時刻
                    await asyncio.to_thread(
                        self.store.shoots.update_one,
                        {"_id": "current"},
                        {"$set": {f"participants.{uid}.ended_at": end}},
                    )
                    from alien_bot.score_store import duration_reward
                    minutes, amount = duration_reward(person["joined_at"], end)
                    # 早退の終了時刻だけ確定。スコアは企画名登録後に付与する。
                    response_text = (
                        f"早退確定：<@{uid}> {minutes}分、"
                        f"企画名登録後に+{score_text(amount)} スコアを付与"
                    )
                await asyncio.to_thread(
                    self.store.confirmations.update_one,
                    {"_id": confirm["_id"], "state": "pending"},
                    {"$set": {"state": "confirmed", "confirmed_at": utcnow(),
                              "confirmed_by": str(interaction.user.id)}},
                )
                await interaction.message.edit(view=None)
                await interaction.followup.send(
                    response_text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
                )
            except Exception as err:
                print(f"[Alien/Shoot] Yes処理エラー: {type(err).__name__}: {err}", flush=True)
                await interaction.followup.send(
                    "確認処理でエラーが発生しました。もう一度お試しください。", ephemeral=True
                )


async def setup(bot):
    await bot.add_cog(ShootingCog(bot))
