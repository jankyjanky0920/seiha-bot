import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
from dotenv import load_dotenv
from flask import Flask
from threading import Thread
import pymongo
import time
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
daily_collection = db['daily_status'] # ボーナス受取状況用

ALLOWED_GUILD_ID = 1480208337533534379
ANNOUNCE_CHANNEL_ID = 1480421657913852005
CIPHER_VC_ID = 1480212977650110828
DJ_BOOTH_CHANNEL_ID = 1480284498942759166 # DJブース

# --- 2. 時間設定と監視用変数 ---
JST = datetime.timezone(datetime.timedelta(hours=9))
announce_time = datetime.time(hour=20, minute=50, tzinfo=JST) 
exit_time_info = datetime.time(hour=23, minute=0, tzinfo=JST)

# 監視中の一時的な累積時間 (分)
voice_active_minutes = {}

# --- 3. データベース用関数 ---
def load_data():
    data = {}
    try:
        for doc in collection.find():
            data[doc['user_id']] = doc['balance']
    except Exception as e:
        print(f"DB読み込みエラー: {e}")
    return data

def save_data(data):
    try:
        for user_id, balance in data.items():
            collection.update_one(
                {'user_id': str(user_id)}, 
                {'$set': {'balance': balance}}, 
                upsert=True
            )
    except Exception as e:
        print(f"DB保存エラー: {e}")

# ボーナス受取済みリストをDBから取得
def get_rewarded_users():
    today = datetime.datetime.now(JST).strftime('%Y-%m-%d')
    doc = daily_collection.find_one({'date': today})
    return set(doc['users']) if doc else set()

# ボーナス受取済みリストをDBへ保存
def save_rewarded_user(user_id):
    today = datetime.datetime.now(JST).strftime('%Y-%m-%d')
    daily_collection.update_one(
        {'date': today},
        {'$addToSet': {'users': str(user_id)}},
        upsert=True
    )

# 記録削除（-dislogin用）
def remove_rewarded_user(user_id):
    today = datetime.datetime.now(JST).strftime('%Y-%m-%d')
    daily_collection.update_one(
        {'date': today},
        {'$pull': {'users': str(user_id)}}
    )

# --- 4. Webサーバー (Render対策) ---
app = Flask('')
@app.route('/')
def home(): return "Bot is running!"
def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, threaded=True)
def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

# --- 5. Botの基本設定 ---
# プレフィックスを "-" に設定
intents = discord.Intents.all()
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="-", intents=intents)

    async def setup_hook(self):
        guild = discord.Object(id=ALLOWED_GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild) 
        daily_cipher_announce.start()

bot = MyBot()

# --- ★ サイファー監視メインロジック (判定時間を可変にしました) ---
async def run_cipher_logic(end_time_obj, is_test=False):
    channel = bot.get_channel(ANNOUNCE_CHANNEL_ID)
    dj_booth = bot.get_channel(DJ_BOOTH_CHANNEL_ID)
    
    # テスト時と本番時で判定時間を変える
    required_minutes = 0.99 if is_test else 29.9
    mode_name = "【テストモード(1分判定)】" if is_test else "【通常モード(30分判定)】"
    
    print(f"{mode_name} 監視を開始します。")

    if channel:
        menus = ["**16小節サイファー**", "**2小節サイファー**", "**バトル**"]
        message = (
            f"（メンション通知）\n"
            f"{'⚠️テスト起動中⚠️ ' if is_test else ''}ラップの練習のお時間です！ <#{CIPHER_VC_ID}> に集まれ！🔥\n"
            f"練習メニュー案：{random.choice(menus)}"
        )
        await channel.send(message)

    voice_active_minutes.clear()
    vc_channel = bot.get_channel(CIPHER_VC_ID)
    if not vc_channel: return

    try:
        for old_vc in bot.voice_clients: await old_vc.disconnect(force=True)
        vc = await vc_channel.connect(timeout=20.0, reconnect=True)
        
        check_interval = 10  
        while True:
            now = datetime.datetime.now(JST).time()
            if now >= end_time_obj: break
            
            await asyncio.sleep(check_interval)
            
            data = load_data()
            rewarded_list = get_rewarded_users()
            updated = False

            current_vc = bot.get_channel(CIPHER_VC_ID)
            for member in current_vc.members:
                if member.bot: continue
                user_id = str(member.id)
                if user_id in rewarded_list: continue

                v_state = member.voice
                if v_state and not (v_state.self_mute or v_state.mute or v_state.suppress):
                    voice_active_minutes[user_id] = voice_active_minutes.get(user_id, 0.0) + (check_interval / 60.0)

                    # 動的に設定した時間で判定
                    if voice_active_minutes[user_id] >= required_minutes:
                        bonus = random.randint(50, 100)
                        data[user_id] = data.get(user_id, 0) + bonus
                        save_rewarded_user(user_id)
                        updated = True
                        
                        if dj_booth:
                            await dj_booth.send(f"{member.mention} さんのデイリーサイファーを確認！**{bonus} SP** を付与しました。現在の所持金は **{data[user_id]} SP** です。")

            if updated: save_data(data)

    except Exception as e: print(f"Error: {e}")
    finally:
        for current_vc in bot.voice_clients: await current_vc.disconnect(force=True)

