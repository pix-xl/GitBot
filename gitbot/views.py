"""Persistent PR buttons and review modals."""
import logging
import re
from typing import TYPE_CHECKING

import discord

from .github_api import GitHub, GitHubError

if TYPE_CHECKING:
    from .__main__ import GitBot

log = logging.getLogger("gitbot.views")

MAX_CUSTOM_ID = 100
REVIEWERS_FIELD = "Reviewer(s)"

BUTTONS = {
    "claim": ("Claim review", discord.ButtonStyle.blurple),
    "approve": ("Approve", discord.ButtonStyle.green),
    "changes": ("Request changes", discord.ButtonStyle.red),
}

# action -> (modal title, GitHub review event, comment label, comment required)
REVIEW_ACTIONS = {
    "approve": ("Approve #{number}", "APPROVE", "Comment (optional)", False),
    "changes": ("Request changes on #{number}", "REQUEST_CHANGES", "What needs to change?", True),
}


async def send_ephemeral(interaction: discord.Interaction, message: str):
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


async def report_error(interaction: discord.Interaction, error: Exception):
    if isinstance(error, GitHubError):
        message = f"GitHub said: {error.message}"
    else:
        log.error("PR action failed", exc_info=error)
        message = "Something went wrong."
    await send_ephemeral(interaction, message)


def set_reviewers(embed: discord.Embed, discord_ids: list[str]):
    value = ", ".join(f"<@{discord_id}>" for discord_id in discord_ids)
    for index, field in enumerate(embed.fields):
        if field.name == REVIEWERS_FIELD:
            embed.set_field_at(index, name=REVIEWERS_FIELD, value=value, inline=False)
            return
    embed.add_field(name=REVIEWERS_FIELD, value=value, inline=False)


def custom_id_for(action: str, repo: str, number: int) -> str:
    return f"pr:{action}:{repo}:{number}"


class ReviewModal(discord.ui.Modal):
    def __init__(self, action: str, repo: str, number: int):
        title, event, label, required = REVIEW_ACTIONS[action]
        super().__init__(title=title.format(number=number))
        self.event = event
        self.repo = repo
        self.number = number
        self.comment = discord.ui.TextInput(
            label=label, style=discord.TextStyle.paragraph, max_length=1000, required=required
        )
        self.add_item(self.comment)

    async def on_submit(self, interaction: discord.Interaction["GitBot"]):
        # Without thinking=True, discord.py ignores ephemeral when deferring a modal submit.
        await interaction.response.defer(ephemeral=True, thinking=True)
        user = interaction.client.db.get_user(str(interaction.user.id))
        if user is None:
            await interaction.followup.send("Run /link first", ephemeral=True)
            return

        github = GitHub(user.token)
        pr = await github.get_pr(self.repo, self.number)
        if pr["state"] != "open":
            status = "merged" if pr["merged"] else "closed"
            await interaction.followup.send(f"#{self.number} is already {status}", ephemeral=True)
            return

        # The public confirmation comes from the pull_request_review webhook.
        await github.create_review(self.repo, self.number, self.event, self.comment.value)
        await interaction.followup.send("Submitted", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await report_error(interaction, error)


class PRButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"pr:(?P<action>claim|approve|changes):(?P<repo>[\w.-]+/[\w.-]+):(?P<num>\d+)",
):
    def __init__(self, action: str, repo: str, number: int):
        label, style = BUTTONS[action]
        button = discord.ui.Button(
            label=label, style=style, custom_id=custom_id_for(action, repo, number)
        )
        super().__init__(button)
        self.action = action
        self.repo = repo
        self.number = number

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /
    ):
        return cls(match["action"], match["repo"], int(match["num"]))

    async def callback(self, interaction: discord.Interaction["GitBot"]):
        # tree.on_error doesn't cover buttons: discord.py logs and drops these exceptions,
        # which would leave the user stuck on "thinking...".
        try:
            if self.action == "claim":
                await self.claim(interaction)
            else:
                await self.open_review(interaction)
        except Exception as error:
            await report_error(interaction, error)

    async def claim(self, interaction: discord.Interaction["GitBot"]):
        await interaction.response.defer(ephemeral=True, thinking=True)
        db = interaction.client.db
        discord_id = str(interaction.user.id)
        user = db.get_user(discord_id)
        if user is None:
            await interaction.followup.send("Run /link first", ephemeral=True)
            return

        await GitHub(user.token).request_reviewers(self.repo, self.number, [user.github_login])
        db.add_claim(self.repo, self.number, discord_id)

        # message.edit, not edit_original_response: that would target the ephemeral reply.
        message = interaction.message
        if message and message.embeds:
            embed = message.embeds[0]
            set_reviewers(embed, db.claims_for(self.repo, self.number))
            await message.edit(embed=embed)

        await interaction.followup.send(f"You're reviewing #{self.number}", ephemeral=True)

    async def open_review(self, interaction: discord.Interaction["GitBot"]):
        # A modal has to be the first response, so this can't defer. Check the link now
        # rather than after the user has typed a comment.
        if interaction.client.db.get_user(str(interaction.user.id)) is None:
            await send_ephemeral(interaction, "Run /link first")
            return
        await interaction.response.send_modal(ReviewModal(self.action, self.repo, self.number))


def build_pr_view(repo: str, number: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    # A long repo name can overflow custom_id, and Discord would reject the whole message.
    if len(custom_id_for("changes", repo, number)) <= MAX_CUSTOM_ID:
        for action in BUTTONS:
            view.add_item(PRButton(action, repo, number))
    view.add_item(
        discord.ui.Button(label="View on GitHub", url=f"https://github.com/{repo}/pull/{number}")
    )
    return view
