# Local data (not published)

This directory is created at runtime and is **gitignored** on purpose.

It may contain private information such as:

- `settings.json` — LM Studio URL, model names, system prompt, Obsidian vault path
- `chats/*.json` — full conversation history
- `runtime-audio/` — temporary generated WAV files (cleared on restart)

Do not commit this folder. If you clone the repo, the app recreates it automatically on first run.
