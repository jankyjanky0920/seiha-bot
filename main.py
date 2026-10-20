import os
import discord
from discord.ext import commands
from flask import Flask
from threading import Thread
import time
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

# --- メモリ使用量のログ記録（Render / Linux）---
def log_memory_usage():
    """現在・最大メモリ使用量を5分ごとにログへ記録します。"""
    while True:
        try:
            stats = {}
            with open("/proc/self/status", encoding="utf-8") as status:
                for line in status:
                    if line.startswith(("VmRSS:", "VmHWM:")):
                        key, value = line.split(":", 1)
                        stats[key] = int(value.strip().split()[0]) / 1024

            current = stats.get("VmRSS")
            peak = stats.get("VmHWM")
            if current is not None and peak is not None:
                print(f"[Memory] 現在の使用量: {current:.1f} MiB | 最大使用量: {peak:.1f} MiB", flush=True)
            else:
                print("[Memory] 使用量の取得に必要な情報がありません", flush=True)
        except (OSError, ValueError, IndexError) as error:
            print(f"[Memory] 使用量を取得できませんでした: {error}", flush=True)
        time.sleep(300)

# --- Bot本体の定義 ---
intents = discord.Intents.all()
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="-", intents=intents)
        # 💡 再接続時のAPI連打（自爆）を防ぐための初回起動フラグを追加
        self.is_first_ready = True 
        
    async def setup_hook(self):
        # 読み込む子ファイルのリスト
        extensions = [
            "cogs.tasks",
            "cogs.cipher",
            "cogs.economy",
            "cogs.entertainment",
            "cogs.rating_b"  
        ]
        for ext in extensions:
            await self.load_extension(ext)

bot = MyBot()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    
    # 💡 初回起動時のみ重い処理を実行し、2回目以降（再接続時）はスルーする
    if bot.is_first_ready:
        bot.is_first_ready = False
        print("--- 🚀 初回起動初期化タスクを実行します ---")
        
        # 初回のYouTubeプレイリスト取得
        try:
            core.cached_beats = await asyncio.to_thread(core.get_playlist_urls, core.PLAYLIST_URL)
        except Exception as e:
            print(f"プレイリスト取得エラー: {e}")
            
        await core.update_ranking_message(bot)
        await core.update_b_rank_guide_message(bot)
        
        # コマンドの同期処理
        try:
            guild = discord.Object(id=core.ALLOWED_GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            print("--- ✅ 初回コマンド同期が正常に完了しました ---")
        except Exception as e:
            print(f"同期エラー: {e}")
    else:
        # 再接続時はログを出すだけでDiscord APIを一切叩かない（安全）
        print("--- 🔄 ネットワーク再接続を確認（初期化処理はスキップされました） ---")

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
    Thread(target=log_memory_usage, daemon=True).start()
    keep_alive()
    bot.run(core.TOKEN)
