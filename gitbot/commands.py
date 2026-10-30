"""Slash commands"""
import discord
from discord import app_commands


def setup_commands(tree: app_commands.CommandTree, guild: discord.abc.Snowflake):
    @tree.command(name="ping", description="Check if the bot is alive", guild=guild)
    async def ping(interaction: discord.Interaction):
        await interaction.response.send_message("Pong!", ephemeral=True)

    @tree.command(name="pong", description="Check if the bot is alive", guild=guild)
    async def pong(interaction: discord.Interaction):
        await interaction.response.send_message("Ping!", ephemeral=True)
