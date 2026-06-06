import os
import discord
from discord.ext import commands
from flask import Flask
from threading import Thread
import asyncio
import core

# --- Webサーバー (Render維持用) ---
app = Flask('')
@app.route('/')
def home(): return "Bot is running!"
def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

# --- Bot本体の定義 ---
intents = discord.Intents.all()
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="-", intents=intents)
        
    async def setup_hook(self):
        # 💡 読み込む子ファイルのリストに "cogs.rating_b" を追加しました
        extensions = [
            "cogs.tasks",
            "cogs.cipher",
            "cogs.economy",
            "cogs.entertainment",
            "cogs.rating_b"  # 👈 ここを追加！
        ]
        for ext in extensions:
            await self.load_extension(ext)

bot = MyBot()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    # 初回のYouTubeプレイリスト取得
    core.cached_beats = await asyncio.to_thread(core.get_playlist_urls, core.PLAYLIST_URL)
    await core.update_ranking_message(bot)
    await core.update_b_rank_guide_message(bot)
    
    try:
        guild = discord.Object(id=core.ALLOWED_GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    except Exception as e:
        print(f"同期エラー: {e}")

# プレフィックス型の同期コマンド
@bot.command(name="sync")
@commands.has_permissions(administrator=True)
async def sync_commands(ctx):
    guild = discord.Object(id=core.ALLOWED_GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    await core.update_b_rank_guide_message(bot)
    await core.update_ranking_message(bot)
    await ctx.send("✅ スラッシュコマンド・メッセージの強制上書き同期が完了しました！")

if __name__ == "__main__":
    keep_alive()
    bot.run(core.TOKEN)
