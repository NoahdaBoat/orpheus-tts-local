"""Application factory: wire storage, TTS, routes, and static mounts."""

from __future__ import annotations

import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from storage import ChatStore
from tts import OrpheusEngine
from web.context import AppContext
from web.paths import DEFAULT_DATA_DIR, SOUNDS_DIR, STATIC_DIR
from web.routes import register_routes


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    store = ChatStore(data_dir or DEFAULT_DATA_DIR)
    runtime_root = store.root / "runtime-audio"
    session_id = uuid.uuid4().hex
    runtime_dir = runtime_root / session_id

    ctx = AppContext(
        store=store,
        runtime_dir=runtime_dir,
        engine=OrpheusEngine(),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if runtime_root.exists():
            shutil.rmtree(runtime_root, ignore_errors=True)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        yield
        shutil.rmtree(runtime_root, ignore_errors=True)

    app = FastAPI(title="Orpheus Glass Chat", version="1.0.0", lifespan=lifespan)
    app.state.store = store
    app.state.runtime_dir = runtime_dir
    app.state.engine = ctx.engine
    app.state.ctx = ctx

    register_routes(app, ctx)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    if SOUNDS_DIR.is_dir():
        app.mount("/sounds", StaticFiles(directory=SOUNDS_DIR), name="sounds")

    return app
