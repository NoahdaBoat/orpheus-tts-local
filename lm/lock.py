"""Serialize chat-model traffic so a single loaded LLM is not double-booked.

Main replies and the wiki scribe share the chat model; TTS uses a different
model and is unaffected.
"""

from __future__ import annotations

import threading

chat_model_lock = threading.Lock()
