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
import re

# ★新しく作成したメッセージ管理ファイルを読み込む
import messages as msg

# --- 1. 環境変数の読み込みとDB設定 ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

MONGO_URL = os.getenv('MONGO_URL')
client = pymongo.MongoClient(MONGO_URL, tlsAllowInvalidCertificates=True)
db = client['discord_bot_db']
collection = db['user_balance']
daily_collection = db['daily_status']
word_collection = db['word_dictionary']
rank_collection = db['user_ranks']

PLAYLIST_URL = "https://youtube.com/playlist?list=PL1vnrKZzRuE6pKv-aVWdjs7p0UPu0Hulz&si=dZWYzD6Ji9TpAo3O"
MC_ROLE_ID = 1480235861244383262
ALLOWED_GUILD_ID = 1480208337533534379
ANNOUNCE_CHANNEL_ID = 1492856858078220542
CIPHER_VC_ID = 1480212977650110828
DJ_BOOTH_CHANNEL_ID = 1492856858078220542
RANKING_CHANNEL_ID = 1511865035578933278 
B_RANK_GUIDE_CHANNEL_ID = 1512171832512352396

task_collection = db['tasks']
notice_collection = db['pending_notices']
delete_collection = db['auto_delete_messages']

# --- ランク名称・基準定義 ---
B_NAMES = [
    "No_Battle", "Choke_Prone", "Off_the_Dome", "Cypher_Freak",
    "Raw_Vibe", "Hard_Core", "Mic_Killing", "The_Freestyle"
]
T_NAMES = [
    "No_Track", "Bedroom_Artist", "Sound_Creator", "Lyricist",
    "New_Wave", "Trendsetter", "Architect", "G.O.A.T."
]

def calculate_rank_level(points):
    if points <= 0: return 0
    elif points <= 100: return 1
    elif points <= 200: return 2
    elif points <= 400: return 3
    elif points <= 600: return 4
    elif points <= 800: return 5
    elif points <= 1000: return 6
    else: return 7

# --- 2. 時間設定 ---
JST = datetime.timezone(datetime.timedelta(hours=9))
START_TIME = datetime.time(hour=20, minute=50, tzinfo=JST) 
MIDNIGHT_TIME = datetime.time(hour=0, minute=0, tzinfo=JST) 
NOTICE_TIME = datetime.time(hour=9, minute=0, tzinfo=JST)

voice_active_minutes = {}

# --- 3. データベース・便利関数 ---
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

    lines = []
    for i, (name, sp) in enumerate(ranking_data, 1):
        medal = "🥇 1位" if i == 1 else "🥈 2位" if i == 2 else "🥉 3位" if i == 3 else f"{i}位"
        lines.append(f"{medal}：{name} (**{sp} SP**)")

    lines.reverse()
    
    # ★ 外部ファイルのテキストフォーマットを利用
    return msg.SP_RANKING_HEADER.format(ranking_lines="\n".join(lines))

async def update_ranking_message():
    channel = bot.get_channel(RANKING_CHANNEL_ID)
    if not channel: return
    ranking_text = await get_sp_ranking()
    async for message in channel.history(limit=10):
        if message.author == bot.user:
            await message.edit(content=ranking_text, allowed_mentions=discord.AllowedMentions.none())
            return
    await channel.send(content=ranking_text, allowed_mentions=discord.AllowedMentions.none())

# --- 3.5 B軸ランクポイント基準テキスト更新 ---
async def update_b_rank_guide_message():
    channel = bot.get_channel(B_RANK_GUIDE_CHANNEL_ID)
    if not channel: return
    
    # ★ 外部ファイルのB軸長文テキストを呼び出し
    guide_text = msg.B_RANK_GUIDE
    
    async for message in channel.history(limit=10):
        if message.author == bot.user:
            await message.edit(content=guide_text, allowed_mentions=discord.AllowedMentions.none())
            return
    await channel.send(content=guide_text, allowed_mentions=discord.AllowedMentions.none())


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


