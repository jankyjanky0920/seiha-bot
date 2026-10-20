"""撮影協力者向けスコア確認・ショップ申請と管理者による承認処理。"""

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from alien_bot.score_store import (
    ScoreStore, ADMIN_CHANNEL_ID, PRODUCTS, is_admin, is_worker, score_text,
)


class OrderDecisionView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="承認", style=discord.ButtonStyle.success,
        custom_id="alien:shop:approve",
    )
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.decide(interaction, approved=True)

    @discord.ui.button(
        label="却下", style=discord.ButtonStyle.danger,
        custom_id="alien:shop:reject",
    )
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.decide(interaction, approved=False)


class ShopCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.store = ScoreStore()
        self.bot.add_view(OrderDecisionView(self))

    def cog_unload(self):
        self.store.close()

    def guild_member(self, interaction):
        return (
            interaction.guild is not None
            and interaction.guild.id == self.bot.target_guild_id
            and isinstance(interaction.user, discord.Member)
        )

    async def in_personal_workspace(self, interaction):
        if not self.guild_member(interaction) or not is_worker(interaction.user):
            return False
        channel_id = await asyncio.to_thread(
            self.store.workspace_channel_id,
            interaction.guild.id, interaction.user.id,
        )
        return channel_id == interaction.channel_id

    async def notify_user(self, order, message):
        try:
            channel_id = await asyncio.to_thread(
                self.store.workspace_channel_id, self.bot.target_guild_id,
                int(order["user_id"]),
            )
            if not channel_id:
                return
            ch = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            await ch.send(message, allowed_mentions=discord.AllowedMentions.none())
        except (discord.HTTPException, OSError) as error:
            print(f"[Alien/Shop] 個人作業場の通知失敗: {error}", flush=True)

    @app_commands.command(name="score", description="自分の所持スコアを確認します")
    async def score(self, interaction: discord.Interaction):
        if not self.guild_member(interaction):
            await interaction.response.send_message("このサーバー専用のコマンドです。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await self.in_personal_workspace(interaction):
            await interaction.followup.send(
                "自分の作業場チャンネルで使用してください。", ephemeral=True
            )
            return
        try:
            wallet = await asyncio.to_thread(self.store.wallet, interaction.user.id)
            available = wallet.get("balance_milli", 0)
            held = wallet.get("held_milli", 0)
            await interaction.followup.send(
                f"💰 **所持スコア：{score_text(available + held)}**\n"
                f"利用可能：**{score_text(available)}**\n"
                f"購入申請中：**{score_text(held)}**",
                ephemeral=True,
            )
        except Exception as error:
            print(f"[Alien/Score] 残高取得失敗: {error}", flush=True)
            await interaction.followup.send(
                "残高を取得できませんでした。", ephemeral=True
            )

    @app_commands.command(name="shop", description="スコアショップの表示・購入申請")
    @app_commands.describe(what="購入したい商品（省略すると一覧表示）", how="購入数（自然数）")
    @app_commands.choices(what=[
        app_commands.Choice(name="shorts編集（2スコア）", value="shorts"),
        app_commands.Choice(name="依頼強制購入（3スコア）", value="commission"),
        app_commands.Choice(name="謎解き（1スコア）", value="puzzle"),
        app_commands.Choice(name="あなた専用discord bot（5スコア）", value="discord_bot"),
        app_commands.Choice(name="あなた専用web site（5スコア）", value="website"),
    ])
    async def shop(
        self, interaction: discord.Interaction,
        what: app_commands.Choice[str] | None = None,
        how: int | None = None,
    ):
        if not self.guild_member(interaction):
            await interaction.response.send_message("このサーバー専用です。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await self.in_personal_workspace(interaction):
            await interaction.followup.send(
                "自分の作業場チャンネルで利用してください。", ephemeral=True
            )
            return
        try:
            if what is None:
                lines = [
                    f"**{name}：{score_text(price)} スコア**\n{description}"
                    for name, price, description in PRODUCTS.values()
                ]
                await interaction.followup.send(
                    "🛒 **スコアショップ**\n\n" + "\n\n".join(lines) +
                    "\n\n購入申請：`/shop what:商品 how:個数`",
                    ephemeral=True,
                )
                return
            if how is None or how < 1 or how > 1000:
                await interaction.followup.send(
                    "商品の購入には、1～1000の自然数を how に指定してください。", ephemeral=True
                )
                return
            if what.value not in PRODUCTS:
                await interaction.followup.send("商品が見つかりません。", ephemeral=True)
                return
            order = await asyncio.to_thread(
                self.store.reserve, interaction.user.id, what.value, how
            )
            if order is None:
                wallet = await asyncio.to_thread(self.store.wallet, interaction.user.id)
                await interaction.followup.send(
                    f"スコアが不足しています。利用可能：{score_text(wallet['balance_milli'])}",
                    ephemeral=True,
                )
                return
            admin = self.bot.get_channel(ADMIN_CHANNEL_ID)
            if admin is None:
                admin = await self.bot.fetch_channel(ADMIN_CHANNEL_ID)
            try:
                message = await admin.send(
                    f"🛒 **購入申請**\n"
                    f"申請ID：`{order['_id']}`\n"
                    f"申請者：<@{order['user_id']}>\n"
                    f"商品：**{order['product_name']}** × {order['quantity']}\n"
                    f"確保スコア：**{score_text(order['cost_milli'])}**\n"
                    f"承認または却下を選択してください。",
                    view=OrderDecisionView(self),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await asyncio.to_thread(
                    self.store.shop_orders.update_one,
                    {"_id": order["_id"]},
                    {"$set": {"message_id": message.id}},
                )
                await interaction.followup.send(
                    f"申請しました！ **{order['product_name']} × {how}**\n"
                    f"管理者の回答まで {score_text(order['cost_milli'])} スコアを確保します。",
                    ephemeral=True,
                )
            except discord.HTTPException as error:
                print(f"[Alien/Shop] 管理者への通知失敗: {error}", flush=True)
                await interaction.followup.send(
                    f"申請を保存しスコアを確保しましたが、管理者へ通知できませんでした。\n"
                    f"申請ID：`{order['_id']}`\n"
                    "管理者に /admin_shoprequests の確認を依頼してください。",
                    ephemeral=True,
                )
        except Exception as error:
            print(f"[Alien/Shop] 購入申請失敗: {type(error).__name__}: {error}", flush=True)
            await interaction.followup.send(
                "ショップ処理でエラーになりました。管理者に確認してください。",
                ephemeral=True,
            )

    @app_commands.command(name="admin_shoprequests", description="保留中の購入申請を確認【管理者】")
    async def requests(self, interaction: discord.Interaction):
        if not self.guild_member(interaction) or not is_admin(interaction.user):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            records = await asyncio.to_thread(
                lambda: list(self.store.shop_orders.find(
                    {"state": "pending"}
                ).sort("created_at", -1).limit(20))
            )
            body = "\n".join(
                f"`{o['_id']}` <@{o['user_id']}> {o['product_name']} ×{o['quantity']} ({score_text(o['cost_milli'])})"
                for o in records
            ) or "現在、未処理の申請はありません。"
            await interaction.followup.send(
                body, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
            )
        except Exception as error:
            print(f"[Alien/Shop] 申請一覧エラー: {error}", flush=True)
            await interaction.followup.send("申請を確認できませんでした。", ephemeral=True)

    @app_commands.command(name="admin_shopdecision", description="申請IDを指定して手動で審査【管理者】")
    @app_commands.describe(order_id="申請ID", decision="承認または却下")
    @app_commands.choices(decision=[
        app_commands.Choice(name="承認", value="approve"),
        app_commands.Choice(name="却下", value="reject"),
    ])
    async def admin_decision(
        self, interaction: discord.Interaction, order_id: str,
        decision: app_commands.Choice[str],
    ):
        if not self.guild_member(interaction) or not is_admin(interaction.user):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.do_decide(
            interaction, order_id, approved=decision.value == "approve",
            from_button=False,
        )

    async def decide(self, interaction: discord.Interaction, approved: bool):
        if (
            not self.guild_member(interaction)
            or not is_admin(interaction.user)
            or interaction.channel_id != ADMIN_CHANNEL_ID
        ):
            await interaction.response.send_message("管理者専用です。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        order = await asyncio.to_thread(
            self.store.shop_orders.find_one,
            {"message_id": interaction.message.id},
        )
        if not order:
            await interaction.followup.send("この購入申請は見つかりません。", ephemeral=True)
            return
        await self.do_decide(interaction, order["_id"], approved, from_button=True)

    async def do_decide(self, interaction, order_id, approved, from_button):
        try:
            order, outcome = await asyncio.to_thread(
                self.store.settle_order, order_id, approved, interaction.user.id,
            )
            label = "承認" if outcome == "approved" else "却下"
            if from_button:
                try:
                    await interaction.message.edit(view=None)
                except discord.HTTPException:
                    pass
            await self.notify_user(
                order,
                f"🛒 ショップ申請「{order['product_name']}」×{order['quantity']} は"
                f"**{label}**されました。" +
                (" スコアを消費しました。" if outcome == "approved" else " 確保スコアを返却しました。"),
            )
            await interaction.followup.send(
                f"申請 `{order_id}` を{label}として処理しました。",
                ephemeral=True,
            )
        except (ValueError, RuntimeError) as error:
            await interaction.followup.send(str(error), ephemeral=True)
        except Exception as error:
            print(f"[Alien/Shop] 決裁エラー: {type(error).__name__}: {error}", flush=True)
            await interaction.followup.send(
                "処理中にエラーになりました。申請IDを確認して再試行してください。",
                ephemeral=True,
            )


async def setup(bot):
    await bot.add_cog(ShopCog(bot))
