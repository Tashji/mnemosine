"""Test della superficie MCP della Fase 3."""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from agent_memory.embeddings import DeterministicEmbedder
from agent_memory.retrieval import RetrievalService
from agent_memory.server import create_server

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def call_tool(mcp: FastMCP, name: str, arguments: dict[str, Any]) -> Any:
    _content, structured = asyncio.run(mcp.call_tool(name, arguments))
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    return structured


class McpSemanticTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copytree(
            PROJECT_ROOT / "config",
            self.root / "config",
            ignore=shutil.ignore_patterns("models", "indexes"),
        )
        shutil.copytree(
            PROJECT_ROOT / "data",
            self.root / "data",
            ignore=shutil.ignore_patterns("models", "indexes"),
        )
        shutil.rmtree(self.root / "data" / "indexes", ignore_errors=True)
        vector_path = self.root / "data" / "vector_index.db"
        with sqlite3.connect(vector_path) as conn:
            conn.execute("DELETE FROM vector_entries")
            conn.execute("DELETE FROM index_meta")
            conn.commit()

        self.embedder = DeterministicEmbedder()
        self.index_service = RetrievalService(self.root, embedder=self.embedder)
        self.index_service.sync_index(rebuild=True)
        self.mcp = create_server(self.root, embedder=self.embedder)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_tool_surface_includes_semantic_and_still_hides_shadow(self) -> None:
        tools = asyncio.run(self.mcp.list_tools())
        names = {tool.name for tool in tools}
        for expected in (
            "search_memory",
            "search_semantic_memory",
            "get_semantic_item",
            "list_semantic_memory",
            "create_semantic_item",
            "get_semantic_links",
        ):
            self.assertIn(expected, names)
        self.assertFalse(any("shadow" in name for name in names))
        self.assertNotIn("get_shadow_traits", names)

    def test_hybrid_and_semantic_search_tools(self) -> None:
        hybrid = call_tool(
            self.mcp,
            "search_memory",
            {"query": "bicicletta", "min_similarity": 0.0, "limit": 6},
        )
        self.assertGreater(hybrid["count"], 0)
        ids = {item["record_id"] for item in hybrid["results"]}
        self.assertIn("mem_0001", ids)
        self.assertIn("sem_0001", ids)
        self.assertNotIn("mem_0002", ids)

        semantic = call_tool(
            self.mcp,
            "search_semantic_memory",
            {"query": "programmazione", "min_similarity": 0.0},
        )
        self.assertTrue(semantic["results"])
        self.assertTrue(
            all(item["kind"] == "semantic" for item in semantic["results"])
        )
        self.assertIn("sem_0002", {item["record_id"] for item in semantic["results"]})

        with self.assertRaises(ToolError):
            call_tool(
                self.mcp,
                "search_memory",
                {"query": "test", "mode": "invented"},
            )

    def test_semantic_read_tools(self) -> None:
        listed = call_tool(self.mcp, "list_semantic_memory", {"limit": 100})
        self.assertEqual(listed["count"], 9)

        item = call_tool(self.mcp, "get_semantic_item", {"item_id": "sem_0001"})
        self.assertEqual(item["title"], "Una bicicletta è un veicolo a due ruote")

        links = call_tool(self.mcp, "get_semantic_links", {"item_id": "sem_0001"})
        self.assertTrue(
            any(link["memory_id"] == "mem_0001" for link in links["episodic_links"])
        )

        with self.assertRaises(ToolError):
            call_tool(self.mcp, "get_semantic_item", {"item_id": "sem_unknown"})

    def test_semantic_write_tool_restrictions(self) -> None:
        with self.assertRaises(ToolError):
            call_tool(
                self.mcp,
                "create_semantic_item",
                {
                    "title": "Fatto non verificato",
                    "content": "Non deve essere accettato.",
                    "category": "test",
                    "knowledge_type": "verified_fact",
                },
            )
        with self.assertRaises(ToolError):
            call_tool(
                self.mcp,
                "create_semantic_item",
                {
                    "title": "Seed non autorizzato",
                    "content": "Non deve essere accettato.",
                    "category": "test",
                    "source": "admin_seed",
                },
            )

        created = call_tool(
            self.mcp,
            "create_semantic_item",
            {
                "title": "Una conoscenza appresa",
                "content": "L'utente ha spiegato un nuovo concetto durante la chat.",
                "category": "conversazione",
                "knowledge_type": "user_provided",
                "source": "conversation",
                "tags": ["chat", "concetto"],
            },
        )
        self.assertTrue(created["id"].startswith("sem_"))
        self.assertEqual(created["knowledge_type"], "user_provided")
        self.assertIn(created["index"].get("status"), {"indexed", "updated"})
        self.assertIsNotNone(
            call_tool(self.mcp, "get_semantic_item", {"item_id": created["id"]})
        )

    def test_mcp_created_memory_is_indexed_when_ready(self) -> None:
        memory = call_tool(
            self.mcp,
            "create_memory",
            {
                "title": "Ricordo creato dopo l'indice",
                "description": "Un ricordo generato via MCP.",
                "source": "generated",
                "tags": ["mcp", "indice"],
            },
        )
        self.assertIn(memory["index"].get("status"), {"indexed", "updated"})
        found = call_tool(
            self.mcp,
            "search_memory",
            {"query": "ricordo creato dopo l'indice", "min_similarity": 0.0},
        )
        self.assertIn(
            memory["id"], {item["record_id"] for item in found["results"]}
        )

    def test_status_includes_semantic_and_index(self) -> None:
        status = call_tool(self.mcp, "get_status", {})
        self.assertTrue(status["ok"])
        self.assertEqual(status["semantic_memory"]["counts"]["items"], 9)
        self.assertTrue(status["vector_index"]["ok"])
        self.assertTrue(status["index_ready"])
        self.assertFalse(status["shadow_policy"]["access_policy"]["conscious_direct_read"])


if __name__ == "__main__":
    unittest.main()
