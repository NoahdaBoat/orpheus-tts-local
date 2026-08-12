"""LM Studio client: chat completions and tool-calling loops."""

from lm.chat import run_chat_completion, stream_chat_completion
from lm.tool_loop import run_tool_loop
from lm.types import (
    AgentError,
    AgentResult,
    ProgressCallback,
    ToolCallEvent,
    ToolHandler,
)
from lm.util import apply_max_tokens, authorization_headers

__all__ = [
    "AgentError",
    "AgentResult",
    "ProgressCallback",
    "ToolCallEvent",
    "ToolHandler",
    "apply_max_tokens",
    "authorization_headers",
    "run_chat_completion",
    "run_tool_loop",
    "stream_chat_completion",
]
