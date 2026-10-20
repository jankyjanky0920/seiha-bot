"""動作確認用スラッシュコマンド。"""

import discord
from discord import app_commands
from discord.ext import commands


class TestCog(commands.Cog):
    @app_commands.command(name="test", description="test と返信します")
    async def test(self, interaction: discord.Interaction):
        await interaction.response.send_message("test")


async def setup(bot: commands.Bot):
    await bot.add_cog(TestCog())
