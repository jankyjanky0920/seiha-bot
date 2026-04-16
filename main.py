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
import yt_dlp

# --- 1. 環境変数の読み込みとDB設定 ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

MONGO_URL = os.getenv('MONGO_URL')
client = pymongo.MongoClient(MONGO_URL, tlsAllowInvalidCertificates=True)
db = client['discord_bot_db']
collection = db['user_balance']
daily_collection = db['daily_status']
word_collection = db['word_dictionary']

PLAYLIST_URL = "https://youtube.com/playlist?list=PL1vnrKZzRuE6pKv-aVWdjs7p0UPu0Hulz&si=dZWYzD6Ji9TpAo3O"
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

# --- 3. データベース・便利関数 ---
def get_playlist_urls(url):
    ydl_opts = {
        'flat_playlist': True,
        'extract_flat': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if 'entries' in info:
        return [entry['url'] for entry in info['entries']]
    return []

def get_user_balance(user_id):
    doc = collection.find_one({'user_id': str(user_id)})
    return doc['balance'] if doc else 0

def add_user_balance(user_id, amount):
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

# --- 5. UI設定 (ヘルプ用のページネーション) ---
class HelpPagination(discord.ui.View):
    def __init__(self, pages):
        super().__init__(timeout=120)
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

# --- 6. Bot本体の定義 ---
intents = discord.Intents.all()

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="-", intents=intents)

    async def setup_hook(self):
        guild = discord.Object(id=ALLOWED_GUILD_ID)
        
        # 修正版：①先にコピーする ②後からグローバルを消す ③両方同期する
        self.tree.copy_global_to(guild=guild)
        self.tree.clear_commands(guild=None)
        
        await self.tree.sync(guild=guild) 
        await self.tree.sync(guild=None)
        
        daily_cipher_task.start() 
        daily_ranking_task.start()

bot = MyBot()

# --- 7. サイファー監視・ランキングロジック ---
async def run_cipher_logic(end_datetime):
    channel = bot.get_channel(ANNOUNCE_CHANNEL_ID)
    dj_booth = bot.get_channel(DJ_BOOTH_CHANNEL_ID)
    vc_channel = bot.get_channel(CIPHER_VC_ID)
    
    if not vc_channel:
        print("エラー: VCチャンネルが見つかりません。")
        return

    required_minutes = 30.0
    print(f"【サイファー監視開始】終了予定: {end_datetime.strftime('%H:%M')}")

    if channel:
        menus = ["**16小節サイファー**", "**2小節サイファー**", "**バトル**"]
        message = (
            f"ラップの練習のお時間です！ <#{CIPHER_VC_ID}> に集まれ！🔥\n"
            f"練習メニュー案：{random.choice(menus)}"
        )
        await channel.send(message)

    voice_active_minutes.clear()

    try:
        for old_vc in bot.voice_clients:
            await old_vc.disconnect(force=True)
        
        vc_client = await vc_channel.connect(timeout=20.0, reconnect=True)
        check_interval = 20
        
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
                
                if user_id in rewarded_list: continue

                v_state = member.voice
                if v_state and not (v_state.self_mute or v_state.mute or v_state.suppress):
                    voice_active_minutes[user_id] = voice_active_minutes.get(user_id, 0.0) + (check_interval / 60.0)

                    if voice_active_minutes[user_id] >= required_minutes:
                        bonus = random.randint(50, 100)
                        add_user_balance(user_id, bonus)
                        save_rewarded_user(user_id)
                        
                        new_balance = get_user_balance(user_id)
                        if dj_booth:
                            await dj_booth.send(
                                f"{member.mention} さんのデイリーサイファー（30分）を確認！**{bonus} SP** を付与しました。現在の所持金: **{new_balance} SP**",
                                allowed_mentions=discord.AllowedMentions.none()
                            )

    except Exception as e: 
        print(f"Error in run_cipher_logic: {e}")
    finally:
        for current_vc_client in bot.voice_clients:
            await current_vc_client.disconnect(force=True)
        print("23:00になりました。監視を終了し、退室しました。")

