"""Background wiki scribe job scheduling."""

from __future__ import annotations

import threading

from lm import AgentError
from storage import utc_now
from web.context import AppContext
from web.helpers import chat_wiki_active
from wiki import VaultError, WikiVault, run_scribe


def schedule_scribe(ctx: AppContext, chat_id: str) -> None:
    """Background wiki scribe using the chat's model_id only (same loaded LLM)."""
    with ctx.wiki_jobs_lock:
        if chat_id in ctx.wiki_jobs:
            return
        cancel_event = threading.Event()
        ctx.wiki_jobs[chat_id] = cancel_event

    def worker() -> None:
        try:
            chat = ctx.store.get_chat(chat_id)
            settings = ctx.store.load_settings()
            if not chat_wiki_active(chat, settings):
                ctx.store.update_wiki_meta(
                    chat_id,
                    {
                        "last_status": "idle",
                        "last_error": (
                            None
                            if chat.get("wiki_enabled", True)
                            else "Wiki disabled for this chat"
                        ),
                    },
                )
                return
            try:
                vault = WikiVault(settings.get("wiki_vault_path") or "")
            except (VaultError, OSError) as exc:
                ctx.store.update_wiki_meta(
                    chat_id,
                    {
                        "last_status": "error",
                        "last_error": str(exc),
                    },
                )
                return

            ctx.store.update_wiki_meta(
                chat_id,
                {"last_status": "running", "last_error": None},
            )
            # Wait until this chat is not generating (message/TTS) so we don't
            # contend with the same loaded model / session jobs map.
            while True:
                with ctx.jobs_lock:
                    busy = chat_id in ctx.jobs
                if not busy:
                    break
                if cancel_event.is_set():
                    ctx.store.update_wiki_meta(
                        chat_id, {"last_status": "idle", "last_error": "Cancelled"}
                    )
                    return
                cancel_event.wait(0.4)

            chat_limit = settings.get("chat_max_tokens")
            if chat_limit is None or int(chat_limit) < 0:
                scribe_max_tokens: int | None = -1
            else:
                scribe_max_tokens = max(int(chat_limit), 1024)
            result = run_scribe(
                chat=chat,
                vault=vault,
                base_url=settings["base_url"],
                api_token=ctx.api_token(),
                temperature=min(float(settings.get("temperature") or 0.4), 0.5),
                top_p=float(settings.get("top_p") or 0.9),
                max_tokens=scribe_max_tokens,
                cancel_event=cancel_event,
            )
            pages = getattr(result, "pages_touched", []) or []
            ctx.store.update_wiki_meta(
                chat_id,
                {
                    "last_status": "ok",
                    "last_error": None,
                    "last_synced_at": utc_now(),
                    "pages_touched": pages,
                },
            )
        except AgentError as exc:
            ctx.store.update_wiki_meta(
                chat_id,
                {"last_status": "error", "last_error": str(exc)},
            )
        except Exception as exc:  # noqa: BLE001
            ctx.store.update_wiki_meta(
                chat_id,
                {"last_status": "error", "last_error": str(exc)},
            )
        finally:
            with ctx.wiki_jobs_lock:
                if ctx.wiki_jobs.get(chat_id) is cancel_event:
                    ctx.wiki_jobs.pop(chat_id, None)

    threading.Thread(
        target=worker, name=f"wiki-scribe-{chat_id[:8]}", daemon=True
    ).start()
