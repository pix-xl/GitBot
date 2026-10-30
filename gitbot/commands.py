"""Slash commands"""
import asyncio
import logging
import re

import discord
from discord import app_commands
from discord.utils import escape_markdown

from . import oauth
from .config import Config
from .db import Database
from .github_api import GitHub, GitHubError
from .views import send_ephemeral

log = logging.getLogger("gitbot.commands")

REPO_PATTERN = re.compile(r"[\w.-]+/[\w.-]+")


def setup_commands(
    tree: app_commands.CommandTree, guild: discord.abc.Snowflake, db: Database, config: Config
):
    link_tasks: set[asyncio.Task] = set()  # the event loop only keeps weak references

    def is_maintainer(interaction: discord.Interaction) -> bool:
        member = interaction.user
        return isinstance(member, discord.Member) and (
            member.guild_permissions.manage_guild
            or discord.utils.get(member.roles, name=config.maintainer_role) is not None
        )

    @tree.error
    async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        original = getattr(error, "original", error)
        if isinstance(error, app_commands.CheckFailure):
            message = "Maintainers only."
        elif isinstance(original, GitHubError):
            message = f"GitHub said: {original.message}"
        elif isinstance(original, oauth.OAuthError):
            message = str(original)
        else:
            log.error("Command failed", exc_info=original)
            message = "Something went wrong."
        await send_ephemeral(interaction, message)

    async def finish_link(interaction: discord.Interaction, device: oauth.DeviceCode):
        try:
            token = await oauth.poll(config.github_client_id, device)
            login = (await GitHub(token).get_user())["login"]
            db.save_user(str(interaction.user.id), login, token)
            message = f"Linked as **{login}**"
        except oauth.OAuthError as error:
            message = str(error)
        except GitHubError as error:
            message = f"GitHub said: {error.message}"
        except Exception:
            log.exception("Linking failed")
            message = "Something went wrong."
        await interaction.followup.send(message, ephemeral=True)

    @tree.command(name="ping", description="Check if the bot is alive", guild=guild)
    async def ping(interaction: discord.Interaction):
        await interaction.response.send_message("Pong!", ephemeral=True)

    @tree.command(name="pong", description="Check if the bot is alive", guild=guild)
    async def pong(interaction: discord.Interaction):
        await interaction.response.send_message("Ping!", ephemeral=True)

    @tree.command(name="link", description="Link your GitHub account", guild=guild)
    async def link(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        device = await oauth.start(config.github_client_id, config.github_scope)

        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Open GitHub", url=device.verification_uri))
        await interaction.followup.send(
            f"Go to {device.verification_uri} and enter **{device.user_code}**",
            view=view,
            ephemeral=True,
        )

        task = asyncio.create_task(finish_link(interaction, device))
        link_tasks.add(task)
        task.add_done_callback(link_tasks.discard)

    @tree.command(name="whoami", description="Show your linked GitHub account", guild=guild)
    async def whoami(interaction: discord.Interaction):
        user = db.get_user(str(interaction.user.id))
        message = f"Linked as **{user.github_login}**" if user else "Not linked — run /link"
        await interaction.response.send_message(message, ephemeral=True)

    @tree.command(name="unlink", description="Forget your linked GitHub account", guild=guild)
    async def unlink(interaction: discord.Interaction):
        if db.delete_user(str(interaction.user.id)):
            # Revoking needs the OAuth client secret, which the device flow doesn't use.
            message = (
                "Unlinked. To revoke the bot's access on GitHub too, "
                "go to https://github.com/settings/applications"
            )
        else:
            message = "Not linked."
        await interaction.response.send_message(message, ephemeral=True)

    @tree.command(
        name="track", description="Post a repo's notifications in this channel", guild=guild
    )
    @app_commands.check(is_maintainer)
    @app_commands.describe(repo="Repository as owner/name")
    async def track(interaction: discord.Interaction, repo: str):
        await interaction.response.defer(ephemeral=True)
        if not REPO_PATTERN.fullmatch(repo):
            await interaction.followup.send("Use the form owner/name", ephemeral=True)
            return
        user = db.get_user(str(interaction.user.id))
        if user is None:
            await interaction.followup.send("Run /link first", ephemeral=True)
            return

        full_name = (await GitHub(user.token).get_repo(repo))["full_name"]
        db.track(full_name, str(interaction.channel_id))
        await interaction.followup.send(f"Tracking `{full_name}` in this channel", ephemeral=True)

    @tree.command(name="assign", description="Assign a reviewer to a pull request", guild=guild)
    @app_commands.check(is_maintainer)
    @app_commands.describe(
        repo="Repository as owner/name", pr="Pull request number", reviewer="Who should review it"
    )
    async def assign(
        interaction: discord.Interaction, repo: str, pr: int, reviewer: discord.Member
    ):
        await interaction.response.defer(ephemeral=True)
        if not REPO_PATTERN.fullmatch(repo):
            await interaction.followup.send("Use the form owner/name", ephemeral=True)
            return
        caller = db.get_user(str(interaction.user.id))
        if caller is None:
            await interaction.followup.send("Run /link first", ephemeral=True)
            return
        target = db.get_user(str(reviewer.id))
        if target is None:
            await interaction.followup.send(
                f"{reviewer.mention} hasn't run /link yet", ephemeral=True
            )
            return

        await GitHub(caller.token).request_reviewers(repo, pr, [target.github_login])

        # After an ephemeral defer, the first followup is ephemeral too, so post to the channel.
        channel = interaction.client.get_partial_messageable(db.channel_for(repo))
        caller_name = escape_markdown(interaction.user.display_name)
        await channel.send(f"📌 {caller_name} assigned {reviewer.mention} to #{pr}")
        await interaction.followup.send(f"Assigned {reviewer.mention} to #{pr}", ephemeral=True)
