import datetime
import calendar
import discord
from discord import app_commands
from discord.ext import commands
import sys
import os

# 親ディレクトリ（ルート）を検索パスに追加して、coreやmessagesを読み込めるようにする
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core
import messages as msg  # 💡 messages.py を読み込み、コード内の 'msg.' をそのまま使えるようにします
from core import JST, rank_collection, calculate_rank_level, B_NAMES, T_NAMES

class BRatingManagementCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 💡 告知用のチャンネルIDを直接指定します
        self.notify_channel_id = 1512173148030767255

    # 1. カテゴリのプルダウン選択肢
    RATING_B_CATEGORIES = [
        app_commands.Choice(name="ネット草大会", value="ネット草大会"),
        app_commands.Choice(name="ネット本戦", value="ネット本戦"),
        app_commands.Choice(name="リアルイベント", value="リアルイベント"),
        app_commands.Choice(name="現場予選大会", value="現場予選大会"),
        app_commands.Choice(name="現場本戦大会", value="現場本戦大会")
    ]

    # 2. B軸ポイント計算用のマスタデータ
    POINT_TABLE_B = {
        "ネット草大会": {"base": 25, "p": 0, "s": 15, "d": 35, "f": 55, "v": 65, "exp_months": 6},
        "ネット本戦": {"base": 25, "p": 0, "s": 20, "d": 40, "f": 60, "v": 70, "exp_months": 6},
        "リアルイベント": {"base": 30, "p": 0, "s": 30, "d": 50, "f": 70, "v": 90, "exp_months": 12},
        "現場予選大会": {"base": 40, "p": 20, "s": 40, "d": 60, "f": 80, "v": 100, "exp_months": 12},
        "现场本战大会": {"base": 100, "p": 0, "s": 100, "d": 150, "f": 200, "v": 300, "exp_months": 36}, 
        "現場本戦大会": {"base": 100, "p": 0, "s": 100, "d": 150, "f": 200, "v": 300, "exp_months": 36}
    }

    async def _update_user_roles(self, interaction: discord.Interaction, mc: discord.Member, old_b_rank: int, new_b_rank: int, t_rank: int):
        """ランク（Bランク）変動に伴うロールの付け替え処理を共通化"""
        if old_b_rank == new_b_rank:
            return "", ""

        guild = interaction.guild
        old_role_name = f"B{old_b_rank}-T{t_rank} {B_NAMES[old_b_rank]}_{T_NAMES[t_rank]}"
        new_role_name = f"B{new_b_rank}-T{t_rank} {B_NAMES[new_b_rank]}_{T_NAMES[t_rank]}"
        
        old_role = discord.utils.get(guild.roles, name=old_role_name)
        new_role = discord.utils.get(guild.roles, name=new_role_name)
        
        if not new_role:
            try:
                new_role = await guild.create_role(name=new_role_name, reason="ランク変動のため自動生成")
            except Exception as e:
                print(f"Role creation failed: {e}")

        if old_role in mc.roles:
            try: await mc.remove_roles(old_role)
            except: pass
            
        if new_role:
            try: await mc.add_roles(new_role)
            except: pass

        old_role_str = f"**{old_role.name}**" if old_role else f"**{old_role_name}**"
        new_role_str = f"**{new_role.name}**" if new_role else f"**{new_role_name}**"
        
        return old_role_str, new_role_str

    def _process_active_rates(self, user_doc, now) -> tuple:
        """既存レコードの読み込み、タイムゾーン補正、有効期限チェックの共通化"""
        old_valid_total = 0
        active_rates = []
        for record in user_doc.get('temporary_rates', []):
            expire_at = record['expire_at']
            if expire_at.tzinfo is None:
                expire_at = expire_at.replace(tzinfo=datetime.timezone.utc).astimezone(JST)
            
            if expire_at > now:
                old_valid_total += record['points']
                record['expire_at'] = expire_at
                if 'event_date' in record and record['event_date'].tzinfo is None:
                    record['event_date'] = record['event_date'].replace(tzinfo=datetime.timezone.utc).astimezone(JST)
                if 'granted_at' in record and record['granted_at'].tzinfo is None:
                    record['granted_at'] = record['granted_at'].replace(tzinfo=datetime.timezone.utc).astimezone(JST)
                active_rates.append(record)
        return old_valid_total, active_rates


    # 1. 大会結果記録用コマンド (/z_rating_b)
    @app_commands.command(name="z_rating_b", description=msg.DESC_Z_RATING_B)
    @app_commands.describe(
        mc=msg.DESC_Z_RATING_B_MC,
        event=msg.DESC_Z_RATING_B_EVENT,
        when=msg.DESC_Z_RATING_B_WHEN,
        category=msg.DESC_Z_RATING_B_CATEGORY,
        result=msg.DESC_Z_RATING_B_RESULT,
        result_how=msg.DESC_Z_RATING_B_RESULT_HOW
    )
    @app_commands.choices(category=RATING_B_CATEGORIES)
    @app_commands.default_permissions(administrator=True)
    async def z_rating_b_slash(
        self,
        interaction: discord.Interaction, 
        mc: discord.Member, 
        event: str, 
        when: str,
        category: str, 
        result: str,
        result_how: str
    ):
        await interaction.response.defer(ephemeral=True)

        if len(when) != 8 or not when.isdigit():
            return await interaction.followup.send(msg.MSG_RATING_B_ERR_DATE)
        
        try:
            event_date = datetime.datetime.strptime(when, "%Y%m%d").replace(tzinfo=JST)
        except ValueError:
            return await interaction.followup.send(msg.MSG_RATING_B_ERR_DATE)

        valid_chars = set("psdfvl")
        result_lower = result.lower()
        if not all(char in valid_chars for char in result_lower):
            return await interaction.followup.send(msg.MSG_RATING_B_ERR_RESULT)
            
        table = self.POINT_TABLE_B[category]
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
        
        now = datetime.datetime.now(JST)
        user_doc = rank_collection.find_one({'user_id': str(mc.id)}) or {'b_points': 0, 't_points': 0, 'b_rank': 0, 't_rank': 0, 'temporary_rates': []}
        
        old_valid_total, active_rates = self._process_active_rates(user_doc, now)
        old_b_rank = calculate_rank_level(old_valid_total)
        t_rank = user_doc.get('t_rank', 0)
        
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

        notify_text = msg.MSG_RATING_ANNOUNCE_BASE.format(
            mention=mc.mention,
            event=event,
            result_how=result_how,
            old_points=old_valid_total,
            new_points=new_valid_total
        )

        if old_b_rank != new_b_rank:
            old_role_str, new_role_str = await self._update_user_roles(interaction, mc, old_b_rank, new_b_rank, t_rank)
            notify_text += msg.MSG_RATING_ANNOUNCE_RANK.format(
                old_role=old_role_str,
                new_role=new_role_str
            )

        notify_channel = self.bot.get_channel(self.notify_channel_id)
        if notify_channel:
            await notify_channel.send(notify_text)

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
        ) + f"\n\n📢 <#{self.notify_channel_id}> に告知を送信しました。"
        
        await interaction.followup.send(admin_log, allowed_mentions=discord.AllowedMentions.none())


    # 2. レート直接変更用コマンド (/z_rating_set