# --- 5. 管理者専用コマンドグループ (タスク) ---
class TaskGroup(app_commands.Group, name="z_task", description="【管理者専用】タスクの管理を行います"):
    
    @app_commands.command(name="add", description=msg.DESC_Z_TASK_ADD)
    @app_commands.describe(task_name="タスク名", member="対象ユーザー", role="対象ロール（どちらか必須）", deadline="期限(MM/DDなど)", description="説明文")
    @app_commands.default_permissions(administrator=True)
    async def task_add(self, interaction: discord.Interaction, task_name: str, member: discord.Member = None, role: discord.Role = None, deadline: str = None, description: str = ""):
        target_ids = set()
        if member: target_ids.add(str(member.id))
        if role:
            for m in role.members: target_ids.add(str(m.id))
        if not target_ids: return await interaction.response.send_message("❌ エラー: 対象を指定してください。", ephemeral=True)
            
        doc = task_collection.find_one({"task_name": task_name})
        if doc:
            task_collection.update_one({"task_name": task_name}, {"$addToSet": {"assignees": {"$each": list(target_ids)}}})
            updates = {}
            if description: updates["description"] = description
            if deadline: updates["deadline"] = deadline
            if updates: task_collection.update_one({"task_name": task_name}, {"$set": updates})
        else:
            task_collection.insert_one({"task_name": task_name, "description": description, "deadline": deadline, "assignees": list(target_ids)})
            
        dl_text = f" (期限: {deadline})" if deadline else ""
        await interaction.response.send_message(f"タスク `{task_name}`{dl_text} を追加/更新しました！ 対象: {len(target_ids)}人")

    @app_commands.command(name="edit", description=msg.DESC_Z_TASK_EDIT)
    @app_commands.describe(task_name="対象のタスク名", new_desc="新しい説明文")
    @app_commands.default_permissions(administrator=True)
    async def task_edit(self, interaction: discord.Interaction, task_name: str, new_desc: str):
        result = task_collection.update_one({"task_name": task_name}, {"$set": {"description": new_desc}})
        if result.matched_count: await interaction.response.send_message(f"タスク `{task_name}` の説明を更新しました。")
        else: await interaction.response.send_message(f"タスクが見つかりません。", ephemeral=True)

    @app_commands.command(name="delete", description=msg.DESC_Z_TASK_DELETE)
    @app_commands.describe(task_name="タスク名", member="対象ユーザー", role="対象ロール")
    @app_commands.default_permissions(administrator=True)
    async def task_delete(self, interaction: discord.Interaction, task_name: str = None, member: discord.Member = None, role: discord.Role = None):
        target_ids = set()
        if member: target_ids.add(str(member.id))
        if role:
            for m in role.members: target_ids.add(str(m.id))
            
        if task_name and target_ids:
            task_collection.update_one({"task_name": task_name}, {"$pull": {"assignees": {"$in": list(target_ids)}}})
            await interaction.response.send_message(f"指定されたユーザーからタスク `{task_name}` を外しました。")
        elif task_name and not target_ids:
            task_collection.delete_one({"task_name": task_name})
            await interaction.response.send_message(f"タスク `{task_name}` を完全に削除しました。")
        elif not task_name and target_ids:
            task_collection.update_many({}, {"$pull": {"assignees": {"$in": list(target_ids)}}})
            await interaction.response.send_message(f"指定されたユーザーが抱えているすべてのタスクを削除しました。")
        else: 
            return await interaction.response.send_message("タスク名か対象メンションを指定してください。", ephemeral=True)
        task_collection.delete_many({"assignees": {"$size": 0}})

    @app_commands.command(name="done", description=msg.DESC_Z_TASK_DONE)
    @app_commands.describe(task_name="タスク名", member="対象ユーザー", role="対象ロール", reward="報酬額(SP)", channel="通知先")
    @app_commands.default_permissions(administrator=True)
    async def task_done(self, interaction: discord.Interaction, task_name: str, member: discord.Member = None, role: discord.Role = None, reward: int = 0, channel: discord.TextChannel = None):
        target_ids = set()
        if member: target_ids.add(str(member.id))
        if role:
            for m in role.members: target_ids.add(str(m.id))
        if not target_ids: return await interaction.response.send_message("❌ エラー: 対象を指定してください。", ephemeral=True)
            
        notify_channel = channel if channel else interaction.channel

        task_collection.update_one({"task_name": task_name}, {"$pull": {"assignees": {"$in": list(target_ids)}}})
        task_collection.delete_many({"assignees": {"$size": 0}}) 

        if reward > 0:
            for uid in target_ids: add_user_balance(int(uid), reward)
            await update_ranking_message()

        now = datetime.datetime.now(JST)
        is_active_time = 8 <= now.hour < 22
        target_mentions = " ".join([f"<@{uid}>" for uid in target_ids])
        reward_text = f"\n💰 **{reward} SP** の報酬が付与されました！" if reward > 0 else ""
        text = f"✅ **タスク完了**\n{target_mentions} さん、タスク `{task_name}` 完了を確認しました{reward_text}"

        if is_active_time:
            sent_msg = await notify_channel.send(text)
            register_deletion(sent_msg.id, sent_msg.channel.id)
            await interaction.response.send_message(f"タスク完了処理を実施し通知しました。")
        else:
            notice_collection.insert_one({"channel_id": notify_channel.id, "message": text})
            await interaction.response.send_message(f"タスク完了処理を実施しました。時間外のため明朝9時に通知します。")

    @app_commands.command(name="notice", description=msg.DESC_Z_TASK_NOTICE)
    @app_commands.describe(task_name="タスク名", member="対象ユーザー", channel="通知先チャンネル")
    @app_commands.default_permissions(administrator=True)
    async def task_notice(self, interaction: discord.Interaction, task_name: str = None, member: discord.Member = None, channel: discord.TextChannel = None):
        notify_channel = channel if channel else interaction.channel
        messages = []
        if task_name:
            task = task_collection.find_one({"task_name": task_name})
            if task and task['assignees']:
                mentions = " ".join([f"<@{uid}>" for uid in task['assignees']])
                dl = f" (期限: {task['deadline']})" if task.get('deadline') else ""
                messages.append(f"🔔 **リマインド**: `{task_name}`{dl}\n{mentions}\n> {task.get('description', '')}")
        elif member:
            tasks_list = list(task_collection.find({"assignees": str(member.id)}))
            if tasks_list:
                task_lines = [f"・`{t['task_name']}`" + (f"(期限:{t['deadline']})" if t.get('deadline') else "") for t in tasks_list]
                messages.append(f"🔔 <@{member.id}> さんの抱えているタスク:\n" + "\n".join(task_lines))
                
        if not messages: return await interaction.response.send_message("対象やタスクが見つかりませんでした。", ephemeral=True)
        for m in messages: 
            sent_msg = await notify_channel.send(m)
            register_deletion(sent_msg.id, sent_msg.channel.id)
        await interaction.response.send_message("通知を送信しました。")

    @app_commands.command(name="list", description=msg.DESC_Z_TASK_LIST)
    @app_commands.default_permissions(administrator=True)
    async def task_list(self, interaction: discord.Interaction, task_name: str = None, member: discord.Member = None):
        embed = discord.Embed(title="📋 タスク一覧", color=discord.Color.blue())
        if task_name:
            task = task_collection.find_one({"task_name": task_name})
            if not task: return await interaction.response.send_message("タスクが見つかりません。", ephemeral=True)
            mentions = " ".join([f"<@{uid}>" for uid in task['assignees']])
            dl = f" (期限: {task['deadline']})" if task.get('deadline') else ""
            embed.add_field(name=f"{task['task_name']}{dl}", value=f"担当: {mentions}\n説明: {task.get('description', 'なし')}")
        elif member:
            uid = str(member.id)
            tasks_list = list(task_collection.find({"assignees": uid}))
            if not tasks_list: return await interaction.response.send_message("対象者はタスクを持っていません。", ephemeral=True)
            val = "".join([f"・**{t['task_name']}**" + (f" (期限:{t['deadline']})" if t.get('deadline') else "") + "\n" for t in tasks_list])
            embed.add_field(name=f"<@{uid}> さんのタスク", value=val)
        else:
            tasks_list = list(task_collection.find())
            if not tasks_list: return await interaction.response.send_message("現在登録されているタスクはありません。", ephemeral=True)
            for t in tasks_list:
                dl = f" [期限:{t['deadline']}]" if t.get('deadline') else ""
                embed.add_field(name=f"{t['task_name']}{dl}", value=f"担当者: {len(t['assignees'])}人", inline=False)
        await interaction.response.send_message(embed=embed)


