import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
from dotenv import load_dotenv
from flask import Flask
from threading import Thread
import pymongo
import datetime
import random
import asyncio

# --- 1. 環境変数の読み込みとDB設定 ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

MONGO_URL = os.getenv('MONGO_URL')
client = pymongo.MongoClient(MONGO_URL, tlsAllowInvalidCertificates=True)
db = client['discord_bot_db']
collection = db['user_balance']
daily_collection = db['daily_status']
word_collection = db['word_dictionary']

MC_ROLE_ID = 1480235861244383262
ALLOWED_GUILD_ID = 1480208337533534379
ANNOUNCE_CHANNEL_ID = 1492856858078220542
CIPHER_VC_ID = 1480212977650110828
DJ_BOOTH_CHANNEL_ID = 1492856858078220542

# --- 2. 時間設定 ---
JST = datetime.timezone(datetime.timedelta(hours=9))
# 20:50に自動開始
START_TIME = datetime.time(hour=20, minute=50, tzinfo=JST) 
RANKING_TIME = datetime.time(hour=22, minute=0, tzinfo=JST)

# 監視用変数
voice_active_minutes = {}

# --- 3. データベース用関数 ---
def get_user_balance(user_id):
    doc = collection.find_one({'user_id': str(user_id)})
    return doc['balance'] if doc else 0

def add_user_balance(user_id, amount):
    """ユーザーのSPを加算する (最適化済み)"""
    collection.update_one(
        {'user_id': str(user_id)},
        {'$inc': {'balance': amount}},
        upsert=True
    )

def get_rewarded_users():
    today = datetime.datetime.now(JST).strftime('%Y-%m-%d')
    doc = daily_collection.find_one({'date': today})
    return set(doc.get('users', [])) if doc else set()

def save_rewarded_user(user_id):
    today = datetime.datetime.now(JST).strftime('%Y-%m-%d')
    daily_collection.update_one(
        {'date': today},
        {'$addToSet': {'users': str(user_id)}},
        upsert=True
    )

def remove_rewarded_user(user_id):
    today = datetime.datetime.now(JST).strftime('%Y-%m-%d')
    daily_collection.update_one(
        {'date': today},
        {'$pull': {'users': str(user_id)}}
    )

# --- 4. Webサーバー (Render/Keep-alive用) ---
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

# --- 5. Botの基本設定 ---
intents = discord.Intents.all()
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="-", intents=intents)

    async def setup_hook(self):
        guild = discord.Object(id=ALLOWED_GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild) 
        
        daily_cipher_task.start() 
        daily_ranking_task.start()

bot = MyBot()

# --- ★ サイファー監視メインロジック ---
async def run_cipher_logic(end_datetime):
    channel = bot.get_channel(ANNOUNCE_CHANNEL_ID)
    dj_booth = bot.get_channel(DJ_BOOTH_CHANNEL_ID)
    vc_channel = bot.get_channel(CIPHER_VC_ID)
    
    if not vc_channel:
        print("エラー: VCチャンネルが見つかりません。")
        return

    required_minutes = 30.0  # 30分固定
    print(f"【サイファー監視開始】終了予定: {end_datetime.strftime('%H:%M')}")

    # 開始アナウンス
    if channel:
        menus = ["**16小節サイファー**", "**2小節サイファー**", "**バトル**"]
        message = (
            f"ラップの練習のお時間です！ <#{CIPHER_VC_ID}> に集まれ！🔥\n"
            f"練習メニュー案：{random.choice(menus)}"
        )
        await channel.send(message)

    voice_active_minutes.clear()

    try:
        # 既存の接続があれば切断
        for old_vc in bot.voice_clients:
            await old_vc.disconnect(force=True)
        
        # VCに入室
        vc_client = await vc_channel.connect(timeout=20.0, reconnect=True)
        
        check_interval = 20 # チェック間隔（秒）
        while True:
            now = datetime.datetime.now(JST)
            if now >= end_datetime:
                break
            
            await asyncio.sleep(check_interval)
            
            rewarded_list = get_rewarded_users()
            current_vc = bot.get_channel(CIPHER_VC_ID)
            if not current_vc: break

            for member in current_vc.members:
                if member.bot: continue
                user_id = str(member.id)
                
                # すでに今日報酬をもらっている人はスキップ
                if user_id in rewarded_list: continue

                # ミュートしていないかチェック
                v_state = member.voice
                if v_state and not (v_state.self_mute or v_state.mute or v_state.suppress):
                    # 加算処理
                    voice_active_minutes[user_id] = voice_active_minutes.get(user_id, 0.0) + (check_interval / 60.0)

                    # 30分経過判定
                    if voice_active_minutes[user_id] >= required_minutes:
                        bonus = random.randint(50, 100)
                        add_user_balance(user_id, bonus) # DB更新
                        save_rewarded_user(user_id)     # 当日済みリストへ
                        
                        new_balance = get_user_balance(user_id)
                        if dj_booth:
                            await dj_booth.send(f"{member.mention} さんのデイリーサイファー（30分）を確認！**{bonus} SP** を付与しました。現在の所持金: **{new_balance} SP**")

    except Exception as e: 
        print(f"Error in run_cipher_logic: {e}")
    finally:
        # 終了時間になったら退室
        for current_vc_client in bot.voice_clients:
            await current_vc_client.disconnect(force=True)
        print("23:00になりました。監視を終了し、退室しました。")

