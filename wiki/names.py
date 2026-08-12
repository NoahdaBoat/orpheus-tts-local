"""Filename helpers and wikilink parsing for Obsidian notes."""

from __future__ import annotations

import re
from datetime import datetime, timezone

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
ILLEGAL_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def title_to_filename(title: str) -> str:
    cleaned = ILLEGAL_FILENAME.sub("", title.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "Untitled"


def note_path_from_title(title: str, folder: str = "") -> str:
    name = title_to_filename(title)
    if not name.lower().endswith(".md"):
        name = f"{name}.md"
    folder = folder.strip("/").replace("\\", "/")
    return f"{folder}/{name}" if folder else name


def parse_wikilinks(text: str) -> list[str]:
    return list(
        dict.fromkeys(match.group(1).strip() for match in WIKILINK_RE.finditer(text))
    )