# --- 6. Bot本体の定義 ---
intents = discord.Intents.all()
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="-", intents=intents)
    async def setup_hook(self):
        self.tree.add_command(TaskGroup())
        daily_cipher_task.start() 
        midnight_ranking_task.start()     
        send_pending_notices_task.start() 
        auto_delete_task.start()          
bot = MyBot()


# --- 7. サイファー監視ロジック ---
async def run_cipher_logic(end_datetime):
    channel = bot.get_channel(ANNOUNCE_CHANNEL_ID)
    dj_booth = bot.get_channel(DJ_BOOTH_CHANNEL_ID)
    vc_channel = bot.get_channel(CIPHER_VC_ID)
    if not vc_channel: return
    required_minutes = 30.0

    if channel:
        menus = ["**16小節サイファー**", "**2小節サイファー**", "**バトル**"]
        message = f"ラップの練習のお時間です！ <#{CIPHER_VC_ID}> に集まれ！🔥\n練習メニュー案：{random.choice(menus)}"
        await channel.send(message)

    voice_active_minutes.clear()

    try:
        for old_vc in bot.voice_clients: await old_vc.disconnect(force=True)
        vc_client = await vc_channel.connect(timeout=20.0, reconnect=True)
        check_interval = 20
        
        while True:
            now = datetime.datetime.now(JST)
            if now >= end_datetime: break
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
                        await update_ranking_message()
                        
                        new_balance = get_user_balance(user_id)
                        if dj_booth:
                            # ★ 外部ファイルのテキストフォーマットを利用
                            text = msg.MSG_CIPHER_REWARD.format(mention=member.mention, amount=bonus, new_balance=new_balance)
                            await dj_booth.send(text, allowed_mentions=discord.AllowedMentions.none())
    except Exception as e: print(f"Error: {e}")
    finally:
        for current_vc_client in bot.voice_clients: await current_vc_client.disconnect(force=True)


