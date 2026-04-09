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

ALLOWED_GUILD_ID = 1480208337533534379
ANNOUNCE_CHANNEL_ID = 1480421657913852005
MC_ROLE_ID = 1480235861244383262
CIPHER_VC_ID = 1480212977650110828

# --- 2. 時間設定と監視用変数（順番を直しました！） ---
JST = datetime.timezone(datetime.timedelta(hours=9))
announce_time = datetime.time(hour=22, minute=34, tzinfo=JST) # テスト用の時間
exit_time_info = datetime.time(hour=23, minute=0, tzinfo=JST)

rewarded_users = set()       # 今日すでに報酬を受け取った人を記録
voice_active_minutes = {}    # 各ユーザーの「マイクON」時間を記録

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

# --- 4. Webサーバー (Render対策) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, threaded=True)

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

# --- 5. Botの基本設定とタスク ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="$", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print("スラッシュコマンドを同期しました！")
        # 起動時に自動送信タイマーをスタート
        daily_cipher_announce.start()

bot = MyBot()

@tasks.loop(time=announce_time)
async def daily_cipher_announce():
    await bot.wait_until_ready()
    
    # --- ① お知らせメッセージの送信 ---
    channel = bot.get_channel(ANNOUNCE_CHANNEL_ID)
    if not channel:
        print("エラー: 送信先のチャンネルが見つかりません。")
        return

    menus = ["**16小節サイファー**", "**2小節サイファー**", "**バトル**"]
    todays_menu = random.choice(menus)

    message = (
        f"（メンション通知）\n"
        f"ラップの練習のお時間です！練習したいMCはぜひ <#{CIPHER_VC_ID}> に集まってください🔥\n"
        f"途中退室も途中入場も構いません！\n\n"
        f"練習メニュー、こんなのはいかが？\n"
        f"21:00~22:00　**8小節サイファー**\n"
        f"22:00~23:00　{todays_menu}"
    )
    await channel.send(message)

    # --- ② ボイスチャンネル入室と監視 ---
    vc_channel = bot.get_channel(CIPHER_VC_ID)
    if not vc_channel:
        print("エラー: ボイスチャンネルが見つかりません。")
        return

    rewarded_users.clear()
    voice_active_minutes.clear()

    # VCに接続
    vc = await vc_channel.connect()
    print(f"{vc_channel.name} に接続し、検知を開始しました。")

    while True:
        now = datetime.datetime.now(JST).time()
        if now >= exit_time_info:
            break
        
        await asyncio.sleep(60)

        data = load_data()
        updated = False

        for member in vc_channel.members:
            if member.bot:
                continue

            v_state = member.voice
            if v_state and not (v_state.self_mute or v_state.mute or v_state.suppress):
                user_id = str(member.id)
                voice_active_minutes[user_id] = voice_active_minutes.get(user_id, 0) + 1

                # テスト用に5分で設定
                if voice_active_minutes[user_id] >= 5 and user_id not in rewarded_users:
                    bonus = random.randint(50, 100)
                    data[user_id] = data.get(user_id, 0) + bonus
                    rewarded_users.add(user_id)
                    updated = True
                    
                    try:
                        await member.send(f"🎤 サイファーお疲れ様です！練習（マイクON）を確認したので、ボーナスとして **{bonus} SP** を付与しました！")
                    except:
                        pass

        if updated:
            save_data(data)

    # --- ③ 23:00になったら退室 ---
    if bot.voice_clients:
        for client in bot.voice_clients:
            if client.channel.id == CIPHER_VC_ID:
                await client.disconnect()
                print("23:00になったので退室しました。")

@bot.event
async def on_ready():
    print(f'ログインしました: {bot.user.name}')
    print("------")

# --- 6. スラッシュコマンド一覧 ---
@bot.tree.command(name="saifu", description="自分の所持金を表示します")
@app_commands.guilds(ALLOWED_GUILD_ID)
async def saifu(interaction: discord.Interaction):
    data = load_data()
    user_id = str(interaction.user.id)
    balance = data.get(user_id, 0)
    await interaction.response.send_message(f"{interaction.user.display_name}さんの所持金は **{balance} SP** です。")

@bot.tree.command(name="sent", description="指定したユーザーにSPを送金します")
@app_commands.describe(member="送金先のユーザー", amount="送る金額")
@app_commands.guilds(ALLOWED_GUILD_ID)
async def sent(interaction: discord.Interaction, member: discord.Member, amount: int):
    if amount <= 0:
        await interaction.response.send_message("1 SP以上を指定してください。", ephemeral=True)
        return

    data = load_data()
    sender_id = str(interaction.user.id)
    receiver_id = str(member.id)

    sender_balance = data.get(sender_id, 0)
    if sender_balance < amount:
        await interaction.response.send_message(f"SPが足りません！（現在の残高: {sender_balance} SP）", ephemeral=True)
        return

    data[sender_id] = sender_balance - amount
    data[receiver_id] = data.get(receiver_id, 0) + amount
    save_data(data)
    
    await interaction.response.send_message(f"{interaction.user.display_name}さんから{member.display_name}さんに **{amount} SP** 送金しました！")

@bot.tree.command(name="p-add", description="【管理者用】指定ユーザーのSPを増やします")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(member="付与先のユーザー", amount="増やす金額")
@app_commands.guilds(ALLOWED_GUILD_ID)
async def p_add(interaction: discord.Interaction, member: discord.Member, amount: int):
    data = load_data()
    user_id = str(member.id)
    
    data[user_id] = data.get(user_id, 0) + amount
    save_data(data)
    
    await interaction.response.send_message(f"管理者操作: {member.display_name}さんに **{amount} SP** 付与しました。")

@bot.tree.command(name="p-remove", description="【管理者用】指定ユーザーのSPを減らします")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(member="没収先のユーザー", amount="減らす金額")
@app_commands.guilds(ALLOWED_GUILD_ID)
async def p_remove(interaction: discord.Interaction, member: discord.Member, amount: int):
    data = load_data()
    user_id = str(member.id)
    
    current_balance = data.get(user_id, 0)
    data[user_id] = max(0, current_balance - amount)
    save_data(data)
    
    await interaction.response.send_message(f"管理者操作: {member.display_name}さんから **{amount} SP** 没収しました。")

# --- 7. 起動シーケンス ---
if __name__ == "__main__":
    if TOKEN is None:
        print("エラー: DISCORD_TOKEN が設定されていません。")
    else:
        print("Webサーバーを起動しています...")
        keep_alive()  
        time.sleep(2) 
        print("Discord Botを起動します...")
        bot.run(TOKEN)