async def get_sp_ranking():
    guild = bot.get_guild(ALLOWED_GUILD_ID)
    if not guild: return "サーバーが見つかりません。"
    mc_role = guild.get_role(MC_ROLE_ID)
    if not mc_role: return "MCロールが見つかりません。"

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

# --- 8. 定期タスク ---
@tasks.loop(time=START_TIME)
async def daily_cipher_task():
    await bot.wait_until_ready()
    now = datetime.datetime.now(JST)
    end_datetime = now.replace(hour=23, minute=0, second=0, microsecond=0)
    await run_cipher_logic(end_datetime)

@tasks.loop(time=RANKING_TIME)
async def daily_ranking_task():
    await bot.wait_until_ready()
    channel = bot.get_channel(DJ_BOOTH_CHANNEL_ID)
    if channel:
        ranking_msg = await get_sp_ranking()
        await channel.send(ranking_msg, allowed_mentions=discord.AllowedMentions.none())

# --- 9. 管理者用コマンド (プレフィックス) ---
@bot.command(name="bonus")
@commands.has_permissions(administrator=True)
async def manual_bonus(ctx, member: discord.Member):
    amount = random.randint(50, 100)
    add_user_balance(member.id, amount)
    new_balance = get_user_balance(member.id)
    await ctx.send(
        f"🎁 {member.mention} さんにボーナスを付与しました！\n付与額: **{amount} SP**\n現在の所持金: **{new_balance} SP**",
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

@bot.command(name="readingbeat")
@commands.has_permissions(administrator=True)
async def readingbeat(ctx):
    global cached_beats
    status_msg = await ctx.send("🔄 YouTube再生リストを読み込み中... しばらくお待ちください。")
    try:
        new_beats = await asyncio.to_thread(get_playlist_urls, PLAYLIST_URL)
        if new_beats:
            cached_beats = new_beats
            await status_msg.edit(content=f"✅ リロード完了！ {len(cached_beats)}件のビートを読み込みました。")
        else:
            await status_msg.edit(content="⚠️ リストが空、または取得に失敗しました。URLを確認してください。")
    except Exception as e:
        print(f"Reload Error: {e}")
        await status_msg.edit(content="❌ エラーが発生しました。ログを確認してください。")

# --- 10. スラッシュコマンド (一般) ---
cached_beats = []

@bot.event
async def on_ready():
    global cached_beats
    print(f'Logged in as {bot.user}')
    print("ビートリストを読み込み中...")
    cached_beats = await asyncio.to_thread(get_playlist_urls, PLAYLIST_URL)
    print(f"{len(cached_beats)}件のビートを読み込みました！")

@bot.tree.command(name="beat", description="再生リストからランダムにビートを選択します")
async def beat(interaction: discord.Interaction):
    if not cached_beats:
        await interaction.response.send_message("ビートリストがまだ読み込まれていないか、空です。", ephemeral=True)
        return
    selected_url = random.choice(cached_beats)
    await interaction.response.send_message(f"m!p {selected_url}")

@bot.tree.command(name="gamerule", description="バトルのBPMとTURNをランダムに決定します")
async def gamerule(interaction: discord.Interaction):
    bpm_options = ['LOW(BPM:~84)', 'MIDDLE(BPM:85~114)', 'FAST(BPM:115~)', 'ACAPPELLA']
    bpm_weights = [30, 30, 30, 10]
    selected_bpm = random.choices(bpm_options, weights=bpm_weights)[0]

    if selected_bpm == 'ACAPPELLA':
        turn_options = ['45s×2', '60s×2']
        turn_weights = [50, 50]
    else:
        turn_options = ['8×2', '8×3', '8×4', '16×2', '32×2', '45s×2', '60s×2']
        turn_weights = [10, 20, 30, 20, 4, 8, 8]

    selected_turn = random.choices(turn_options, weights=turn_weights)[0]
    result_message = f"BPM：**{selected_bpm}**　TURN：**{selected_turn}**"
    await interaction.response.send_message(result_message)

@bot.tree.command(name="vote", description="先攻と後攻の投票パネルを作成します")
@app_commands.describe(first="先攻の名前", second="後攻の名前")
async def vote(interaction: discord.Interaction, first: str, second: str):
    content = f":a: 先攻：{first}\n:regional_indicator_b: 後攻：{second}"
    await interaction.response.send_message(content)
    message = await interaction.original_response()
    try:
        await message.add_reaction("🅰️")
        await message.add_reaction("🇧")
    except Exception as e:
        print(f"リアクションの付与に失敗しました: {e}")

@bot.tree.command(name="help", description="コマンドの一覧をページごとに表示します")
async def help_command(interaction: discord.Interaction):
    pages = [
        (
            "・`/daily`\n今日のデイリーサイファーの進捗状況を確認できます\n\n"
            "・`/ranking`\n所持SPのランキングを表示します。\n\n"
            "・`/saifu`\n自身の所持SPを確認できます。\n\n"
            "・`/sent` [member] [amount]\n自身の所持SPから、[member]に[amount]SPを送金できます\n\n"
            "・`/word-add` [word]\nワードバトルに使用できる言葉に[word]を追加します。（固有名詞に弱いので積極的に追加していってください。）"
        ),
        (
            "・`/wordbattle` (count) (interval)\nワードバトルのお題を送信します。\n\n"
            "・`/vote` [first] [second]\n先攻後攻の投票を全自動で作成します。\n\n"
            "・`/gamerule`\nバトルのテンポとターンを自動でランダムに選択します。\n\n"
            "・`/beat`\n再生リストからランダムにビートを選択します。"
        ),
        (
            "`ここからは管理者用コマンドです。`\n"
            "・`-bonus` [member]\n指定したユーザーに50~100 SPをランダムに付与\n\n"
            "・`-join` / `-leave`\nボットの入退室\n\n"
            "・`-add` [member] [amount]\n所持SPを増減します（マイナスも可）。\n\n"
            "・`-bulk-remove` [words...]\n指定した単語をワードバトルから削除します。"
        ),
        (
            "`ここからは管理者用コマンドです。`（続き）\n"
            "・`-dislogin` [member]\nログイン記録を削除します。\n\n"
            "・`-readingbeat`\nYouTube再生リストをリロードします。"
        )
    ]
    view = HelpPagination(pages)
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
    
    collection.update_one({'user_id': str(interaction.user.id)}, {'$inc': {'balance': -amount}})
    collection.update_one({'user_id': str(member.id)}, {'$inc': {'balance': amount}}, upsert=True)
    await interaction.response.send_message(f"{member.display_name}さんに **{amount} SP** 送金しました！")

@bot.tree.command(name="word-add", description="ワードバトルの辞書に新しい単語を追加します")
@app_commands.describe(word="追加したい単語")
async def word_add(interaction: discord.Interaction, word: str):
    exists = word_collection.find_one({'word': word})
    if exists: return await interaction.response.send_message(f"「{word}」は既に辞書に登録されています！", ephemeral=True)
    word_collection.insert_one({'word': word, 'added_by': str(interaction.user.id)})
    await interaction.response.send_message(f"辞書に「{word}」を追加しました！🔥")

@bot.tree.command(name="wordbattle", description="辞書からランダムに単語を出します")
@app_commands.describe(count="出す単語の合計数", interval="何分ごとに次の単語を出すか")
async def word_battle(interaction: discord.Interaction, count: int = 1, interval: int = 0):
    if count <= 0: return await interaction.response.send_message("数は1以上にしてください。", ephemeral=True)
    total_words = word_collection.count_documents({})
    if total_words == 0: return await interaction.response.send_message("辞書が空っぽです！", ephemeral=True)

    actual_count = min(count, total_words)
    await interaction.response.send_message(f"🎤 ワードバトル開始！ (合計: {actual_count}個 / 間隔: {interval}分 / 登録数: {total_words})")
    
    random_words_cursor = word_collection.aggregate([{ "$sample": { "size": actual_count } }])
    words_list = [doc['word'] for doc in random_words_cursor]

    for i, word in enumerate(words_list):
        await interaction.channel.send(f"**【{i+1}個目】** 👉   **{word}**")
        if i < len(words_list) - 1 and interval > 0:
            await asyncio.sleep(interval * 60)

# --- 11. 起動 ---
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)
