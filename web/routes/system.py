"""Health, bootstrap, models, settings, and index routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from tts import AVAILABLE_VOICES
from web.constants import MESSAGE_SOUNDS
from web.context import AppContext
from web.helpers import model_options
from web.paths import STATIC_DIR
from web.schemas import SettingsUpdate
from wiki import VaultError, WikiVault


def register(app: FastAPI, ctx: AppContext) -> None:
    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/bootstrap")
    async def bootstrap() -> dict[str, Any]:
        settings = ctx.store.load_settings()
        wiki_status: dict[str, Any] = {
            "ok": False,
            "path": settings.get("wiki_vault_path") or "",
        }
        if settings.get("wiki_enabled") and settings.get("wiki_vault_path"):
            try:
                wiki_status = WikiVault(settings["wiki_vault_path"]).status()
            except (VaultError, OSError) as exc:
                wiki_status = {
                    "ok": False,
                    "path": settings["wiki_vault_path"],
                    "error": str(exc),
                }
        return {
            "settings": settings,
            "chats": ctx.store.list_chats(),
            "voices": list(AVAILABLE_VOICES),
            "wiki": wiki_status,
            "message_sounds": MESSAGE_SOUNDS,
        }

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        settings = ctx.store.load_settings()
        url = f"{settings['base_url'].rstrip('/')}/api/v1/models"
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.get(url, headers=ctx.lm_headers())
                response.raise_for_status()
                return model_options(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(
                status_code=503,
                detail=f"LM Studio is unavailable at {settings['base_url']}: {exc}",
            ) from exc

    @app.put("/api/settings")
    async def update_settings(body: SettingsUpdate) -> dict[str, Any]:
        updates = body.model_dump(exclude_none=True)
        if "base_url" in updates and not updates["base_url"].startswith(
            ("http://", "https://")
        ):
            raise HTTPException(
                status_code=422,
                detail="LM Studio URL must start with http:// or https://",
            )
        if "voice" in updates and updates["voice"] not in AVAILABLE_VOICES:
            raise HTTPException(status_code=422, detail="Unknown Orpheus voice")
        if "wiki_vault_path" in updates and updates["wiki_vault_path"]:
            candidate = Path(updates["wiki_vault_path"]).expanduser()
            if not candidate.exists() or not candidate.is_dir():
                raise HTTPException(
                    status_code=422,
                    detail=f"Wiki vault path must be an existing directory: {candidate}",
                )
        if "chat_max_tokens" in updates:
            limit = int(updates["chat_max_tokens"])
            if limit == 0 or (limit < -1) or (0 < limit < 32):
                raise HTTPException(
                    status_code=422,
                    detail="Chat token limit must be -1 (no limit) or at least 32",
                )
            updates["chat_max_tokens"] = limit
        if "tts_max_tokens" in updates:
            tts_limit = int(updates["tts_max_tokens"])
            if tts_limit == 0 or (tts_limit < -1) or (0 < tts_limit < 64):
                raise HTTPException(
                    status_code=422,
                    detail="TTS token limit must be -1 (no limit) or at least 64",
                )
            updates["tts_max_tokens"] = tts_limit
        valid_incoming = {item["id"] for item in MESSAGE_SOUNDS["incoming"]}
        valid_outgoing = {item["id"] for item in MESSAGE_SOUNDS["outgoing"]}
        if (
            "message_sound_incoming" in updates
            and updates["message_sound_incoming"] not in valid_incoming
        ):
            raise HTTPException(
                status_code=422, detail="Unknown incoming message sound"
            )
        if (
            "message_sound_outgoing" in updates
            and updates["message_sound_outgoing"] not in valid_outgoing
        ):
            raise HTTPException(
                status_code=422, detail="Unknown outgoing message sound"
            )
        return ctx.store.save_settings(updates)

    @app.get("/api/message-sounds")
    async def message_sounds_catalog() -> dict[str, Any]:
        return MESSAGE_SOUNDS

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")
