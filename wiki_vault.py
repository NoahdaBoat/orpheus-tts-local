"""Compatibility shim — implementation lives in the ``wiki`` package."""

from wiki import (
    READ_TOOL_NAMES,
    WRITE_TOOL_NAMES,
    VaultError,
    WikiVault,
    all_tool_schemas,
    dispatch_tool,
    note_path_from_title,
    parse_wikilinks,
    read_tool_schemas,
    title_to_filename,
    utc_now,
    write_tool_schemas,
)

__all__ = [
    "READ_TOOL_NAMES",
    "WRITE_TOOL_NAMES",
    "VaultError",
    "WikiVault",
    "all_tool_schemas",
    "dispatch_tool",
    "note_path_from_title",
    "parse_wikilinks",
    "read_tool_schemas",
    "title_to_filename",
    "utc_now",
    "write_tool_schemas",
]