# --- 8. 定期タスク ---
@tasks.loop(time=START_TIME)
async def daily_cipher_task():
    await bot.wait_until_ready()
    now = datetime.datetime.now(JST)
    end_datetime = now.replace(hour=23, minute=0, second=0, microsecond=0)
    await run_cipher_logic(end_datetime)

@tasks.loop(time=MIDNIGHT_TIME)
async def midnight_ranking_task():
    await bot.wait_until_ready()
    await update_ranking_message()
    await update_b_rank_guide_message()
    
    guild = bot.get_guild(ALLOWED_GUILD_ID)
    if not guild: return
    mc_role = guild.get_role(MC_ROLE_ID)
    if not mc_role: return
    
    init_role_name = f"B0-T0 {B_NAMES[0]}_{T_NAMES[0]}"
    init_role = discord.utils.get(guild.roles, name=init_role_name)
    if not init_role: return

    for member in mc_role.members:
        has_rank = any(re.match(r"^B[0-7]-T[0-7]", role.name) for role in member.roles)
        if not has_rank:
            try:
                rank_collection.update_one({'user_id': str(member.id)}, {'$setOnInsert': {'b_points': 0, 't_points': 0, 'b_rank': 0, 't_rank': 0, 'temporary_rates': []}}, upsert=True)
                await member.add_roles(init_role, reason="自動割り当て")
                await asyncio.sleep(0.3)
            except Exception as e: print(f"自動ランク付与失敗: {e}")

@tasks.loop(time=NOTICE_TIME)
async def send_pending_notices_task():
    await bot.wait_until_ready()
    pending = list(notice_collection.find())
    if not pending: return
    for doc in pending:
        channel = bot.get_channel(doc['channel_id'])
        if channel:
            try:
                sent_msg = await channel.send(doc['message'])
                register_deletion(sent_msg.id, sent_msg.channel.id)
            except Exception: pass
        notice_collection.delete_one({"_id": doc["_id"]})

@tasks.loop(minutes=30)
async def auto_delete_task():
    await bot.wait_until_ready()
    now = datetime.datetime.now(JST)
    expired_docs = list(delete_collection.find({"delete_at": {"$lte": now}}))
    for doc in expired_docs:
        channel = bot.get_channel(doc['channel_id'])
        if channel:
            try:
                msg_obj = await channel.fetch_message(doc['message_id'])
                await msg_obj.delete()
            except Exception: pass
        delete_collection.delete_one({"_id": doc["_id"]})


