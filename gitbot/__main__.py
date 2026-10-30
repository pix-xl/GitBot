"""Entry point: python -m gitbot"""
import logging

import discord
from aiohttp import web
from discord import app_commands

from .commands import setup_commands
from .config import Config, load_config
from .webhooks import build_app

log = logging.getLogger("gitbot")


class GitBot(discord.Client):
    def __init__(self, config: Config):
        super().__init__(intents=discord.Intents.default())
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self.guild = discord.Object(id=config.guild_id)
        self.web_runner: web.AppRunner | None = None

    async def setup_hook(self):
        self.web_runner = web.AppRunner(build_app(), access_log=None)
        await self.web_runner.setup()
        await web.TCPSite(self.web_runner, "0.0.0.0", self.config.webhook_port).start()
        log.info("Webhook server on :%d", self.config.webhook_port)

        setup_commands(self.tree, self.guild)
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
