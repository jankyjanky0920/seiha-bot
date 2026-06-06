import discord
from discord.ext import commands
from discord import app_commands
import random
import asyncio
import messages as msg
import core

class EntertainmentCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="z_bulk_remove", description=msg.DESC_Z_BULK_REMOVE)
    @app_commands.default_permissions(administrator=True)
    async def bulk_remove_slash(self, interaction: discord.Interaction, words_str: str):
        words_to_delete = words_str.split()
        result = core.word_collection.delete_many({'word': {'$in': words_to_delete}})
        await interaction.response.send_message(f"{result.deleted_count} 個の単語を削除しました。")

    @app_commands.command(name="z_readingbeat", description=msg.DESC_Z_READINGBEAT)
    @app_commands.default_permissions(administrator=True)
    async def readingbeat_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            new_beats = await asyncio.to_thread(core.get_playlist_urls, core.PLAYLIST_URL)
            if new_beats:
                core.cached_beats = new_beats
                await interaction.followup.send(f"✅ {len(core.cached_beats)}件のビートを読み込みました。")
            else: await interaction.followup.send("⚠️ リストが空です。")
        except Exception: await interaction.followup.send("❌ エラーが発生しました。")

    @app_commands.command(name="beat", description=msg.DESC_BEAT)
    async def beat(self, interaction: discord.Interaction):
        if not core.cached_beats: return await interaction.response.send_message("空です。", ephemeral=True)
        await interaction.response.send_message(f"m!p {random.choice(core.cached_beats)}")

    @app_commands.command(name="gamerule", description=msg.DESC_GAMERULE)
    async def gamerule(self, interaction: discord.Interaction):
        bpm = random.choices(['LOW', 'MIDDLE', 'FAST', 'ACAPPELLA'], weights=[30, 30, 30, 10])[0]
        turn = random.choices(['45s×2', '60s×2'], weights=[50, 50])[0] if bpm == 'ACAPPELLA' else random.choices(['8×2', '8×3', '8×4', '16×2', '32×2', '45s×2', '60s×2'], weights=[10, 20, 30, 20, 4, 8, 8])[0]
        await interaction.response.send_message(f"BPM：**{bpm}** TURN：**{turn}**")

    @app_commands.command(name="vote", description=msg.DESC_VOTE)
    async def vote(self, interaction: discord.Interaction, first: str, second: str):
        await interaction.response.send_message(f":a: 先攻：{first}\n:regional_indicator_b: 後攻：{second}")
        msg_obj = await interaction.original_response()
        await msg_obj.add_reaction("🅰️")
        await msg_obj.add_reaction("🇧")

    @app_commands.command(name="help", description=msg.DESC_HELP)
    async def help_command(self, interaction: discord.Interaction):
        await interaction.response.send_message("🔗 **[マニュアルを開く](https://note.com/preview/nfbf42a5fb2b3?prev_access_key=941730f8a7759e7dcd7daf2822f4faa8)**", ephemeral=True)

    @app_commands.command(name="add_word", description=msg.DESC_ADD_WORD)
    async def word_add(self, interaction: discord.Interaction, word: str):
        if core.word_collection.find_one({'word': word}): return await interaction.response.send_message("登録済み！", ephemeral=True)
        core.word_collection.insert_one({'word': word, 'added_by': str(interaction.user.id)})
        await interaction.response.send_message(f"「{word}」を追加しました！🔥")

    @app_commands.command(name="wordbattle", description=msg.DESC_WORDBATTLE)
    async def word_battle(self, interaction: discord.Interaction, count: int = 1, interval: int = 0):
        total = core.word_collection.count_documents({})
        if count <= 0 or total == 0: return await interaction.response.send_message("エラー", ephemeral=True)
        actual = min(count, total)
        await interaction.response.send_message(f"🎤 開始！ (合計: {actual}個 / 間隔: {interval}分)")
        words = [d['word'] for d in core.word_collection.aggregate([{ "$sample": { "size": actual } }])]
        for i, w in enumerate(words):
            await interaction.channel.send(f"**【{i+1}個目】** 👉   **{w}**")
            if i < len(words) - 1 and interval > 0: await asyncio.sleep(interval * 60)

async def setup(bot):
    await bot.add_cog(EntertainmentCog(bot))
