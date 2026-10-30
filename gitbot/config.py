"""Load .env and validate settings. Error messages name variables, never values."""
import os
from dataclasses import dataclass

from cryptography.fernet import Fernet
from dotenv import load_dotenv

REQUIRED = (
    "DISCORD_TOKEN",
    "DISCORD_GUILD_ID",
    "DEFAULT_CHANNEL_ID",
    "GITHUB_OAUTH_CLIENT_ID",
    "GITHUB_WEBHOOK_SECRET",
    "FERNET_KEY",
)
NUMERIC = ("DISCORD_GUILD_ID", "DEFAULT_CHANNEL_ID", "WEBHOOK_PORT")


@dataclass(frozen=True)
class Config:
    discord_token: str
    guild_id: int
    default_channel_id: int
    github_client_id: str
    github_scope: str
    webhook_secret: str
    fernet_key: str
    maintainer_role: str
    webhook_port: int
    db_path: str


def load_config() -> Config:
    # override=True: launch.json's envFile parses inline comments differently,
    # so python-dotenv's reading of .env always wins.
    load_dotenv(override=True)
    env = {key: os.getenv(key, "").strip() for key in REQUIRED}
    env["WEBHOOK_PORT"] = os.getenv("WEBHOOK_PORT", "").strip() or "8080"

    problems = [f"{key} is missing" for key in REQUIRED if not env[key]]
    problems += [
        f"{key} must be a number"
        for key in NUMERIC
        if env[key] and not env[key].isdigit()
    ]
    if env["FERNET_KEY"]:
        try:
            Fernet(env["FERNET_KEY"])
        except ValueError:
            problems.append("FERNET_KEY is not a valid Fernet key")
    if problems:
        raise SystemExit(
            "Bad .env (see .env.example):\n  " + "\n  ".join(problems)
        )

    return Config(
        discord_token=env["DISCORD_TOKEN"],
        guild_id=int(env["DISCORD_GUILD_ID"]),
        default_channel_id=int(env["DEFAULT_CHANNEL_ID"]),
        github_client_id=env["GITHUB_OAUTH_CLIENT_ID"],
        github_scope=os.getenv("GITHUB_OAUTH_SCOPE", "").strip() or "repo",
        webhook_secret=env["GITHUB_WEBHOOK_SECRET"],
        fernet_key=env["FERNET_KEY"],
        maintainer_role=os.getenv("MAINTAINER_ROLE", "").strip() or "Maintainer",
        webhook_port=int(env["WEBHOOK_PORT"]),
        db_path=os.getenv("DB_PATH", "").strip() or "ghbot.db",
    )
