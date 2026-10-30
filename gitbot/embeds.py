"""Webhook payload -> Notification (content + embed + view)."""
from dataclasses import dataclass

import discord
from discord.utils import MISSING, escape_markdown

from .db import Database
from .views import build_pr_view

MAX_COMMIT_LINES = 8
MAX_REVIEW_BODY = 300

REVIEW_STYLES = {
    "approved": ("✅ {reviewer} approved #{number}", discord.Color.green()),
    "changes_requested": ("❌ {reviewer} requested changes on #{number}", discord.Color.red()),
    "commented": ("💬 {reviewer} commented on #{number}", discord.Color.light_grey()),
}


@dataclass
class Notification:
    content: str | None = None  # mentions go here: inside an embed they never ping
    embed: discord.Embed = MISSING
    view: discord.ui.View = MISSING


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def make_embed(
    payload: dict, title: str, url: str, color: discord.Color, description: str | None = None
) -> discord.Embed:
    sender = payload["sender"]
    embed = discord.Embed(title=clip(title, 256), url=url, color=color, description=description)
    embed.set_author(name=sender["login"], url=sender["html_url"], icon_url=sender["avatar_url"])
    return embed


def mention(db: Database, login: str) -> str | None:
    user = db.get_user_by_login(login)
    return f"<@{user['discord_id']}>" if user else None


def commit_line(commit: dict) -> str:
    message = commit["message"].partition("\n")[0]
    author = commit["author"]["name"]
    return (
        f"[`{commit['id'][:7]}`]({commit['url']}) "
        f"{escape_markdown(clip(message, 72))} — {escape_markdown(author)}"
    )


def render_ping(payload: dict, db: Database) -> Notification:
    return Notification(content=f"✅ Webhook connected for `{payload['repository']['full_name']}`")


def render_push(payload: dict, db: Database) -> Notification | None:
    ref = payload["ref"]
    if not ref.startswith("refs/heads/"):
        return None
    branch = ref.removeprefix("refs/heads/")
    pusher = payload["pusher"]["name"]
    commits = payload["commits"]

    if payload["deleted"]:
        title = f"🗑️ {pusher} deleted `{branch}`"
        url = payload["repository"]["html_url"]
        return Notification(embed=make_embed(payload, title, url, discord.Color.dark_grey()))
    if not commits:
        return None

    count = len(commits)
    title = f"⬆️ {pusher} pushed {count} commit{'' if count == 1 else 's'} to `{branch}`"
    if payload["forced"]:
        title += " (force-push)"
    lines = [commit_line(commit) for commit in commits[:MAX_COMMIT_LINES]]
    if count > MAX_COMMIT_LINES:
        lines.append(f"[…and {count - MAX_COMMIT_LINES} more]({payload['compare']})")
    embed = make_embed(payload, title, payload["compare"], discord.Color.blurple(), "\n".join(lines))
    return Notification(embed=embed)


def render_pull_request(payload: dict, db: Database) -> Notification | None:
    action = payload["action"]
    pr = payload["pull_request"]
    number = pr["number"]
    url = pr["html_url"]
    pr_title = escape_markdown(pr["title"])

    if action in ("opened", "reopened", "ready_for_review"):
        if pr["draft"]:
            return None
        embed = make_embed(payload, f"🔀 PR #{number}: {pr_title}", url, discord.Color.green())
        embed.add_field(
            name="Branch", value=f"`{pr['head']['ref']}` → `{pr['base']['ref']}`", inline=False
        )
        embed.add_field(name="Author", value=pr["user"]["login"])
        embed.add_field(name="Changes", value=f"+{pr['additions']} / −{pr['deletions']}")
        return Notification(
            embed=embed, view=build_pr_view(payload["repository"]["full_name"], number)
        )

    if action == "closed" and pr["merged"]:
        merged_by = (pr["merged_by"] or payload["sender"])["login"]
        title = f"🟣 #{number} merged into `{pr['base']['ref']}` by {merged_by}"
        return Notification(embed=make_embed(payload, title, url, discord.Color.purple(), pr_title))

    if action == "closed":
        return Notification(
            embed=make_embed(payload, f"⛔ #{number} closed", url, discord.Color.red(), pr_title)
        )

    if action == "review_requested":
        reviewer = payload.get("requested_reviewer")
        name = reviewer["login"] if reviewer else f"team {payload['requested_team']['name']}"
        title = f"👀 Review requested on #{number} from {name}"
        return Notification(
            content=mention(db, reviewer["login"]) if reviewer else None,
            embed=make_embed(payload, title, url, discord.Color.gold(), pr_title),
        )

    return None


def render_review(payload: dict, db: Database) -> Notification | None:
    review = payload["review"]
    style = REVIEW_STYLES.get(review["state"].lower())
    if payload["action"] != "submitted" or style is None:
        return None

    template, color = style
    pr = payload["pull_request"]
    reviewer = review["user"]["login"]
    author = pr["user"]["login"]
    title = template.format(reviewer=reviewer, number=pr["number"])
    description = clip(review["body"] or escape_markdown(pr["title"]), MAX_REVIEW_BODY)
    return Notification(
        content=mention(db, author) if author.lower() != reviewer.lower() else None,
        embed=make_embed(payload, title, review["html_url"], color, description),
    )


RENDERERS = {
    "ping": render_ping,
    "push": render_push,
    "pull_request": render_pull_request,
    "pull_request_review": render_review,
}


def render(event: str, payload: dict, db: Database) -> Notification | None:
    renderer = RENDERERS.get(event)
    return renderer(payload, db) if renderer else None
