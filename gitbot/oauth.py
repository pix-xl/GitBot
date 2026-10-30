"""GitHub OAuth device flow: start + poll."""
import asyncio
import time
from dataclasses import dataclass, field

import aiohttp

DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
TIMEOUT = aiohttp.ClientTimeout(total=20)

# Discord interaction tokens die after 15 minutes, the same lifetime as a device code.
# Stop polling first so the "code expired" followup can still be delivered.
MAX_POLL_SECONDS = 14 * 60

EXPIRED_MESSAGE = "That code expired. Run /link to get a new one."


class OAuthError(Exception):
    """The message is safe to show to the user."""


@dataclass(frozen=True)
class DeviceCode:
    device_code: str = field(repr=False)
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


async def post_form(url: str, form: dict) -> dict:
    headers = {"Accept": "application/json"}
    async with aiohttp.ClientSession(headers=headers, timeout=TIMEOUT) as session:
        async with session.post(url, data=form) as response:
            return await response.json(content_type=None)


async def start(client_id: str, scope: str) -> DeviceCode:
    data = await post_form(DEVICE_CODE_URL, {"client_id": client_id, "scope": scope})
    if "device_code" not in data:
        reason = data.get("error_description") or data.get("error") or "unknown error"
        raise OAuthError(f"GitHub refused to start linking: {reason}")
    return DeviceCode(
        device_code=data["device_code"],
        user_code=data["user_code"],
        verification_uri=data["verification_uri"],
        expires_in=data["expires_in"],
        interval=data["interval"],
    )


async def poll(client_id: str, device: DeviceCode) -> str:
    """Wait for the user to authorize and return their token."""
    form = {"client_id": client_id, "device_code": device.device_code, "grant_type": GRANT_TYPE}
    interval = device.interval
    deadline = time.monotonic() + min(device.expires_in, MAX_POLL_SECONDS)

    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        try:
            data = await post_form(TOKEN_URL, form)
        except (aiohttp.ClientError, TimeoutError, ValueError):
            continue  # flaky network or a bad response: try again on the next tick

        if "access_token" in data:
            return data["access_token"]

        error = data.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
        elif error == "expired_token":
            raise OAuthError(EXPIRED_MESSAGE)
        elif error == "access_denied":
            raise OAuthError("Linking cancelled on GitHub.")
        else:
            raise OAuthError(f"GitHub login failed: {data.get('error_description') or error}")

    raise OAuthError(EXPIRED_MESSAGE)
