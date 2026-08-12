"""Non-streaming OpenAI-compatible tool-calling loop against LM Studio."""

from __future__ import annotations

import json
import threading
from typing import Any

import httpx

from lm.lock import chat_model_lock
from lm.types import (
    AgentError,
    AgentResult,
    ProgressCallback,
    ToolCallEvent,
    ToolHandler,
)
from lm.util import (
    apply_max_tokens,
    authorization_headers,
    message_content,
    parse_tool_arguments,
)


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
                    raise AgentError(
                        f"LM Studio returned {response.status_code}: {detail}"
                    )
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
                    content = message_content(message)
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
                    arguments = parse_tool_arguments(function.get("arguments"))
                    call_id = call.get("id") or f"call_{len(events)}"
                    if on_progress:
                        on_progress(
                            "tool_started", {"name": name, "arguments": arguments}
                        )
                    event = ToolCallEvent(name=name, arguments=arguments)
                    try:
                        result = tool_handler(name, arguments)
                        event.result = result
                        result_payload = json.dumps(
                            result, ensure_ascii=False, default=str
                        )
                    except (
                        Exception
                    ) as exc:  # noqa: BLE001 — surface tool errors to the model
                        event.error = str(exc)
                        result_payload = json.dumps(
                            {"error": str(exc)}, ensure_ascii=False
                        )
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
        with chat_model_lock:
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
