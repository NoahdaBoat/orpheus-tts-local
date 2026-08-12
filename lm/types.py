"""Shared types for the LM Studio client."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class AgentError(RuntimeError):
    pass


@dataclass
class ToolCallEvent:
    name: str
    arguments: dict[str, Any]
    result: Any = None
    error: str | None = None


@dataclass
class AgentResult:
    content: str
    tool_events: list[ToolCallEvent] = field(default_factory=list)
    rounds: int = 0
    raw_messages: list[dict[str, Any]] = field(default_factory=list)


ToolHandler = Callable[[str, dict[str, Any]], Any]
ProgressCallback = Callable[[str, dict[str, Any]], None]
