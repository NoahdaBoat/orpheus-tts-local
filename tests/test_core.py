from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import create_app, extract_chat_end_text, model_options, sse
from chat_store import ChatStore, atomic_write_json
from orpheus_engine import (
    CustomTokenParser,
    OrpheusEngine,
    sanitize_for_speech,
    split_for_speech,
    write_wav,
)


class HelpersTest(unittest.TestCase):
    def test_custom_tokens_survive_fragmentation(self):
        parser = CustomTokenParser()
        self.assertEqual(parser.feed("noise <custom_to"), [])
        self.assertEqual(parser.feed("ken_123><custom_token_"), [123])
        self.assertEqual(parser.feed("456> tail"), [456])

    def test_speech_sanitizer_omits_code_and_urls(self):
        source = "# Hello **friend**. [Read this](https://example.com).\n```py\nprint('no')\n``` Visit https://bad.example/path"
        spoken = sanitize_for_speech(source)
        self.assertEqual(spoken, "Hello friend. Read this. Visit")

    def test_sentence_chunks_are_bounded_and_complete(self):
        text = " ".join([f"Sentence {index} is here." for index in range(80)])
        chunks = split_for_speech(text, target=80, maximum=120)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 120 for chunk in chunks))
        self.assertEqual(" ".join(chunks), text)

    def test_unlimited_tts_allows_longer_chunks(self):
        from orpheus_engine import speech_chunk_bounds

        self.assertEqual(speech_chunk_bounds(-1), (900, 1400))
        self.assertEqual(speech_chunk_bounds(1200), (350, 440))
        short = "Hello world."
        self.assertEqual(split_for_speech(short, tts_max_tokens=-1), [short])

    def test_wav_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.wav"
            self.assertEqual(write_wav(path, [b"\x00\x00" * 16]), 32)
            with wave.open(str(path), "rb") as audio:
                self.assertEqual(audio.getframerate(), 24000)
                self.assertEqual(audio.getnchannels(), 1)
                self.assertEqual(audio.getsampwidth(), 2)
                self.assertEqual(audio.getnframes(), 16)

    def test_model_discovery_prefers_loaded_models(self):
        result = model_options({"models": [
            {"type": "llm", "key": "chat-key", "display_name": "Chat", "loaded_instances": [{"id": "gemma-live", "config": {}}]},
            {"type": "llm", "key": "tts-key", "display_name": "Voice", "loaded_instances": [{"id": "orpheus-live", "config": {}}]},
            {"type": "embedding", "key": "embed", "loaded_instances": []},
        ]})
        self.assertEqual(result["suggested_chat_model"], "gemma-live")
        self.assertEqual(result["suggested_tts_model"], "orpheus-live")

    def test_chat_end_helpers(self):
        result = {"output": [{"type": "reasoning", "content": "hidden"}, {"type": "message", "content": "Hello"}]}
        self.assertEqual(extract_chat_end_text(result), "Hello")
        self.assertIn("event: ready", sse("ready", {"ok": True}))


class StoreTest(unittest.TestCase):
    def test_unlimited_chat_tokens_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatStore(directory)
            settings = store.save_settings({"chat_max_tokens": -1})
            self.assertEqual(settings["chat_max_tokens"], -1)

    def test_legacy_message_sound_id_migrates(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatStore(directory)
            atomic_write_json(
                store.settings_path,
                {
                    **store.load_settings(),
                    "message_sound_incoming": "whatsapp_incoming1",
                },
            )
            settings = store.load_settings()
            self.assertEqual(settings["message_sound_incoming"], "incoming1")

    def test_settings_and_chat_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatStore(directory)
            settings = store.save_settings({"chat_model": "gemma", "tts_model": "orpheus"})
            self.assertEqual(settings["chat_model"], "gemma")
            chat = store.create_chat()
            self.assertEqual(chat["model_id"], "gemma")
            self.assertEqual(chat["status"], "open")
            updated = store.update_chat(chat["id"], {"title": "A title", "system_prompt": "Be kind"})
            self.assertEqual(updated["system_prompt"], "Be kind")
            updated["messages"].append({"id": "one", "role": "user", "content": "Hi"})
            store.save_chat(updated)
            locked = store.update_chat(chat["id"], {"model_id": "other", "system_prompt": "Changed"})
            self.assertEqual(locked["model_id"], "gemma")
            self.assertEqual(locked["system_prompt"], "Be kind")
            self.assertEqual(store.list_chats()[0]["title"], "A title")
            ended = store.end_chat(chat["id"])
            self.assertEqual(ended["status"], "ended")
            resumed = store.resume_chat(chat["id"])
            self.assertEqual(resumed["status"], "open")

    def test_atomic_write_replaces_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            atomic_write_json(path, {"version": 1})
            atomic_write_json(path, {"version": 2})
            self.assertEqual(json.loads(path.read_text())["version"], 2)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])


class FakeDecoder:
    def decode(self, multiframe):
        self.last = list(multiframe)
        return b"\x00\x00" * 32


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_lines(self):
        return iter(self.lines)


class EngineTest(unittest.TestCase):
    def test_mocked_lm_stream_writes_audio(self):
        token_text = "".join(
            f"<custom_token_{100 + 10 + ((index % 7) * 4096)}>" for index in range(35)
        )
        halfway = len(token_text) // 2
        fragments = [token_text[:halfway], token_text[halfway:]]
        lines = [f"data: {json.dumps({'choices': [{'text': fragment}]})}".encode() for fragment in fragments]
        lines.append(b"data: [DONE]")
        with tempfile.TemporaryDirectory() as directory, patch(
            "orpheus_engine.requests.post", return_value=FakeResponse(lines)
        ):
            path = Path(directory) / "voice.wav"
            size = OrpheusEngine(FakeDecoder()).synthesize_to_wav(
                text="Hello", output_path=path, base_url="http://localhost:1234", model="orpheus"
            )
            self.assertGreater(size, 0)
            self.assertTrue(path.exists())


class ApiTest(unittest.TestCase):
    def test_local_chat_crud(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(directory)) as client:
                self.assertEqual(client.get("/api/health").json(), {"status": "ok"})
                created = client.post("/api/chats", json={}).json()
                chat_id = created["id"]
                renamed = client.patch(f"/api/chats/{chat_id}", json={"title": "Renamed"}).json()
                self.assertEqual(renamed["title"], "Renamed")
                self.assertEqual(client.get(f"/api/chats/{chat_id}").status_code, 200)
                self.assertEqual(client.delete(f"/api/chats/{chat_id}").status_code, 204)
                self.assertEqual(client.get(f"/api/chats/{chat_id}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
