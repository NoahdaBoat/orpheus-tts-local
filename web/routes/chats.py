"""Chat CRUD, lifecycle, messaging, and cancel routes."""

from __future__ import annotations

import threading
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from storage import utc_now
from web.context import AppContext
from web.helpers import chat_wiki_active
from web.schemas import ChatCreate, ChatUpdate, MessageCreate
from web.services.chat_stream import stream_assistant_reply
from web.services.speech import speech_stream
from web.services.wiki_jobs import schedule_scribe


def _get_chat_or_404(ctx: AppContext, chat_id: str) -> dict[str, Any]:
    try:
        return ctx.store.get_chat(chat_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chat not found") from exc


def register(app: FastAPI, ctx: AppContext) -> None:
    @app.get("/api/chats")
    async def list_chats() -> list[dict[str, Any]]:
        return ctx.store.list_chats()

    @app.post("/api/chats")
    async def create_chat_endpoint(body: ChatCreate | None = None) -> dict[str, Any]:
        return ctx.store.create_chat(body.title if body else None)

    @app.get("/api/chats/{chat_id}")
    async def get_chat(chat_id: str) -> dict[str, Any]:
        return _get_chat_or_404(ctx, chat_id)

    @app.patch("/api/chats/{chat_id}")
    async def update_chat(chat_id: str, body: ChatUpdate) -> dict[str, Any]:
        _get_chat_or_404(ctx, chat_id)
        return ctx.store.update_chat(chat_id, body.model_dump(exclude_none=True))

    @app.delete("/api/chats/{chat_id}", status_code=204, response_class=Response)
    async def delete_chat(chat_id: str) -> Response:
        try:
            ctx.store.delete_chat(chat_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Chat not found") from exc
        return Response(status_code=204)

    @app.post("/api/chats/{chat_id}/end")
    async def end_chat(chat_id: str) -> dict[str, Any]:
        chat = _get_chat_or_404(ctx, chat_id)
        with ctx.jobs_lock:
            if chat_id in ctx.jobs:
                raise HTTPException(
                    status_code=409,
                    detail="Stop generation before ending the conversation",
                )
        chat = ctx.store.end_chat(chat_id)
        settings = ctx.store.load_settings()
        if chat_wiki_active(chat, settings) and settings.get("wiki_auto_on_end"):
            ctx.store.update_wiki_meta(
                chat_id, {"last_status": "queued", "last_error": None}
            )
            schedule_scribe(ctx, chat_id)
            chat = ctx.store.get_chat(chat_id)
        return chat

    @app.post("/api/chats/{chat_id}/resume")
    async def resume_chat(chat_id: str) -> dict[str, Any]:
        _get_chat_or_404(ctx, chat_id)
        return ctx.store.resume_chat(chat_id)

    @app.post("/api/chats/{chat_id}/wiki-sync")
    async def wiki_sync(chat_id: str) -> dict[str, Any]:
        chat = _get_chat_or_404(ctx, chat_id)
        settings = ctx.store.load_settings()
        if not settings.get("wiki_enabled"):
            raise HTTPException(
                status_code=422, detail="Enable the wiki in Settings first"
            )
        if not settings.get("wiki_vault_path"):
            raise HTTPException(
                status_code=422, detail="Set a wiki vault path in Settings"
            )
        if not chat.get("wiki_enabled", True):
            raise HTTPException(
                status_code=422, detail="Wiki is disabled for this conversation"
            )
        with ctx.jobs_lock:
            if chat_id in ctx.jobs:
                raise HTTPException(
                    status_code=409, detail="Wait for generation to finish"
                )
        with ctx.wiki_jobs_lock:
            if chat_id in ctx.wiki_jobs:
                raise HTTPException(status_code=409, detail="Wiki sync already running")
        ctx.store.update_wiki_meta(
            chat_id, {"last_status": "queued", "last_error": None}
        )
        schedule_scribe(ctx, chat_id)
        return ctx.store.get_chat(chat_id)

    @app.post("/api/chats/{chat_id}/messages")
    async def create_message(
        chat_id: str, body: MessageCreate, request: Request
    ) -> StreamingResponse:
        chat = _get_chat_or_404(ctx, chat_id)
        if chat.get("status") == "ended":
            raise HTTPException(
                status_code=409,
                detail="This conversation is ended. Resume it before sending messages.",
            )
        if not chat.get("model_id"):
            raise HTTPException(
                status_code=422, detail="Choose a chat model before sending a message"
            )
        with ctx.jobs_lock:
            if chat_id in ctx.jobs:
                raise HTTPException(
                    status_code=409, detail="This chat is already generating"
                )
            cancel_event = threading.Event()
            ctx.jobs[chat_id] = cancel_event

        content = body.content.strip()
        now = utc_now()
        user_message = {
            "id": str(uuid.uuid4()),
            "role": "user",
            "content": content,
            "created_at": now,
        }
        assistant = {
            "id": str(uuid.uuid4()),
            "role": "assistant",
            "content": "",
            "created_at": now,
            "status": "streaming",
            "stats": {},
            "tool_trace": [],
        }
        if not chat.get("messages") and chat.get("title") == "New conversation":
            chat["title"] = content[:57] + ("…" if len(content) > 57 else "")
        chat.setdefault("messages", []).extend((user_message, assistant))
        ctx.store.save_chat(chat)

        async def stream():
            async for event in stream_assistant_reply(
                ctx,
                chat=chat,
                chat_id=chat_id,
                user_message=user_message,
                assistant=assistant,
                cancel_event=cancel_event,
                request=request,
            ):
                yield event

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/chats/{chat_id}/messages/{message_id}/speech")
    async def replay_speech(chat_id: str, message_id: str) -> StreamingResponse:
        chat = _get_chat_or_404(ctx, chat_id)
        message = next(
            (
                item
                for item in chat.get("messages", [])
                if item.get("id") == message_id and item.get("role") == "assistant"
            ),
            None,
        )
        if not message or not message.get("content"):
            raise HTTPException(status_code=404, detail="Assistant message not found")
        with ctx.jobs_lock:
            if chat_id in ctx.jobs:
                raise HTTPException(
                    status_code=409, detail="This chat is already generating"
                )
            cancel_event = threading.Event()
            ctx.jobs[chat_id] = cancel_event

        async def stream():
            try:
                async for event in speech_stream(
                    ctx, chat_id, message_id, message["content"], cancel_event
                ):
                    yield event
            finally:
                with ctx.jobs_lock:
                    if ctx.jobs.get(chat_id) is cancel_event:
                        ctx.jobs.pop(chat_id, None)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/chats/{chat_id}/cancel")
    async def cancel(chat_id: str) -> dict[str, bool]:
        with ctx.jobs_lock:
            event = ctx.jobs.get(chat_id)
        if event:
            event.set()
        return {"cancelled": bool(event)}
