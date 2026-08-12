"""HTTP helpers shared by chat completions and tool loops."""

from __future__ import annotations

import json
from typing import Any


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


def parse_tool_arguments(raw: Any) -> dict[str, Any]:
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


def message_content(message: dict[str, Any]) -> str:
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
