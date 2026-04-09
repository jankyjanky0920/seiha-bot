import discord
from discord.ext import commands
from discord import app_commands
import os
from dotenv import load_dotenv
from flask import Flask
from threading import Thread
import pymongo
import time
from discord.ext import tasks
import datetime
import random

# --- 1. 環境変数の読み込みとDB設定 ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

MONGO_URL = os.getenv('MONGO_URL')
client = pymongo.MongoClient(MONGO_URL, tlsAllowInvalidCertificates=True)
db = client['discord_bot_db']
collection = db['user_balance']
ALLOWED_GUILD_ID = 1480208337533534379

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

# --- 2. Webサーバー (Render対策) ---
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

# --- 3. Botの基本設定 ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

# スラッシュコマンドを同期するための特別なBotクラス
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="$", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print("スラッシュコマンドを同期しました！")
        # 起動時に自動送信タイマーをスタートさせる
        daily_cipher_announce.start()

# ここで一度だけBotを作る
bot = MyBot()

# --- 自動送信の設定 ---
# 取得したIDをここに貼り付けます
ANNOUNCE_CHANNEL_ID = 1480421657913852005
MC_ROLE_ID = 1480235861244383262
CIPHER_VC_ID = 1480212977650110828

# 日本時間（JST）の設定
JST = datetime.timezone(datetime.timedelta(hours=9))
# 毎日 20:50 に設定
announce_time = datetime.time(hour=20, minute=50, tzinfo=JST)

@tasks.loop(time=announce_time)
async def daily_cipher_announce():
    # Botが完全に起動するまで待つ
    await bot.wait_until_ready()
    
    channel = bot.get_channel(ANNOUNCE_CHANNEL_ID)
    if not channel:
        print("エラー: 送信先のチャンネルが見つかりません。")
        return

    # ランダムメニューのリスト
    menus = [
        "**16小節サイファー**",
        "**2小節サイファー**",
        "**バトル**"
    ]
    todays_menu = random.choice(menus)

    # 送信するメッセージ（IDを使ってメンションやリンク化）
    message = (
        f"<@&{MC_ROLE_ID}>\n"
        f"ラップの練習のお時間です！練習したいMCはぜひ <#{CIPHER_VC_ID}> に集まってください🔥\n"
        f"途中退室も途中入場も構いません！\n\n"
        f"練習メニュー、こんなのはいかが？\n"
        f"21:00~22:00　**8小節サイファー**\n"
        f"22:00~23:00　{todays_menu}"
    )
    
    await channel.send(message)

@bot.event
async def on_ready():
    print(f'ログインしました: {bot.user.name}')
    print("------")

# --- 4. スラッシュコマンド一覧 ---

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
    
    # member.display_name を使うことで、メンション通知は絶対に飛びません
    await interaction.response.send_message(f"{interaction.user.display_name}さんから{member.display_name}さんに **{amount} SP** 送金しました！")

@bot.tree.command(name="p-add", description="【管理者用】指定ユーザーのSPを増やします")
@app_commands.default_permissions(administrator=True) # 管理者のみ実行可能
@app_commands.describe(member="付与先のユーザー", amount="増やす金額")
@app_commands.guilds(ALLOWED_GUILD_ID)
async def p_add(interaction: discord.Interaction, member: discord.Member, amount: int):
    data = load_data()
    user_id = str(member.id)
    
    data[user_id] = data.get(user_id, 0) + amount
    save_data(data)
    
    await interaction.response.send_message(f"管理者操作: {member.display_name}さんに **{amount} SP** 付与しました。")

@bot.tree.command(name="p-remove", description="【管理者用】指定ユーザーのSPを減らします")
@app_commands.default_permissions(administrator=True) # 管理者のみ実行可能
@app_commands.describe(member="没収先のユーザー", amount="減らす金額")
@app_commands.guilds(ALLOWED_GUILD_ID)
async def p_remove(interaction: discord.Interaction, member: discord.Member, amount: int):
    data = load_data()
    user_id = str(member.id)
    
    current_balance = data.get(user_id, 0)
    data[user_id] = max(0, current_balance - amount)
    save_data(data)
    
    await interaction.response.send_message(f"管理者操作: {member.display_name}さんから **{amount} SP** 没収しました。")


# --- 5. 起動シーケンス ---
if __name__ == "__main__":
    if TOKEN is None:
        print("エラー: DISCORD_TOKEN が設定されていません。")
    else:
        print("Webサーバーを起動しています...")
        keep_alive()  
        time.sleep(2) 
        print("Discord Botを起動します...")
        bot.run(TOKEN)
