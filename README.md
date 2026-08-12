# Orpheus Local Chat

A **local-only** web chat that connects any LM Studio chat model to Orpheus TTS. Responses stream into a chat interface, then Orpheus synthesizes sentence-sized WAV segments and plays them in order.

> **Privacy first:** the server binds to `127.0.0.1` by default. Chat history, settings, vault paths, and generated audio stay on your machine under `data/` (gitignored — never committed).

## What it includes

- Dynamic model discovery from LM Studio
- Persistent multi-chat history stored in `data/chats/`
- Streaming chat through LM Studio's OpenAI-compatible `/v1/chat/completions` endpoint
- Optional **Obsidian wiki**: point Settings at a vault folder; the chat agent can **read** notes, and a separate **scribe** pass **writes** pages when you end a conversation (same loaded chat model — no third model in VRAM)
- Conversation lifecycle: **End** / **Resume** (ended chats stay in the list until deleted)
- Glassmorphic WhatsApp-style bubbles and chat list
- Eight Orpheus voices with automatic playback and replay
- Session-only generated audio, cleared when the app restarts
- A reusable Orpheus engine shared by the webapp and CLI

## Requirements

- Python 3.10+
- [LM Studio](https://lmstudio.ai/) with a chat model and an Orpheus TTS model loaded
- Optional: an Obsidian vault folder for wiki features

## Setup

1. Start LM Studio's local server, normally at `http://127.0.0.1:1234`.
2. Load a chat model and an Orpheus model.
3. Create and activate a Python environment, then install dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

4. Start the webapp with the **same** Python environment where you installed dependencies
   (Orpheus Speak needs the `snac` package for audio decoding):

   ```bash
   python3 app.py
   ```

   If Speak fails with `No module named 'snac'`, install it into that environment:

   ```bash
   python3 -m pip install 'snac>=1.2.1'
   ```

5. Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The first speech request downloads the SNAC decoder model if it is not already cached.

### Optional authentication

If your LM Studio server requires an API token:

```bash
export LM_STUDIO_API_TOKEN=your-token-here
python3 app.py
```

See [`.env.example`](.env.example). Never commit real tokens; `.env` is gitignored.

## Privacy & what is not published

| Path | Contents | In git? |
|------|----------|---------|
| `data/settings.json` | Models, system prompt, **vault path** | No |
| `data/chats/` | Full conversation history | No |
| `data/runtime-audio/` | Temporary speech WAVs | No |
| `.env` | API tokens | No |
| `.venv/` | Local packages | No |

Only source code, tests, static assets, sample WAVs under `examples/`, and `data/README.md` are meant for the public repository.

**Do not** bind the app to `0.0.0.0` or expose it on a public network without adding authentication. It is designed for local use.

## Webapp behavior

The app automatically suggests the first non-Orpheus model for chat and a model whose ID contains `orpheus` for speech. Both can be changed in Settings. The chat model and system prompt lock after a conversation's first message; new conversations inherit the latest global choices.

Chat history and settings are local JSON files under `data/`. Generated WAV files live under `data/runtime-audio/` and are removed on restart or clean shutdown.

### Conversation lifecycle

- **New conversation** starts an open chat.
- **End** marks the chat as ended (composer locks). If the wiki is enabled and “Write on end” is on, a background **wiki scribe** digests the transcript into markdown under your vault.
- **Resume** reopens an ended chat so you can continue later. Ending again re-runs the scribe over the full conversation.
- Delete removes the chat JSON only; vault notes are left in place.

### Obsidian wiki

1. In Settings → **Wiki (Obsidian)**, enable the wiki and set **Vault folder path** to your Obsidian vault root (an existing directory).
2. Use **Test vault** to confirm the path and see the note count.
3. While chatting (wiki on), the model may call **read-only** tools (`wiki_search`, `wiki_read`, …) against the vault. That needs a **tool-capable** chat model in LM Studio.
4. When you **End** the conversation, a separate scribe session runs with **write** tools. It reuses the **same `model_id`** as the chat — keep only your chat model + Orpheus loaded; the app never loads a second LLM for the scribe.
5. Scribe traffic is serialized with chat completions so the single loaded chat model is not double-booked.

Suggested note layout (prompt guidance, not hard-coded): `Conversations/…`, `Concepts/…`, wikilinks `[[Like This]]`, optional `$LaTeX$`.

Vault access is sandboxed to the chosen folder (path traversal is rejected). The model only sees note content you allow through tools.

## CLI

The original CLI remains available with explicit server and model selection:

```bash
python gguf_orpheus.py \
  --text "Hello from Orpheus" \
  --voice tara \
  --model "orpheus-3b-0.1-ft" \
  --base-url http://127.0.0.1:1234 \
  --output outputs/hello.wav
```

List voices with:

```bash
python gguf_orpheus.py --list-voices
```

## Tests

```bash
python -m unittest discover -s tests -v
```

Live LM Studio inference is intentionally not required by the automated tests.

## Project layout

```
app.py                 # Entry point (uvicorn app:app) — thin re-export
gguf_orpheus.py        # CLI entry point
decoder.py             # SNAC audio decoder
folder_picker.py       # Native folder dialog (macOS/Windows/Linux)

web/                   # FastAPI app package
  factory.py           # create_app()
  context.py           # Shared AppContext (store, jobs, engine)
  schemas.py           # Request body models
  helpers.py           # SSE helpers, model discovery, wiki checks
  routes/              # HTTP route modules (system, chats, wiki, media)
  services/            # Speech stream, chat stream, wiki jobs

lm/                    # LM Studio client (completions + tool loop)
tts/                   # Orpheus TTS engine, text prep, WAV
wiki/                  # Obsidian vault sandbox, tools, scribe
storage/               # Chat/settings JSON persistence

# Compatibility shims (old import paths still work):
agent_runtime.py, orpheus_engine.py, chat_store.py,
wiki_vault.py, wiki_scribe.py

static/                # Web UI
tests/                 # Unit tests (no live LM Studio required)
examples/              # Sample WAVs
data/                  # Local runtime data (gitignored)
```

## License

Apache 2.0 — see [LICENSE](LICENSE).
