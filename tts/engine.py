"""Orpheus synthesis client against LM Studio completions API."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Protocol, Sequence

import requests

from decoder import SNACDecoder
from tts.constants import DEFAULT_VOICE
from tts.parser import (
    CustomTokenParser,
    extract_text_from_completion_sse,
    format_prompt,
    token_id,
)
from tts.wav import write_wav


class OrpheusError(RuntimeError):
    """A user-facing Orpheus generation failure."""


class TokenDecoder(Protocol):
    """Minimal interface required by OrpheusEngine (real SNAC or test doubles)."""

    def decode(self, multiframe: Sequence[int]) -> bytes | None: ...


class OrpheusEngine:
    def __init__(self, decoder: TokenDecoder | None = None) -> None:
        self.decoder: TokenDecoder = decoder or SNACDecoder()
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
        token_budget = (
            -1 if max_tokens is None or int(max_tokens) < 0 else int(max_tokens)
        )
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
                    endpoint,
                    headers=headers,
                    json=payload,
                    stream=True,
                    timeout=(10, 600),
                ) as response:
                    if response.status_code != 200:
                        detail = response.text[:500]
                        raise OrpheusError(
                            f"LM Studio rejected Orpheus synthesis ({response.status_code}): {detail}"
                        )
                    lines = (
                        line.decode("utf-8", errors="replace")
                        for line in response.iter_lines()
                    )
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
                raise OrpheusError(
                    f"Could not reach LM Studio for speech: {exc}"
                ) from exc
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
