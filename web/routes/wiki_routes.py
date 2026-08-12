"""Wiki status and vault folder picker routes."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException

from folder_picker import FolderPickerError, pick_folder
from web.context import AppContext
from wiki import VaultError, WikiVault


def register(app: FastAPI, ctx: AppContext) -> None:
    @app.get("/api/wiki/status")
    async def wiki_status() -> dict[str, Any]:
        settings = ctx.store.load_settings()
        path = (settings.get("wiki_vault_path") or "").strip()
        if not path:
            return {
                "ok": False,
                "enabled": bool(settings.get("wiki_enabled")),
                "error": "No vault path set",
            }
        try:
            status = WikiVault(path).status()
            status["enabled"] = bool(settings.get("wiki_enabled"))
            status["auto_on_end"] = bool(settings.get("wiki_auto_on_end"))
            return status
        except (VaultError, OSError) as exc:
            return {
                "ok": False,
                "enabled": bool(settings.get("wiki_enabled")),
                "path": path,
                "error": str(exc),
            }

    @app.post("/api/wiki/pick-folder")
    async def wiki_pick_folder() -> dict[str, Any]:
        """Open a native OS folder dialog; browsers cannot return absolute paths."""
        settings = ctx.store.load_settings()
        initial = (settings.get("wiki_vault_path") or "").strip() or None
        try:
            path = await asyncio.to_thread(pick_folder, initial)
        except FolderPickerError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if not path:
            return {"cancelled": True, "path": None}
        return {"cancelled": False, "path": path}
