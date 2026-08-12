"""Filesystem roots for the web app."""

from __future__ import annotations

from pathlib import Path

# Project root (parent of the web/ package)
ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
SOUNDS_DIR = ROOT / "sounds"
DEFAULT_DATA_DIR = ROOT / "data"
