"""aiohttp app for GitHub webhooks: POST /github, HMAC verify, dispatch by event."""
import asyncio
import hashlib
import hmac
import json
import logging
from urllib.parse import parse_qs

import discord
from aiohttp import web

from .db import Database
from .embeds import Notification, render

log = logging.getLogger("gitbot.webhooks")

# GitHub caps payloads at 25 MB; aiohttp's default limit is only 1 MB.
MAX_BODY_SIZE = 25 * 1024 * 1024


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # Compare bytes: compare_digest raises on non-ASCII str, and anyone can send this header.
    return hmac.compare_digest(expected.encode(), signature.encode(errors="replace"))


def parse_payload(body: bytes, content_type: str) -> dict:
    if content_type == "application/x-www-form-urlencoded":
        return json.loads(parse_qs(body.decode())["payload"][0])
    return json.loads(body)


class WebhookReceiver:
    def __init__(self, client: discord.Client, db: Database, secret: str):
        self.client = client
        self.db = db
        self.secret = secret
        self.tasks: set[asyncio.Task] = set()  # the event loop only keeps weak references

    async def github(self, request: web.Request) -> web.Response:
        body = await request.read()
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not verify_signature(self.secret, body, signature):
            log.warning("Rejected webhook: bad signature (check GITHUB_WEBHOOK_SECRET)")
            return web.Response(status=401, text="bad signature")

        try:
            payload = parse_payload(body, request.content_type)
        except (ValueError, KeyError):
            log.warning("Rejected webhook: unreadable payload")
            return web.Response(status=400, text="bad payload")

        event = request.headers.get("X-GitHub-Event", "")
        task = asyncio.create_task(self.dispatch(event, payload))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return web.Response(text="ok")

    async def dispatch(self, event: str, payload: dict):
        try:
            repo = payload.get("repository", {}).get("full_name")
            notification = render(event, payload, self.db) if repo else None
            if notification:
                await self.post(repo, notification)
            else:
                log.info("No notification for %s event on %s", event, repo)
        except Exception:
            log.exception("Failed to handle %s event", event)

    async def post(self, repo: str, notification: Notification):
        channel = self.client.get_partial_messageable(self.db.channel_for(repo))
        await channel.send(notification.content, embed=notification.embed, view=notification.view)


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def build_app(client: discord.Client, db: Database, secret: str) -> web.Application:
    receiver = WebhookReceiver(client, db, secret)
    app = web.Application(client_max_size=MAX_BODY_SIZE)
    app.router.add_get("/", health)
    app.router.add_post("/github", receiver.github)
    return app
