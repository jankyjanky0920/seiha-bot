import discord
from discord.ext import commands
import os
import json
from dotenv import load_dotenv
from flask import Flask
from threading import Thread

# --- Webサーバーの設定 ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run_web():
    # Renderはデフォルトで10000番ポートを期待することが多いため、
    # 環境変数から取得するか、直接10000を指定します。
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True # プログラム終了時に一緒に終了するように設定
    t.start()

# --- 通貨管理用の関数 ---
def load_data():
    try:
        if not os.path.exists('data.json'):
            with open('data.json', 'w') as f:
                json.dump({}, f)
            return {}
        with open('data.json', 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"JSON読み込みエラー: {e}")
        return {}

def save_data(data):
    try:
        with open('data.json', 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"JSON保存エラー: {e}")

# --- Botの設定 ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix='$', intents=intents)

@bot.event
async def on_ready():
    print(f'ログインしました: {bot.user.name}')
    print("------")

@bot.command()
async def ping(ctx):
    await ctx.send('pong!')

@bot.command()
async def money(ctx):
    data = load_data()
    user_id = str(ctx.author.id)
    balance = data.get(user_id, 0)
    await ctx.send(f"{ctx.author.display_name}さんの所持金は {balance} 通貨です。")

@bot.command()
async def earn(ctx):
    data = load_data()
    user_id = str(ctx.author.id)
    data[user_id] = data.get(user_id, 0) + 100
    save_data(data)
    await ctx.send(f"100 通貨を手に入れた！ 現在の残高: {data[user_id]}")

# 起動シーケンス
if __name__ == "__main__":
    if TOKEN is None:
        print("エラー: DISCORD_TOKEN が設定されていません。")
    else:
        keep_alive()  # Webサーバー起動
        print("Webサーバーを起動しました。Botを起動します...")
        bot.run(TOKEN) # Bot起動
