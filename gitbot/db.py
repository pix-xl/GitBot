"""sqlite3 storage + Fernet token encryption."""
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from cryptography.fernet import Fernet

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  discord_id   TEXT PRIMARY KEY,
  github_login TEXT NOT NULL,
  token_enc    BLOB NOT NULL          -- Fernet-encrypted OAuth token
);
CREATE TABLE IF NOT EXISTS tracked_repos (
  repo       TEXT PRIMARY KEY,         -- 'owner/name', lowercase
  channel_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
  repo TEXT, pr INTEGER, discord_id TEXT, claimed_at TEXT,
  PRIMARY KEY (repo, pr, discord_id)
);
"""


@dataclass(frozen=True)
class LinkedUser:
    discord_id: str
    github_login: str
    token: str = field(repr=False)  # keeps the token out of logs and tracebacks


class Database:
    def __init__(self, path: str, default_channel_id: int, fernet_key: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.default_channel_id = default_channel_id
        self.fernet = Fernet(fernet_key)

    def get_user(self, discord_id: str) -> LinkedUser | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE discord_id = ?", (discord_id,)
        ).fetchone()
        if row is None:
            return None
        token = self.fernet.decrypt(row["token_enc"]).decode()
        return LinkedUser(row["discord_id"], row["github_login"], token)

    def get_user_by_login(self, login: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM users WHERE github_login = ? COLLATE NOCASE", (login,)
        ).fetchone()

    def save_user(self, discord_id: str, login: str, token: str):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO users (discord_id, github_login, token_enc) VALUES (?, ?, ?)",
                (discord_id, login, self.fernet.encrypt(token.encode())),
            )

    def delete_user(self, discord_id: str) -> bool:
        with self.conn:
            cursor = self.conn.execute("DELETE FROM users WHERE discord_id = ?", (discord_id,))
        return cursor.rowcount > 0

    def channel_for(self, repo: str) -> int:
        row = self.conn.execute(
            "SELECT channel_id FROM tracked_repos WHERE repo = ?", (repo.lower(),)
        ).fetchone()
        return int(row[0]) if row else self.default_channel_id

    def track(self, repo: str, channel_id: str):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO tracked_repos (repo, channel_id) VALUES (?, ?)",
                (repo.lower(), channel_id),
            )

    def add_claim(self, repo: str, pr: int, discord_id: str):
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO claims (repo, pr, discord_id, claimed_at) VALUES (?, ?, ?, ?)",
                (repo.lower(), pr, discord_id, datetime.now(timezone.utc).isoformat()),
            )

    def claims_for(self, repo: str, pr: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT discord_id FROM claims WHERE repo = ? AND pr = ? ORDER BY claimed_at",
            (repo.lower(), pr),
        ).fetchall()
        return [row["discord_id"] for row in rows]
