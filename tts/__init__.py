"""Orpheus TTS engine, text prep, and WAV helpers."""

from tts.constants import AVAILABLE_VOICES, DEFAULT_VOICE, SAMPLE_RATE
from tts.engine import OrpheusEngine, OrpheusError
from tts.parser import (
    CustomTokenParser,
    extract_text_from_completion_sse,
    format_prompt,
    token_id,
)
from tts.text import sanitize_for_speech, speech_chunk_bounds, split_for_speech
from tts.wav import write_wav

__all__ = [
    "AVAILABLE_VOICES",
    "DEFAULT_VOICE",
    "SAMPLE_RATE",
    "CustomTokenParser",
    "OrpheusEngine",
    "OrpheusError",
    "extract_text_from_completion_sse",
    "format_prompt",
    "sanitize_for_speech",
    "speech_chunk_bounds",
    "split_for_speech",
    "token_id",
    "write_wav",
]
