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
class HelpPagination(discord.ui.View):
    def __init__(self, pages):
        super().__init__(timeout=120)  # 操作がないと2分で無効化
        self.pages = pages
        self.current_page = 0

    async def update_message(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content=self.pages[self.current_page], view=self)

    @discord.ui.button(label="◀ 前のページ", style=discord.ButtonStyle.gray)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = (self.current_page - 1) % len(self.pages)
        await self.update_message(interaction)

    @discord.ui.button(label="次のページ ▶", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = (self.current_page + 1) % len(self.pages)
        await self.update_message(interaction)
        
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
                            await dj_booth.send(
                                f"{member.mention} さんのデイリーサイファー（30分）を確認！**{bonus} SP** を付与しました。現在の所持金: **{new_balance} SP**",
                                allowed_mentions=discord.AllowedMentions.none()
                            )

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

@bot.command(name="bonus")
@commands.has_permissions(administrator=True)
async def manual_bonus(ctx, member: discord.Member):
    """【管理者】指定したユーザーに50~100 SPをランダムに付与"""
    amount = random.randint(50, 100)
    collection.update_one(
        {'user_id': str(member.id)},
        {'$inc': {'balance': amount}},
        upsert=True
    )
    doc = collection.find_one({'user_id': str(member.id)})
    new_balance = doc.get('balance', 0)
    
    await ctx.send(
        f"🎁 {member.mention} さんにボーナスを付与しました！\n"
        f"付与額: **{amount} SP**\n"
        f"現在の所持金: **{new_balance} SP**",
        allowed_mentions=discord.AllowedMentions.none()
    )

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

@bot.command(name="bulk-remove")
@commands.has_permissions(administrator=True)
async def bulk_remove(ctx, *, words_str: str):
    words_to_delete = words_str.split()
    result = word_collection.delete_many({'word': {'$in': words_to_delete}})
    await ctx.send(f"{result.deleted_count} 個の単語を削除しました。")

@bot.command(name="dislogin")
@commands.has_permissions(administrator=True)
async def dislogin(ctx, member: discord.Member):
    remove_rewarded_user(member.id)
    await ctx.send(f"{member.display_name}のデイリー記録を削除しました。")

# --- 8. スラッシュコマンド (一般) ---

@bot.tree.command(name="vote", description="先攻と後攻の投票パネルを作成します")
@app_commands.describe(first="先攻の名前", second="後攻の名前")
async def vote(interaction: discord.Interaction, first: str, second: str):
    # 投票用メッセージの組み立て
    content = (
        f":a: 先攻：{first}\n"
        f":regional_indicator_b: 後攻：{second}"
    )
    
    # メッセージを送信
    await interaction.response.send_message(content)
    
    # 送信したメッセージ（InteractionResponse）を取得してリアクションを追加
    message = await interaction.original_response()
    
    # 順次リアクションを付与
    try:
        await message.add_reaction("🅰️")
        await message.add_reaction("🇧")
    except Exception as e:
        print(f"リアクションの付与に失敗しました: {e}")

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

    msg = "🏆 **所持SPランキング** 🏆\n"
    for i, (name, sp) in enumerate(ranking_data[:10], 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}位"
        msg += f"{medal} {name}: **{sp} SP**\n"
    return msg

@bot.tree.command(name="help", description="コマンドの一覧をページごとに表示します")
async def help_command(interaction: discord.Interaction):
    # 原文を「5コマンドずつ」かつ「一般と管理者を分ける」ルールで分割
    pages = [
        # 1ページ目：一般コマンド (1~5個目)
        (
            "・`/daily`\n"
            "今日のデイリーサイファーの進捗状況を確認できます\n\n"
            "・`/ranking`\n"
            "所持SPのランキングを表示します。\n\n"
            "・`/saifu`\n"
            "自身の所持SPを確認できます。\n\n"
            "・`/sent` [member] [amount]\n"
            "自身の所持SPから、[member]に[amount]SPを送金できます\n\n"
            "・`/word-add` [word]\n"
            "ワードバトルに使用できる言葉に[word]を追加します。（ワードバトルは固有名詞に弱いので、積極的に追加していってください。）"
        ),
        # 2ページ目：一般コマンド (残り)
        (
            "・`/wordbattle` (count) (interval)\n"
            "ワードバトルのお題を送信します。任意で、(count)個の単語を送信します。また、単語を１つづつ(interval)分ごとに送信します。\n\n"
            "・`/vote` [first] [second]\n"
            "先攻の[first]、後攻の[second]の投票を全自動で作成します。"
        ),
        # 3ページ目：管理者用コマンド (1~5個目)
        (
            "`ここからは管理者用コマンドです。`\n"
            "・`-bonus`\n"
            "指定したユーザーに50~100 SPをランダムに付与\n\n"
            "・`-join`\n"
            "声覇マネジメントがバトル・サイファーに入ります。\n\n"
            "・`-leave`\n"
            "声覇マネジメントがバトル・サイファーから抜けます。\n\n"
            "・`-add` [member] [amount]\n"
            "[member]の所持SPを[amount]増やします。[amount]にマイナスを指定すれば没収できます。\n\n"
            "・`-bulk-remove` [word1] [word2] [word3]...\n"
            "指定した[word]すべてをワードバトルから削除します。"
        ),
        # 4ページ目：管理者用コマンド (残り)
        (
            "`ここからは管理者用コマンドです。`（続き）\n"
            "・`-dislogin` [member]\n"
            "[member]のログイン記録を削除します。"
        )
    ]

    view = HelpPagination(pages)
    # ephemeral=True により、実行した本人以外には見えません
    await interaction.response.send_message(pages[0], view=view, ephemeral=True)
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

@bot.tree.command(name="sent", description="SPを送金")
async def sent(interaction: discord.Interaction, member: discord.Member, amount: int):
    if amount <= 0: return await interaction.response.send_message("1以上を指定してください。", ephemeral=True)
    
    sender_balance = get_user_balance(interaction.user.id)
    if sender_balance < amount: 
        return await interaction.response.send_message("SPが不足しています。", ephemeral=True)
    
    # DB直接更新: 送信者から減算し、受信者に加算
    collection.update_one({'user_id': str(interaction.user.id)}, {'$inc': {'balance': -amount}})
    collection.update_one({'user_id': str(member.id)}, {'$inc': {'balance': amount}}, upsert=True)
    
    await interaction.response.send_message(f"{member.display_name}さんに **{amount} SP** 送金しました！")

@bot.tree.command(name="word-add", description="ワードバトルの辞書に新しい単語を追加します")
@app_commands.describe(word="追加したい単語")
async def word_add(interaction: discord.Interaction, word: str):
    exists = word_collection.find_one({'word': word})
    if exists:
        return await interaction.response.send_message(f"「{word}」は既に辞書に登録されています！", ephemeral=True)
    
    word_collection.insert_one({'word': word, 'added_by': str(interaction.user.id)})
    await interaction.response.send_message(f"辞書に「{word}」を追加しました！🔥")

@bot.tree.command(name="wordbattle", description="辞書からランダムに単語を出します")
@app_commands.describe(
    count="出す単語の合計数（指定なしなら1個）",
    interval="何分ごとに次の単語を出すか（分単位）"
)
async def word_battle(interaction: discord.Interaction, count: int = 1, interval: int = 0):
    if count <= 0:
        return await interaction.response.send_message("数は1以上にしてください。", ephemeral=True)

    total_words = word_collection.count_documents({})
    if total_words == 0:
        return await interaction.response.send_message("辞書が空っぽです！ `/word-add` で単語を追加してください。", ephemeral=True)

    actual_count = min(count, total_words)
    await interaction.response.send_message(f"🎤 ワードバトル開始！ (合計: {actual_count}個 / 間隔: {interval}分 / 登録数: {total_words})")
    
    # DBからランダム取得
    random_words_cursor = word_collection.aggregate([{ "$sample": { "size": actual_count } }])
    words_list = [doc['word'] for doc in random_words_cursor]

    for i, word in enumerate(words_list):
        msg = f"**【{i+1}個目】** 👉   **{word}**"
        await interaction.channel.send(msg)
        
        if i < len(words_list) - 1 and interval > 0:
            await asyncio.sleep(interval * 60)

# --- 9. 起動 ---
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)
