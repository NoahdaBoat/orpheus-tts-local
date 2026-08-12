from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent_runtime import apply_max_tokens
from app import create_app
from chat_store import ChatStore
from wiki_vault import VaultError, WikiVault, dispatch_tool, parse_wikilinks


class WikiVaultTest(unittest.TestCase):
    def test_sandbox_and_crud(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = WikiVault(directory)
            vault.write_note("Concepts/Gravity.md", "# Gravity\n\nMass attracts mass. See [[Physics]].\n")
            vault.write_note("Physics.md", "# Physics\n\nLinks to [[Gravity]].\n")
            listed = vault.list_notes()
            self.assertEqual(listed["count"], 2)
            note = vault.read_note("Concepts/Gravity.md")
            self.assertIn("Mass attracts", note["content"])
            self.assertIn("Physics", note["wikilinks"])
            hits = vault.search("attracts")
            self.assertEqual(hits["count"], 1)
            backs = vault.backlinks("Gravity")
            self.assertEqual(backs["count"], 1)
            self.assertEqual(backs["backlinks"][0]["path"], "Physics.md")

            vault.append_note("Physics.md", "\n## More\n")
            vault.patch_note("Physics.md", "## More", "## Extended")
            patched = vault.read_note("Physics.md")
            self.assertIn("## Extended", patched["content"])

            ensured = vault.ensure_note(title="Orbit", folder="Concepts")
            self.assertTrue(ensured["created"])
            again = vault.ensure_note(title="Orbit", folder="Concepts")
            self.assertFalse(again["created"])

            vault.add_link("Physics.md", "Orbit")
            self.assertIn("[[Orbit]]", vault.read_note("Physics.md")["content"])

    def test_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = WikiVault(directory)
            with self.assertRaises(VaultError):
                vault.read_note("../secret.md")
            with self.assertRaises(VaultError):
                vault.write_note("../../etc/passwd.md", "nope")
            with self.assertRaises(VaultError):
                vault.write_note("notes/../../escape.md", "nope")

    def test_wikilink_parse(self):
        text = "See [[Alpha]] and [[Beta|alias]] and [[Gamma#heading]]."
        self.assertEqual(parse_wikilinks(text), ["Alpha", "Beta", "Gamma"])

    def test_dispatch_read_only_blocks_write(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = WikiVault(directory)
            with self.assertRaises(VaultError):
                dispatch_tool(
                    vault,
                    "wiki_write",
                    {"path": "a.md", "content": "x"},
                    allow_write=False,
                )


class MaxTokensHelperTest(unittest.TestCase):
    def test_apply_max_tokens_unlimited(self):
        self.assertEqual(apply_max_tokens({}, -1)["max_tokens"], -1)
        self.assertEqual(apply_max_tokens({}, None)["max_tokens"], -1)
        self.assertEqual(apply_max_tokens({}, 1200)["max_tokens"], 1200)


class ChatLifecycleTest(unittest.TestCase):
    def test_end_resume_and_wiki_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatStore(directory)
            settings = store.save_settings(
                {
                    "wiki_enabled": True,
                    "wiki_vault_path": directory,
                    "wiki_auto_on_end": True,
                    "chat_model": "gemma",
                }
            )
            self.assertTrue(settings["wiki_enabled"])
            chat = store.create_chat()
            self.assertEqual(chat["status"], "open")
            chat["messages"].append({"id": "1", "role": "user", "content": "Hello"})
            store.save_chat(chat)
            ended = store.end_chat(chat["id"])
            self.assertEqual(ended["status"], "ended")
            self.assertIsNotNone(ended["ended_at"])
            resumed = store.resume_chat(chat["id"])
            self.assertEqual(resumed["status"], "open")
            listed = store.list_chats()
            self.assertEqual(listed[0]["preview"], "Hello")
            self.assertEqual(listed[0]["status"], "open")
            self.assertTrue(chat.get("wiki_enabled", True))
            disabled = store.update_chat(chat["id"], {"wiki_enabled": False})
            self.assertFalse(disabled["wiki_enabled"])


class WikiApiTest(unittest.TestCase):
    def test_settings_accept_unlimited_chat_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(directory)) as client:
                result = client.put("/api/settings", json={"chat_max_tokens": -1}).json()
                self.assertEqual(result["chat_max_tokens"], -1)
                bad = client.put("/api/settings", json={"chat_max_tokens": 0})
                self.assertEqual(bad.status_code, 422)

    def test_pick_folder_cancelled(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.pick_folder", return_value=None):
                with TestClient(create_app(directory)) as client:
                    result = client.post("/api/wiki/pick-folder").json()
                    self.assertTrue(result["cancelled"])
                    self.assertIsNone(result["path"])

    def test_pick_folder_sets_path(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory) / "MyVault"
            vault.mkdir()
            with patch("app.pick_folder", return_value=str(vault)):
                with TestClient(create_app(directory)) as client:
                    result = client.post("/api/wiki/pick-folder").json()
                    self.assertFalse(result["cancelled"])
                    self.assertEqual(result["path"], str(vault))

    def test_end_resume_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            vault_dir = Path(directory) / "vault"
            vault_dir.mkdir()
            with TestClient(create_app(directory)) as client:
                client.put(
                    "/api/settings",
                    json={
                        "wiki_enabled": True,
                        "wiki_vault_path": str(vault_dir),
                        "wiki_auto_on_end": False,
                        "chat_model": "gemma",
                    },
                )
                created = client.post("/api/chats", json={}).json()
                chat_id = created["id"]
                # Seed a message without LM Studio
                store = ChatStore(directory)
                chat = store.get_chat(chat_id)
                chat["messages"] = [
                    {"id": "u", "role": "user", "content": "Talk about orbits", "created_at": "t"},
                    {
                        "id": "a",
                        "role": "assistant",
                        "content": "Orbits are elliptical.",
                        "created_at": "t",
                        "status": "complete",
                    },
                ]
                store.save_chat(chat)

                ended = client.post(f"/api/chats/{chat_id}/end").json()
                self.assertEqual(ended["status"], "ended")
                status = client.get("/api/wiki/status").json()
                self.assertTrue(status["ok"])
                self.assertEqual(status["note_count"], 0)

                resumed = client.post(f"/api/chats/{chat_id}/resume").json()
                self.assertEqual(resumed["status"], "open")

                toggled = client.patch(f"/api/chats/{chat_id}", json={"wiki_enabled": False}).json()
                self.assertFalse(toggled["wiki_enabled"])
                blocked = client.post(f"/api/chats/{chat_id}/wiki-sync")
                self.assertEqual(blocked.status_code, 422)

    def test_scribe_mocked_writes_notes(self):
        from agent_runtime import AgentResult
        from wiki_scribe import run_scribe

        with tempfile.TemporaryDirectory() as directory:
            vault_dir = Path(directory) / "vault"
            vault_dir.mkdir()

            def fake_run_tool_loop(**kwargs):
                handler = kwargs["tool_handler"]
                self.assertEqual(kwargs["model"], "gemma-local")
                handler(
                    "wiki_write",
                    {
                        "path": "Conversations/Test.md",
                        "content": "---\ntype: conversation\n---\n\n# Test\n\nSee [[Orbits]].\n",
                    },
                )
                handler(
                    "wiki_ensure_note",
                    {
                        "title": "Orbits",
                        "folder": "Concepts",
                        "template": "# Orbits\n\nElliptical paths.\n",
                    },
                )
                return AgentResult(content="done")

            with patch("wiki_scribe.run_tool_loop", side_effect=fake_run_tool_loop):
                chat = {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "title": "Orbits chat",
                    "model_id": "gemma-local",
                    "messages": [
                        {"role": "user", "content": "What is an orbit?"},
                        {"role": "assistant", "content": "A path around a body.", "status": "complete"},
                    ],
                }
                vault = WikiVault(vault_dir)
                result = run_scribe(chat=chat, vault=vault, base_url="http://127.0.0.1:1234")
                self.assertTrue((vault_dir / "Conversations" / "Test.md").exists())
                self.assertTrue((vault_dir / "Concepts" / "Orbits.md").exists())
                self.assertIn("Conversations/Test.md", getattr(result, "pages_touched", []))


if __name__ == "__main__":
    unittest.main()
