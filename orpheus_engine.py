"""Reusable Orpheus client, token parser, text cleanup, and WAV generation."""

from __future__ import annotations

import html
import json
import re
import threading
import wave
from pathlib import Path
from typing import Iterable, Iterator

import requests

from decoder import SNACDecoder

SAMPLE_RATE = 24_000
AVAILABLE_VOICES = ("tara", "leah", "jess", "leo", "dan", "mia", "zac", "zoe")
DEFAULT_VOICE = "tara"
CUSTOM_TOKEN_RE = re.compile(r"<custom_token_(\d+)>")


class OrpheusError(RuntimeError):
    """A user-facing Orpheus generation failure."""


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
        raise ValueError(f"Unknown voice '{voice}'. Choose from: {', '.join(AVAILABLE_VOICES)}")
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


def write_wav(path: str | Path, pcm_chunks: Iterable[bytes]) -> int:
    """Write mono 16-bit PCM and return the number of bytes written."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with wave.open(str(output), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        for chunk in pcm_chunks:
            wav_file.writeframes(chunk)
            total += len(chunk)
    return total


class OrpheusEngine:
    def __init__(self, decoder: SNACDecoder | None = None) -> None:
        self.decoder = decoder or SNACDecoder()
        self._generation_lock = threading.Lock()

    def synthesize_pcm(
        self,
        *,
        text: str,
        base_url: str,
        model: str,
        voice: str = DEFAULT_VOICE,
        temperature: float = 0.6,
        top_p: float = 0.9,
        repeat_penalty: float = 1.1,
        max_tokens: int | None = 1200,
        api_token: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[bytes]:
        """Generate PCM frames for one text span (not necessarily full message)."""
        if not model:
            raise OrpheusError("No Orpheus model is selected.")
        # LM Studio: -1 = no generation cap (same convention as chat max_tokens)
        token_budget = -1 if max_tokens is None or int(max_tokens) < 0 else int(max_tokens)
        payload = {
            "model": model,
            "prompt": format_prompt(text, voice),
            "max_tokens": token_budget,
            "temperature": temperature,
            "top_p": top_p,
            "repeat_penalty": repeat_penalty,
            "stream": True,
        }
        headers = {"Content-Type": "application/json"}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"

        parser = CustomTokenParser()
        code_buffer: list[int] = []
        pcm_chunks: list[bytes] = []
        token_count = 0
        endpoint = f"{base_url.rstrip('/')}/v1/completions"

        with self._generation_lock:
            try:
                with requests.post(
                    endpoint, headers=headers, json=payload, stream=True, timeout=(10, 600)
                ) as response:
                    if response.status_code != 200:
                        detail = response.text[:500]
                        raise OrpheusError(
                            f"LM Studio rejected Orpheus synthesis ({response.status_code}): {detail}"
                        )
                    lines = (line.decode("utf-8", errors="replace") for line in response.iter_lines())
                    for fragment in extract_text_from_completion_sse(lines):
                        if cancel_event and cancel_event.is_set():
                            raise OrpheusError("Speech generation was cancelled.")
                        for raw in parser.feed(fragment):
                            code = token_id(raw, token_count)
                            if 0 < code <= 4096:
                                code_buffer.append(code)
                                token_count += 1
                                if token_count % 7 == 0 and token_count > 27:
                                    pcm = self.decoder.decode(code_buffer[-28:])
                                    if pcm:
                                        pcm_chunks.append(pcm)
            except requests.RequestException as exc:
                raise OrpheusError(f"Could not reach LM Studio for speech: {exc}") from exc
            except RuntimeError as exc:
                # SNAC / torch load failures from the decoder
                raise OrpheusError(str(exc)) from exc
            except ImportError as exc:
                raise OrpheusError(
                    f"Speech decoder dependency missing: {exc}. "
                    "Install with: python3 -m pip install 'snac>=1.2.1' torch"
                ) from exc

        if not pcm_chunks:
            raise OrpheusError("Orpheus returned no decodable audio tokens.")
        return pcm_chunks

    def synthesize_to_wav(
        self,
        *,
        text: str,
        output_path: str | Path,
        base_url: str,
        model: str,
        voice: str = DEFAULT_VOICE,
        temperature: float = 0.6,
        top_p: float = 0.9,
        repeat_penalty: float = 1.1,
        max_tokens: int | None = 1200,
        api_token: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> int:
        pcm_chunks = self.synthesize_pcm(
            text=text,
            base_url=base_url,
            model=model,
            voice=voice,
            temperature=temperature,
            top_p=top_p,
            repeat_penalty=repeat_penalty,
            max_tokens=max_tokens,
            api_token=api_token,
            cancel_event=cancel_event,
        )
        return write_wav(output_path, pcm_chunks)

