"""Entry point: python -m gitbot"""
import logging

import discord
from aiohttp import web
from discord import app_commands

from .commands import setup_commands
from .config import Config, load_config
from .db import Database
from .views import PRButton
from .webhooks import build_app

log = logging.getLogger("gitbot")


class GitBot(discord.Client):
    def __init__(self, config: Config):
        super().__init__(
            intents=discord.Intents.default(),
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        self.config = config
        self.db = Database(config.db_path, config.default_channel_id, config.fernet_key)
        self.tree = app_commands.CommandTree(self)
        self.guild = discord.Object(id=config.guild_id)
        self.web_runner: web.AppRunner | None = None

    async def setup_hook(self):
        app = build_app(self, self.db, self.config.webhook_secret)
        self.web_runner = web.AppRunner(app, access_log=None)
        await self.web_runner.setup()
        await web.TCPSite(self.web_runner, "0.0.0.0", self.config.webhook_port).start()
        log.info("Webhook server on :%d", self.config.webhook_port)

        self.add_dynamic_items(PRButton)
        setup_commands(self.tree, self.guild, self.db, self.config)
        await self.tree.sync(guild=self.guild)

    async def on_ready(self):
        assert self.user is not None
        log.info("Logged in as %s (id %s)", self.user, self.user.id)

    async def close(self):
        if self.web_runner:
            await self.web_runner.cleanup()
        await super().close()


def main():
    config = load_config()
    discord.utils.setup_logging(level=logging.INFO)
    GitBot(config).run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