# --- 9. 管理者用コマンド ---
@bot.command(name="sync")
@commands.has_permissions(administrator=True)
async def sync_commands(ctx):
    guild = discord.Object(id=ALLOWED_GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    await update_b_rank_guide_message()
    await update_ranking_message()
    await ctx.send("✅ スラッシュコマンド・メッセージの強制上書き同期が完了しました！")

@bot.tree.command(name="z_update_ranking", description=msg.DESC_Z_UPDATE_RANKING)
@app_commands.default_permissions(administrator=True)
async def manual_update_ranking_slash(interaction: discord.Interaction):
    await update_ranking_message()
    await interaction.response.send_message("✅ 強制更新しました。")

@bot.tree.command(name="z_bonus", description=msg.DESC_Z_BONUS)
@app_commands.default_permissions(administrator=True)
async def manual_bonus_slash(interaction: discord.Interaction, member: discord.Member):
    amount = random.randint(50, 100)
    add_user_balance(member.id, amount)
    await update_ranking_message()
    new_balance = get_user_balance(member.id)
    
    # ★ 外部ファイルのテキストフォーマットを利用
    text = msg.MSG_BONUS_SUCCESS.format(mention=member.mention, amount=amount, new_balance=new_balance)
    await interaction.response.send_message(text, allowed_mentions=discord.AllowedMentions.none())

@bot.tree.command(name="z_join", description=msg.DESC_Z_JOIN)
@app_commands.default_permissions(administrator=True)
async def join_vc_slash(interaction: discord.Interaction):
    vc_channel = bot.get_channel(CIPHER_VC_ID)
    if not vc_channel: return await interaction.response.send_message("VCが見つかりません。", ephemeral=True)
    if interaction.guild.voice_client: await interaction.guild.voice_client.move_to(vc_channel)
    else: await vc_channel.connect()
    await interaction.response.send_message("入室しました！🎤")

@bot.tree.command(name="z_leave", description=msg.DESC_Z_LEAVE)
@app_commands.default_permissions(administrator=True)
async def leave_vc_slash(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("退室しました。👋")
    else: await interaction.response.send_message("参加していません。", ephemeral=True)

@bot.tree.command(name="z_add", description=msg.DESC_Z_ADD)
@app_commands.default_permissions(administrator=True)
async def add_sp_slash(interaction: discord.Interaction, member: discord.Member, amount: int):
    add_user_balance(member.id, amount)
    await update_ranking_message()
    await interaction.response.send_message(f"{member.display_name}に **{amount} SP** 付与しました。")

@bot.tree.command(name="z_set", description=msg.DESC_Z_SET)
@app_commands.default_permissions(administrator=True)
async def set_sp_slash(interaction: discord.Interaction, member: discord.Member, amount: int):
    set_user_balance(member.id, amount)
    await update_ranking_message()
    await interaction.response.send_message(f"所持金を **{amount} SP** に設定しました。")

@bot.tree.command(name="z_bulk_remove", description=msg.DESC_Z_BULK_REMOVE)
@app_commands.default_permissions(administrator=True)
async def bulk_remove_slash(interaction: discord.Interaction, words_str: str):
    words_to_delete = words_str.split()
    result = word_collection.delete_many({'word': {'$in': words_to_delete}})
    await interaction.response.send_message(f"{result.deleted_count} 個の単語を削除しました。")

@bot.tree.command(name="z_dislogin", description=msg.DESC_Z_DISLOGIN)
@app_commands.default_permissions(administrator=True)
async def dislogin_slash(interaction: discord.Interaction, member: discord.Member):
    remove_rewarded_user(member.id)
    await interaction.response.send_message("デイリー記録を削除しました。")

@bot.tree.command(name="z_readingbeat", description=msg.DESC_Z_READINGBEAT)
@app_commands.default_permissions(administrator=True)
async def readingbeat_slash(interaction: discord.Interaction):
    global cached_beats
    await interaction.response.defer()
    try:
        new_beats = await asyncio.to_thread(get_playlist_urls, PLAYLIST_URL)
        if new_beats:
            cached_beats = new_beats
            await interaction.followup.send(f"✅ {len(cached_beats)}件のビートを読み込みました。")
        else: await interaction.followup.send("⚠️ リストが空です。")
    except Exception: await interaction.followup.send("❌ エラーが発生しました。")

# --------------------------------------------------
# B軸レート管理用コマンド（ここから）
# --------------------------------------------------

# 1. カテゴリのプルダウン選択肢（※必ずコマンドより上に定義する）
RATING_B_CATEGORIES = [
    app_commands.Choice(name="ネット草大会", value="ネット草大会"),
    app_commands.Choice(name="ネット本戦", value="ネット本戦"),
    app_commands.Choice(name="リアルイベント", value="リアルイベント"),
    app_commands.Choice(name="現場予選大会", value="現場予選大会"),
    app_commands.Choice(name="現場本戦大会", value="現場本戦大会")
]

# 2. B軸ポイント計算用のマスタデータ
# base: 参加ポイント, p: プレ予選, s: 1回戦/シード, d: 2回戦以降, f: 決勝, v: 優勝, exp_months: 有効期限(月)
POINT_TABLE_B = {
    "ネット草大会": {"base": 25, "p": 0, "s": 15, "d": 35, "f": 55, "v": 65, "exp_months": 6},
    "ネット本戦": {"base": 25, "p": 0, "s": 20, "d": 40, "f": 60, "v": 70, "exp_months": 6},
    "リアルイベント": {"base": 30, "p": 0, "s": 30, "d": 50, "f": 70, "v": 90, "exp_months": 12},
    "現場予選大会": {"base": 40, "p": 20, "s": 40, "d": 60, "f": 80, "v": 100, "exp_months": 12},
    "現場本戦大会": {"base": 100, "p": 0, "s": 100, "d": 150, "f": 200, "v": 300, "exp_months": 36}
}

# 3. コマンド本体
B_RATING_NOTIFY_CHANNEL_ID = 1512173148030767255  # 通知先のチャンネルID

@bot.tree.command(name="z_rating_b", description=msg.DESC_Z_RATING_B)
@app_commands.describe(
    mc="レートが上がる対象のユーザー",
    event="イベント名（自由入力）",
    when="開催日（YYYYMMDDの8桁 例: 20260605）",
    category="イベントのカテゴリ",
    result="結果（p, s, d, f, v, l で指定。例: ssdl）",
    result_how="結果の自由な説明（例: ベスト8、優勝 など）"
)
@app_commands.choices(category=RATING_B_CATEGORIES)
@app_commands.default_permissions(administrator=True)
async def z_rating_b_slash(
    interaction: discord.Interaction, 
    mc: discord.Member, 
    event: str, 
    when: str,
    category: str, 
    result: str,
    result_how: str
):
    # 1. 開催日(when)のバリデーション
    if len(when) != 8 or not when.isdigit():
        return await interaction.response.send_message(msg.MSG_RATING_B_ERR_DATE, ephemeral=True)
    
    try:
        event_date = datetime.datetime.strptime(when, "%Y%m%d").replace(tzinfo=JST)
    except ValueError:
        return await interaction.response.send_message(msg.MSG_RATING_B_ERR_DATE, ephemeral=True)

    # 2. 結果文字列のバリデーション
    valid_chars = set("psdfvl")
    result_lower = result.lower()
    if not all(char in valid_chars for char in result_lower):
        return await interaction.response.send_message(msg.MSG_RATING_B_ERR_RESULT, ephemeral=True)
        
    # 3. 今回の獲得ポイントと有効期限の計算
    table = POINT_TABLE_B[category]
    gained_points = table["base"]
    for char in result_lower:
        gained_points += table.get(char, 0)
        
    months_to_add = table["exp_months"]
    new_month = event_date.month - 1 + months_to_add
    expire_year = event_date.year + new_month // 12
    expire_month = new_month % 12 + 1
    _, last_day = calendar.monthrange(expire_year, expire_month)
    expire_day = min(event_date.day, last_day)
    expire_date = event_date.replace(year=expire_year, month=expire_month, day=expire_day)
    
    # 4. 現在のDB情報をもとに、既存の「有効ポイント」と「ランク」を計算
    now = datetime.datetime.now(JST)
    user_doc = rank_collection.find_one({'user_id': str(mc.id)}) or {'b_points': 0, 't_points': 0, 'b_rank': 0, 't_rank': 0, 'temporary_rates': []}
    
    old_valid_total = 0
    active_rates = []
    
    for record in user_doc.get('temporary_rates', []):
        if record['expire_at'] > now:
            old_valid_total += record['points']
            active_rates.append(record)
            
    old_b_rank = calculate_rank_level(old_valid_total)
    t_rank = user_doc.get('t_rank', 0) # T軸のランクはそのまま引き継ぐ
    
    # 5. 新しいレコードを追加した後の「新ポイント」と「新ランク」を計算
    rate_record = {
        "event": event,
        "category": category,
        "event_date": event_date,
        "result": result_lower,
        "points": gained_points,
        "granted_at": now,
        "expire_at": expire_date
    }
    active_rates.append(rate_record)
    
    new_valid_total = old_valid_total + gained_points
    new_b_rank = calculate_rank_level(new_valid_total)
    
    # DBをまとめて上書き更新
    rank_collection.update_one(
        {'user_id': str(mc.id)},
        {
            '$set': {
                'b_points': new_valid_total,
                'b_rank': new_b_rank,
                'temporary_rates': active_rates
            }
        },
        upsert=True
    )

    # 6. 通知メッセージの作成
    notify_text = msg.MSG_RATING_ANNOUNCE_BASE.format(
        mention=mc.mention,
        event=event,
        result_how=result_how,
        old_points=old_valid_total,
        new_points=new_valid_total
    )

    # 7. ランクの変動（ロールの付け替え）処理
    if old_b_rank != new_b_rank:
        guild = interaction.guild
        old_role_name = f"B{old_b_rank}-T{t_rank} {B_NAMES[old_b_rank]}_{T_NAMES[t_rank]}"
        new_role_name = f"B{new_b_rank}-T{t_rank} {B_NAMES[new_b_rank]}_{T_NAMES[t_rank]}"
        
        old_role = discord.utils.get(guild.roles, name=old_role_name)
        new_role = discord.utils.get(guild.roles, name=new_role_name)
        
        # 新しいロールが存在しない場合はBotに自動作成させる
        if not new_role:
            try:
                new_role = await guild.create_role(name=new_role_name, reason="ランクアップのため自動生成")
            except Exception as e:
                print(f"Role creation failed: {e}")

        # ロールの剥奪と付与
        if old_role in mc.roles:
            try:
                await mc.remove_roles(old_role)
            except: pass
            
        if new_role:
            try:
                await mc.add_roles(new_role)
            except: pass

        # メンション用文字列の作成（ロールが無い場合は単なるテキストにする安全対策）
        old_role_str = old_role.mention if old_role else f"`{old_role_name}`"
        new_role_str = new_role.mention if new_role else f"`{new_role_name}`"
        
        # ランク変動文をメッセージに継ぎ足す
        notify_text += msg.MSG_RATING_ANNOUNCE_RANK.format(
            old_role=old_role_str,
            new_role=new_role_str
        )

    # 8. 指定チャンネルへ送信 ＆ 実行した管理者へレスポンス
    notify_channel = bot.get_channel(B_RATING_NOTIFY_CHANNEL_ID)
    if notify_channel:
        await notify_channel.send(notify_text)

    # 管理者用ログ（成功の証）
    event_str = event_date.strftime("%Y/%m/%d")
    expire_str = expire_date.strftime("%Y/%m/%d")
    admin_log = msg.MSG_RATING_B_SUCCESS.format(
        mention=mc.mention,
        event=event,
        event_date=event_str,
        category=category,
        result=result,
        points=gained_points,
        expire_date=expire_str,
        total_points=new_valid_total
    ) + f"\n\n📢 <#{B_RATING_NOTIFY_CHANNEL_ID}> に告知を送信しました。"
    
    await interaction.response.send_message(admin_log, allowed_mentions=discord.AllowedMentions.none())

# --- 10. スラッシュコマンド (一般ユーザー用) ---
cached_beats = []

@bot.event
async def on_ready():
    global cached_beats
    print(f'Logged in as {bot.user}')
    cached_beats = await asyncio.to_thread(get_playlist_urls, PLAYLIST_URL)
    await update_ranking_message()
    await update_b_rank_guide_message()
    
    try:
        guild = discord.Object(id=ALLOWED_GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    except Exception as e: print(f"同期エラー: {e}")

@bot.tree.command(name="beat", description=msg.DESC_BEAT)
async def beat(interaction: discord.Interaction):
    if not cached_beats: return await interaction.response.send_message("空です。", ephemeral=True)
    await interaction.response.send_message(f"m!p {random.choice(cached_beats)}")

@bot.tree.command(name="gamerule", description=msg.DESC_GAMERULE)
async def gamerule(interaction: discord.Interaction):
    bpm = random.choices(['LOW', 'MIDDLE', 'FAST', 'ACAPPELLA'], weights=[30, 30, 30, 10])[0]
    turn = random.choices(['45s×2', '60s×2'], weights=[50, 50])[0] if bpm == 'ACAPPELLA' else random.choices(['8×2', '8×3', '8×4', '16×2', '32×2', '45s×2', '60s×2'], weights=[10, 20, 30, 20, 4, 8, 8])[0]
    await interaction.response.send_message(f"BPM：**{bpm}** TURN：**{turn}**")

@bot.tree.command(name="vote", description=msg.DESC_VOTE)
async def vote(interaction: discord.Interaction, first: str, second: str):
    await interaction.response.send_message(f":a: 先攻：{first}\n:regional_indicator_b: 後攻：{second}")
    msg_obj = await interaction.original_response()
    await msg_obj.add_reaction("🅰️")
    await msg_obj.add_reaction("🇧")

@bot.tree.command(name="help", description=msg.DESC_HELP)
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message("🔗 **[マニュアルを開く](https://note.com/preview/nfbf42a5fb2b3?prev_access_key=941730f8a7759e7dcd7daf2822f4faa8)**", ephemeral=True)

@bot.tree.command(name="daily", description=msg.DESC_DAILY)
async def daily_status(interaction: discord.Interaction):
    if str(interaction.user.id) in get_rewarded_users(): await interaction.response.send_message("獲得済みです！✅", ephemeral=True)
    else: await interaction.response.send_message(f"約{int(voice_active_minutes.get(str(interaction.user.id), 0))}分 / 30分", ephemeral=True)

@bot.tree.command(name="saifu", description=msg.DESC_SAIFU)
async def saifu(interaction: discord.Interaction):
    balance = get_user_balance(interaction.user.id)
    # ★ 外部ファイルのテキストフォーマットを利用
    await interaction.response.send_message(msg.MSG_SAIFU_CHECK.format(name=interaction.user.display_name, balance=balance))

@bot.tree.command(name="sent", description=msg.DESC_SENT)
async def sent(interaction: discord.Interaction, member: discord.Member, amount: int):
    if amount <= 0 or get_user_balance(interaction.user.id) < amount: return await interaction.response.send_message("無効な額または不足。", ephemeral=True)
    collection.update_one({'user_id': str(interaction.user.id)}, {'$inc': {'balance': -amount}})
    collection.update_one({'user_id': str(member.id)}, {'$inc': {'balance': amount}}, upsert=True)
    await update_ranking_message()
    await interaction.response.send_message(f"{member.display_name}さんに **{amount} SP** 送金しました！")

@bot.tree.command(name="add_word", description=msg.DESC_ADD_WORD)
async def word_add(interaction: discord.Interaction, word: str):
    if word_collection.find_one({'word': word}): return await interaction.response.send_message("登録済み！", ephemeral=True)
    word_collection.insert_one({'word': word, 'added_by': str(interaction.user.id)})
    await interaction.response.send_message(f"「{word}」を追加しました！🔥")

@bot.tree.command(name="wordbattle", description=msg.DESC_WORDBATTLE)
async def word_battle(interaction: discord.Interaction, count: int = 1, interval: int = 0):
    total = word_collection.count_documents({})
    if count <= 0 or total == 0: return await interaction.response.send_message("エラー", ephemeral=True)
    actual = min(count, total)
    await interaction.response.send_message(f"🎤 開始！ (合計: {actual}個 / 間隔: {interval}分)")
    words = [d['word'] for d in word_collection.aggregate([{ "$sample": { "size": actual } }])]
    for i, w in enumerate(words):
        await interaction.channel.send(f"**【{i+1}個目】** 👉   **{w}**")
        if i < len(words) - 1 and interval > 0: await asyncio.sleep(interval * 60)

@bot.tree.command(name="my_task", description=msg.DESC_MY_TASK)
async def my_task(interaction: discord.Interaction):
    tasks = list(task_collection.find({"assignees": str(interaction.user.id)}))
    if not tasks: return await interaction.response.send_message("タスクはありません", ephemeral=True)
    embed = discord.Embed(title="📋 タスク", color=discord.Color.green())
    for t in tasks: embed.add_field(name=f"📌 {t['task_name']}", value=f"・{t.get('description', '')}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)
