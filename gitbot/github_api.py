"""Async GitHub REST client."""
import json
from typing import Any

import aiohttp

API_URL = "https://api.github.com"
TIMEOUT = aiohttp.ClientTimeout(total=20)


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def parse_error(status: int, text: str) -> str:
    fallback = f"HTTP {status}"
    try:
        data = json.loads(text)
    except ValueError:
        return fallback
    if not isinstance(data, dict):
        return fallback

    message = data.get("message") or fallback
    errors = data.get("errors")
    if isinstance(errors, list) and errors:
        # GitHub sends these as objects or as plain strings, depending on the endpoint.
        first = errors[0]
        detail = first.get("message") if isinstance(first, dict) else first
        if detail:
            message += f": {detail}"
    return message


class GitHub:
    def __init__(self, token: str):
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def request(self, method: str, path: str, body: dict | None = None) -> Any:
        async with aiohttp.ClientSession(headers=self.headers, timeout=TIMEOUT) as session:
            async with session.request(method, API_URL + path, json=body) as response:
                text = await response.text()
                if not 200 <= response.status < 300:
                    raise GitHubError(response.status, parse_error(response.status, text))
                return json.loads(text) if text else None

    async def get_user(self) -> dict:
        return await self.request("GET", "/user")

    async def get_repo(self, repo: str) -> dict:
        return await self.request("GET", f"/repos/{repo}")

    async def get_pr(self, repo: str, number: int) -> dict:
        return await self.request("GET", f"/repos/{repo}/pulls/{number}")

    async def request_reviewers(self, repo: str, number: int, logins: list[str]) -> dict:
        path = f"/repos/{repo}/pulls/{number}/requested_reviewers"
        return await self.request("POST", path, {"reviewers": logins})

    async def create_review(self, repo: str, number: int, event: str, body: str) -> dict:
        path = f"/repos/{repo}/pulls/{number}/reviews"
        return await self.request("POST", path, {"event": event, "body": body})
