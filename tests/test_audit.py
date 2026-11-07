"""Test del registro di audit: redazione, rotazione e integrazione MCP."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from agent_memory.audit import AuditLog, redact, summarize_arguments
from agent_memory.server import create_server
from tests.support import copy_profile


class AuditRedactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_sensitive_values_and_phone_numbers_are_not_written(self) -> None:
        log = AuditLog(self.root)
        self.assertTrue(
            log.record(
                "external_action",
                recipient="+39 333 1234567",
                message="Ciao Marco, ci vediamo domani",
                token="sk-live-segreto-123456789",
                nested={"authorization": "Bearer abcdefghijk"},
                count=2,
            )
        )
        raw = log.path.read_text(encoding="utf-8")
        self.assertNotIn("333", raw)
        self.assertNotIn("Ciao Marco", raw)
        self.assertNotIn("sk-live", raw)
        self.assertNotIn("abcdefghijk", raw)
        item = json.loads(raw)
        self.assertEqual(item["recipient"], "<redacted>")
        self.assertEqual(item["message"], "<redacted>")
        self.assertEqual(item["token"], "<redacted>")
        self.assertEqual(item["nested"]["authorization"], "<redacted>")
        self.assertEqual(item["count"], 2)

    def test_tool_summary_contains_keys_but_not_arguments(self) -> None:
        self.assertEqual(
            summarize_arguments({"message": "privato", "limit": 3}),
            {
                "arg_keys": ["limit", "message"],
                "arg_key_count": 2,
                "arg_bytes": 34,
            },
        )
        log = AuditLog(self.root)
        self.assertTrue(
            log.record_tool_call(
                "deliver_impulse",
                {"message": "Ciao Marco", "phone": "+39 333 1234567"},
                outcome="ok",
                duration_ms=1.25,
            )
        )
        raw = log.path.read_text(encoding="utf-8")
        self.assertNotIn("Ciao Marco", raw)
        self.assertNotIn("333", raw)
        self.assertIn("arg_keys", raw)
        self.assertIn("deliver_impulse", raw)

    def test_phone_is_masked_even_under_a_neutral_key(self) -> None:
        value = redact("+39 333 1234567", key="nota_neutra")
        self.assertNotIn("333", value)
        self.assertIn("<redacted>", value)

    def test_rotation_keeps_the_log_bounded(self) -> None:
        log = AuditLog(self.root, max_bytes=250, rotated=2)
        for index in range(20):
            self.assertTrue(log.record("evento", index=index))
        self.assertTrue(log.path.is_file())
        self.assertTrue((self.root / "data" / "logs" / "audit.1.jsonl").is_file())
        status = log.status()
        self.assertGreaterEqual(status["entries"], 1)
        self.assertTrue(status["rotated"])

    def test_server_call_is_audited_without_values(self) -> None:
        copy_profile(self.root)
        mcp = create_server(self.root)

        async def invoke() -> None:
            await mcp.call_tool("life_status", {})

        asyncio.run(invoke())
        path = self.root / "data" / "logs" / "audit.jsonl"
        self.assertTrue(path.is_file())
        events = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        tool_events = [item for item in events if item.get("event") == "tool_call"]
        self.assertTrue(tool_events)
        event = tool_events[-1]
        self.assertEqual(event["tool"], "life_status")
        self.assertEqual(event["outcome"], "ok")
        self.assertNotIn("arguments", event)


if __name__ == "__main__":
    unittest.main()
