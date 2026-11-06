"""Test della memoria semantica della Fase 3."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_memory.errors import ConflictError, NotFoundError, ValidationError
from agent_memory.semantic import SemanticService
from tests.support import copy_profile


class SemanticServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        copy_profile(self.root)
        self.semantic = SemanticService(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_seed_archive_and_knowledge_types(self) -> None:
        items = self.semantic.list_items(limit=100)
        self.assertEqual(len(items), 9)
        by_id = {item["id"]: item for item in items}
        self.assertEqual(by_id["sem_0001"]["knowledge_type"], "verified_fact")
        self.assertEqual(by_id["sem_0005"]["knowledge_type"], "user_provided")
        self.assertEqual(by_id["sem_0006"]["knowledge_type"], "opinion")
        self.assertEqual(by_id["sem_0007"]["knowledge_type"], "hypothesis")
        self.assertEqual(by_id["sem_0008"]["knowledge_type"], "uncertain")
        self.assertIn("bicicletta", by_id["sem_0001"]["tags"])
        self.assertIsInstance(by_id["sem_0001"]["metadata"], dict)

        health = self.semantic.health()
        self.assertTrue(health["ok"])
        self.assertEqual(health["counts"]["items"], 9)
        self.assertEqual(health["counts"]["revisions"], 9)
        self.assertEqual(health["counts"]["episodic_links"], 4)

    def test_create_update_and_revisions(self) -> None:
        item = self.semantic.create_item(
            title="Il progetto MCP dell'agente",
            content="L'agente sta costruendo un server MCP con memoria persistente.",
            category="informatica",
            knowledge_type="user_provided",
            source="conversation",
            source_ref="chat_test",
            reliability=0.7,
            importance=6,
            tags=["mcp", "progetto"],
            metadata={"channel": "test"},
        )
        self.assertTrue(item["id"].startswith("sem_"))
        self.assertEqual(item["version"], 1)
        self.assertEqual(item["metadata"]["channel"], "test")
        self.assertEqual(self.semantic.count_revisions(item["id"]), 1)

        updated = self.semantic.update_item(
            item["id"],
            {
                "content": (
                    "L'agente sta costruendo un server MCP con memoria "
                    "episodica e semantica."
                ),
                "reliability": 0.8,
            },
        )
        self.assertEqual(updated["version"], 2)
        self.assertIn("semantica", updated["content"])
        self.assertIsNone(updated["embedding_model"])
        self.assertEqual(self.semantic.count_revisions(item["id"]), 2)

        retracted = self.semantic.update_item(item["id"], {"status": "retracted"})
        self.assertEqual(retracted["status"], "retracted")
        self.assertIsNone(self.semantic.get_item(item["id"]))
        self.assertIsNotNone(
            self.semantic.get_item(item["id"], include_inactive=True)
        )
        self.assertIn(
            item["id"],
            {entry["id"] for entry in self.semantic.list_items(status="retracted")},
        )
        self.assertNotIn(item["id"], self.semantic.filter_item_ids())

    def test_verified_fact_requires_authoritative_source(self) -> None:
        with self.assertRaises(ValidationError):
            self.semantic.create_item(
                title="Fatto non verificato",
                content="Questo non dovrebbe essere un fatto verificato.",
                category="test",
                knowledge_type="verified_fact",
                source="conversation",
            )
        with self.assertRaises(ValidationError):
            self.semantic.create_item(
                title="Fatto con affidabilità bassa",
                content="Questo non dovrebbe essere un fatto verificato.",
                category="test",
                knowledge_type="verified_fact",
                source="document",
                reliability=0.4,
            )
        item = self.semantic.create_item(
            title="Fatto documentato",
            content="Un fatto proveniente da un documento.",
            category="test",
            knowledge_type="verified_fact",
            source="document",
            reliability=0.9,
        )
        self.assertEqual(item["knowledge_type"], "verified_fact")

    def test_contradictions_are_links_not_overwrites(self) -> None:
        left = self.semantic.get_item("sem_0007")
        right = self.semantic.get_item("sem_0009")
        assert left is not None and right is not None
        self.assertEqual(left["status"], "active")
        self.assertEqual(right["status"], "active")
        self.assertEqual(left["content"], self.semantic.get_item("sem_0007")["content"])

        links = self.semantic.get_links("sem_0007")["semantic_links"]
        self.assertTrue(
            any(
                link["to_item_id"] == "sem_0009"
                and link["relation_type"] == "contradicts"
                for link in links
            )
        )
        with self.assertRaises(ConflictError):
            self.semantic.link_items("sem_0007", "sem_0009", "contradicts")

    def test_episodic_links_are_conscious_only(self) -> None:
        link = self.semantic.link_to_memory(
            "sem_0002", "mem_0001", "related_to", 0.6, "Concetto e ricordo personale"
        )
        self.assertEqual(link["memory_id"], "mem_0001")
        with self.assertRaises(NotFoundError):
            self.semantic.link_to_memory("sem_0002", "mem_0002", "related_to")

        links = self.semantic.get_links("sem_0002")["episodic_links"]
        memory_ids = {entry["memory_id"] for entry in links}
        self.assertIn("mem_0001", memory_ids)
        self.assertIn("mem_0004", memory_ids)
        self.assertNotIn("mem_0002", memory_ids)

        # Un collegamento residuo verso un ricordo diventato latente non
        # deve rivelarne l'esistenza attraverso la superficie semantica.
        with self.semantic.db.timeline() as conn, conn:
            conn.execute(
                "UPDATE episodic_memories SET access_level = 'latent' "
                "WHERE id = 'mem_0001'"
            )
        after_hiding = self.semantic.get_links("sem_0002")["episodic_links"]
        self.assertNotIn("mem_0001", {entry["memory_id"] for entry in after_hiding})
        self.assertIn("mem_0004", {entry["memory_id"] for entry in after_hiding})

    def test_search_and_filters(self) -> None:
        found = self.semantic.search_items("programmazione", limit=5)
        self.assertEqual(found[0]["id"], "sem_0002")

        bicycle = self.semantic.search_items("bicicletta", limit=5)
        self.assertEqual(bicycle[0]["id"], "sem_0001")

        people = self.semantic.search_items(category="persone", limit=20)
        self.assertEqual({item["id"] for item in people}, {"sem_0005", "sem_0008"})

        hypotheses = self.semantic.search_items(
            knowledge_type="hypothesis", limit=20
        )
        self.assertEqual(
            {item["id"] for item in hypotheses}, {"sem_0007", "sem_0009"}
        )

        conversation = self.semantic.list_items(source="conversation", limit=20)
        self.assertEqual({item["id"] for item in conversation}, {"sem_0005"})

        sqlite_tag = self.semantic.list_items(tag="sqlite", limit=20)
        self.assertEqual({item["id"] for item in sqlite_tag}, {"sem_0003", "sem_0008"})

        important = self.semantic.list_items(min_importance=6, limit=20)
        self.assertEqual(
            {item["id"] for item in important},
            {"sem_0002", "sem_0003", "sem_0004"},
        )

        with self.assertRaises(ValidationError):
            self.semantic.list_items(knowledge_type="invented")
        with self.assertRaises(ValidationError):
            self.semantic.list_items(acquired_after="not-a-date")


if __name__ == "__main__":
    unittest.main()
