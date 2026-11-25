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
    DEFAULT_POLICY,
    SENSITIVE_CATEGORIES,
    AcquaintanceService,
    AcquaintanceStore,
    extract_facts,
    is_sensitive,
    visible_person,
)
from agent_memory.config import ConfigStore
from agent_memory.consistency import ConsistencyChecker
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

    def test_extraction_creates_candidates_and_review_queue(self) -> None:
        service = AcquaintanceService(self.root)
        person, candidates = service.store.extract(
            "Mi chiamo Luca Bianchi, ho 34 anni e abito a Torino. "
            "Lavoro come insegnante di storia. Mi piace la fotografia.",
            display_name="Luca",
            channel="forum",
            handle="luca84",
        )
        self.assertEqual(person["display_name"], "Luca")
        values = {candidate["value"] for candidate in candidates}
        self.assertIn("Luca Bianchi", values)
        self.assertIn("34 anni", values)
        self.assertIn("Torino", values)
        self.assertIn("insegnante di storia", values)
        self.assertIn("fotografia", values)
        self.assertTrue(
            all(
                fact["status"] == "candidate" and fact["origin"] == "extracted"
                for fact in person["facts"]
            )
        )
        snapshot = service.snapshot()
        self.assertEqual(snapshot["stats"]["candidates"], len(candidates))
        self.assertEqual(snapshot["review"][0]["display_name"], "Luca")
        self.assertEqual(snapshot["policy"], DEFAULT_POLICY)
        with self.assertRaises(ValidationError):
            service.store.extract("Ciao, come stai?", display_name="Nessuno")
        self.assertIsNone(
            AcquaintanceStore(self.root).get("nessuno")
        )

    def test_agent_policy_filters_facts(self) -> None:
        service = AcquaintanceService(self.root)
        person = service.store.create("Nina", channel="chat", handle="nina")
        observed = service.store.observe(
            person_id=person["person_id"], category="city", value="Bologna"
        )
        observed = service.store.observe(
            person_id=person["person_id"], category="health", value="Asma"
        )
        health = next(
            fact for fact in observed["facts"] if fact["category"] == "health"
        )
        decorated = service.person(person["person_id"])
        policy = service.store.read()["policy"]
        visible = visible_person(decorated, policy)
        self.assertEqual(visible["facts"], [])
        self.assertEqual(visible["hidden_facts"], 2)

        service.store.save_policy({"agent_fact_visibility": "all"})
        visible = visible_person(
            decorated, service.store.read()["policy"]
        )
        self.assertEqual([fact["category"] for fact in visible["facts"]], ["city"])
        self.assertEqual(visible["hidden_facts"], 1)

        service.store.set_fact_status(
            person["person_id"], health["id"], "confirmed", allow_sensitive=True
        )
        service.store.save_policy({"agent_sensitive_access": True})
        visible = visible_person(
            service.person(person["person_id"]), service.store.read()["policy"]
        )
        statuses = {fact["category"]: fact["status"] for fact in visible["facts"]}
        self.assertEqual(statuses["health"], "confirmed")

        with self.assertRaises(ValidationError):
            service.store.save_policy({"chiave": True})
        with self.assertRaises(ConfigError):
            service.store.save_policy({"retention_days": 0})
        with self.assertRaises(ConfigError):
            service.store.save_policy({"agent_fact_visibility": "tutto"})

    def test_promotion_copies_only_confirmed_non_sensitive_facts(self) -> None:
        service = AcquaintanceService(self.root)
        person = service.store.create("Nina", channel="chat", handle="nina")
        service.store.observe(
            person_id=person["person_id"], category="city", value="Bologna"
        )
        observed = service.store.observe(
            person_id=person["person_id"], category="health", value="Asma"
        )
        for fact in observed["facts"]:
            service.store.set_fact_status(
                person["person_id"], fact["id"], "confirmed", allow_sensitive=True
            )
        with self.assertRaisesRegex(ValidationError, "PROMUOVI"):
            service.save(
                {"action": "promote", "person_id": person["person_id"]}
            )
        with self.assertRaises(ValidationError):
            service.save(
                {
                    "action": "promote",
                    "person_id": person["person_id"],
                    "confirmation": "PROMUOVI",
                    "trust": True,
                }
            )
        result = service.save(
            {
                "action": "promote",
                "person_id": person["person_id"],
                "relation": "Amica",
                "trust": 60,
                "intimacy": 25,
                "confirmation": "PROMUOVI",
            }
        )
        self.assertEqual(result["user_id"], person["person_id"])
        contact = next(
            item
            for item in ConfigStore(self.root).get("social_graph")["people"]
            if item["user_id"] == person["person_id"]
        )
        self.assertEqual(contact["relation"], "Amica")
        self.assertEqual(contact["trust"], 60)
        self.assertIn("Città: Bologna", contact["confirmed_facts"])
        self.assertFalse(
            any("Asma" in item for item in contact["confirmed_facts"])
        )
        self.assertIn("Promosso dal registro", contact["notes"])
        promoted = service.store.get(person["person_id"])
        self.assertEqual(promoted["status"], "known")
        self.assertTrue(promoted["promoted_at"])
        self.assertEqual(promoted["promoted_user_id"], person["person_id"])
        with self.assertRaises(ValidationError):
            service.save(
                {
                    "action": "promote",
                    "person_id": person["person_id"],
                    "confirmation": "PROMUOVI",
                }
            )

    def test_retention_export_and_consistency(self) -> None:
        service = AcquaintanceService(self.root)
        person = service.store.create("Vecchia", channel="forum", handle="old")
        observed = service.store.observe(
            person_id=person["person_id"], category="city", value="Roma"
        )
        fact = observed["facts"][0]
        service.store.set_fact_status(
            person["person_id"], fact["id"], "rejected"
        )
        document = service.store.read()
        document["people"][0]["last_seen_at"] = "2000-01-01T00:00:00+00:00"
        document["people"][0]["facts"][0][
            "observed_at"
        ] = "2000-01-01T00:00:00+00:00"
        service.store._write(document)
        with self.assertRaises(ValidationError):
            service.store.apply_retention(30, confirmation="no")
        result = service.store.apply_retention(30, confirmation="APPLICA")
        self.assertEqual(result["removed_facts"], 1)
        self.assertEqual(result["removed_people"], 1)
        exported = service.export()
        self.assertIn("exported_at", exported)
        self.assertEqual(exported["policy"], DEFAULT_POLICY)
        self.assertEqual(exported["people"], [])

        nina = service.store.create("Nina", channel="chat", handle="nina")
        service.save(
            {
                "action": "promote",
                "person_id": nina["person_id"],
                "confirmation": "PROMUOVI",
            }
        )
        config = ConfigStore(self.root)
        graph = config.get("social_graph")
        graph["people"] = [
            item
            for item in graph["people"]
            if item["user_id"] != nina["person_id"]
        ]
        config.write("social_graph", graph)
        check = ConsistencyChecker(self.root)._check_acquaintances()
        self.assertFalse(check["ok"])
        self.assertTrue(
            any("assente da social_graph" in error for error in check["errors"])
        )

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
            "extract_acquaintance_facts",
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
        # La policy predefinita nasconde i candidati all'agente.
        self.assertEqual(fetched["facts"], [])
        self.assertEqual(fetched["hidden_facts"], 1)

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

    def test_policy_and_extraction_tools(self) -> None:
        result = call_tool(
            self.mcp,
            "extract_acquaintance_facts",
            {
                "text": "Mi chiamo Elisa, ho 29 anni e abito a Parma. "
                "Mi piace il cinema.",
                "display_name": "Elisa",
                "channel": "forum",
                "handle": "elisa29",
            },
        )
        person = result["person"]
        self.assertGreaterEqual(result["count"], 3)
        self.assertTrue(
            all(fact["origin"] == "extracted" for fact in person["facts"])
        )
        fetched = call_tool(
            self.mcp, "get_acquaintance", {"person_id": person["person_id"]}
        )
        self.assertEqual(fetched["facts"], [])
        self.assertGreaterEqual(fetched["hidden_facts"], 3)

        service = AcquaintanceService(self.root)
        stored = service.store.get(person["person_id"])
        city = next(fact for fact in stored["facts"] if fact["category"] == "city")
        service.store.set_fact_status(
            person["person_id"], city["id"], "confirmed"
        )
        fetched = call_tool(
            self.mcp, "get_acquaintance", {"person_id": person["person_id"]}
        )
        self.assertEqual(
            [fact["category"] for fact in fetched["facts"]], ["city"]
        )
        self.assertGreaterEqual(fetched["hidden_facts"], 2)

        service.store.save_policy({"agent_fact_visibility": "all"})
        fetched = call_tool(
            self.mcp, "get_acquaintance", {"person_id": person["person_id"]}
        )
        self.assertGreaterEqual(len(fetched["facts"]), 4)
        listed = call_tool(self.mcp, "list_acquaintances", {})
        self.assertEqual(
            listed["agent_access"]["fact_visibility"], "all"
        )


if __name__ == "__main__":
    unittest.main()
