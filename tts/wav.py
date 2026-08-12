"""WAV file helpers for Orpheus PCM output."""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Iterable

from tts.constants import SAMPLE_RATE


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
