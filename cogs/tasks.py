import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
import messages as msg
from core import JST, NOTICE_TIME, task_collection, notice_collection, delete_collection, register_deletion, add_user_balance, update_ranking_message

class TaskGroup(app_commands.Group, name="z_task", description="【管理者専用】タスクの管理を行います"):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot

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
            await update_ranking_message(self.bot)

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


class TasksCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.send_pending_notices_task.start()
        self.auto_delete_task.start()

    def cog_unload(self):
        self.send_pending_notices_task.cancel()
        self.auto_delete_task.cancel()

    @app_commands.command(name="my_task", description=msg.DESC_MY_TASK)
    async def my_task(self, interaction: discord.Interaction):
        tasks_list = list(task_collection.find({"assignees": str(interaction.user.id)}))
        if not tasks_list: return await interaction.response.send_message("タスクはありません", ephemeral=True)
        embed = discord.Embed(title="📋 タスク", color=discord.Color.green())
        for t in tasks_list: embed.add_field(name=f"📌 {t['task_name']}", value=f"・{t.get('description', '')}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tasks.loop(time=NOTICE_TIME)
    async def send_pending_notices_task(self):
        await self.bot.wait_until_ready()
        pending = list(notice_collection.find())
        if not pending: return
        for doc in pending:
            channel = self.bot.get_channel(doc['channel_id'])
            if channel:
                try:
                    sent_msg = await channel.send(doc['message'])
                    register_deletion(sent_msg.id, sent_msg.channel.id)
                except Exception: pass
            notice_collection.delete_one({"_id": doc["_id"]})

    @tasks.loop(minutes=30)
    async def auto_delete_task(self):
        await self.bot.wait_until_ready()
        now = datetime.datetime.now(JST)
        expired_docs = list(delete_collection.find({"delete_at": {"$lte": now}}))
        for doc in expired_docs:
            channel = self.bot.get_channel(doc['channel_id'])
            if channel:
                try:
                    msg_obj = await channel.fetch_message(doc['message_id'])
                    await msg_obj.delete()
                except Exception: pass
            delete_collection.delete_one({"_id": doc["_id"]})

async def setup(bot):
    bot.tree.add_command(TaskGroup(bot))
    await bot.add_cog(TasksCog(bot))
