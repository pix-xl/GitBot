"""aiohttp app for GitHub webhooks. POST /github arrives in M1."""
from aiohttp import web


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", health)
    return app
