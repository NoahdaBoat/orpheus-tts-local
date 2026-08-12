"""OpenAI-style tool schemas and dispatch for the vault."""

from __future__ import annotations

from typing import Any, Callable

from wiki.vault import VaultError, WikiVault

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


def _schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
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
                "folder": {
                    "type": "string",
                    "description": "Relative folder under the vault root",
                },
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
                "create_dirs": {
                    "type": "boolean",
                    "description": "Create parent folders (default true)",
                },
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
                "path": {
                    "type": "string",
                    "description": "Optional explicit relative path",
                },
                "folder": {
                    "type": "string",
                    "description": "Folder when using title (e.g. Concepts)",
                },
                "template": {
                    "type": "string",
                    "description": "Optional initial markdown",
                },
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


def dispatch_tool(
    vault: WikiVault, name: str, arguments: dict[str, Any], *, allow_write: bool
) -> Any:
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
        "wiki_backlinks": lambda: vault.backlinks(
            str(arguments.get("note_name") or "")
        ),
        "wiki_outgoing_links": lambda: vault.outgoing_links(
            str(arguments.get("path") or "")
        ),
        "wiki_write": lambda: vault.write_note(
            path=str(arguments.get("path") or ""),
            content=str(
                arguments.get("content") if arguments.get("content") is not None else ""
            ),
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
