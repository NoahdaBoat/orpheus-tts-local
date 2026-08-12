"""Separate wiki scribe agent: digests a chat into an Obsidian vault.

Uses the same already-loaded chat model as the conversation — never a second model.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable

from agent_runtime import AgentError, AgentResult, run_tool_loop
from wiki_vault import (
    WikiVault,
    all_tool_schemas,
    dispatch_tool,
    read_tool_schemas,
)

SCRIBE_SYSTEM = """You are a wiki librarian for a local Obsidian vault.

Your job is to distill a finished conversation into durable wiki pages.
You have tools to list, search, read, and write markdown notes inside the vault only.

Rules:
- Read existing notes before creating duplicates; prefer updating related pages.
- Use Obsidian wikilinks liberally: [[Note Title]] so backlinks form naturally.
- Use LaTeX math with $inline$ or $$block$$ when formulas appear.
- Create a conversation digest under Conversations/ (path like Conversations/YYYY-MM-DD Title.md).
- Spin out Concepts/, People/, Topics/ (or similar) pages for reusable knowledge.
- Include YAML frontmatter when creating pages:
  ---
  type: conversation | concept | entity
  source_chat_id: <id>
  updated: <iso if known>
  tags: [orpheus, wiki]
  ---
- Structure conversation pages with: Summary, Key points, Concepts, Decisions, Follow-ups.
- Link the conversation page to concept pages and vice versa.
- Do not invent filesystem paths outside the vault tools.
- Do not dump the entire raw transcript unless a short quote is essential.
- When finished, reply with a short plain-text summary of pages you created or updated (no tool calls).
"""

MAIN_WIKI_ADDENDUM = """
Wiki access:
- You may use read-only wiki tools (list/search/read/backlinks) to ground answers in the user's Obsidian vault.
- You cannot write to the wiki. Vault updates happen only when the user ends the conversation (a separate scribe pass reuses this same chat model).
- Prefer citing note titles with [[wikilinks]] in your answer when you used the vault.
"""


def build_chat_messages_for_lm(
    chat: dict[str, Any],
    *,
    wiki_enabled: bool,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    system = (chat.get("system_prompt") or "").strip()
    if wiki_enabled:
        system = (system + "\n" + MAIN_WIKI_ADDENDUM).strip() if system else MAIN_WIKI_ADDENDUM.strip()
    if system:
        messages.append({"role": "system", "content": system})
    for item in chat.get("messages") or []:
        role = item.get("role")
        content = item.get("content") or ""
        if role not in ("user", "assistant") or item.get("status") == "streaming":
            continue
        if role == "assistant" and item.get("status") in ("error", "cancelled") and not content:
            continue
        messages.append({"role": role, "content": content})
    return messages


def format_transcript(chat: dict[str, Any], max_chars: int = 60_000) -> str:
    lines: list[str] = []
    for item in chat.get("messages") or []:
        role = item.get("role")
        if role not in ("user", "assistant"):
            continue
        content = (item.get("content") or "").strip()
        if not content:
            continue
        label = "User" if role == "user" else "Assistant"
        lines.append(f"### {label}\n{content}")
    text = "\n\n".join(lines)
    if len(text) > max_chars:
        text = text[-max_chars:]
        text = "[…earlier transcript truncated…]\n\n" + text
    return text


def run_scribe(
    *,
    chat: dict[str, Any],
    vault: WikiVault,
    base_url: str,
    api_token: str | None = None,
    temperature: float = 0.3,
    top_p: float = 0.9,
    max_tokens: int | None = 2048,
    max_rounds: int = 20,
    cancel_event: threading.Event | None = None,
    on_progress: Callable[[str, dict[str, Any]], None] | None = None,
) -> AgentResult:
    """Run the scribe with the chat's model_id only (same loaded weights)."""
    model = chat.get("model_id") or ""
    if not model:
        raise AgentError("Chat has no model_id; cannot run wiki scribe")

    pages_before = {item["path"] for item in vault.list_notes(limit=200).get("notes", [])}
    touched: list[str] = []

    def handler(name: str, arguments: dict[str, Any]) -> Any:
        result = dispatch_tool(vault, name, arguments, allow_write=True)
        if isinstance(result, dict) and result.get("path"):
            path = str(result["path"])
            if path not in touched:
                touched.append(path)
        return result

    inventory = vault.list_notes(limit=80)
    user_blob = {
        "chat_id": chat.get("id"),
        "title": chat.get("title"),
        "ended_at": chat.get("ended_at"),
        "existing_notes_sample": inventory.get("notes", []),
        "transcript": format_transcript(chat),
    }
    messages = [
        {"role": "system", "content": SCRIBE_SYSTEM},
        {
            "role": "user",
            "content": (
                "Digest this conversation into the Obsidian vault using your tools.\n\n"
                + json.dumps(user_blob, ensure_ascii=False, indent=2)
            ),
        },
    ]

    result = run_tool_loop(
        base_url=base_url,
        model=model,
        messages=messages,
        tools=all_tool_schemas(),
        tool_handler=handler,
        api_token=api_token,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        max_rounds=max_rounds,
        cancel_event=cancel_event,
        on_progress=on_progress,
        acquire_model_lock=True,
    )
    # Prefer paths we observed from write tools; fall back to inventory delta.
    if not touched:
        after = {item["path"] for item in vault.list_notes(limit=200).get("notes", [])}
        touched = sorted(after - pages_before)
    # Attach pages for the app layer (AgentResult is a simple dataclass)
    setattr(result, "pages_touched", touched)
    return result


def main_read_handler(vault: WikiVault) -> Callable[[str, dict[str, Any]], Any]:
    def handler(name: str, arguments: dict[str, Any]) -> Any:
        return dispatch_tool(vault, name, arguments, allow_write=False)

    return handler


def main_tool_schemas() -> list[dict[str, Any]]:
    return read_tool_schemas()
