"""Test del recupero semantico e ibrido della Fase 3."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_memory.embeddings import DeterministicEmbedder
from agent_memory.errors import AccessDeniedError, VectorIndexError
from agent_memory.retrieval import RetrievalService
from agent_memory.service import ShadowService

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RetrievalServiceTests(unittest.TestCase):
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
        self.service = RetrievalService(self.root, embedder=self.embedder)
        self.memory = self.service.memory
        self.semantic = self.service.semantic
        self.index = self.service.index
        self.shadow = ShadowService(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _rebuild(self) -> dict:
        result = self.service.sync_index(rebuild=True)
        self.assertTrue(result["ok"], result)
        return result

    def _conscious_count(self) -> int:
        with self.memory.db.timeline() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM episodic_memories WHERE access_level = 'conscious'"
            ).fetchone()[0]

    def test_rebuild_indexes_only_conscious_records(self) -> None:
        result = self._rebuild()
        self.assertEqual(
            result["indexes"]["episodic"]["count"], self._conscious_count()
        )
        self.assertEqual(result["indexes"]["semantic"]["count"], 9)
        status = self.index.status()
        self.assertTrue(status["ok"], status)
        self.assertTrue(self.index.check()["ok"])

        with self.index.db.vector() as conn:
            rows = conn.execute(
                "SELECT kind, record_id, access_level FROM vector_entries"
            ).fetchall()
        ids = {row["record_id"] for row in rows}
        self.assertNotIn("mem_0002", ids)
        self.assertTrue(all(row["access_level"] == "conscious" for row in rows))
        self.assertIn("mem_0001", ids)
        self.assertIn("sem_0001", ids)

    def test_semantic_and_hybrid_search(self) -> None:
        self._rebuild()
        semantic = self.service.hybrid_search(
            "bicicletta", mode="semantic", min_similarity=0.0, limit=5
        )
        semantic_ids = {item["record_id"] for item in semantic["results"]}
        self.assertIn("mem_0001", semantic_ids)
        self.assertIn("sem_0001", semantic_ids)
        self.assertNotIn("mem_0002", semantic_ids)

        hybrid = self.service.hybrid_search(
            "programmazione Python", min_similarity=0.0, limit=6
        )
        hybrid_ids = {item["record_id"] for item in hybrid["results"]}
        self.assertIn("sem_0002", hybrid_ids)
        self.assertIn("mem_0004", hybrid_ids)
        by_id = {item["record_id"]: item for item in hybrid["results"]}
        self.assertIn("semantic", by_id["sem_0002"]["matched_by"])

        lexical = self.service.hybrid_search("Marco", mode="lexical", limit=10)
        lexical_ids = {item["record_id"] for item in lexical["results"]}
        self.assertIn("mem_0004", lexical_ids)
        self.assertIn("sem_0005", lexical_ids)
        self.assertFalse(lexical["semantic_available"])

    def test_filters_are_applied(self) -> None:
        self._rebuild()
        by_source = self.service.hybrid_search(
            "programmazione", source="real_interaction", min_similarity=0.0, limit=10
        )
        episodic = [item for item in by_source["results"] if item["kind"] == "episodic"]
        self.assertTrue(episodic)
        self.assertTrue(
            all(item["record"]["source"] == "real_interaction" for item in episodic)
        )

        by_person = self.service.hybrid_search(
            "conversazione", person="user_marco", min_similarity=0.0, limit=10
        )
        episodic = [item for item in by_person["results"] if item["kind"] == "episodic"]
        self.assertEqual({item["record_id"] for item in episodic}, {"mem_0004"})

        by_date = self.service.hybrid_search(
            "conversazione",
            start_date="2026-10-01",
            end_date="2026-10-19",
            min_similarity=0.0,
            limit=10,
        )
        episodic = [item for item in by_date["results"] if item["kind"] == "episodic"]
        self.assertEqual({item["record_id"] for item in episodic}, {"mem_0004"})

        by_tag = self.service.hybrid_search(
            "isolamento", tag="lockdown", min_similarity=0.0, limit=10
        )
        episodic = [item for item in by_tag["results"] if item["kind"] == "episodic"]
        self.assertEqual({item["record_id"] for item in episodic}, {"mem_0003"})

        semantic_people = self.service.hybrid_search(
            "Marco",
            include_episodic=False,
            category="persone",
            min_similarity=0.0,
            limit=10,
        )
        self.assertEqual(
            {item["record_id"] for item in semantic_people["results"]},
            {"sem_0005", "sem_0008"},
        )

        hypotheses = self.service.hybrid_search(
            "italiano",
            include_episodic=False,
            knowledge_type="hypothesis",
            min_similarity=0.0,
            limit=10,
        )
        self.assertEqual(
            {item["record_id"] for item in hypotheses["results"]},
            {"sem_0007", "sem_0009"},
        )

    def test_sync_updates_and_removes(self) -> None:
        self._rebuild()
        memory = self.memory.create_memory(
            title="Una nuova passeggiata",
            description="Ha camminato lungo il porto di Catania.",
            importance=4,
            source="generated",
            tags=["passeggiata", "catania"],
        )
        result = self.service.sync_index()
        self.assertEqual(result["indexes"]["episodic"]["indexed"], 1)
        self.assertEqual(
            result["indexes"]["episodic"]["count"], self._conscious_count()
        )

        found = self.service.hybrid_search(
            "porto di Catania", min_similarity=0.0, limit=10
        )
        self.assertIn(
            memory["id"], {item["record_id"] for item in found["results"]}
        )

        updated = self.semantic.update_item(
            "sem_0006",
            {"content": "Il gaming cooperativo aiuta a ridurre la pressione sociale."},
        )
        self.assertEqual(updated["version"], 2)
        result = self.service.sync_index()
        self.assertEqual(result["indexes"]["semantic"]["updated"], 1)

        self.semantic.update_item("sem_0006", {"status": "retracted"})
        result = self.service.sync_index()
        self.assertEqual(result["indexes"]["semantic"]["removed"], 1)
        self.assertEqual(result["indexes"]["semantic"]["count"], 8)
        self.assertIsNone(self.semantic.get_item("sem_0006"))
        after = self.service.hybrid_search(
            "gaming cooperativo", include_episodic=False, min_similarity=0.0, limit=10
        )
        self.assertNotIn("sem_0006", {item["record_id"] for item in after["results"]})

    def test_latent_memories_are_never_indexed(self) -> None:
        self._rebuild()
        latent = self.shadow.create_episodic_memory(
            title="Ricordo latente di test",
            description="Un ricordo che non deve entrare nell'indice vettoriale.",
            importance=6,
            source="generated",
            access_level="latent",
            event_time="2026-10-24T19:00:00+02:00",
        )
        result = self.service.sync_index()
        self.assertEqual(
            result["indexes"]["episodic"]["count"], self._conscious_count()
        )
        with self.index.db.vector() as conn:
            row = conn.execute(
                "SELECT 1 FROM vector_entries WHERE record_id = ?", (latent["id"],)
            ).fetchone()
        self.assertIsNone(row)

        with self.assertRaises(AccessDeniedError):
            self.index.index_one("episodic", "mem_0002", "testo latente")

        with (
            self.assertRaises(sqlite3.IntegrityError),
            self.index.db.vector() as conn,
            conn,
        ):
            conn.execute(
                """
                INSERT INTO vector_entries (
                    kind, record_id, access_level, text_hash, model,
                    dimension, indexed_at, updated_at
                ) VALUES ('episodic', 'latent_probe', 'latent', 'x',
                          'deterministic-test-v1', 128, ?, ?)
                """,
                (
                    "2026-10-24T15:00:00+02:00",
                    "2026-10-24T15:00:00+02:00",
                ),
            )

        results = self.service.hybrid_search(
            "presentazione", min_similarity=0.0, limit=10
        )
        self.assertNotIn("mem_0002", {item["record_id"] for item in results["results"]})

    def test_stale_index_entries_cannot_leak_hidden_records(self) -> None:
        self._rebuild()

        # Un elemento semantico ritirato resta nell'indice finché non si
        # esegue una sincronizzazione: il recupero deve comunque ignorarlo.
        self.semantic.update_item("sem_0006", {"status": "retracted"})
        with self.index.db.vector() as conn:
            row = conn.execute(
                "SELECT 1 FROM vector_entries WHERE kind = 'semantic' "
                "AND record_id = 'sem_0006'"
            ).fetchone()
        self.assertIsNotNone(row)
        after_retraction = self.service.hybrid_search(
            "gaming cooperativo",
            include_episodic=False,
            min_similarity=0.0,
            limit=10,
        )
        self.assertNotIn(
            "sem_0006",
            {item["record_id"] for item in after_retraction["results"]},
        )

        # Anche un ricordo che diventa latente non deve riemergere da un
        # vettore residuo: la fonte di verità è la vista conscia.
        with self.index.db.timeline() as conn, conn:
            conn.execute(
                "UPDATE episodic_memories SET access_level = 'latent' "
                "WHERE id = 'mem_0004'"
            )
        with self.index.db.vector() as conn:
            row = conn.execute(
                "SELECT 1 FROM vector_entries WHERE kind = 'episodic' "
                "AND record_id = 'mem_0004'"
            ).fetchone()
        self.assertIsNotNone(row)
        after_hiding = self.service.hybrid_search(
            "conversazione Python Marco",
            include_semantic=False,
            min_similarity=0.0,
            limit=10,
        )
        self.assertNotIn(
            "mem_0004",
            {item["record_id"] for item in after_hiding["results"]},
        )
        self.assertFalse(self.index.check()["ok"])

    def test_degraded_hybrid_and_index_repair(self) -> None:
        degraded = self.service.hybrid_search("bicicletta", min_similarity=0.0)
        self.assertFalse(degraded["semantic_available"])
        self.assertTrue(degraded["warnings"])
        self.assertTrue(degraded["results"])

        with self.assertRaises(VectorIndexError):
            self.service.hybrid_search("bicicletta", mode="semantic")

        self._rebuild()
        index_file = self.root / "data" / "indexes" / "episodic.faiss"
        self.assertTrue(index_file.exists())
        index_file.unlink()
        self.assertFalse(self.index.status()["ok"])
        self.assertFalse(self.index.check()["ok"])
        self.assertTrue(self.service.sync_index(rebuild=True)["ok"])
        self.assertTrue(self.index.check()["ok"])

    def test_index_if_ready(self) -> None:
        not_ready = self.service.index_if_ready("episodic", "mem_0001")
        self.assertFalse(not_ready["indexed"])

        self._rebuild()
        memory = self.memory.create_memory(
            title="Ricordo indicizzato subito",
            description="Creato dopo la costruzione dell'indice.",
            importance=3,
            source="generated",
        )
        indexed = self.service.index_if_ready("episodic", memory["id"])
        self.assertIn(indexed.get("status"), {"indexed", "updated"})
        self.assertEqual(
            self.index.status()["indexes"]["episodic"]["count"],
            self._conscious_count(),
        )


if __name__ == "__main__":
    unittest.main()
