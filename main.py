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
    # Renderから指定されるポート番号を取得、なければ10000を使う
    port = int(os.environ.get("PORT", 10000))
    # host='0.0.0.0' を指定することで、外部からのアクセスを許可する
    # threaded=True を追加して、リクエストを並列で処理できるようにする
    app.run(host='0.0.0.0', port=port, threaded=True)

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

# --- コマンド一覧 ---

# $saifu: 自分の所持金を表示
@bot.command(name="saifu")
async def saifu(ctx):
    data = load_data()
    user_id = str(ctx.author.id)
    balance = data.get(user_id, 0)
    await ctx.send(f"{ctx.author.display_name}さんの所持金は **{balance} SP** です。")

# $sent: 他のユーザーに送金
@bot.command(name="sent")
async def sent(ctx, member: discord.Member, amount: int):
    if amount <= 0:
        await ctx.send("1 SP以上を指定してください。")
        return

    data = load_data()
    sender_id = str(ctx.author.id)
    receiver_id = str(member.id)

    # 送り主の残高確認
    sender_balance = data.get(sender_id, 0)
    if sender_balance < amount:
        await ctx.send(f"SPが足りません！（現在の残高: {sender_balance} SP）")
        return

    # 送金処理
    data[sender_id] = sender_balance - amount
    data[receiver_id] = data.get(receiver_id, 0) + amount
    
    save_data(data)
    await ctx.send(f"{ctx.author.display_name}さんから{member.display_name}さんに **{amount} SP** 送金しました！")

# $p-add: 管理者が指定ユーザーのSPを増やす
@bot.command(name="p-add")
@commands.has_permissions(administrator=True) # 管理者権限チェック
async def p_add(ctx, member: discord.Member, amount: int):
    data = load_data()
    user_id = str(member.id)
    
    data[user_id] = data.get(user_id, 0) + amount
    
    save_data(data)
    await ctx.send(f"管理者操作: {member.display_name}さんに **{amount} SP** 付与しました。")

# $p-remove: 管理者が指定ユーザーのSPを減らす
@bot.command(name="p-remove")
@commands.has_permissions(administrator=True) # 管理者権限チェック
async def p_remove(ctx, member: discord.Member, amount: int):
    data = load_data()
    user_id = str(member.id)
    
    current_balance = data.get(user_id, 0)
    data[user_id] = max(0, current_balance - amount) # 0未満にはならないように設定
    
    save_data(data)
    await ctx.send(f"管理者操作: {member.display_name}さんから **{amount} SP** 没収しました。")

# エラーハンドリング（管理者権限がない場合）
@p_add.error
@p_remove.error
async def admin_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("このコマンドを実行する権限（管理者権限）がありません。")

# 起動シーケンス
# --- 修正後の起動シーケンス ---
if __name__ == "__main__":
    if TOKEN is None:
        print("エラー: DISCORD_TOKEN が設定されていません。")
    else:
        # 1. まずWebサーバーをスレッドで起動
        print("Webサーバーを起動しています...")
        keep_alive()  
        
        # 2. 少しだけ待機（Webサーバーを確実に安定させるため）
        import time
        time.sleep(2) 
        
        # 3. 最後にBotを起動
        print("Discord Botを起動します...")
        bot.run(TOKEN)
