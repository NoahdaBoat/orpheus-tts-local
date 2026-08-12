"""Shared runtime context passed to routes and services."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from lm import authorization_headers
from storage import ChatStore
from tts import OrpheusEngine


@dataclass
class AppContext:
    store: ChatStore
    runtime_dir: Path
    engine: OrpheusEngine
    jobs: dict[str, threading.Event] = field(default_factory=dict)
    jobs_lock: threading.Lock = field(default_factory=threading.Lock)
    wiki_jobs: dict[str, threading.Event] = field(default_factory=dict)
    wiki_jobs_lock: threading.Lock = field(default_factory=threading.Lock)

    def lm_headers(self) -> dict[str, str]:
        return authorization_headers(os.getenv("LM_STUDIO_API_TOKEN"))

    def api_token(self) -> str | None:
        return os.getenv("LM_STUDIO_API_TOKEN")
