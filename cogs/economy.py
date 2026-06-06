import discord
from discord.ext import commands, tasks
from discord import app_commands
import random
import re
import asyncio
import messages as msg
from core import (
    MIDNIGHT_TIME, ALLOWED_GUILD_ID, MC_ROLE_ID, B_NAMES, T_NAMES, rank_collection, collection,
    get_user_balance, add_user_balance, set_user_balance, update_ranking_message, update_b_rank_guide_message
)

class EconomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.midnight_ranking_task.start()

    def cog_unload(self):
        self.midnight_ranking_task.cancel()

    @tasks.loop(time=MIDNIGHT_TIME)
    async def midnight_ranking_task(self):
        await self.bot.wait_until_ready()
        await update_ranking_message(self.bot)
        await update_b_rank_guide_message(self.bot)
        
        guild = self.bot.get_guild(ALLOWED_GUILD_ID)
        if not guild: return
        mc_role = guild.get_role(MC_ROLE_ID)
        if not mc_role: return
        
        init_role_name = f"B0-T0 {B_NAMES[0]}_{T_NAMES[0]}"
        init_role = discord.utils.get(guild.roles, name=init_role_name)
        if not init_role: return

        for member in mc_role.members:
            has_rank = any(re.match(r"^B[0-7]-T[0-7]", role.name) for role in member.roles)
            if not has_rank:
                try:
                    rank_collection.update_one({'user_id': str(member.id)}, {'$setOnInsert': {'b_points': 0, 't_points': 0, 'b_rank': 0, 't_rank': 0, 'temporary_rates': []}}, upsert=True)
                    await member.add_roles(init_role, reason="自動割り当て")
                    await asyncio.sleep(0.3)
                except Exception as e: print(f"自動ランク付与失敗: {e}")

    @app_commands.command(name="z_update_ranking", description=msg.DESC_Z_UPDATE_RANKING)
    @app_commands.default_permissions(administrator=True)
    async def manual_update_ranking_slash(self, interaction: discord.Interaction):
        await update_ranking_message(self.bot)
        await interaction.response.send_message("✅ 強制更新しました。")

    @app_commands.command(name="z_bonus", description=msg.DESC_Z_BONUS)
    @app_commands.default_permissions(administrator=True)
    async def manual_bonus_slash(self, interaction: discord.Interaction, member: discord.Member):
        amount = random.randint(50, 100)
        add_user_balance(member.id, amount)
        await update_ranking_message(self.bot)
        new_balance = get_user_balance(member.id)
        
        text = msg.MSG_BONUS_SUCCESS.format(mention=member.mention, amount=amount, new_balance=new_balance)
        await interaction.response.send_message(text, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="z_add", description=msg.DESC_Z_ADD)
    @app_commands.default_permissions(administrator=True)
    async def add_sp_slash(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        add_user_balance(member.id, amount)
        await update_ranking_message(self.bot)
        await interaction.response.send_message(f"{member.display_name}に **{amount} SP** 付与しました。")

    @app_commands.command(name="z_set", description=msg.DESC_Z_SET)
    @app_commands.default_permissions(administrator=True)
    async def set_sp_slash(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        set_user_balance(member.id, amount)
        await update_ranking_message(self.bot)
        await interaction.response.send_message(f"所持金を **{amount} SP** に設定しました。")

    @app_commands.command(name="saifu", description=msg.DESC_SAIFU)
    async def saifu(self, interaction: discord.Interaction):
        balance = get_user_balance(interaction.user.id)
        await interaction.response.send_message(msg.MSG_SAIFU_CHECK.format(name=interaction.user.display_name, balance=balance))

    @app_commands.command(name="sent", description=msg.DESC_SENT)
    async def sent(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        if amount <= 0 or get_user_balance(interaction.user.id) < amount: return await interaction.response.send_message("無効な額または不足。", ephemeral=True)
        collection.update_one({'user_id': str(interaction.user.id)}, {'$inc': {'balance': -amount}})
        collection.update_one({'user_id': str(member.id)}, {'$inc': {'balance': amount}}, upsert=True)
        await update_ranking_message(self.bot)
        await interaction.response.send_message(f"{member.display_name}さんに **{amount} SP** 送金しました！")

async def setup(bot):
    await bot.add_cog(EconomyCog(bot))
