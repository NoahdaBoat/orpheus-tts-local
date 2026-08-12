#!/usr/bin/env python3
"""Command-line interface and compatibility API for local Orpheus TTS."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from orpheus_engine import (
    AVAILABLE_VOICES,
    DEFAULT_VOICE,
    OrpheusEngine,
)

DEFAULT_BASE_URL = "http://127.0.0.1:1234"
DEFAULT_MODEL = "orpheus-3b-0.1-ft"
TEMPERATURE = 0.6
TOP_P = 0.9
REPETITION_PENALTY = 1.1
MAX_TOKENS = 1200


def generate_speech_from_api(
    prompt: str,
    voice: str = DEFAULT_VOICE,
    output_file: str | None = None,
    temperature: float = TEMPERATURE,
    top_p: float = TOP_P,
    max_tokens: int = MAX_TOKENS,
    repetition_penalty: float = REPETITION_PENALTY,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
) -> list[bytes]:
    """Generate a WAV file and return its PCM payload for older callers."""

    if output_file is None:
        Path("outputs").mkdir(exist_ok=True)
        output_file = f"outputs/{voice}_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    engine = OrpheusEngine()
    engine.synthesize_to_wav(
        text=prompt,
        output_path=output_file,
        base_url=base_url,
        model=model,
        voice=voice,
        temperature=temperature,
        top_p=top_p,
        repeat_penalty=repetition_penalty,
        max_tokens=max_tokens,
        api_token=os.getenv("LM_STUDIO_API_TOKEN"),
    )
    import wave

    with wave.open(output_file, "rb") as wav_file:
        return [wav_file.readframes(wav_file.getnframes())]


def list_available_voices() -> None:
    print("Available voices:")
    for voice in AVAILABLE_VOICES:
        marker = "★" if voice == DEFAULT_VOICE else " "
        print(f"{marker} {voice}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Orpheus TTS through LM Studio")
    parser.add_argument("text_args", nargs="*", help="Text to synthesize")
    parser.add_argument("--text", help="Text to synthesize")
    parser.add_argument("--voice", default=DEFAULT_VOICE, choices=AVAILABLE_VOICES)
    parser.add_argument("--output")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--temperature", type=float, default=TEMPERATURE)
    parser.add_argument("--top-p", type=float, default=TOP_P)
    parser.add_argument("--repetition-penalty", type=float, default=REPETITION_PENALTY)
    parser.add_argument("--max-tokens", type=int, default=MAX_TOKENS)
    parser.add_argument("--list-voices", action="store_true")
    args = parser.parse_args()

    if args.list_voices:
        list_available_voices()
        return
    text = args.text or " ".join(args.text_args) or input("Enter text to synthesize: ").strip()
    if not text:
        parser.error("Text is required")
    output = args.output or f"outputs/{args.voice}_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    started = time.monotonic()
    generate_speech_from_api(
        text,
        voice=args.voice,
        output_file=output,
        temperature=args.temperature,
        top_p=args.top_p,
        max_tokens=args.max_tokens,
        repetition_penalty=args.repetition_penalty,
        base_url=args.base_url,
        model=args.model,
    )
    print(f"Audio saved to {output} in {time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    main()
