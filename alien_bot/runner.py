"""既存のseiha-botと独立して動く、星人Discord Botの起動処理。"""

import asyncio
import os
import pkgutil
from threading import Thread

import discord
from discord.ext import commands

import alien_bot.cogs


class AlienBot(commands.Bot):
    def __init__(self, guild_id: int):
        # /test はスラッシュコマンドなので Message Content Intent は不要。
        intents = discord.Intents.default()
        self.management_enabled = os.environ.get(
            "ALIEN_SERVER_MANAGEMENT_ENABLED", "0"
        ).strip().lower() in ("1", "true", "yes", "on")
        if self.management_enabled:
            # Discord Developer Portalの両IntentをONにしてから有効化する。
            intents.members = True
            intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.target_guild_id = guild_id

    async def setup_hook(self):
        # 追加したCogファイルを自動で読み込む。
        for module in pkgutil.iter_modules(alien_bot.cogs.__path__):
            if module.name.startswith("_"):
                continue
            if module.name == "server_management" and not self.management_enabled:
                print("[Alien] サーバー管理機能は設定待ちのため無効", flush=True)
                continue
            await self.load_extension(f"alien_bot.cogs.{module.name}")

        guild = discord.Object(id=self.target_guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        print("[Alien] スラッシュコマンドの同期完了", flush=True)

    async def on_ready(self):
        print(f"[Alien] ログイン成功: {self.user}", flush=True)


def start_alien_bot():
    """環境変数が揃った場合だけ、星人Botを別スレッドで起動する。"""
    token = os.environ.get("ALIEN_DISCORD_TOKEN", "").strip()
    guild_id_text = os.environ.get("ALIEN_GUILD_ID", "").strip()

    if not token and not guild_id_text:
        print("[Alien] 未設定のため起動をスキップ（既存Botは通常どおり動作）", flush=True)
        return
    if not token or not guild_id_text:
        print("[Alien] ALIEN_DISCORD_TOKEN と ALIEN_GUILD_ID の両方を設定してください", flush=True)
        return

    try:
        guild_id = int(guild_id_text)
        if guild_id <= 0:
            raise ValueError("サーバーIDは正の整数である必要があります")
    except ValueError:
        print("[Alien] ALIEN_GUILD_ID が正しい数字ではありません", flush=True)
        return

    def run_in_thread():
        async def run_bot():
            alien = AlienBot(guild_id)
            async with alien:
                await alien.start(token, reconnect=True)

        try:
            asyncio.run(run_bot())
        except Exception as error:
            # 星人側のエラーが既存Bot全体を停止させないようにする。
            print(f"[Alien] 起動・稼働エラー: {type(error).__name__}: {error}", flush=True)

    Thread(target=run_in_thread, name="alien-discord-bot", daemon=True).start()
