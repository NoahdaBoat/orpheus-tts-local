"""Local FastAPI entry point for LM Studio chat plus Orpheus speech.

Implementation lives in the ``web`` package; this module keeps the public
``app:app`` / ``create_app`` import path stable for uvicorn and tests.
"""

from __future__ import annotations

from web.factory import create_app
from web.helpers import extract_chat_end_text, model_options, sse

# Re-export helpers used by unit tests
__all__ = [
    "app",
    "create_app",
    "extract_chat_end_text",
    "model_options",
    "sse",
]

app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
