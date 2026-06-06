# cogs/cipher.py
import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
import random
import asyncio
import messages as msg
from core import (
    JST, START_TIME, CIPHER_VC_ID, ANNOUNCE_CHANNEL_ID, DJ_BOOTH_CHANNEL_ID,
    get_rewarded_users, add_user_balance, save_rewarded_user, remove_rewarded_user,
    get_user_balance, update_ranking_message, voice_active_minutes
)

class CipherCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.daily_cipher_task.start()

    def cog_unload(self):
        self.daily_cipher_task.cancel()

    async def run_cipher_logic(self, end_datetime):
        channel = self.bot.get_channel(ANNOUNCE_CHANNEL_ID)
        dj_booth = self.bot.get_channel(DJ_BOOTH_CHANNEL_ID)
        vc_channel = self.bot.get_channel(CIPHER_VC_ID)
        if not vc_channel: return
        required_minutes = 30.0

        if channel:
            menus = ["**16小節サイファー**", "**2小節サイファー**", "**バトル**"]
            message = f"ラップの練習のお時間です！ <#{CIPHER_VC_ID}> に集まれ！🔥\n練習メニュー案：{random.choice(menus)}"
            await channel.send(message)

        voice_active_minutes.clear()

        try:
            for old_vc in self.bot.voice_clients: await old_vc.disconnect(force=True)
            vc_client = await vc_channel.connect(timeout=20.0, reconnect=True)
            check_interval = 20
            
            while True:
                now = datetime.datetime.now(JST)
                if now >= end_datetime: break
                await asyncio.sleep(check_interval)
                
                rewarded_list = get_rewarded_users()
                current_vc = self.bot.get_channel(CIPHER_VC_ID)
                if not current_vc: break

                for member in current_vc.members:
                    if member.bot: continue
                    user_id = str(member.id)
                    if user_id in rewarded_list: continue

                    v_state = member.voice
                    if v_state and not (v_state.self_mute or v_state.mute or v_state.suppress):
                        voice_active_minutes[user_id] = voice_active_minutes.get(user_id, 0.0) + (check_interval / 60.0)

                        if voice_active_minutes[user_id] >= required_minutes:
                            bonus = random.randint(50, 100)
                            add_user_balance(user_id, bonus)
                            save_rewarded_user(user_id)
                            await update_ranking_message(self.bot)
                            
                            new_balance = get_user_balance(user_id)
                            if dj_booth:
                                text = msg.MSG_CIPHER_REWARD.format(mention=member.mention, amount=bonus, new_balance=new_balance)
                                await dj_booth.send(text, allowed_mentions=discord.AllowedMentions.none())
        except Exception as e: print(f"Error: {e}")
        finally:
            for current_vc_client in self.bot.voice_clients: await current_vc_client.disconnect(force=True)

    @tasks.loop(time=START_TIME)
    async def daily_cipher_task(self):
        await self.bot.wait_until_ready()
        now = datetime.datetime.now(JST)
        end_datetime = now.replace(hour=23, minute=0, second=0, microsecond=0)
        await self.run_cipher_logic(end_datetime)

    @app_commands.command(name="z_join", description=msg.DESC_Z_JOIN)
    @app_commands.default_permissions(administrator=True)
    async def join_vc_slash(self, interaction: discord.Interaction):
        vc_channel = self.bot.get_channel(CIPHER_VC_ID)
        if not vc_channel: return await interaction.response.send_message("VCが見つかりません。", ephemeral=True)
        if interaction.guild.voice_client: await interaction.guild.voice_client.move_to(vc_channel)
        else: await vc_channel.connect()
        await interaction.response.send_message("入室しました！🎤")

    @app_commands.command(name="z_leave", description=msg.DESC_Z_LEAVE)
    @app_commands.default_permissions(administrator=True)
    async def leave_vc_slash(self, interaction: discord.Interaction):
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect()
            await interaction.response.send_message("退室しました。👋")
        else: await interaction.response.send_message("参加していません。", ephemeral=True)

    @app_commands.command(name="daily", description=msg.DESC_DAILY)
    async def daily_status(self, interaction: discord.Interaction):
        if str(interaction.user.id) in get_rewarded_users(): await interaction.response.send_message("獲得済みです！✅", ephemeral=True)
        else: await interaction.response.send_message(f"約{int(voice_active_minutes.get(str(interaction.user.id), 0))}分 / 30分", ephemeral=True)

    @app_commands.command(name="z_dislogin", description=msg.DESC_Z_DISLOGIN)
    @app_commands.default_permissions(administrator=True)
    async def dislogin_slash(self, interaction: discord.Interaction, member: discord.Member):
        remove_rewarded_user(member.id)
        await interaction.response.send_message("デイリー記録を削除しました。")

async def setup(bot):
    await bot.add_cog(CipherCog(bot))
