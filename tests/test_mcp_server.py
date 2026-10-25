"""Test del server MCP e della sua superficie pubblica."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from agent_memory.browser import BrowserController, BrowserPolicy
from agent_memory.server import create_server

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def call_tool(mcp: FastMCP, name: str, arguments: dict[str, Any]) -> Any:
    """Invoca un tool e normalizza la forma strutturata di FastMCP.

    FastMCP restituisce ``(content, structured)``. Per i valori di ritorno che
    non sono oggetti, lo structured content è incapsulato in ``{"result": ...}``.
    """

    _content, structured = asyncio.run(mcp.call_tool(name, arguments))
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    return structured


class McpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copytree(PROJECT_ROOT / "config", self.root / "config")
        shutil.copytree(
            PROJECT_ROOT / "data",
            self.root / "data",
            ignore=shutil.ignore_patterns("models", "indexes"),
        )
        self.mcp = create_server(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_tool_surface_does_not_expose_shadow(self) -> None:
        tools = asyncio.run(self.mcp.list_tools())
        names = {tool.name for tool in tools}
        self.assertIn("get_identity", names)
        self.assertIn("create_memory", names)
        self.assertIn("browser_status", names)
        self.assertIn("browser_open", names)
        self.assertIn("browser_links", names)
        self.assertIn("browser_open_link", names)
        self.assertIn("browser_back", names)
        self.assertIn("browser_elements", names)
        self.assertIn("browser_press", names)
        self.assertIn("browser_wait", names)
        self.assertIn("list_files", names)
        self.assertIn("read_file", names)
        self.assertIn("write_file", names)
        self.assertFalse(any("shadow" in name for name in names))
        self.assertNotIn("get_shadow_traits", names)
        self.assertNotIn("list_shadow_entries", names)
        self.assertIn("record_interaction", names)
        self.assertIn("acknowledge_impulse", names)
        self.assertIn("deliver_impulse", names)

    def test_identity_and_state_tools(self) -> None:
        identity = call_tool(self.mcp, "get_identity", {})
        self.assertEqual(identity["personal_data"]["full_name"], "Alessandro Vitale")

        updated = call_tool(
            self.mcp,
            "update_current_state",
            {"patch": {"mental_load": 48, "emotion": {"label": "sereno"}}},
        )
        self.assertEqual(updated["mental_load"], 48)
        self.assertEqual(updated["emotion"]["label"], "sereno")
        self.assertEqual(updated["emotion"]["valence"], 0.35)

    def test_heartbeat_tools_update_load_and_clear_impulses(self) -> None:
        before = call_tool(self.mcp, "get_current_state", {})["mental_load"]

        updated = call_tool(
            self.mcp,
            "record_interaction",
            {"messages": 2, "complexity": "complessa"},
        )

        self.assertEqual(updated["mental_load"], min(100, before + 14))
        self.assertEqual(updated["messages"], 2)
        self.assertEqual(updated["availability"]["response_style"], "normale")

        acknowledged = call_tool(
            self.mcp, "acknowledge_impulse", {"note": "nessun messaggio"}
        )
        self.assertFalse(acknowledged["spontaneous_message_pending"])
        self.assertIsNone(acknowledged["pending_impulse"])
        self.assertEqual(acknowledged["last_decision"], "impulso_gestito")

    def test_record_interaction_refuses_invalid_complexity(self) -> None:
        with self.assertRaises(ToolError):
            call_tool(self.mcp, "record_interaction", {"complexity": "enorme"})

    def test_deliver_impulse_respects_whitelist_and_flags(self) -> None:
        with self.assertRaises(ToolError) as ctx:
            call_tool(self.mcp, "deliver_impulse", {"text": "Ciao Marco"})
        self.assertIn("nessun impulso in attesa", str(ctx.exception))

        with self.assertRaises(ToolError) as flag_error:
            call_tool(
                self.mcp,
                "update_current_state",
                {"patch": {"flags": {"whatsapp_sending_enabled": True}}},
            )
        self.assertIn("whatsapp_sending_enabled", str(flag_error.exception))

        state_path = self.root / "config" / "current_state.json"
        state = json.loads(state_path.read_text("utf-8"))
        state["flags"]["whatsapp_sending_enabled"] = True
        state["heartbeat"]["pending_impulse"] = {
            "trigger": "relazione",
            "target": {"user_id": "user_marco", "display_name": "Marco"},
            "context": {},
            "created_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
            "delivery": {"channel": "whatsapp", "authorized": True, "note": ""},
            "requires_message": True,
        }
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

        integrations_path = self.root / "config" / "integrations.json"
        integrations = json.loads(integrations_path.read_text("utf-8"))
        integrations["whatsapp"] = {
            "enabled": True,
            "allowed_contacts": [
                {"user_id": "user_marco", "display_name": "Marco", "phone": "+39"}
            ],
            "quiet_hours": {"start": "00:00", "end": "07:00"},
            "max_per_day": 3,
        }
        integrations_path.write_text(
            json.dumps(integrations, ensure_ascii=False), encoding="utf-8"
        )

        result = call_tool(self.mcp, "deliver_impulse", {"text": "Ciao Marco"})
        self.assertTrue(result["delivered"])
        self.assertEqual(result["target"]["user_id"], "user_marco")
        self.assertEqual(result["transport"], "outbox")

        state = json.loads(state_path.read_text("utf-8"))
        self.assertIsNone(state["heartbeat"]["pending_impulse"])

    def test_world_history_and_social_tools(self) -> None:
        events_payload = call_tool(
            self.mcp,
            "get_world_history",
            {"start_date": "2020-01-01", "end_date": "2021-12-31"},
        )
        events = events_payload["events"]
        self.assertTrue(events)
        self.assertEqual(events_payload["count"], len(events))
        self.assertTrue(all("2020-01-01" <= event["date"] <= "2021-12-31" for event in events))

        graph = call_tool(self.mcp, "get_social_graph", {})
        self.assertEqual(len(graph["people"]), 3)
        marco = call_tool(self.mcp, "get_person", {"user_id": "user_marco"})
        self.assertEqual(marco["display_name"], "Marco")
        with self.assertRaises(ToolError):
            call_tool(self.mcp, "get_person", {"user_id": "user_unknown"})

    def test_search_and_memory_hiding(self) -> None:
        memories_payload = call_tool(self.mcp, "search_memories", {"limit": 100})
        memories = memories_payload["memories"]
        ids = {memory["id"] for memory in memories}
        self.assertTrue(
            {"mem_0001", "mem_0003", "mem_0004", "mem_0005"} <= ids
        )
        self.assertEqual(memories_payload["count"], len(memories))
        self.assertNotIn("mem_0002", ids)

        found = call_tool(self.mcp, "search_memories", {"query": "Marco"})["memories"]
        self.assertEqual([memory["id"] for memory in found], ["mem_0004"])

        with self.assertRaises(ToolError):
            call_tool(self.mcp, "get_memory", {"memory_id": "mem_0002"})

    def test_create_memory_tool_restricts_sources(self) -> None:
        with self.assertRaises(ToolError):
            call_tool(
                self.mcp,
                "create_memory",
                {
                    "title": "Interazione non autorizzata",
                    "description": "Non deve essere creata.",
                    "source": "real_interaction",
                },
            )

        memory = call_tool(
            self.mcp,
            "create_memory",
            {
                "title": "Ricordo creato via MCP",
                "description": "Un ricordo generato dall'agente.",
                "source": "generated",
                "importance": 4,
                "tags": ["mcp", "test"],
            },
        )
        self.assertTrue(memory["id"].startswith("mem_"))
        self.assertEqual(memory["source"], "generated")
        self.assertEqual(memory["is_simulated"], 1)
        self.assertEqual(memory["access_level"], "conscious")
        self.assertIn(memory["id"], {
            item["id"]
            for item in call_tool(self.mcp, "search_memories", {"query": "MCP"})["memories"]
        })

    def test_links_tool(self) -> None:
        links_payload = call_tool(
            self.mcp, "get_memory_links", {"memory_id": "mem_0004"}
        )
        links = links_payload["links"]
        self.assertTrue(any(link["to_memory_id"] == "mem_0005" for link in links))
        self.assertEqual(
            call_tool(self.mcp, "get_memory_links", {"memory_id": "mem_0002"})["links"],
            [],
        )

    def test_browser_tools_with_enabled_controller(self) -> None:
        class FakeBackend:
            url = ""

            def open(self, url: str) -> None:
                self.url = url

            def title(self) -> str:
                return "Esempio"

            def text(self) -> str:
                return "Testo della pagina"

            def click(self, selector: str) -> None:
                self.url = f"{self.url}#{selector}"

            def fill(self, selector: str, value: str) -> None:
                self.url = f"{self.url}#{selector}"

            def elements(self, limit: int) -> dict[str, Any]:
                return {
                    "elements": [
                        {
                            "index": 1,
                            "tag": "input",
                            "role": "",
                            "type": "search",
                            "label": "Cerca",
                            "value": "",
                            "multiline": False,
                        },
                        {
                            "index": 2,
                            "tag": "div",
                            "role": "textbox",
                            "type": "",
                            "label": "Scrivi",
                            "value": "",
                            "multiline": True,
                        },
                    ][:limit],
                    "total": 2,
                }

            def click_element(self, index: int) -> None:
                self.url = f"{self.url}#elemento{index}"

            def fill_element(self, index: int, value: str) -> None:
                self.url = f"{self.url}#elemento{index}"

            def press(
                self,
                key: str,
                *,
                selector: str | None = None,
                index: int | None = None,
            ) -> None:
                self.url = f"{self.url}#tasto{key}"

            def wait(
                self,
                *,
                selector: str | None = None,
                text: str | None = None,
                timeout_ms: int | None = None,
            ) -> None:
                return None

            def links(self) -> list[dict[str, str]]:
                return [
                    {"text": "Forum", "url": "https://example.com/forum"},
                    {"text": "Altrove", "url": "https://evil.test/"},
                ]

            def screenshot(self, path: Path) -> None:
                path.write_bytes(b"png")

            def close(self) -> None:
                self.url = ""

        controller = BrowserController(
            policy=BrowserPolicy(
                ["https://example.com"],
                enabled=True,
                resolver=lambda _host: ["93.184.216.34"],
            ),
            backend=FakeBackend(),
        )
        mcp = create_server(self.root, browser=controller)
        status = call_tool(mcp, "browser_status", {})
        self.assertTrue(status["enabled"])
        self.assertEqual(status["current_url"], None)

        opened = call_tool(
            mcp,
            "browser_open",
            {"url": "https://example.com", "max_chars": 4},
        )
        self.assertEqual(opened["title"], "Esempio")
        self.assertEqual(opened["text"], "Test")
        self.assertTrue(opened["truncated"])

        links = call_tool(mcp, "browser_links", {})
        self.assertEqual(links["count"], 1)
        self.assertEqual(links["blocked_count"], 1)
        self.assertEqual(links["links"][0]["index"], 1)

        moved = call_tool(mcp, "browser_open_link", {"index": 1})
        self.assertEqual(moved["url"], "https://example.com/forum")

        previous = call_tool(mcp, "browser_back", {})
        self.assertEqual(previous["url"], "https://example.com")
        self.assertFalse(call_tool(mcp, "browser_status", {})["can_go_back"])

        elements = call_tool(mcp, "browser_elements", {"limit": 5})
        self.assertEqual(elements["count"], 2)
        self.assertFalse(elements["truncated"])
        self.assertEqual(
            [item["index"] for item in elements["elements"]], [1, 2]
        )

        waited = call_tool(mcp, "browser_wait", {"text": "Testo"})
        self.assertEqual(waited["url"], "https://example.com")

        with self.assertRaises(ToolError):
            call_tool(mcp, "browser_click", {"selector": "#send"})
        with self.assertRaises(ToolError):
            call_tool(mcp, "browser_press", {"key": "Enter"})
        call_tool(mcp, "browser_close", {})
        self.assertIsNone(controller.current_url)

    def test_browser_is_disabled_by_default(self) -> None:
        status = call_tool(self.mcp, "browser_status", {})
        self.assertFalse(status["enabled"])
        self.assertIsNone(status["current_url"])
        self.assertEqual(status["allowed_origins"], [])
        with self.assertRaises(ToolError):
            call_tool(
                self.mcp,
                "browser_open",
                {"url": "https://example.com"},
            )

    def test_file_tools_are_sandboxed(self) -> None:
        created = call_tool(
            self.mcp, "write_file", {"path": "note/diario.txt", "content": "ciao"}
        )
        self.assertEqual(created["path"], "note/diario.txt")
        self.assertTrue(created["created"])

        read = call_tool(self.mcp, "read_file", {"path": "note/diario.txt"})
        self.assertEqual(read["text"], "ciao")

        listing = call_tool(self.mcp, "list_files", {})
        self.assertEqual([entry["name"] for entry in listing["entries"]], ["note"])

        with self.assertRaises(ToolError):
            call_tool(self.mcp, "read_file", {"path": "../config/identity.json"})
        with self.assertRaises(ToolError):
            call_tool(self.mcp, "read_file", {"path": "/etc/passwd"})
        with self.assertRaises(ToolError):
            call_tool(
                self.mcp, "write_file", {"path": "note/diario.txt", "content": "x"}
            )

    def test_status_contains_no_shadow_content(self) -> None:
        status = call_tool(self.mcp, "get_status", {})
        self.assertTrue(status["ok"])
        self.assertTrue(status["databases"]["timeline_episodic"]["ok"])
        self.assertTrue(status["databases"]["shadow_diary"]["ok"])
        self.assertFalse(status["shadow_policy"]["access_policy"]["conscious_direct_read"])
        self.assertNotIn("traits", status["shadow_policy"])
        self.assertNotIn("entries", status["shadow_policy"])
