"""Atomic JSON persistence for local chat history and settings."""

from __future__ import annotations

import copy
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_SETTINGS: dict[str, Any] = {
    "base_url": "http://127.0.0.1:1234",
    "chat_model": "",
    "tts_model": "",
    "voice": "tara",
    "system_prompt": "",
    "autoplay": True,
    "message_sounds": True,
    "message_sounds_muted": False,
    "message_sound_volume": 0.7,
    "message_sound_incoming": "incoming1",
    "message_sound_outgoing": "outgoing1",
    "temperature": 0.7,
    "top_p": 0.9,
    "repeat_penalty": 1.1,
    # -1 means unlimited generation length (LM Studio max_tokens convention)
    "chat_max_tokens": 1200,
    "tts_max_tokens": 1200,  # also -1 for unlimited Orpheus audio tokens
    "wiki_vault_path": "",
    "wiki_enabled": False,
    "wiki_auto_on_end": True,
}

# Older installs used brand-named sound IDs; map them to the public catalog.
_LEGACY_SOUND_IDS: dict[str, str] = {
    "whatsapp_incoming1": "incoming1",
}

DEFAULT_WIKI: dict[str, Any] = {
    "last_synced_at": None,
    "last_status": "idle",
    "last_error": None,
    "pages_touched": [],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def normalize_chat(chat: dict[str, Any]) -> dict[str, Any]:
    """Backfill lifecycle / wiki fields for older chat files."""
    chat.setdefault("status", "open")
    chat.setdefault("ended_at", None)
    # Per-chat wiki opt-in; when false, this conversation never reads/writes the vault
    # even if global wiki is enabled. Defaults to True so global settings control it.
    if "wiki_enabled" not in chat:
        chat["wiki_enabled"] = True
    else:
        chat["wiki_enabled"] = bool(chat["wiki_enabled"])
    wiki = chat.get("wiki")
    if not isinstance(wiki, dict):
        chat["wiki"] = copy.deepcopy(DEFAULT_WIKI)
    else:
        for key, value in DEFAULT_WIKI.items():
            wiki.setdefault(
                key, copy.deepcopy(value) if isinstance(value, list) else value
            )
    return chat


def last_message_preview(chat: dict[str, Any], limit: int = 80) -> str:
    messages = chat.get("messages") or []
    for item in reversed(messages):
        content = (item.get("content") or "").strip().replace("\n", " ")
        if content:
            return content[: limit - 1] + ("…" if len(content) > limit else "")
    return ""


class ChatStore:
    def __init__(self, root: str | Path = "data") -> None:
        self.root = Path(root)
        self.chats_dir = self.root / "chats"
        self.settings_path = self.root / "settings.json"
        self._lock = threading.RLock()
        self.chats_dir.mkdir(parents=True, exist_ok=True)

    def load_settings(self) -> dict[str, Any]:
        with self._lock:
            settings = copy.deepcopy(DEFAULT_SETTINGS)
            if self.settings_path.exists():
                try:
                    saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
                    if isinstance(saved, dict):
                        settings.update(
                            {
                                key: value
                                for key, value in saved.items()
                                if key in settings
                            }
                        )
                except (json.JSONDecodeError, OSError):
                    pass
            for key in ("message_sound_incoming", "message_sound_outgoing"):
                mapped = _LEGACY_SOUND_IDS.get(str(settings.get(key) or ""))
                if mapped:
                    settings[key] = mapped
            return settings

    def save_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            settings = self.load_settings()
            for key, value in updates.items():
                if key not in DEFAULT_SETTINGS:
                    continue
                if key in (
                    "wiki_enabled",
                    "wiki_auto_on_end",
                    "autoplay",
                    "message_sounds",
                    "message_sounds_muted",
                ):
                    settings[key] = bool(value)
                elif key == "wiki_vault_path":
                    settings[key] = str(value or "").strip()
                elif key == "message_sound_volume":
                    try:
                        settings[key] = max(0.0, min(1.0, float(value)))
                    except (TypeError, ValueError):
                        settings[key] = DEFAULT_SETTINGS["message_sound_volume"]
                elif key in ("message_sound_incoming", "message_sound_outgoing"):
                    settings[key] = str(value or "").strip() or DEFAULT_SETTINGS[key]
                else:
                    settings[key] = value
            atomic_write_json(self.settings_path, settings)
            return settings

    def _path(self, chat_id: str) -> Path:
        try:
            safe_id = str(uuid.UUID(chat_id))
        except ValueError as exc:
            raise KeyError(chat_id) from exc
        return self.chats_dir / f"{safe_id}.json"

    def create_chat(self, title: str | None = None) -> dict[str, Any]:
        with self._lock:
            settings = self.load_settings()
            now = utc_now()
            chat = {
                "id": str(uuid.uuid4()),
                "title": (title or "New conversation").strip()[:80]
                or "New conversation",
                "created_at": now,
                "updated_at": now,
                "model_id": settings["chat_model"],
                "system_prompt": settings["system_prompt"],
                "previous_response_id": None,
                "status": "open",
                "ended_at": None,
                "wiki_enabled": True,
                "wiki": copy.deepcopy(DEFAULT_WIKI),
                "messages": [],
            }
            atomic_write_json(self._path(str(chat["id"])), chat)
            return chat

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        with self._lock:
            path = self._path(chat_id)
            if not path.exists():
                raise KeyError(chat_id)
            chat = json.loads(path.read_text(encoding="utf-8"))
            return normalize_chat(chat)

    def save_chat(self, chat: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            chat = normalize_chat(chat)
            chat["updated_at"] = utc_now()
            atomic_write_json(self._path(str(chat["id"])), chat)
            return chat

    def list_chats(self) -> list[dict[str, Any]]:
        chats: list[dict[str, Any]] = []
        with self._lock:
            for path in self.chats_dir.glob("*.json"):
                try:
                    chat = normalize_chat(json.loads(path.read_text(encoding="utf-8")))
                    wiki = chat.get("wiki") or {}
                    chats.append(
                        {
                            "id": chat["id"],
                            "title": chat.get("title", "Untitled"),
                            "created_at": chat.get("created_at"),
                            "updated_at": chat.get("updated_at"),
                            "message_count": len(chat.get("messages", [])),
                            "status": chat.get("status", "open"),
                            "ended_at": chat.get("ended_at"),
                            "preview": last_message_preview(chat),
                            "wiki_enabled": bool(chat.get("wiki_enabled", True)),
                            "wiki_status": wiki.get("last_status", "idle"),
                        }
                    )
                except (json.JSONDecodeError, KeyError, OSError):
                    continue
        return sorted(
            chats, key=lambda item: item.get("updated_at") or "", reverse=True
        )

    def update_chat(self, chat_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            chat = self.get_chat(chat_id)
            if "title" in updates:
                chat["title"] = str(updates["title"]).strip()[:80] or chat["title"]
            if "wiki_enabled" in updates:
                chat["wiki_enabled"] = bool(updates["wiki_enabled"])
            if not chat.get("messages"):
                if "model_id" in updates:
                    chat["model_id"] = str(updates["model_id"])
                if "system_prompt" in updates:
                    chat["system_prompt"] = str(updates["system_prompt"])
            return self.save_chat(chat)

    def end_chat(self, chat_id: str) -> dict[str, Any]:
        with self._lock:
            chat = self.get_chat(chat_id)
            chat["status"] = "ended"
            chat["ended_at"] = utc_now()
            return self.save_chat(chat)

    def resume_chat(self, chat_id: str) -> dict[str, Any]:
        with self._lock:
            chat = self.get_chat(chat_id)
            chat["status"] = "open"
            chat["ended_at"] = None
            return self.save_chat(chat)

    def update_wiki_meta(self, chat_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            chat = self.get_chat(chat_id)
            wiki = chat.setdefault("wiki", copy.deepcopy(DEFAULT_WIKI))
            for key, value in updates.items():
                if key in DEFAULT_WIKI or key in wiki:
                    wiki[key] = value
            return self.save_chat(chat)

    def delete_chat(self, chat_id: str) -> None:
        with self._lock:
            path = self._path(chat_id)
            if not path.exists():
                raise KeyError(chat_id)
            path.unlink()
