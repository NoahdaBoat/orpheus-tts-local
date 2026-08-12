"""Server-sent events for Orpheus speech synthesis."""

from __future__ import annotations

import asyncio
import threading
import uuid
from typing import AsyncIterator

from tts import OrpheusError, sanitize_for_speech, split_for_speech, write_wav
from web.context import AppContext
from web.helpers import sse


async def speech_stream(
    ctx: AppContext,
    chat_id: str,
    message_id: str,
    text: str,
    cancel_event: threading.Event,
) -> AsyncIterator[str]:
    settings = ctx.store.load_settings()
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
                ctx.engine.synthesize_pcm,
                text=chunk,
                base_url=settings["base_url"],
                model=settings["tts_model"],
                voice=settings["voice"],
                temperature=settings["temperature"],
                top_p=settings["top_p"],
                repeat_penalty=settings["repeat_penalty"],
                max_tokens=tts_budget,
                api_token=ctx.api_token(),
                cancel_event=cancel_event,
            )
        except OrpheusError as exc:
            yield sse("error", {"stage": "tts", "message": str(exc)})
            return
        except (
            Exception
        ) as exc:  # noqa: BLE001 — keep SSE alive for unexpected TTS failures
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
    output_path = ctx.runtime_dir / filename
    try:
        await asyncio.to_thread(write_wav, output_path, pcm_all)
    except OSError as exc:
        yield sse(
            "error", {"stage": "tts", "message": f"Could not write speech audio: {exc}"}
        )
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
    yield sse(
        "tts_done", {"message_id": message_id, "chunks": 1, "segments": completed}
    )
