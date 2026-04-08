import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
from flask import Flask
from threading import Thread

# --- Webサーバーの設定 ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is moving now!"

def run_web():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_web)
    t.start()
# -----------------------

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix='$', intents=intents)

# --- 通貨管理用の関数 ---

# JSONからデータを読み込む
def load_data():
    if not os.path.exists('data.json'):
        return {}
    with open('data.json', 'r') as f:
        return json.load(f)

# JSONにデータを保存する
def save_data(data):
    with open('data.json', 'w') as f:
        json.dump(data, f, indent=4)

# --- コマンドの実装 ---

@bot.command()
async def money(ctx):
    """現在の所持金を確認するコマンド"""
    data = load_data()
    user_id = str(ctx.author.id) # DiscordのIDは文字列として扱うのが定石
    
    # ユーザーが登録されていない場合は0円にする
    balance = data.get(user_id, 0)
    
    await ctx.send(f"{ctx.author.display_name}さんの所持金は {balance} 通貨です。")

@bot.command()
async def pay(ctx, member: discord.Member, amount: int):
    """お金を他の人に送金するコマンド ($pay @ユーザー 100)"""
    if amount <= 0:
        await ctx.send("1以上の金額を指定してください。")
        return

    data = load_data()
    sender_id = str(ctx.author.id)
    receiver_id = str(member.id)

    # 送金者の残高確認
    sender_balance = data.get(sender_id, 0)
    if sender_balance < amount:
        await ctx.send("お金が足りません！")
        return

    # 計算（競プロ的な値の更新）
    data[sender_id] = sender_balance - amount
    data[receiver_id] = data.get(receiver_id, 0) + amount

    save_data(data)
    await ctx.send(f"{member.display_name}さんに {amount} 通貨を送金しました！")

@bot.command()
async def earn(ctx):
    """【テスト用】お金を増やすコマンド"""
    data = load_data()
    user_id = str(ctx.author.id)
    
    data[user_id] = data.get(user_id, 0) + 100
    
    save_data(data)
    await ctx.send(f"100 通貨を手に入れた！現在の残高: {data[user_id]}")

# 最後に起動
# keep_alive()
bot.run(TOKEN)
