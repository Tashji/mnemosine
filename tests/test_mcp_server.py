"""Test del server MCP e della sua superficie pubblica."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
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
        self.assertFalse(any("shadow" in name for name in names))
        self.assertNotIn("get_shadow_traits", names)
        self.assertNotIn("list_shadow_entries", names)

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

        with self.assertRaises(ToolError):
            call_tool(mcp, "browser_click", {"selector": "#send"})
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

    def test_status_contains_no_shadow_content(self) -> None:
        status = call_tool(self.mcp, "get_status", {})
        self.assertTrue(status["ok"])
        self.assertTrue(status["databases"]["timeline_episodic"]["ok"])
        self.assertTrue(status["databases"]["shadow_diary"]["ok"])
        self.assertFalse(status["shadow_policy"]["access_policy"]["conscious_direct_read"])
        self.assertNotIn("traits", status["shadow_policy"])
        self.assertNotIn("entries", status["shadow_policy"])
