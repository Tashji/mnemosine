"""Test end-to-end del trasporto MCP stdio.

Avvia un processo ``python -m agent_memory.server`` su una copia temporanea
del progetto, inizializza una sessione MCP e invoca i tool come farebbe un
client esterno.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StdioTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copytree(PROJECT_ROOT / "config", self.root / "config")
        shutil.copytree(
            PROJECT_ROOT / "data",
            self.root / "data",
            ignore=shutil.ignore_patterns("models", "indexes"),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_stdio_session_and_tools(self) -> None:
        async def exercise() -> None:
            params = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "agent_memory.server",
                    "--root",
                    str(self.root),
                ],
                cwd=str(PROJECT_ROOT),
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    initialize = await session.initialize()
                    self.assertEqual(initialize.serverInfo.name, "agent-memory")

                    tools = await session.list_tools()
                    names = {tool.name for tool in tools.tools}
                    self.assertIn("get_identity", names)
                    self.assertIn("create_memory", names)
                    self.assertIn("search_memory", names)
                    self.assertIn("search_semantic_memory", names)
                    self.assertFalse(any("shadow" in name for name in names))

                    result = await session.call_tool("get_identity", {})
                    self.assertFalse(result.isError)
                    identity = json.loads(result.content[0].text)
                    self.assertEqual(identity["personal_data"]["full_name"], "Alessandro Vitale")

                    result = await session.call_tool(
                        "search_memories", {"query": "Marco", "limit": 5}
                    )
                    self.assertFalse(result.isError)
                    search = json.loads(result.content[0].text)
                    self.assertEqual(
                        [memory["id"] for memory in search["memories"]],
                        ["mem_0004"],
                    )

                    result = await session.call_tool(
                        "get_memory", {"memory_id": "mem_0002"}
                    )
                    self.assertTrue(result.isError)
                    self.assertIn("non trovato", result.content[0].text.lower())

                    semantic_result = await session.call_tool(
                        "search_semantic_memory",
                        {"query": "Python", "mode": "lexical", "limit": 5},
                    )
                    self.assertFalse(semantic_result.isError)
                    semantic_payload = json.loads(semantic_result.content[0].text)
                    self.assertIn(
                        "sem_0002",
                        {
                            item["record_id"]
                            for item in semantic_payload["results"]
                        },
                    )

                    created_result = await session.call_tool(
                        "create_memory",
                        {
                            "title": "Ricordo creato via stdio",
                            "description": "Un test end-to-end del server MCP.",
                            "source": "generated",
                            "importance": 3,
                        },
                    )
                    self.assertFalse(created_result.isError)
                    created = json.loads(created_result.content[0].text)
                    self.assertTrue(created["id"].startswith("mem_"))
                    self.assertEqual(created["source"], "generated")

                    read_result = await session.call_tool(
                        "get_memory", {"memory_id": created["id"]}
                    )
                    self.assertFalse(read_result.isError)
                    self.assertEqual(
                        json.loads(read_result.content[0].text)["title"],
                        "Ricordo creato via stdio",
                    )

                    status_result = await session.call_tool("get_status", {})
                    self.assertFalse(status_result.isError)
                    status = json.loads(status_result.content[0].text)
                    self.assertTrue(status["ok"])
                    self.assertFalse(
                        status["shadow_policy"]["access_policy"]["conscious_direct_read"]
                    )

        asyncio.run(exercise())
