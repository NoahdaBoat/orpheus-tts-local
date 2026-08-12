"""Custom token parsing and SSE fragment extraction for Orpheus."""

from __future__ import annotations

import json
import re
from typing import Iterable, Iterator

from tts.constants import AVAILABLE_VOICES, DEFAULT_VOICE

CUSTOM_TOKEN_RE = re.compile(r"<custom_token_(\d+)>")


class CustomTokenParser:
    """Extract tokens safely even when LM Studio splits them across chunks."""

    def __init__(self) -> None:
        self.buffer = ""

    def feed(self, text: str) -> list[int]:
        self.buffer += text
        tokens: list[int] = []
        consumed = 0
        for match in CUSTOM_TOKEN_RE.finditer(self.buffer):
            tokens.append(int(match.group(1)))
            consumed = match.end()
        if consumed:
            self.buffer = self.buffer[consumed:]
        elif len(self.buffer) > 96:
            marker = self.buffer.rfind("<custom_token_")
            self.buffer = self.buffer[marker:] if marker >= 0 else self.buffer[-32:]
        return tokens


def format_prompt(text: str, voice: str = DEFAULT_VOICE) -> str:
    if voice not in AVAILABLE_VOICES:
        raise ValueError(
            f"Unknown voice '{voice}'. Choose from: {', '.join(AVAILABLE_VOICES)}"
        )
    return f"<|audio|>{voice}: {text}<|eot_id|>"


def token_id(raw_token: int, index: int) -> int:
    return raw_token - 10 - ((index % 7) * 4096)


def extract_text_from_completion_sse(lines: Iterable[str]) -> Iterator[str]:
    """Yield completion text fragments from OpenAI-compatible SSE lines."""
    for line in lines:
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choices = data.get("choices") or []
        if choices:
            text = choices[0].get("text", "")
            if text:
                yield text
