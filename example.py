#!/usr/bin/env python3
"""Simple example of using Orpheus TTS as a library."""

from __future__ import annotations

from pathlib import Path

from gguf_orpheus import AVAILABLE_VOICES, generate_speech_from_api

EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"


def text_to_speech(text: str, voice: str = "tara", output_file: str | Path | None = None) -> list[bytes]:
    """Convert text to speech and optionally write a WAV file."""
    print(f"Converting: '{text}' with voice '{voice}'")
    return generate_speech_from_api(
        prompt=text,
        voice=voice,
        output_file=str(output_file) if output_file else None,
    )


def main() -> None:
    EXAMPLES_DIR.mkdir(exist_ok=True)

    text_to_speech(
        "Hello, I'm Tara. This is an example of using Orpheus TTS as a library.",
        voice="tara",
        output_file=EXAMPLES_DIR / "example_tara.wav",
    )
    text_to_speech(
        "Hi there, I'm Leo. I have a different voice than Tara.",
        voice="leo",
        output_file=EXAMPLES_DIR / "example_leo.wav",
    )

    print("All available voices:")
    for voice in AVAILABLE_VOICES:
        print(f"- {voice}")


if __name__ == "__main__":
    main()
