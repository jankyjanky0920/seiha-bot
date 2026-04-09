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

# --- 2. 時間設定と監視用変数 ---
JST = datetime.timezone(datetime.timedelta(hours=9))
# ここは本番で動かしたい時間に設定して大丈夫です！
announce_time = datetime.time(hour=21, minute=0, tzinfo=JST) 
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
        guild = discord.Object(id=ALLOWED_GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild) 
        
        print(f"{ALLOWED_GUILD_ID} のスラッシュコマンドを同期しました！")
        daily_cipher_announce.start()

bot = MyBot()

# --- ★ サイファー監視のメイン処理（テストからも呼び出せるように独立させました） ---
async def run_cipher_logic(end_time_obj):
    # ① メッセージ送信
    channel = bot.get_channel(ANNOUNCE_CHANNEL_ID)
    if channel:
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

    # ② VC入室
    vc_channel = bot.get_channel(CIPHER_VC_ID)
    if not vc_channel:
        print("エラー: VCチャンネルが見つかりません。")
        return

    # 変数をリセット
    rewarded_users.clear()
    voice_active_minutes.clear()
    
    try:
        # 既存の接続を掃除
        for old_vc in bot.voice_clients:
            await old_vc.disconnect(force=True)
        
        print(f"{vc_channel.name} への接続を開始します...")
        vc = await vc_channel.connect(timeout=20.0, reconnect=True)
        print(f"VC接続成功。監視を開始します。（終了予定: {end_time_obj}）")

        # ③ 監視ループ (10秒ごとにチェック)
        check_interval = 10  
        while True:
            now = datetime.datetime.now(JST).time()
            
            if now >= end_time_obj:
                print("終了時間になったため、ループを終了します。")
                break
            
            await asyncio.sleep(check_interval)

            data = load_data()
            updated = False

            # 最新のメンバー情報を取得
            current_vc = bot.get_channel(CIPHER_VC_ID)
            if not current_vc: continue

            for member in current_vc.members:
                if member.bot: continue

                v_state = member.voice
                # マイクON判定
                if v_state and not (v_state.self_mute or v_state.mute or v_state.suppress):
                    user_id = str(member.id)
                    # 10秒加算 (1分 = 1.0)
                    voice_active_minutes[user_id] = voice_active_minutes.get(user_id, 0.0) + (check_interval / 60.0)

                    # 小数の計算誤差を考慮して 0.99 以上で判定
                    if voice_active_minutes[user_id] >= 0.99 and user_id not in rewarded_users:
                        bonus = random.randint(50, 100)
                        data[user_id] = data.get(user_id, 0) + bonus
                        rewarded_users.add(user_id)
                        updated = True
                        print(f"【ボーナス付与】{member.display_name} に {bonus} SP")
                        
                        try:
                            await member.send(f"🎤 サイファーお疲れ様です！練習を確認したので **{bonus} SP** を付与しました！")
                        except Exception as e:
                            pass # DMが送れない場合はスキップ

            if updated:
                save_data(data)

    except Exception as e:
        print(f"ループ中にエラーが発生しました: {e}")

    finally:
        # ④ 退室 (何があっても必ず実行)
        print("退室処理を開始します...")
        for current_vc in bot.voice_clients:
            if current_vc.channel.id == CIPHER_VC_ID:
                await current_vc.disconnect(force=True)
                print("正常に退室しました。")

# --- 定期実行タスク（本番用） ---
@tasks.loop(time=announce_time)
async def daily_cipher_announce():
    await bot.wait_until_ready()
    # メインのロジックを呼び出す
    await run_cipher_logic(exit_time_info)

@bot.event
async def on_ready():
    print(f'ログインしました: {bot.user.name}')
    print("------")

# --- 6. スラッシュコマンド一覧 ---

@bot.tree.command(name="test-run", description="【管理者用テスト】強制的にサイファー監視を5分間スタートします")
@app_commands.default_permissions(administrator=True)
@app_commands.guilds(ALLOWED_GUILD_ID)
async def test_run(interaction: discord.Interaction):
    await interaction.response.send_message("テスト用のサイファー監視を開始します！（5分間で自動退室します）", ephemeral=True)
    
    # 現在時刻から5分後の時間を計算して終了時間にする
    now_dt = datetime.datetime.now(JST)
    test_end_time = (now_dt + datetime.timedelta(minutes=5)).time()
    
    # バックグラウンドで監視ロジックを動かす
    bot.loop.create_task(run_cipher_logic(test_end_time))

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
