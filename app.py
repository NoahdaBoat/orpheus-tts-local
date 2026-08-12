"""Local FastAPI web application for LM Studio chat plus Orpheus speech."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import shutil
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent_runtime import AgentError, authorization_headers, run_tool_loop, stream_chat_completion
from chat_store import ChatStore, utc_now
from folder_picker import FolderPickerError, pick_folder
from orpheus_engine import (
    AVAILABLE_VOICES,
    OrpheusEngine,
    OrpheusError,
    sanitize_for_speech,
    split_for_speech,
    write_wav,
)
from wiki_scribe import build_chat_messages_for_lm, main_read_handler, main_tool_schemas, run_scribe
from wiki_vault import VaultError, WikiVault

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
SOUNDS_DIR = ROOT / "sounds"

# Catalog of message notification MP3s under sounds/
MESSAGE_SOUNDS: dict[str, list[dict[str, str]]] = {
    "incoming": [
        {"id": "incoming1", "label": "Incoming 1", "file": "incoming1.mp3"},
        {"id": "incoming2", "label": "Incoming 2", "file": "incoming2.mp3"},
        {"id": "incoming3", "label": "Incoming 3", "file": "incoming3.mp3"},
    ],
    "outgoing": [
        {"id": "outgoing1", "label": "Outgoing 1", "file": "outgoing1.mp3"},
    ],
}

class SettingsUpdate(BaseModel):
    base_url: str | None = None
    chat_model: str | None = None
    tts_model: str | None = None
    voice: str | None = None
    system_prompt: str | None = None
    autoplay: bool | None = None
    message_sounds: bool | None = None
    message_sounds_muted: bool | None = None
    message_sound_volume: float | None = Field(default=None, ge=0, le=1)
    message_sound_incoming: str | None = None
    message_sound_outgoing: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    repeat_penalty: float | None = Field(default=None, ge=1, le=2)
    # -1 = unlimited response length (LM Studio max_tokens convention)
    chat_max_tokens: int | None = Field(default=None, ge=-1, le=32768)
    # -1 = unlimited speech generation tokens (LM Studio convention)
    tts_max_tokens: int | None = Field(default=None, ge=-1, le=32768)
    wiki_vault_path: str | None = None
    wiki_enabled: bool | None = None
    wiki_auto_on_end: bool | None = None


class ChatCreate(BaseModel):
    title: str | None = None


class ChatUpdate(BaseModel):
    title: str | None = None
    model_id: str | None = None
    system_prompt: str | None = None
    wiki_enabled: bool | None = None


def chat_wiki_active(chat: dict[str, Any], settings: dict[str, Any]) -> bool:
    """Global wiki on AND this chat has not opted out."""
    if not settings.get("wiki_enabled"):
        return False
    if not (settings.get("wiki_vault_path") or "").strip():
        return False
    return bool(chat.get("wiki_enabled", True))


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def model_options(payload: dict[str, Any]) -> dict[str, Any]:
    models: list[dict[str, Any]] = []
    for model in payload.get("models", []):
        if model.get("type") != "llm":
            continue
        instances = model.get("loaded_instances") or []
        if instances:
            for instance in instances:
                models.append(
                    {
                        "id": instance.get("id") or model.get("key"),
                        "key": model.get("key"),
                        "name": model.get("display_name") or model.get("key"),
                        "loaded": True,
                        "architecture": model.get("architecture"),
                        "context_length": (instance.get("config") or {}).get("context_length"),
                    }
                )
        else:
            models.append(
                {
                    "id": model.get("key"),
                    "key": model.get("key"),
                    "name": model.get("display_name") or model.get("key"),
                    "loaded": False,
                    "architecture": model.get("architecture"),
                    "context_length": None,
                }
            )
    models = [model for model in models if model.get("id")]
    loaded = [model for model in models if model["loaded"]]
    candidates = loaded or models
    tts = next((model["id"] for model in candidates if "orpheus" in model["id"].lower()), "")
    chat = next((model["id"] for model in candidates if "orpheus" not in model["id"].lower()), "")
    return {"models": models, "suggested_chat_model": chat, "suggested_tts_model": tts}


def extract_chat_end_text(result: dict[str, Any]) -> str:
    return "\n\n".join(
        item.get("content", "")
        for item in result.get("output", [])
        if item.get("type") == "message" and item.get("content")
    )


def vault_from_settings(settings: dict[str, Any], chat: dict[str, Any] | None = None) -> WikiVault | None:
    if chat is not None and not chat_wiki_active(chat, settings):
        return None
    if not settings.get("wiki_enabled"):
        return None
    path = (settings.get("wiki_vault_path") or "").strip()
    if not path:
        return None
    try:
        return WikiVault(path)
    except (VaultError, OSError):
        return None


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    store = ChatStore(data_dir or ROOT / "data")
    runtime_root = store.root / "runtime-audio"
    session_id = uuid.uuid4().hex
    runtime_dir = runtime_root / session_id
    jobs: dict[str, threading.Event] = {}
    jobs_lock = threading.Lock()
    wiki_jobs: dict[str, threading.Event] = {}
    wiki_jobs_lock = threading.Lock()
    engine = OrpheusEngine()

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
    app.state.engine = engine

    def lm_headers() -> dict[str, str]:
        return authorization_headers(os.getenv("LM_STUDIO_API_TOKEN"))

    def get_chat_or_404(chat_id: str) -> dict[str, Any]:
        try:
            return store.get_chat(chat_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Chat not found") from exc

    def schedule_scribe(chat_id: str) -> None:
        """Background wiki scribe using the chat's model_id only (same loaded LLM)."""
        with wiki_jobs_lock:
            if chat_id in wiki_jobs:
                return
            cancel_event = threading.Event()
            wiki_jobs[chat_id] = cancel_event

        def worker() -> None:
            try:
                chat = store.get_chat(chat_id)
                settings = store.load_settings()
                if not chat_wiki_active(chat, settings):
                    store.update_wiki_meta(
                        chat_id,
                        {
                            "last_status": "idle",
                            "last_error": None if chat.get("wiki_enabled", True) else "Wiki disabled for this chat",
                        },
                    )
                    return
                try:
                    vault = WikiVault(settings.get("wiki_vault_path") or "")
                except (VaultError, OSError) as exc:
                    store.update_wiki_meta(
                        chat_id,
                        {
                            "last_status": "error",
                            "last_error": str(exc),
                        },
                    )
                    return

                store.update_wiki_meta(
                    chat_id,
                    {"last_status": "running", "last_error": None},
                )
                # Wait until this chat is not generating (message/TTS) so we don't
                # contend with the same loaded model / session jobs map.
                while True:
                    with jobs_lock:
                        busy = chat_id in jobs
                    if not busy:
                        break
                    if cancel_event.is_set():
                        store.update_wiki_meta(chat_id, {"last_status": "idle", "last_error": "Cancelled"})
                        return
                    cancel_event.wait(0.4)

                chat_limit = settings.get("chat_max_tokens")
                if chat_limit is None or int(chat_limit) < 0:
                    scribe_max_tokens: int | None = -1
                else:
                    scribe_max_tokens = max(int(chat_limit), 1024)
                result = run_scribe(
                    chat=chat,
                    vault=vault,
                    base_url=settings["base_url"],
                    api_token=os.getenv("LM_STUDIO_API_TOKEN"),
                    temperature=min(float(settings.get("temperature") or 0.4), 0.5),
                    top_p=float(settings.get("top_p") or 0.9),
                    max_tokens=scribe_max_tokens,
                    cancel_event=cancel_event,
                )
                pages = getattr(result, "pages_touched", []) or []
                store.update_wiki_meta(
                    chat_id,
                    {
                        "last_status": "ok",
                        "last_error": None,
                        "last_synced_at": utc_now(),
                        "pages_touched": pages,
                    },
                )
            except AgentError as exc:
                store.update_wiki_meta(
                    chat_id,
                    {"last_status": "error", "last_error": str(exc)},
                )
            except Exception as exc:  # noqa: BLE001
                store.update_wiki_meta(
                    chat_id,
                    {"last_status": "error", "last_error": str(exc)},
                )
            finally:
                with wiki_jobs_lock:
                    if wiki_jobs.get(chat_id) is cancel_event:
                        wiki_jobs.pop(chat_id, None)

        threading.Thread(target=worker, name=f"wiki-scribe-{chat_id[:8]}", daemon=True).start()

    async def speech_stream(
        chat_id: str,
        message_id: str,
        text: str,
        cancel_event: threading.Event,
    ) -> AsyncIterator[str]:
        settings = store.load_settings()
        spoken = sanitize_for_speech(text)
        tts_budget = settings.get("tts_max_tokens")
        # Split only for synthesis reliability; PCM is stitched into one continuous WAV.
        chunks = split_for_speech(spoken, tts_max_tokens=tts_budget)
        if not chunks:
            yield sse("tts_done", {"message_id": message_id, "chunks": 0})
            return
        if not settings["tts_model"]:
            yield sse(
                "error",
                {"stage": "tts", "message": "Choose an Orpheus model in Settings."},
            )
            return

        yield sse(
            "tts_started",
            {
                "message_id": message_id,
                "chunks": len(chunks),
                "continuous": True,
            },
        )
        pcm_all: list[bytes] = []
        completed = 0
        for index, chunk in enumerate(chunks):
            if cancel_event.is_set():
                yield sse("tts_done", {"message_id": message_id, "cancelled": True})
                return
            yield sse(
                "tts_progress",
                {
                    "message_id": message_id,
                    "index": index,
                    "total": len(chunks),
                    "text": chunk[:120],
                },
            )
            try:
                piece = await asyncio.to_thread(
                    engine.synthesize_pcm,
                    text=chunk,
                    base_url=settings["base_url"],
                    model=settings["tts_model"],
                    voice=settings["voice"],
                    temperature=settings["temperature"],
                    top_p=settings["top_p"],
                    repeat_penalty=settings["repeat_penalty"],
                    max_tokens=tts_budget,
                    api_token=os.getenv("LM_STUDIO_API_TOKEN"),
                    cancel_event=cancel_event,
                )
            except OrpheusError as exc:
                yield sse("error", {"stage": "tts", "message": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001 — keep SSE alive for unexpected TTS failures
                yield sse(
                    "error",
                    {
                        "stage": "tts",
                        "message": f"Speech failed: {exc}",
                    },
                )
                return
            pcm_all.extend(piece)
            completed += 1

        if cancel_event.is_set():
            yield sse("tts_done", {"message_id": message_id, "cancelled": True})
            return
        if not pcm_all:
            yield sse("error", {"stage": "tts", "message": "Orpheus returned no audio."})
            return

        filename = f"{chat_id}_{message_id}_full_{uuid.uuid4().hex[:8]}.wav"
        output_path = runtime_dir / filename
        try:
            await asyncio.to_thread(write_wav, output_path, pcm_all)
        except OSError as exc:
            yield sse("error", {"stage": "tts", "message": f"Could not write speech audio: {exc}"})
            return

        yield sse(
            "audio_ready",
            {
                "message_id": message_id,
                "index": 0,
                "url": f"/api/audio/{filename}",
                "text": spoken[:200],
                "continuous": True,
                "segments": completed,
            },
        )
        yield sse("tts_done", {"message_id": message_id, "chunks": 1, "segments": completed})

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/bootstrap")
    async def bootstrap() -> dict[str, Any]:
        settings = store.load_settings()
        wiki_status: dict[str, Any] = {"ok": False, "path": settings.get("wiki_vault_path") or ""}
        if settings.get("wiki_enabled") and settings.get("wiki_vault_path"):
            try:
                wiki_status = WikiVault(settings["wiki_vault_path"]).status()
            except (VaultError, OSError) as exc:
                wiki_status = {"ok": False, "path": settings["wiki_vault_path"], "error": str(exc)}
        return {
            "settings": settings,
            "chats": store.list_chats(),
            "voices": list(AVAILABLE_VOICES),
            "wiki": wiki_status,
            "message_sounds": MESSAGE_SOUNDS,
        }

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        settings = store.load_settings()
        url = f"{settings['base_url'].rstrip('/')}/api/v1/models"
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.get(url, headers=lm_headers())
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
        if "base_url" in updates and not updates["base_url"].startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="LM Studio URL must start with http:// or https://")
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
            # Allow -1 (unlimited) or a positive generation cap; reject 0
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
        if "message_sound_incoming" in updates and updates["message_sound_incoming"] not in valid_incoming:
            raise HTTPException(status_code=422, detail="Unknown incoming message sound")
        if "message_sound_outgoing" in updates and updates["message_sound_outgoing"] not in valid_outgoing:
            raise HTTPException(status_code=422, detail="Unknown outgoing message sound")
        return store.save_settings(updates)

    @app.get("/api/wiki/status")
    async def wiki_status() -> dict[str, Any]:
        settings = store.load_settings()
        path = (settings.get("wiki_vault_path") or "").strip()
        if not path:
            return {"ok": False, "enabled": bool(settings.get("wiki_enabled")), "error": "No vault path set"}
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
        settings = store.load_settings()
        initial = (settings.get("wiki_vault_path") or "").strip() or None
        try:
            path = await asyncio.to_thread(pick_folder, initial)
        except FolderPickerError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if not path:
            return {"cancelled": True, "path": None}
        return {"cancelled": False, "path": path}

    @app.get("/api/chats")
    async def list_chats() -> list[dict[str, Any]]:
        return store.list_chats()

    @app.post("/api/chats")
    async def create_chat_endpoint(body: ChatCreate | None = None) -> dict[str, Any]:
        return store.create_chat(body.title if body else None)

    @app.get("/api/chats/{chat_id}")
    async def get_chat(chat_id: str) -> dict[str, Any]:
        return get_chat_or_404(chat_id)

    @app.patch("/api/chats/{chat_id}")
    async def update_chat(chat_id: str, body: ChatUpdate) -> dict[str, Any]:
        get_chat_or_404(chat_id)
        return store.update_chat(chat_id, body.model_dump(exclude_none=True))

    @app.delete("/api/chats/{chat_id}", status_code=204, response_class=Response)
    async def delete_chat(chat_id: str) -> Response:
        try:
            store.delete_chat(chat_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Chat not found") from exc
        return Response(status_code=204)

    @app.post("/api/chats/{chat_id}/end")
    async def end_chat(chat_id: str) -> dict[str, Any]:
        chat = get_chat_or_404(chat_id)
        with jobs_lock:
            if chat_id in jobs:
                raise HTTPException(status_code=409, detail="Stop generation before ending the conversation")
        chat = store.end_chat(chat_id)
        settings = store.load_settings()
        if chat_wiki_active(chat, settings) and settings.get("wiki_auto_on_end"):
            store.update_wiki_meta(chat_id, {"last_status": "queued", "last_error": None})
            schedule_scribe(chat_id)
            chat = store.get_chat(chat_id)
        return chat

    @app.post("/api/chats/{chat_id}/resume")
    async def resume_chat(chat_id: str) -> dict[str, Any]:
        get_chat_or_404(chat_id)
        return store.resume_chat(chat_id)

    @app.post("/api/chats/{chat_id}/wiki-sync")
    async def wiki_sync(chat_id: str) -> dict[str, Any]:
        chat = get_chat_or_404(chat_id)
        settings = store.load_settings()
        if not settings.get("wiki_enabled"):
            raise HTTPException(status_code=422, detail="Enable the wiki in Settings first")
        if not settings.get("wiki_vault_path"):
            raise HTTPException(status_code=422, detail="Set a wiki vault path in Settings")
        if not chat.get("wiki_enabled", True):
            raise HTTPException(status_code=422, detail="Wiki is disabled for this conversation")
        with jobs_lock:
            if chat_id in jobs:
                raise HTTPException(status_code=409, detail="Wait for generation to finish")
        with wiki_jobs_lock:
            if chat_id in wiki_jobs:
                raise HTTPException(status_code=409, detail="Wiki sync already running")
        store.update_wiki_meta(chat_id, {"last_status": "queued", "last_error": None})
        schedule_scribe(chat_id)
        return store.get_chat(chat_id)

    @app.post("/api/chats/{chat_id}/messages")
    async def create_message(chat_id: str, body: MessageCreate, request: Request) -> StreamingResponse:
        chat = get_chat_or_404(chat_id)
        if chat.get("status") == "ended":
            raise HTTPException(
                status_code=409,
                detail="This conversation is ended. Resume it before sending messages.",
            )
        if not chat.get("model_id"):
            raise HTTPException(status_code=422, detail="Choose a chat model before sending a message")
        with jobs_lock:
            if chat_id in jobs:
                raise HTTPException(status_code=409, detail="This chat is already generating")
            cancel_event = threading.Event()
            jobs[chat_id] = cancel_event

        content = body.content.strip()
        now = utc_now()
        user_message = {"id": str(uuid.uuid4()), "role": "user", "content": content, "created_at": now}
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
        store.save_chat(chat)

        async def stream() -> AsyncIterator[str]:
            accumulated = ""
            settings = store.load_settings()
            # Re-read chat so per-chat wiki_enabled stays accurate mid-stream
            live_chat = store.get_chat(chat_id)
            vault = vault_from_settings(settings, live_chat)
            use_tools = vault is not None

            yield sse("message_started", {"user": user_message, "assistant": assistant})
            try:
                if use_tools:
                    # Tool loop is non-streaming; emit tool events then final text.
                    lm_messages = build_chat_messages_for_lm(live_chat, wiki_enabled=True)
                    # Last assistant is the empty streaming stub — drop it for the LM.
                    if lm_messages and lm_messages[-1].get("role") == "assistant" and not lm_messages[-1].get("content"):
                        lm_messages = lm_messages[:-1]

                    event_q: queue.Queue[tuple[str, Any] | None] = queue.Queue()

                    def on_progress(kind: str, data: dict[str, Any]) -> None:
                        event_q.put((kind, data))

                    def producer() -> None:
                        try:
                            result = run_tool_loop(
                                base_url=settings["base_url"],
                                model=chat["model_id"],
                                messages=lm_messages,
                                tools=main_tool_schemas(),
                                tool_handler=main_read_handler(vault),
                                api_token=os.getenv("LM_STUDIO_API_TOKEN"),
                                temperature=float(settings["temperature"]),
                                top_p=float(settings["top_p"]),
                                max_tokens=int(settings.get("chat_max_tokens") if settings.get("chat_max_tokens") is not None else 1200),
                                max_rounds=12,
                                cancel_event=cancel_event,
                                on_progress=on_progress,
                                acquire_model_lock=True,
                            )
                            event_q.put(("final", result))
                        except Exception as exc:  # noqa: BLE001
                            event_q.put(("error", str(exc)))
                        finally:
                            event_q.put(None)

                    thread = threading.Thread(target=producer, daemon=True)
                    thread.start()
                    tool_trace: list[dict[str, Any]] = []
                    final_content = ""
                    while True:
                        if await request.is_disconnected():
                            cancel_event.set()
                        item = await asyncio.to_thread(event_q.get)
                        if item is None:
                            break
                        kind, data = item
                        if kind == "tool_started":
                            yield sse(
                                "tool_started",
                                {
                                    "message_id": assistant["id"],
                                    "name": data.get("name"),
                                },
                            )
                        elif kind == "tool_done":
                            entry = {
                                "name": data.get("name"),
                                "summary": data.get("summary"),
                                "error": data.get("error"),
                            }
                            tool_trace.append(entry)
                            yield sse("tool_done", {"message_id": assistant["id"], **entry})
                        elif kind == "final":
                            final_content = data.content or ""
                            tool_trace = [
                                {
                                    "name": event.name,
                                    "summary": event.name if not event.error else f"{event.name} failed",
                                    "error": event.error,
                                }
                                for event in data.tool_events
                            ] or tool_trace
                        elif kind == "error":
                            raise RuntimeError(str(data))
                    thread.join(timeout=1)
                    accumulated = final_content
                    assistant["content"] = accumulated
                    assistant["tool_trace"] = tool_trace
                    if accumulated:
                        # Fake stream for UI smoothness
                        step = max(24, len(accumulated) // 40)
                        for index in range(0, len(accumulated), step):
                            if cancel_event.is_set():
                                break
                            piece = accumulated[index : index + step]
                            yield sse("assistant_delta", {"message_id": assistant["id"], "content": piece})
                            await asyncio.sleep(0)
                else:
                    # Completions API with local history (unified path, no second model).
                    lm_messages = build_chat_messages_for_lm(chat, wiki_enabled=False)
                    if lm_messages and lm_messages[-1].get("role") == "assistant" and not lm_messages[-1].get("content"):
                        lm_messages = lm_messages[:-1]

                    delta_q: queue.Queue[str | None] = queue.Queue()
                    error_box: list[str] = []

                    def on_delta(piece: str) -> None:
                        delta_q.put(piece)

                    def producer() -> None:
                        try:
                            text = stream_chat_completion(
                                base_url=settings["base_url"],
                                model=chat["model_id"],
                                messages=lm_messages,
                                api_token=os.getenv("LM_STUDIO_API_TOKEN"),
                                temperature=float(settings["temperature"]),
                                top_p=float(settings["top_p"]),
                                max_tokens=int(settings.get("chat_max_tokens") if settings.get("chat_max_tokens") is not None else 1200),
                                cancel_event=cancel_event,
                                on_delta=on_delta,
                                acquire_model_lock=True,
                            )
                            delta_q.put(None)
                            # Store full text if stream pieces were incomplete
                            if text and not accumulated_holder[0]:
                                accumulated_holder[0] = text
                        except Exception as exc:  # noqa: BLE001
                            error_box.append(str(exc))
                            delta_q.put(None)

                    accumulated_holder = [""]
                    thread = threading.Thread(target=producer, daemon=True)
                    thread.start()
                    while True:
                        if await request.is_disconnected():
                            cancel_event.set()
                        piece = await asyncio.to_thread(delta_q.get)
                        if piece is None:
                            break
                        accumulated += piece
                        accumulated_holder[0] = accumulated
                        assistant["content"] = accumulated
                        yield sse("assistant_delta", {"message_id": assistant["id"], "content": piece})
                    thread.join(timeout=1)
                    if error_box:
                        raise RuntimeError(error_box[0])
                    if not accumulated and accumulated_holder[0]:
                        accumulated = accumulated_holder[0]
                        assistant["content"] = accumulated

                if cancel_event.is_set() and not accumulated:
                    assistant["status"] = "cancelled"
                elif cancel_event.is_set():
                    assistant["status"] = "cancelled"
                    assistant["content"] = accumulated
                else:
                    assistant["status"] = "complete"
                    assistant["content"] = accumulated
                store.save_chat(chat)
                yield sse(
                    "assistant_done",
                    {
                        "message": assistant,
                        "chat": {
                            "id": chat["id"],
                            "title": chat["title"],
                            "updated_at": chat["updated_at"],
                            "status": chat.get("status", "open"),
                        },
                    },
                )

                if assistant["status"] == "complete" and assistant["content"] and not cancel_event.is_set():
                    async for event in speech_stream(
                        chat_id, assistant["id"], assistant["content"], cancel_event
                    ):
                        yield event
            except (httpx.HTTPError, RuntimeError, AgentError) as exc:
                assistant["content"] = accumulated
                assistant["status"] = "error"
                assistant["error"] = str(exc)
                store.save_chat(chat)
                yield sse("error", {"stage": "chat", "message": str(exc), "message_id": assistant["id"]})
                yield sse("assistant_done", {"message": assistant})
            finally:
                with jobs_lock:
                    if jobs.get(chat_id) is cancel_event:
                        jobs.pop(chat_id, None)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/chats/{chat_id}/messages/{message_id}/speech")
    async def replay_speech(chat_id: str, message_id: str) -> StreamingResponse:
        chat = get_chat_or_404(chat_id)
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
        with jobs_lock:
            if chat_id in jobs:
                raise HTTPException(status_code=409, detail="This chat is already generating")
            cancel_event = threading.Event()
            jobs[chat_id] = cancel_event

        async def stream() -> AsyncIterator[str]:
            try:
                async for event in speech_stream(chat_id, message_id, message["content"], cancel_event):
                    yield event
            finally:
                with jobs_lock:
                    if jobs.get(chat_id) is cancel_event:
                        jobs.pop(chat_id, None)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/chats/{chat_id}/cancel")
    async def cancel(chat_id: str) -> dict[str, bool]:
        with jobs_lock:
            event = jobs.get(chat_id)
        if event:
            event.set()
        return {"cancelled": bool(event)}

    @app.get("/api/audio/{filename}")
    async def audio(filename: str) -> FileResponse:
        if Path(filename).name != filename:
            raise HTTPException(status_code=404, detail="Audio not found")
        path = runtime_dir / filename
        if not path.exists() or path.suffix.lower() != ".wav":
            raise HTTPException(status_code=404, detail="Audio not found")
        return FileResponse(path, media_type="audio/wav", filename=filename)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    if SOUNDS_DIR.is_dir():
        app.mount("/sounds", StaticFiles(directory=SOUNDS_DIR), name="sounds")

    @app.get("/api/message-sounds")
    async def message_sounds_catalog() -> dict[str, Any]:
        return MESSAGE_SOUNDS

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
