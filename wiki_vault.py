"""Sandboxed Obsidian vault helpers for wiki read/write tools."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
MAX_READ_BYTES = 400_000
MAX_WRITE_BYTES = 800_000
MAX_SEARCH_RESULTS = 40
MAX_LIST_ENTRIES = 200
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
    return list(dict.fromkeys(match.group(1).strip() for match in WIKILINK_RE.finditer(text)))


@dataclass
class VaultError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass
class WikiVault:
    root: Path
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()
        if not self.root.exists():
            raise VaultError(f"Vault path does not exist: {self.root}")
        if not self.root.is_dir():
            raise VaultError(f"Vault path is not a directory: {self.root}")

    @classmethod
    def from_settings(cls, path: str | None) -> WikiVault | None:
        if not path or not str(path).strip():
            return None
        try:
            return cls(Path(path).expanduser())
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

    def resolve_relative(self, relative: str, *, must_exist: bool = False, for_write: bool = False) -> Path:
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

    def write_note(self, path: str, content: str, create_dirs: bool = True) -> dict[str, Any]:
        if content is None:
            raise VaultError("Content is required")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            raise VaultError(f"Content exceeds max write size ({MAX_WRITE_BYTES} bytes)")
        target = self.resolve_relative(path, for_write=True)
        with self._lock:
            if create_dirs:
                target.parent.mkdir(parents=True, exist_ok=True)
            elif not target.parent.exists():
                raise VaultError(f"Parent folder does not exist: {target.parent.relative_to(self.root)}")
            target.write_text(content, encoding="utf-8")
        return {"path": self.relative_of(target), "bytes": len(encoded), "action": "write"}

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
                body = template if template is not None else (
                    f"---\ntype: note\nupdated: {utc_now()}\ntags: [orpheus, wiki]\n---\n\n"
                    f"# {target.stem}\n\n"
                )
                target.write_text(body, encoding="utf-8")
                created = True
        return {"path": self.relative_of(target), "created": created, "action": "ensure"}

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


# ---- OpenAI-style tool schemas & dispatch ---------------------------------


READ_TOOL_NAMES = {
    "wiki_list",
    "wiki_search",
    "wiki_read",
    "wiki_backlinks",
    "wiki_outgoing_links",
}

WRITE_TOOL_NAMES = {
    "wiki_write",
    "wiki_append",
    "wiki_patch",
    "wiki_ensure_note",
    "wiki_add_link",
}


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


def read_tool_schemas() -> list[dict[str, Any]]:
    return [
        _schema(
            "wiki_list",
            "List markdown notes in the Obsidian vault (optional subfolder).",
            {
                "folder": {"type": "string", "description": "Relative folder under the vault root"},
                "limit": {"type": "integer", "description": "Max notes to return"},
            },
        ),
        _schema(
            "wiki_search",
            "Search note titles and content for a query string.",
            {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results"},
            },
            ["query"],
        ),
        _schema(
            "wiki_read",
            "Read a markdown note by relative path (e.g. Concepts/Gravity.md).",
            {"path": {"type": "string", "description": "Relative .md path"}},
            ["path"],
        ),
        _schema(
            "wiki_backlinks",
            "Find notes that link to a note title via [[wikilinks]].",
            {"note_name": {"type": "string", "description": "Note title or path stem"}},
            ["note_name"],
        ),
        _schema(
            "wiki_outgoing_links",
            "List [[wikilinks]] outgoing from a note.",
            {"path": {"type": "string", "description": "Relative .md path"}},
            ["path"],
        ),
    ]


def write_tool_schemas() -> list[dict[str, Any]]:
    return [
        _schema(
            "wiki_write",
            "Create or overwrite a markdown note with full content.",
            {
                "path": {"type": "string", "description": "Relative .md path"},
                "content": {"type": "string", "description": "Full markdown body"},
                "create_dirs": {"type": "boolean", "description": "Create parent folders (default true)"},
            },
            ["path", "content"],
        ),
        _schema(
            "wiki_append",
            "Append text to a note, creating it if missing.",
            {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            ["path", "content"],
        ),
        _schema(
            "wiki_patch",
            "Replace one exact occurrence of old text with new text in a note.",
            {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            ["path", "old", "new"],
        ),
        _schema(
            "wiki_ensure_note",
            "Create a note if it does not exist (idempotent). Prefer for new concept pages.",
            {
                "title": {"type": "string", "description": "Note title"},
                "path": {"type": "string", "description": "Optional explicit relative path"},
                "folder": {"type": "string", "description": "Folder when using title (e.g. Concepts)"},
                "template": {"type": "string", "description": "Optional initial markdown"},
            },
        ),
        _schema(
            "wiki_add_link",
            "Ensure a [[wikilink]] to to_title exists in from_path.",
            {
                "from_path": {"type": "string"},
                "to_title": {"type": "string"},
            },
            ["from_path", "to_title"],
        ),
    ]


def all_tool_schemas() -> list[dict[str, Any]]:
    return read_tool_schemas() + write_tool_schemas()


def dispatch_tool(vault: WikiVault, name: str, arguments: dict[str, Any], *, allow_write: bool) -> Any:
    if name in WRITE_TOOL_NAMES and not allow_write:
        raise VaultError(f"Write tool not allowed: {name}")
    if name not in READ_TOOL_NAMES and name not in WRITE_TOOL_NAMES:
        raise VaultError(f"Unknown tool: {name}")

    handlers: dict[str, Callable[[], Any]] = {
        "wiki_list": lambda: vault.list_notes(
            folder=str(arguments.get("folder") or ""),
            limit=int(arguments.get("limit") or 100),
        ),
        "wiki_search": lambda: vault.search(
            query=str(arguments.get("query") or ""),
            limit=int(arguments.get("limit") or 20),
        ),
        "wiki_read": lambda: vault.read_note(str(arguments.get("path") or "")),
        "wiki_backlinks": lambda: vault.backlinks(str(arguments.get("note_name") or "")),
        "wiki_outgoing_links": lambda: vault.outgoing_links(str(arguments.get("path") or "")),
        "wiki_write": lambda: vault.write_note(
            path=str(arguments.get("path") or ""),
            content=str(arguments.get("content") if arguments.get("content") is not None else ""),
            create_dirs=bool(arguments.get("create_dirs", True)),
        ),
        "wiki_append": lambda: vault.append_note(
            path=str(arguments.get("path") or ""),
            content=str(arguments.get("content") or ""),
        ),
        "wiki_patch": lambda: vault.patch_note(
            path=str(arguments.get("path") or ""),
            old=str(arguments.get("old") or ""),
            new=str(arguments.get("new") if arguments.get("new") is not None else ""),
        ),
        "wiki_ensure_note": lambda: vault.ensure_note(
            title=arguments.get("title"),
            path=arguments.get("path"),
            template=arguments.get("template"),
            folder=str(arguments.get("folder") or ""),
        ),
        "wiki_add_link": lambda: vault.add_link(
            from_path=str(arguments.get("from_path") or ""),
            to_title=str(arguments.get("to_title") or ""),
        ),
    }
    try:
        return handlers[name]()
    except VaultError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise VaultError(str(exc)) from exc
