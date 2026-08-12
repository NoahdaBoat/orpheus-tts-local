"""Small pure helpers shared by routes and services."""

from __future__ import annotations

import json
from typing import Any

from wiki import VaultError, WikiVault


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def chat_wiki_active(chat: dict[str, Any], settings: dict[str, Any]) -> bool:
    """Global wiki on AND this chat has not opted out."""
    if not settings.get("wiki_enabled"):
        return False
    if not (settings.get("wiki_vault_path") or "").strip():
        return False
    return bool(chat.get("wiki_enabled", True))


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
                        "context_length": (instance.get("config") or {}).get(
                            "context_length"
                        ),
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
    tts = next(
        (model["id"] for model in candidates if "orpheus" in model["id"].lower()), ""
    )
    chat = next(
        (model["id"] for model in candidates if "orpheus" not in model["id"].lower()),
        "",
    )
    return {"models": models, "suggested_chat_model": chat, "suggested_tts_model": tts}


def extract_chat_end_text(result: dict[str, Any]) -> str:
    return "\n\n".join(
        item.get("content", "")
        for item in result.get("output", [])
        if item.get("type") == "message" and item.get("content")
    )


def vault_from_settings(
    settings: dict[str, Any], chat: dict[str, Any] | None = None
) -> WikiVault | None:
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
