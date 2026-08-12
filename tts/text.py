"""Markdown cleanup and punctuation-aware chunking for speech."""

from __future__ import annotations

import html
import re


def sanitize_for_speech(markdown: str) -> str:
    """Turn assistant Markdown into useful spoken prose."""
    text = re.sub(r"```[\s\S]*?```", " ", markdown)
    text = re.sub(r"`[^`]+`", " ", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\((?:https?://|mailto:)[^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"^\s{0,3}(?:#{1,6}|>|[-*+] |\d+[.)] )", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_~]", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def speech_chunk_bounds(tts_max_tokens: int | None) -> tuple[int, int]:
    """Choose text chunk sizes from the TTS generation token budget.

    Orpheus emits many audio tokens per second of speech, so a low max_tokens
    forces short text pieces. Unlimited (-1) or large budgets allow longer spans.
    """
    if tts_max_tokens is None or int(tts_max_tokens) < 0:
        return 900, 1400
    tokens = int(tts_max_tokens)
    if tokens >= 4096:
        return 700, 1000
    if tokens >= 2400:
        return 500, 700
    if tokens >= 1600:
        return 400, 520
    return 350, 440


def split_for_speech(
    text: str,
    target: int = 350,
    maximum: int = 440,
    *,
    tts_max_tokens: int | None = None,
) -> list[str]:
    """Create punctuation-aware chunks while keeping every chunk bounded.

    Chunking is for model reliability / token budget, not for playback. Callers
    should stitch PCM into one continuous WAV when they want seamless audio.
    """
    if tts_max_tokens is not None:
        target, maximum = speech_chunk_bounds(tts_max_tokens)

    cleaned = text.strip()
    if not cleaned:
        return []
    if len(cleaned) <= maximum:
        return [cleaned]

    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        while len(sentence) > maximum:
            split_at = sentence.rfind(" ", 0, maximum + 1)
            if split_at < target // 2:
                split_at = maximum
            prefix, sentence = sentence[:split_at].strip(), sentence[split_at:].strip()
            if current and len(current) + len(prefix) + 1 <= maximum:
                current = f"{current} {prefix}"
                flush()
            else:
                flush()
                chunks.append(prefix)
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > maximum:
            flush()
            current = sentence
        else:
            current = candidate
        if len(current) >= target and re.search(r"[.!?]$", current):
            flush()
    flush()
    return chunks
