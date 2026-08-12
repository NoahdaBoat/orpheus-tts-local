"""Compatibility shim — implementation lives in ``wiki.scribe``."""

from wiki.scribe import (
    MAIN_WIKI_ADDENDUM,
    SCRIBE_SYSTEM,
    build_chat_messages_for_lm,
    format_transcript,
    main_read_handler,
    main_tool_schemas,
    run_scribe,
)

__all__ = [
    "MAIN_WIKI_ADDENDUM",
    "SCRIBE_SYSTEM",
    "build_chat_messages_for_lm",
    "format_transcript",
    "main_read_handler",
    "main_tool_schemas",
    "run_scribe",
]
