"""HTTP route registration."""

from __future__ import annotations

from fastapi import FastAPI

from web.context import AppContext
from web.routes import chats, media, system, wiki_routes


def register_routes(app: FastAPI, ctx: AppContext) -> None:
    system.register(app, ctx)
    wiki_routes.register(app, ctx)
    chats.register(app, ctx)
    media.register(app, ctx)
