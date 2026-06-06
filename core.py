# core.py
import os
import datetime
import pymongo
import yt_dlp
import discord
from dotenv import load_dotenv
import messages as msg

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
MONGO_URL = os.getenv('MONGO_URL')

# --- データベース接続 ---
client = pymongo.MongoClient(MONGO_URL, tlsAllowInvalidCertificates=True)
db = client['discord_bot_db']
collection = db['user_balance']
daily_collection = db['daily_status']
word_collection = db['word_dictionary']
rank_collection = db['user_ranks']
task_collection = db['tasks']
notice_collection = db['pending_notices']
delete_collection = db['auto_delete_messages']

# --- 各種定数設定 ---
PLAYLIST_URL = "https://youtube.com/playlist?list=PL1vnrKZzRuE6pKv-aVWdjs7p0UPu0Hulz&si=dZWYzD6Ji9TpAo3O"
MC_ROLE_ID = 1480235861244383262
ALLOWED_GUILD_ID = 1480208337533534379
ANNOUNCE_CHANNEL_ID = 1492856858078220542
CIPHER_VC_ID = 1480212977650110828
DJ_BOOTH_CHANNEL_ID = 1492856858078220542
RANKING_CHANNEL_ID = 1511865035578933278 
B_RANK_GUIDE_CHANNEL_ID = 1512171832512352396

B_NAMES = ["No_Battle", "Choke_Prone", "Off_the_Dome", "Cypher_Freak", "Raw_Vibe", "Hard_Core", "Mic_Killing", "The_Freestyle"]
T_NAMES = ["No_Track", "Bedroom_Artist", "Sound_Creator", "Lyricist", "New_Wave", "Trendsetter", "Architect", "G.O.A.T."]

# --- 時間・タイムゾーン ---
JST = datetime.timezone(datetime.timedelta(hours=9))
START_TIME = datetime.time(hour=20, minute=50, tzinfo=JST) 
MIDNIGHT_TIME = datetime.time(hour=0, minute=0, tzinfo=JST) 
NOTICE_TIME = datetime.time(hour=9, minute=0, tzinfo=JST)

# --- グローバル状態の共有 ---
voice_active_minutes = {}
cached_beats = []

# --- 便利関数マスタ ---
def calculate_rank_level(points):
    if points <= 0: return 0
    elif points <= 100: return 1
    elif points <= 200: return 2
    elif points <= 400: return 3
    elif points <= 600: return 4
    elif points <= 800: return 5
    elif points <= 1000: return 6
    else: return 7

def get_playlist_urls(url):
    ydl_opts = {'flat_playlist': True, 'extract_flat': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if 'entries' in info: return [entry['url'] for entry in info['entries']]
    return []

def get_user_balance(user_id):
    doc = collection.find_one({'user_id': str(user_id)})
    return doc['balance'] if doc else 0

def add_user_balance(user_id, amount):
    collection.update_one({'user_id': str(user_id)}, {'$inc': {'balance': amount}}, upsert=True)

def set_user_balance(user_id, amount):
    collection.update_one({'user_id': str(user_id)}, {'$set': {'balance': amount}}, upsert=True)

def get_rewarded_users():
    today = datetime.datetime.now(JST).strftime('%Y-%m-%d')
    doc = daily_collection.find_one({'date': today})
    return set(doc.get('users', [])) if doc else set()

def save_rewarded_user(user_id):
    today = datetime.datetime.now(JST).strftime('%Y-%m-%d')
    daily_collection.update_one({'date': today}, {'$addToSet': {'users': str(user_id)}}, upsert=True)

def remove_rewarded_user(user_id):
    today = datetime.datetime.now(JST).strftime('%Y-%m-%d')
    daily_collection.update_one({'date': today}, {'$pull': {'users': str(user_id)}})

def register_deletion(message_id, channel_id, hours=24):
    delete_at = datetime.datetime.now(JST) + datetime.timedelta(hours=hours)
    delete_collection.insert_one({"message_id": message_id, "channel_id": channel_id, "delete_at": delete_at})

async def get_sp_ranking(bot):
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

    lines = []
    for i, (name, sp) in enumerate(ranking_data, 1):
        medal = "🥇 1位" if i == 1 else "🥈 2位" if i == 2 else "🥉 3位" if i == 3 else f"{i}位"
        lines.append(f"{medal}：{name} (**{sp} SP**)")

    lines.reverse()
    return msg.SP_RANKING_HEADER.format(ranking_lines="\n".join(lines))

async def update_ranking_message(bot):
    channel = bot.get_channel(RANKING_CHANNEL_ID)
    if not channel: return
    ranking_text = await get_sp_ranking(bot)
    async for message in channel.history(limit=10):
        if message.author == bot.user:
            await message.edit(content=ranking_text, allowed_mentions=discord.AllowedMentions.none())
            return
    await channel.send(content=ranking_text, allowed_mentions=discord.AllowedMentions.none())

async def update_b_rank_guide_message(bot):
    channel = bot.get_channel(B_RANK_GUIDE_CHANNEL_ID)
    if not channel: return
    guide_text = msg.B_RANK_GUIDE
    async for message in channel.history(limit=10):
        if message.author == bot.user:
            await message.edit(content=guide_text, allowed_mentions=discord.AllowedMentions.none())
            return
    await channel.send(content=guide_text, allowed_mentions=discord.AllowedMentions.none())
