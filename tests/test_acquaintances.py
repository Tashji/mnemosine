"""Test del registro progressivo delle persone incontrate."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from agent_memory.acquaintances import (
    CORE_CATEGORIES,
    SENSITIVE_CATEGORIES,
    AcquaintanceService,
    AcquaintanceStore,
    is_sensitive,
)
from agent_memory.errors import ConfigError, ValidationError
from agent_memory.server import create_server
from tests.support import copy_profile


def call_tool(mcp: FastMCP, name: str, arguments: dict[str, Any]) -> Any:
    _content, structured = asyncio.run(mcp.call_tool(name, arguments))
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    return structured


class AcquaintanceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        copy_profile(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_registry_is_lazy_and_facts_are_candidates(self) -> None:
        store = AcquaintanceStore(self.root)
        path = self.root / "config" / "acquaintances.json"
        self.assertFalse(path.exists())

        person = store.create("Orso", channel="forum", handle="orso77")
        self.assertEqual(person["status"], "unknown")
        self.assertEqual(person["facts"], [])
        self.assertEqual(person["observation_count"], 1)

        person = store.observe(
            person_id=person["person_id"],
            category="city",
            value="Torino",
            confidence=0.4,
            source="thread presentazioni",
        )
        self.assertTrue(path.exists())
        self.assertEqual(len(person["facts"]), 1)
        fact = person["facts"][0]
        self.assertEqual(fact["status"], "candidate")
        self.assertFalse(fact["sensitive"])

        person = store.observe(
            person_id=person["person_id"],
            category="city",
            value="torino",
            confidence=0.9,
            source="secondo messaggio",
        )
        self.assertEqual(len(person["facts"]), 1)
        self.assertEqual(person["facts"][0]["confidence"], 0.9)
        self.assertEqual(person["facts"][0]["source"], "secondo messaggio")
        self.assertEqual(person["observation_count"], 3)

        confirmed = store.set_fact_status(
            person["person_id"], fact["id"], "confirmed"
        )
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertIsNotNone(confirmed["confirmed_at"])

    def test_sensitive_facts_need_dashboard_confirmation(self) -> None:
        store = AcquaintanceStore(self.root)
        person = store.create("Anon", channel="forum", handle="anon")
        person = store.observe(
            person_id=person["person_id"],
            category="health",
            value="Ha un problema al ginocchio",
        )
        fact = person["facts"][0]
        self.assertTrue(fact["sensitive"])
        with self.assertRaisesRegex(ValidationError, "dashboard"):
            store.set_fact_status(person["person_id"], fact["id"], "confirmed")
        confirmed = store.set_fact_status(
            person["person_id"],
            fact["id"],
            "confirmed",
            allow_sensitive=True,
        )
        self.assertEqual(confirmed["status"], "confirmed")
        for category in SENSITIVE_CATEGORIES:
            self.assertTrue(is_sensitive(category))

    def test_validation_and_lifecycle(self) -> None:
        store = AcquaintanceStore(self.root)
        with self.assertRaises(ValidationError):
            store.create(" ")
        with self.assertRaises(ValidationError):
            store.create("A", person_id="bad id!")
        first = store.create("A", person_id="persona_a")
        with self.assertRaises(ValidationError):
            store.create("B", person_id="persona_a")
        with self.assertRaises(ValidationError):
            store.observe(
                person_id=first["person_id"], category="boh", value="x"
            )
        with self.assertRaises(ValidationError):
            store.observe(
                person_id=first["person_id"],
                category="city",
                value="X",
                confidence=2,
            )
        with self.assertRaises(ValidationError):
            store.observe(
                person_id=first["person_id"], category="city", value=""
            )
        with self.assertRaises(ValidationError):
            store.update_person(first["person_id"], status="amico")

        updated = store.update_person(
            first["person_id"], status="known", notes="Solo un saluto"
        )
        self.assertEqual(updated["status"], "known")
        self.assertEqual(updated["notes"], "Solo un saluto")

        person = store.observe(
            person_id=first["person_id"], category="city", value="Roma"
        )
        fact = person["facts"][0]
        store.remove_fact(first["person_id"], fact["id"])
        self.assertEqual(store.get(first["person_id"])["facts"], [])

        store.remove(first["person_id"])
        self.assertIsNone(store.get(first["person_id"]))
        with self.assertRaises(ValidationError):
            store.remove(first["person_id"])

    def test_malformed_registry_is_rejected(self) -> None:
        path = self.root / "config" / "acquaintances.json"
        path.write_text("{", encoding="utf-8")
        with self.assertRaises(ConfigError):
            AcquaintanceStore(self.root).read()
        path.write_text(
            '{"schema_version": 1, "people": [{"person_id": "x"}]}',
            encoding="utf-8",
        )
        with self.assertRaises(ConfigError):
            AcquaintanceStore(self.root).read()

    def test_snapshot_decorates_completeness(self) -> None:
        service = AcquaintanceService(self.root)
        person = service.store.create("Nina", channel="chat", handle="nina")
        service.store.observe(
            person_id=person["person_id"], category="city", value="Bologna"
        )
        service.store.observe(
            person_id=person["person_id"],
            category="interests",
            value="fotografia",
        )
        snapshot = service.snapshot()
        self.assertEqual(snapshot["count"], 1)
        profile = snapshot["people"][0]
        self.assertEqual(profile["fact_counts"]["candidate"], 2)
        self.assertEqual(
            profile["completeness"], round(100 / len(CORE_CATEGORIES))
        )

        fact = next(f for f in profile["facts"] if f["category"] == "city")
        service.store.set_fact_status(profile["person_id"], fact["id"], "confirmed")
        profile = service.snapshot()["people"][0]
        self.assertEqual(profile["fact_counts"]["confirmed"], 1)
        self.assertIn("category_label", profile["facts"][0])
        self.assertEqual(
            profile["completeness"], round(200 / len(CORE_CATEGORIES))
        )


class AcquaintanceMcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        copy_profile(self.root)
        self.mcp = create_server(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_tools_read_and_record_only_candidates(self) -> None:
        names = {tool.name for tool in asyncio.run(self.mcp.list_tools())}
        for name in (
            "list_acquaintances",
            "get_acquaintance",
            "record_acquaintance_observation",
        ):
            self.assertIn(name, names)

        result = call_tool(
            self.mcp,
            "record_acquaintance_observation",
            {
                "category": "city",
                "value": "Torino",
                "display_name": "Orso",
                "channel": "forum",
                "handle": "orso77",
                "source": "thread presentazioni",
            },
        )
        person = result["person"]
        self.assertEqual(person["display_name"], "Orso")
        self.assertEqual(person["status"], "unknown")
        self.assertEqual(person["facts"][0]["status"], "candidate")

        listed = call_tool(self.mcp, "list_acquaintances", {})
        self.assertEqual(listed["count"], 1)
        fetched = call_tool(
            self.mcp, "get_acquaintance", {"person_id": person["person_id"]}
        )
        self.assertEqual(fetched["person_id"], person["person_id"])

        result = call_tool(
            self.mcp,
            "record_acquaintance_observation",
            {
                "person_id": person["person_id"],
                "category": "health",
                "value": "Ha un ginocchio malandato",
            },
        )
        health = next(
            fact
            for fact in result["person"]["facts"]
            if fact["category"] == "health"
        )
        self.assertTrue(health["sensitive"])
        self.assertEqual(health["status"], "candidate")


if __name__ == "__main__":
    unittest.main()
