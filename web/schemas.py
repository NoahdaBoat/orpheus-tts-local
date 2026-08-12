"""Pydantic request bodies for the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field


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


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
