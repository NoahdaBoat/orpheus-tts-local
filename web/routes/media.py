"""Generated audio file serving."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from web.context import AppContext


def register(app: FastAPI, ctx: AppContext) -> None:
    @app.get("/api/audio/{filename}")
    async def audio(filename: str) -> FileResponse:
        if Path(filename).name != filename:
            raise HTTPException(status_code=404, detail="Audio not found")
        path = ctx.runtime_dir / filename
        if not path.exists() or path.suffix.lower() != ".wav":
            raise HTTPException(status_code=404, detail="Audio not found")
        return FileResponse(path, media_type="audio/wav", filename=filename)
