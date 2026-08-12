"""Streaming chat reply generation (with optional wiki tools + TTS)."""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import Any, AsyncIterator

import httpx
from fastapi import Request

from lm import AgentError, run_tool_loop, stream_chat_completion
from web.context import AppContext
from web.helpers import sse, vault_from_settings
from web.services.speech import speech_stream
from wiki import (
    WikiVault,
    build_chat_messages_for_lm,
    main_read_handler,
    main_tool_schemas,
)

# Internal marker prefix so path helpers can return final text through the same iterator.
_CONTENT_PREFIX = "\0CONTENT:"


def _chat_max_tokens(settings: dict[str, Any], default: int = 1200) -> int:
    """Coerce settings chat_max_tokens to int; None falls back to default."""
    raw = settings.get("chat_max_tokens")
    if raw is None:
        return default
    return int(raw)


async def stream_assistant_reply(
    ctx: AppContext,
    *,
    chat: dict[str, Any],
    chat_id: str,
    user_message: dict[str, Any],
    assistant: dict[str, Any],
    cancel_event: threading.Event,
    request: Request,
) -> AsyncIterator[str]:
    accumulated = ""
    settings = ctx.store.load_settings()
    live_chat = ctx.store.get_chat(chat_id)
    vault = vault_from_settings(settings, live_chat)
    use_tools = vault is not None

    yield sse("message_started", {"user": user_message, "assistant": assistant})
    try:
        path = (
            _run_with_tools(
                ctx,
                chat=chat,
                assistant=assistant,
                live_chat=live_chat,
                vault=vault,
                settings=settings,
                cancel_event=cancel_event,
                request=request,
            )
            if use_tools
            else _run_streaming(
                chat=chat,
                assistant=assistant,
                settings=settings,
                cancel_event=cancel_event,
                request=request,
                api_token=ctx.api_token(),
            )
        )
        async for event in path:
            if event.startswith(_CONTENT_PREFIX):
                accumulated = event[len(_CONTENT_PREFIX) :]
            else:
                yield event

        if cancel_event.is_set():
            assistant["status"] = "cancelled"
            assistant["content"] = accumulated
        else:
            assistant["status"] = "complete"
            assistant["content"] = accumulated
        ctx.store.save_chat(chat)
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

        if (
            assistant["status"] == "complete"
            and assistant["content"]
            and not cancel_event.is_set()
        ):
            async for event in speech_stream(
                ctx, chat_id, assistant["id"], assistant["content"], cancel_event
            ):
                yield event
    except (httpx.HTTPError, RuntimeError, AgentError) as exc:
        assistant["content"] = accumulated
        assistant["status"] = "error"
        assistant["error"] = str(exc)
        ctx.store.save_chat(chat)
        yield sse(
            "error",
            {"stage": "chat", "message": str(exc), "message_id": assistant["id"]},
        )
        yield sse("assistant_done", {"message": assistant})
    finally:
        with ctx.jobs_lock:
            if ctx.jobs.get(chat_id) is cancel_event:
                ctx.jobs.pop(chat_id, None)


async def _run_with_tools(
    ctx: AppContext,
    *,
    chat: dict[str, Any],
    assistant: dict[str, Any],
    live_chat: dict[str, Any],
    vault: WikiVault,
    settings: dict[str, Any],
    cancel_event: threading.Event,
    request: Request,
) -> AsyncIterator[str]:
    """Non-streaming tool loop; emit tool events then fake-stream final text."""
    lm_messages = build_chat_messages_for_lm(live_chat, wiki_enabled=True)
    if (
        lm_messages
        and lm_messages[-1].get("role") == "assistant"
        and not lm_messages[-1].get("content")
    ):
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
                api_token=ctx.api_token(),
                temperature=float(settings["temperature"]),
                top_p=float(settings["top_p"]),
                max_tokens=_chat_max_tokens(settings),
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
                    "summary": (
                        event.name if not event.error else f"{event.name} failed"
                    ),
                    "error": event.error,
                }
                for event in data.tool_events
            ] or tool_trace
        elif kind == "error":
            raise RuntimeError(str(data))
    thread.join(timeout=1)

    assistant["content"] = final_content
    assistant["tool_trace"] = tool_trace
    if final_content:
        step = max(24, len(final_content) // 40)
        for index in range(0, len(final_content), step):
            if cancel_event.is_set():
                break
            piece = final_content[index : index + step]
            yield sse(
                "assistant_delta", {"message_id": assistant["id"], "content": piece}
            )
            await asyncio.sleep(0)
    yield f"{_CONTENT_PREFIX}{final_content}"


async def _run_streaming(
    *,
    chat: dict[str, Any],
    assistant: dict[str, Any],
    settings: dict[str, Any],
    cancel_event: threading.Event,
    request: Request,
    api_token: str | None,
) -> AsyncIterator[str]:
    """Token-streaming completion without wiki tools."""
    lm_messages = build_chat_messages_for_lm(chat, wiki_enabled=False)
    if (
        lm_messages
        and lm_messages[-1].get("role") == "assistant"
        and not lm_messages[-1].get("content")
    ):
        lm_messages = lm_messages[:-1]

    delta_q: queue.Queue[str | None] = queue.Queue()
    error_box: list[str] = []
    accumulated_holder = [""]
    accumulated = ""

    def on_delta(piece: str) -> None:
        delta_q.put(piece)

    def producer() -> None:
        try:
            text = stream_chat_completion(
                base_url=settings["base_url"],
                model=chat["model_id"],
                messages=lm_messages,
                api_token=api_token,
                temperature=float(settings["temperature"]),
                top_p=float(settings["top_p"]),
                max_tokens=_chat_max_tokens(settings),
                cancel_event=cancel_event,
                on_delta=on_delta,
                acquire_model_lock=True,
            )
            delta_q.put(None)
            if text and not accumulated_holder[0]:
                accumulated_holder[0] = text
        except Exception as exc:  # noqa: BLE001
            error_box.append(str(exc))
            delta_q.put(None)

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
    yield f"{_CONTENT_PREFIX}{accumulated}"