# --- 通常スケジュール (is_test=False) ---
@tasks.loop(time=announce_time)
async def daily_cipher_announce():
    await bot.wait_until_ready()
    await run_cipher_logic(exit_time_info, is_test=False)

# --- 管理者用テストコマンド (is_test=True) ---
@bot.command(name="testrun")
@commands.has_permissions(administrator=True)
async def testrun(ctx):
    """【管理者】テスト監視開始(10分間 / 1分でボーナス)"""
    await ctx.send("テスト監視を開始します（10分間 / 1分喋れば報酬付与）")
    now_dt = datetime.datetime.now(JST)
    test_end = (now_dt + datetime.timedelta(minutes=10)).time()
    bot.loop.create_task(run_cipher_logic(test_end, is_test=True))

@bot.command(name="add")
@commands.has_permissions(administrator=True)
async def add_sp(ctx, member: discord.Member, amount: int):
    """【管理者】SP付与"""
    data = load_data()
    data[str(member.id)] = data.get(str(member.id), 0) + amount
    save_data(data)
    await ctx.send(f"管理者操作: {member.display_name}に **{amount} SP** 付与しました。")

@bot.command(name="remove")
@commands.has_permissions(administrator=True)
async def remove_sp(ctx, member: discord.Member, amount: int):
    """【管理者】SP没収"""
    data = load_data()
    uid = str(member.id)
    data[uid] = max(0, data.get(uid, 0) - amount)
    save_data(data)
    await ctx.send(f"管理者操作: {member.display_name}から **{amount} SP** 没収しました。")

@bot.command(name="dislogin")
@commands.has_permissions(administrator=True)
async def dislogin(ctx, member: discord.Member):
    """【管理者】今日のデイリー記録を削除"""
    remove_rewarded_user(member.id)
    await ctx.send(f"管理者操作: {member.display_name}の今日のデイリー記録を削除しました。再度ログイン可能です。")

# --- 7. 一般用スラッシュコマンド (/) ---

@bot.tree.command(name="daily", description="今日のデイリーボーナスを受け取ったか確認します")
async def daily_status(interaction: discord.Interaction):
    rewarded_list = get_rewarded_users()
    if str(interaction.user.id) in rewarded_list:
        await interaction.response.send_message("今日のデイリーサイファーは【完了】しています！✅", ephemeral=True)
    else:
        # 現在の累積時間も表示
        current_min = voice_active_minutes.get(str(interaction.user.id), 0)
        await interaction.response.send_message(f"今日のデイリーサイファーは【未完了】です。現在のマイクON時間: 約{int(current_min)}分 / 30分", ephemeral=True)

@bot.tree.command(name="saifu", description="所持金を確認")
async def saifu(interaction: discord.Interaction):
    data = load_data()
    balance = data.get(str(interaction.user.id), 0)
    await interaction.response.send_message(f"{interaction.user.display_name}さんの所持金: **{balance} SP**")

@bot.tree.command(name="sent", description="SPを送金")
async def sent(interaction: discord.Interaction, member: discord.Member, amount: int):
    if amount <= 0: return await interaction.response.send_message("1以上を指定してください。", ephemeral=True)
    data = load_data()
    sid, rid = str(interaction.user.id), str(member.id)
    if data.get(sid, 0) < amount: return await interaction.response.send_message("SP不足です。", ephemeral=True)
    data[sid] -= amount
    data[rid] = data.get(rid, 0) + amount
    save_data(data)
    await interaction.response.send_message(f"{member.display_name}さんに **{amount} SP** 送金しました！")

# --- 8. 起動 ---
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)
