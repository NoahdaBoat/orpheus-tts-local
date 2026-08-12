"""Streaming and non-streaming chat completions (no tools)."""

from __future__ import annotations

import json
import threading
from typing import Any, Callable

import httpx

from lm.lock import chat_model_lock
from lm.types import AgentError
from lm.util import apply_max_tokens, authorization_headers, message_content


def run_chat_completion(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    api_token: str | None = None,
    temperature: float = 0.7,
    top_p: float = 0.9,
    max_tokens: int | None = 1200,
    cancel_event: threading.Event | None = None,
    acquire_model_lock: bool = True,
) -> str:
    """Non-tool chat completion (same loaded model only)."""
    if not model:
        raise AgentError("Chat model id is required")
    endpoint = f"{base_url.rstrip('/')}/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "stream": False,
    }
    apply_max_tokens(payload, max_tokens)

    def _run() -> str:
        if cancel_event is not None and cancel_event.is_set():
            raise AgentError("Cancelled")
        with httpx.Client(timeout=httpx.Timeout(300, connect=15)) as client:
            try:
                response = client.post(
                    endpoint,
                    headers=authorization_headers(api_token),
                    json=payload,
                )
            except httpx.HTTPError as exc:
                raise AgentError(f"LM Studio request failed: {exc}") from exc
            if response.status_code >= 400:
                raise AgentError(
                    f"LM Studio returned {response.status_code}: {response.text[:800]}"
                )
            body = response.json()
            choices = body.get("choices") or []
            if not choices:
                raise AgentError("LM Studio returned no choices")
            return message_content(choices[0].get("message") or {})

    if acquire_model_lock:
        with chat_model_lock:
            return _run()
    return _run()


def stream_chat_completion(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    api_token: str | None = None,
    temperature: float = 0.7,
    top_p: float = 0.9,
    max_tokens: int | None = 1200,
    cancel_event: threading.Event | None = None,
    on_delta: Callable[[str], None] | None = None,
    acquire_model_lock: bool = True,
) -> str:
    """Stream a non-tool completion; returns full text. Holds the chat-model lock."""
    if not model:
        raise AgentError("Chat model id is required")
    endpoint = f"{base_url.rstrip('/')}/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "stream": True,
    }
    apply_max_tokens(payload, max_tokens)

    def _run() -> str:
        accumulated = ""
        with httpx.Client(timeout=httpx.Timeout(300, connect=15)) as client:
            with client.stream(
                "POST",
                endpoint,
                headers=authorization_headers(api_token),
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    detail = response.read().decode("utf-8", errors="replace")[:800]
                    raise AgentError(
                        f"LM Studio returned {response.status_code}: {detail}"
                    )
                for line in response.iter_lines():
                    if cancel_event is not None and cancel_event.is_set():
                        break
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content") or ""
                    if piece:
                        accumulated += piece
                        if on_delta:
                            on_delta(piece)
        return accumulated

    if acquire_model_lock:
        with chat_model_lock:
            return _run()
    return _run()
