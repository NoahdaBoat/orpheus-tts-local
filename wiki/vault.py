"""Sandboxed Obsidian vault filesystem operations."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wiki.names import note_path_from_title, parse_wikilinks, utc_now

MAX_READ_BYTES = 400_000
MAX_WRITE_BYTES = 800_000
MAX_SEARCH_RESULTS = 40
MAX_LIST_ENTRIES = 200


@dataclass
class VaultError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass
class WikiVault:
    root: Path
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def __init__(self, root: str | Path) -> None:
        resolved = Path(root).expanduser().resolve()
        if not resolved.exists():
            raise VaultError(f"Vault path does not exist: {resolved}")
        if not resolved.is_dir():
            raise VaultError(f"Vault path is not a directory: {resolved}")
        self.root = resolved
        self._lock = threading.RLock()

    @classmethod
    def from_settings(cls, path: str | None) -> WikiVault | None:
        if not path or not str(path).strip():
            return None
        try:
            return cls(path)
        except (VaultError, OSError):
            return None

    def status(self) -> dict[str, Any]:
        notes = list(self.root.rglob("*.md"))
        return {
            "ok": True,
            "path": str(self.root),
            "note_count": len(notes),
            "exists": True,
        }

    def resolve_relative(
        self, relative: str, *, must_exist: bool = False, for_write: bool = False
    ) -> Path:
        raw = (relative or "").strip().replace("\\", "/")
        if not raw:
            raise VaultError("Path is required")
        if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
            raise VaultError("Absolute paths are not allowed")
        if ".." in Path(raw).parts:
            raise VaultError("Path traversal is not allowed")
        candidate = (self.root / raw).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise VaultError("Path escapes the vault root") from exc
        if for_write and candidate.suffix.lower() != ".md":
            raise VaultError("Only markdown (.md) notes may be written")
        if must_exist and not candidate.exists():
            raise VaultError(f"Note not found: {raw}")
        return candidate

    def relative_of(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def list_notes(self, folder: str = "", limit: int = 100) -> dict[str, Any]:
        limit = max(1, min(int(limit or 100), MAX_LIST_ENTRIES))
        base = self.root
        if folder:
            base = self.resolve_relative(folder.rstrip("/"))
            if not base.is_dir():
                raise VaultError(f"Folder not found: {folder}")
        entries: list[dict[str, Any]] = []
        for path in sorted(base.rglob("*.md")):
            if len(entries) >= limit:
                break
            rel = self.relative_of(path)
            entries.append(
                {
                    "path": rel,
                    "title": path.stem,
                    "size": path.stat().st_size,
                }
            )
        return {"folder": folder or "", "count": len(entries), "notes": entries}

    def search(self, query: str, limit: int = 20) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            raise VaultError("Search query is required")
        limit = max(1, min(int(limit or 20), MAX_SEARCH_RESULTS))
        needle = query.lower()
        hits: list[dict[str, Any]] = []
        for path in sorted(self.root.rglob("*.md")):
            if len(hits) >= limit:
                break
            rel = self.relative_of(path)
            title_hit = needle in path.stem.lower() or needle in rel.lower()
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if len(text.encode("utf-8", errors="replace")) > MAX_READ_BYTES:
                text = text[: MAX_READ_BYTES // 2]
            content_hit = needle in text.lower()
            if not title_hit and not content_hit:
                continue
            snippet = ""
            lower = text.lower()
            idx = lower.find(needle)
            if idx >= 0:
                start = max(0, idx - 60)
                end = min(len(text), idx + len(query) + 80)
                snippet = text[start:end].replace("\n", " ").strip()
            elif title_hit:
                snippet = text[:120].replace("\n", " ").strip()
            hits.append({"path": rel, "title": path.stem, "snippet": snippet})
        return {"query": query, "count": len(hits), "results": hits}

    def read_note(self, path: str) -> dict[str, Any]:
        target = self.resolve_relative(path, must_exist=True)
        if not target.is_file():
            raise VaultError(f"Not a file: {path}")
        data = target.read_bytes()
        if len(data) > MAX_READ_BYTES:
            raise VaultError(f"Note exceeds max read size ({MAX_READ_BYTES} bytes)")
        content = data.decode("utf-8", errors="replace")
        return {
            "path": self.relative_of(target),
            "title": target.stem,
            "content": content,
            "wikilinks": parse_wikilinks(content),
        }

    def write_note(
        self, path: str, content: str, create_dirs: bool = True
    ) -> dict[str, Any]:
        if content is None:
            raise VaultError("Content is required")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            raise VaultError(
                f"Content exceeds max write size ({MAX_WRITE_BYTES} bytes)"
            )
        target = self.resolve_relative(path, for_write=True)
        with self._lock:
            if create_dirs:
                target.parent.mkdir(parents=True, exist_ok=True)
            elif not target.parent.exists():
                raise VaultError(
                    f"Parent folder does not exist: {target.parent.relative_to(self.root)}"
                )
            target.write_text(content, encoding="utf-8")
        return {
            "path": self.relative_of(target),
            "bytes": len(encoded),
            "action": "write",
        }

    def append_note(self, path: str, content: str) -> dict[str, Any]:
        if not content:
            raise VaultError("Content is required")
        target = self.resolve_relative(path, for_write=True)
        with self._lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            existing = ""
            if target.exists():
                existing = target.read_text(encoding="utf-8", errors="replace")
            separator = "" if not existing or existing.endswith("\n") else "\n"
            merged = f"{existing}{separator}{content}"
            if not merged.endswith("\n"):
                merged += "\n"
            if len(merged.encode("utf-8")) > MAX_WRITE_BYTES:
                raise VaultError("Append would exceed max write size")
            target.write_text(merged, encoding="utf-8")
        return {"path": self.relative_of(target), "action": "append"}

    def patch_note(self, path: str, old: str, new: str) -> dict[str, Any]:
        if not old:
            raise VaultError("old text is required")
        target = self.resolve_relative(path, must_exist=True, for_write=True)
        with self._lock:
            content = target.read_text(encoding="utf-8", errors="replace")
            count = content.count(old)
            if count == 0:
                raise VaultError("old text not found in note")
            if count > 1:
                raise VaultError("old text matches more than once; make it unique")
            updated = content.replace(old, new, 1)
            if len(updated.encode("utf-8")) > MAX_WRITE_BYTES:
                raise VaultError("Patched note exceeds max write size")
            target.write_text(updated, encoding="utf-8")
        return {"path": self.relative_of(target), "action": "patch"}

    def ensure_note(
        self,
        title: str | None = None,
        path: str | None = None,
        template: str | None = None,
        folder: str = "",
    ) -> dict[str, Any]:
        if path:
            rel = path if path.lower().endswith(".md") else f"{path}.md"
        elif title:
            rel = note_path_from_title(title, folder)
        else:
            raise VaultError("title or path is required")
        target = self.resolve_relative(rel, for_write=True)
        created = False
        with self._lock:
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                body = (
                    template
                    if template is not None
                    else (
                        f"---\ntype: note\nupdated: {utc_now()}\ntags: [orpheus, wiki]\n---\n\n"
                        f"# {target.stem}\n\n"
                    )
                )
                target.write_text(body, encoding="utf-8")
                created = True
        return {
            "path": self.relative_of(target),
            "created": created,
            "action": "ensure",
        }

    def add_link(self, from_path: str, to_title: str) -> dict[str, Any]:
        to_title = (to_title or "").strip()
        if not to_title:
            raise VaultError("to_title is required")
        link = f"[[{to_title}]]"
        target = self.resolve_relative(from_path, must_exist=True, for_write=True)
        with self._lock:
            content = target.read_text(encoding="utf-8", errors="replace")
            if link in content or f"[[{to_title}|" in content:
                return {"path": self.relative_of(target), "added": False, "link": link}
            suffix = "\n" if content.endswith("\n") else "\n\n"
            target.write_text(f"{content}{suffix}{link}\n", encoding="utf-8")
        return {"path": self.relative_of(target), "added": True, "link": link}

    def outgoing_links(self, path: str) -> dict[str, Any]:
        note = self.read_note(path)
        return {"path": note["path"], "links": note["wikilinks"]}

    def backlinks(self, note_name: str) -> dict[str, Any]:
        name = (note_name or "").strip()
        if name.lower().endswith(".md"):
            name = Path(name).stem
        if not name:
            raise VaultError("note_name is required")
        needle = name.lower()
        found: list[dict[str, str]] = []
        for path in sorted(self.root.rglob("*.md")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for link in parse_wikilinks(text):
                if link.strip().lower() == needle:
                    found.append({"path": self.relative_of(path), "title": path.stem})
                    break
        return {"note": name, "count": len(found), "backlinks": found}
