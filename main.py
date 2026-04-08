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

@bot.event
async def on_ready():
    print(f'ログインしました: {bot.user.name}')

@bot.command()
async def ping(ctx):
    await ctx.send('pong!')

# Webサーバーを起動
keep_alive()
# Botを起動
bot.run(TOKEN)