# --- 6. 定期タスク ---

@tasks.loop(time=START_TIME)
async def daily_cipher_task():
    """20:50に起動し、23:00まで監視する"""
    await bot.wait_until_ready()
    now = datetime.datetime.now(JST)
    # 今日の23:00を終了時刻に設定
    end_datetime = now.replace(hour=23, minute=0, second=0, microsecond=0)
    await run_cipher_logic(end_datetime)

@tasks.loop(time=RANKING_TIME)
async def daily_ranking_task():
    """22:00にランキングを表示"""
    await bot.wait_until_ready()
    channel = bot.get_channel(DJ_BOOTH_CHANNEL_ID)
    if channel:
        ranking_msg = await get_sp_ranking()
        await channel.send(ranking_msg, allowed_mentions=discord.AllowedMentions.none())

# --- 7. 管理者用コマンド ---

@bot.command(name="join")
@commands.has_permissions(administrator=True)
async def join_vc(ctx):
    vc_channel = bot.get_channel(CIPHER_VC_ID)
    if not vc_channel: return await ctx.send("VCが見つかりません。")
    if ctx.guild.voice_client: await ctx.guild.voice_client.move_to(vc_channel)
    else: await vc_channel.connect()
    await ctx.send("サイファーVCに入室しました！🎤")

@bot.command(name="leave")
@commands.has_permissions(administrator=True)
async def leave_vc(ctx):
    if ctx.guild.voice_client:
        await ctx.guild.voice_client.disconnect()
        await ctx.send("退室しました。👋")

@bot.command(name="add")
@commands.has_permissions(administrator=True)
async def add_sp(ctx, member: discord.Member, amount: int):
    add_user_balance(member.id, amount)
    await ctx.send(f"{member.display_name}に **{amount} SP** 付与しました。")

# --- 8. スラッシュコマンド (一般) ---

async def get_sp_ranking():
    guild = bot.get_guild(ALLOWED_GUILD_ID)
    if not guild: return "サーバーが見つかりません。"
    mc_role = guild.get_role(MC_ROLE_ID)
    if not mc_role: return "MCロールが見つかりません。"

    # ランキング作成（スコアがある人のみ抽出）
    ranking_data = []
    for member in mc_role.members:
        sp = get_user_balance(member.id)
        ranking_data.append((member.display_name, sp))

    ranking_data.sort(key=lambda x: x[1], reverse=True)
    if not ranking_data: return "ランキング対象のユーザーがいません。"

    msg = "🏆 **MC限定 SPランキング** 🏆\n"
    for i, (name, sp) in enumerate(ranking_data[:10], 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}位"
        msg += f"{medal} {name}: **{sp} SP**\n"
    return msg

@bot.tree.command(name="ranking", description="SPランキングを表示")
async def ranking(interaction: discord.Interaction):
    ranking_msg = await get_sp_ranking()
    await interaction.response.send_message(ranking_msg, allowed_mentions=discord.AllowedMentions.none())

@bot.tree.command(name="daily", description="デイリーボーナスの進捗確認")
async def daily_status(interaction: discord.Interaction):
    rewarded_list = get_rewarded_users()
    if str(interaction.user.id) in rewarded_list:
        await interaction.response.send_message("今日の報酬は獲得済みです！✅", ephemeral=True)
    else:
        current_min = voice_active_minutes.get(str(interaction.user.id), 0)
        await interaction.response.send_message(f"現在のマイクON時間: 約{int(current_min)}分 / 30分", ephemeral=True)

@bot.tree.command(name="saifu", description="所持金を確認")
async def saifu(interaction: discord.Interaction):
    balance = get_user_balance(interaction.user.id)
    await interaction.response.send_message(f"{interaction.user.display_name}さんの所持金: **{balance} SP**")

# --- 9. 起動 ---
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)
