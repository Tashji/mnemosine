"""Test end-to-end del trasporto MCP stdio.

Avvia un processo ``python -m agent_memory.server`` su una copia temporanea
del progetto, inizializza una sessione MCP e invoca i tool come farebbe un
client esterno.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from tests.support import copy_profile

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StdioTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        copy_profile(self.root)

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
            async with (
                stdio_client(params) as (read, write),
                ClientSession(read, write) as session,
            ):
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

                write_result = await session.call_tool(
                    "write_file", {"path": "appunti/nota.txt", "content": "ciao agente"}
                )
                self.assertFalse(write_result.isError)
                self.assertEqual(
                    json.loads(write_result.content[0].text)["path"],
                    "appunti/nota.txt",
                )

                note_result = await session.call_tool(
                    "read_file", {"path": "appunti/nota.txt"}
                )
                self.assertFalse(note_result.isError)
                self.assertEqual(
                    json.loads(note_result.content[0].text)["text"], "ciao agente"
                )

                escape_result = await session.call_tool(
                    "read_file", {"path": "../../config/identity.json"}
                )
                self.assertTrue(escape_result.isError)

                links_result = await session.call_tool("browser_links", {})
                self.assertTrue(links_result.isError)
                self.assertIn("disabilitato", links_result.content[0].text.lower())

                listing_result = await session.call_tool("list_files", {"path": "appunti"})
                self.assertFalse(listing_result.isError)
                self.assertEqual(
                    json.loads(listing_result.content[0].text)["count"], 1
                )

                status_result = await session.call_tool("get_status", {})
                self.assertFalse(status_result.isError)
                status = json.loads(status_result.content[0].text)
                self.assertTrue(status["ok"])
                self.assertFalse(
                    status["shadow_policy"]["access_policy"]["conscious_direct_read"]
                )

        asyncio.run(exercise())

    def test_autonomy_uncertain_across_stdio_restart_and_admin_review(self) -> None:
        from datetime import UTC, datetime

        from agent_memory.autonomy import AutonomyService
        from agent_memory.config import ConfigStore

        config = ConfigStore(self.root)
        config.update_current_state({"flags": {"maintenance_mode": False}, "heartbeat": {
            "pending_impulse": {"trigger": "test", "created_at": datetime.now(UTC).isoformat()},
            "spontaneous_message_pending": True,
        }})
        (self.root / "config" / "integrations.json").write_text(json.dumps({"browser": {
            "enabled": True, "interaction_enabled": True, "allowed_origins": ["https://example.com"],
            "interaction_origins": ["https://example.com"],
        }}), encoding="utf-8")
        # The real MCP subprocess uses a deterministic fake backend and DNS;
        # it never accesses a network site or real browser session.
        code = '''
import sys
from pathlib import Path
import agent_memory.browser as browser
from agent_memory.browser import BrowserController
from agent_memory.errors import BrowserError
from agent_memory.server import create_server
from tests.test_browser import FakeBackend
root = Path(sys.argv[1])
browser._default_resolver = lambda host: ["93.184.216.34"]
class InterruptedBackend(FakeBackend):
    def open(self, url):
        with (root / "attempts.txt").open("a") as log:
            log.write("attempt\\n")
        super().open(url)
        raise BrowserError("risultato non disponibile")
create_server(root, browser=BrowserController(root, backend=InterruptedBackend())).run(transport="stdio")
'''
        params = StdioServerParameters(command=sys.executable, args=["-c", code, str(self.root)], cwd=str(PROJECT_ROOT))

        async def exercise() -> None:
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                claim = await session.call_tool("autonomous_claim", {"url": "https://example.com"})
                self.assertFalse(claim.isError)
                run_id = json.loads(claim.content[0].text)["id"]
                attempt = await session.call_tool("autonomous_act", {
                    "run_id": run_id, "action_id": "one", "action": "open", "arguments": {"url": "https://example.com"},
                })
                self.assertFalse(attempt.isError)
                self.assertEqual(json.loads(attempt.content[0].text)["state"], "uncertain")
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                status = await session.call_tool("autonomous_status", {"run_id": run_id})
                self.assertEqual(json.loads(status.content[0].text)["state"], "uncertain")
                retry = await session.call_tool("autonomous_act", {
                    "run_id": run_id, "action_id": "one", "action": "open", "arguments": {"url": "https://example.com"},
                })
                self.assertEqual(json.loads(retry.content[0].text)["state"], "uncertain")
                self.assertEqual((self.root / "attempts.txt").read_text().splitlines(), ["attempt"])
                review = AutonomyService(self.root, None).review_uncertain(run_id, "HO VERIFICATO", "Esaminato")
                self.assertTrue(review["impulse_cleared"])
                self.assertFalse(review["remote_delivery_verified"])
                denied = await session.call_tool("autonomous_act", {
                    "run_id": run_id, "action_id": "new", "action": "open", "arguments": {"url": "https://example.com"},
                })
                self.assertTrue(denied.isError)
                self.assertEqual((self.root / "attempts.txt").read_text().splitlines(), ["attempt"])

        asyncio.run(exercise())
