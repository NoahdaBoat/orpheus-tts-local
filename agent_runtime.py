"""OpenAI-compatible tool-calling loop against LM Studio.

Uses the already-loaded chat model only — never loads a second model.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

# Serialize chat-model traffic so the single loaded LLM is not double-booked
# (main reply vs wiki scribe). TTS uses a different model and is unaffected.
_chat_model_lock = threading.Lock()


class AgentError(RuntimeError):
    pass


@dataclass
class ToolCallEvent:
    name: str
    arguments: dict[str, Any]
    result: Any = None
    error: str | None = None


@dataclass
class AgentResult:
    content: str
    tool_events: list[ToolCallEvent] = field(default_factory=list)
    rounds: int = 0
    raw_messages: list[dict[str, Any]] = field(default_factory=list)


ToolHandler = Callable[[str, dict[str, Any]], Any]
ProgressCallback = Callable[[str, dict[str, Any]], None]


def apply_max_tokens(payload: dict[str, Any], max_tokens: int | None) -> dict[str, Any]:
    """Attach max_tokens for LM Studio. -1 / None = no limit (same as LM Studio UI)."""
    if max_tokens is None or int(max_tokens) < 0:
        payload["max_tokens"] = -1
    else:
        payload["max_tokens"] = int(max_tokens)
    return payload


def authorization_headers(api_token: str | None = None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    return headers


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {"value": raw}


def _message_content(message: dict[str, Any]) -> str:
    content = message.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return str(content)


def run_tool_loop(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_handler: ToolHandler,
    api_token: str | None = None,
    temperature: float = 0.4,
    top_p: float = 0.9,
    max_tokens: int | None = 2048,
    max_rounds: int = 16,
    cancel_event: threading.Event | None = None,
    on_progress: ProgressCallback | None = None,
    acquire_model_lock: bool = True,
) -> AgentResult:
    """Run a non-streaming tool loop. Always uses the given model id only."""
    if not model:
        raise AgentError("Chat model id is required")
    if not tools:
        raise AgentError("At least one tool is required")

    endpoint = f"{base_url.rstrip('/')}/v1/chat/completions"
    working = [dict(message) for message in messages]
    events: list[ToolCallEvent] = []
    rounds = 0

    def _run() -> AgentResult:
        nonlocal rounds
        with httpx.Client(timeout=httpx.Timeout(300, connect=15)) as client:
            while rounds < max_rounds:
                if cancel_event is not None and cancel_event.is_set():
                    raise AgentError("Cancelled")
                rounds += 1
                payload: dict[str, Any] = {
                    "model": model,
                    "messages": working,
                    "tools": tools,
                    "tool_choice": "auto",
                    "temperature": temperature,
                    "top_p": top_p,
                    "stream": False,
                }
                apply_max_tokens(payload, max_tokens)
                if on_progress:
                    on_progress("llm_request", {"round": rounds, "model": model})
                try:
                    response = client.post(
                        endpoint,
                        headers=authorization_headers(api_token),
                        json=payload,
                    )
                except httpx.HTTPError as exc:
                    raise AgentError(f"LM Studio request failed: {exc}") from exc
                if response.status_code >= 400:
                    detail = response.text[:800]
                    raise AgentError(f"LM Studio returned {response.status_code}: {detail}")
                try:
                    body = response.json()
                except json.JSONDecodeError as exc:
                    raise AgentError("LM Studio returned non-JSON response") from exc

                choices = body.get("choices") or []
                if not choices:
                    raise AgentError("LM Studio returned no choices")
                message = choices[0].get("message") or {}
                tool_calls = message.get("tool_calls") or []

                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.get("content"),
                }
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                working.append(assistant_msg)

                # Prefer executing tools whenever present (some servers omit finish_reason).
                if not tool_calls:
                    content = _message_content(message)
                    return AgentResult(
                        content=content,
                        tool_events=events,
                        rounds=rounds,
                        raw_messages=working,
                    )

                for call in tool_calls:
                    if cancel_event is not None and cancel_event.is_set():
                        raise AgentError("Cancelled")
                    function = call.get("function") or {}
                    name = function.get("name") or ""
                    arguments = _parse_tool_arguments(function.get("arguments"))
                    call_id = call.get("id") or f"call_{len(events)}"
                    if on_progress:
                        on_progress("tool_started", {"name": name, "arguments": arguments})
                    event = ToolCallEvent(name=name, arguments=arguments)
                    try:
                        result = tool_handler(name, arguments)
                        event.result = result
                        result_payload = json.dumps(result, ensure_ascii=False, default=str)
                    except Exception as exc:  # noqa: BLE001 — surface tool errors to the model
                        event.error = str(exc)
                        result_payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
                    events.append(event)
                    if on_progress:
                        on_progress(
                            "tool_done",
                            {
                                "name": name,
                                "error": event.error,
                                "summary": _tool_summary(name, event),
                            },
                        )
                    working.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": result_payload[:50_000],
                        }
                    )

            raise AgentError(f"Tool loop exceeded max rounds ({max_rounds})")

    if acquire_model_lock:
        with _chat_model_lock:
            return _run()
    return _run()


def _tool_summary(name: str, event: ToolCallEvent) -> str:
    if event.error:
        return f"{name} failed"
    result = event.result
    if isinstance(result, dict):
        if "path" in result:
            return f"{name}: {result.get('path')}"
        if "count" in result:
            return f"{name}: {result.get('count')} hits"
        if "query" in result:
            return f"{name}: {result.get('query')}"
    return name


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
                raise AgentError(f"LM Studio returned {response.status_code}: {response.text[:800]}")
            body = response.json()
            choices = body.get("choices") or []
            if not choices:
                raise AgentError("LM Studio returned no choices")
            return _message_content(choices[0].get("message") or {})

    if acquire_model_lock:
        with _chat_model_lock:
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
                    raise AgentError(f"LM Studio returned {response.status_code}: {detail}")
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
        with _chat_model_lock:
            return _run()
    return _run()
