"""Test della Fase 5: seed esterni, tratti e riconsolidamento a cascata."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from agent_memory.errors import ConflictError, ValidationError
from agent_memory.evolution import EvolutionService
from tests.support import copy_profile

AS_OF = date(2026, 10, 24)


def _seed_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "seed_id": "test_rejection_2024",
        "event_time": "2024-11-05T18:30:00+01:00",
        "title": "Una critica ricevuta in laboratorio",
        "description": (
            "Durante un progetto universitario, un docente criticò "
            "pubblicamente il mio approccio alla risoluzione del problema. "
            "Rimasi in silenzio, poi riscrissi tutto."
        ),
        "importance": 8,
        "place": "Catania",
        "people": ["compagni_universita"],
        "tags": ["critica", "università", "rifiuto"],
        "emotional_state": {
            "label": "vergogna",
            "valence": -0.7,
            "arousal": 0.7,
            "intensity": 8,
        },
        "consequences": "Ho iniziato a preparare ogni consegna con più controllo.",
        "trait_impacts": {},
        "cascade": {"max_memories": 50},
    }
    payload.update(overrides)
    return payload


class EvolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        copy_profile(self.root)
        self.service = EvolutionService(self.root, as_of=AS_OF)
        self.seed_path = self.root / "seed.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- utilità ------------------------------------------------------------

    def _load_seed(self, **overrides: object):
        self.seed_path.write_text(
            json.dumps(_seed_payload(**overrides), ensure_ascii=False),
            encoding="utf-8",
        )
        return self.service.load_seed(self.seed_path)

    def _timeline_count(self) -> int:
        with self.service.memory.db.timeline() as conn:
            return int(
                conn.execute("SELECT COUNT(*) FROM episodic_memories").fetchone()[0]
            )

    def _affected_ids(self, seed) -> list[str]:
        with self.service.memory.db.timeline() as conn:
            rows = conn.execute(
                """
                SELECT id FROM conscious_episodic_memories
                WHERE event_time > ? AND is_simulated = 1 AND id <> ?
                ORDER BY event_time, id
                """,
                (seed.event_time, seed.memory_id),
            ).fetchall()
        return [row["id"] for row in rows]

    def _trait_value(self, trait_id: str) -> int:
        identity = self.service.config.get("identity")
        return int(identity["personality"]["traits"][trait_id]["value"])

    # -- anteprima ----------------------------------------------------------

    def test_preview_does_not_write(self) -> None:
        seed = self._load_seed()
        before = self._timeline_count()
        before_revisions = self._revision_count()
        result = self.service.preview(seed)

        self.assertFalse(result["seed_exists"])
        self.assertEqual(
            result["counts"]["affected_memories"], len(self._affected_ids(seed))
        )
        self.assertEqual(
            {change["trait_id"] for change in result["trait_changes"]},
            {"introversion_extraversion", "resilience_sensitivity"},
        )
        self.assertEqual(result["trait_changes"][0]["delta"], -1)
        self.assertEqual(result["trait_changes"][1]["delta"], 1)
        self.assertEqual(len(self.service.history()["changes"]), 0)
        self.assertEqual(self._timeline_count(), before)
        self.assertEqual(self._revision_count(), before_revisions)
        self.assertEqual(self._trait_value("introversion_extraversion"), 3)
        self.assertEqual(self._trait_value("resilience_sensitivity"), 7)

        with self.service.memory.db.timeline() as conn:
            row = conn.execute(
                "SELECT 1 FROM episodic_memories WHERE source_ref = ?",
                (seed.source_ref,),
            ).fetchone()
        self.assertIsNone(row)

    def _revision_count(self) -> int:
        with self.service.memory.db.timeline() as conn:
            return int(
                conn.execute("SELECT COUNT(*) FROM memory_revisions").fetchone()[0]
            )

    # -- applicazione -------------------------------------------------------

    def test_apply_seed_traits_and_cascade(self) -> None:
        # Rende esplicito che anche una conoscenza verificata collegata a un
        # ricordo della cascata deve restare intatta.
        self.service.semantic.link_to_memory("sem_0001", "mem_0005", "related_to")
        self.service.semantic.link_to_memory("sem_0005", "mem_0005", "related_to")

        seed = self._load_seed()
        expected_ids = self._affected_ids(seed)
        result = self.service.apply(seed, reindex=False)

        self.assertTrue(result["created_seed"])
        self.assertEqual(result["counts"]["updated_memories"], len(expected_ids))
        self.assertEqual(result["counts"]["skipped_memories"], 0)
        self.assertEqual(self._trait_value("introversion_extraversion"), 2)
        self.assertEqual(self._trait_value("resilience_sensitivity"), 8)

        with self.service.memory.db.timeline() as conn:
            seed_row = conn.execute(
                "SELECT * FROM episodic_memories WHERE source_ref = ?",
                (seed.source_ref,),
            ).fetchone()
            self.assertIsNotNone(seed_row)
            assert seed_row is not None
            self.assertEqual(seed_row["source"], "admin_seed")
            self.assertEqual(seed_row["event_date"], "2024-11-05")
            self.assertEqual(seed_row["version"], 1)

            revision_count = conn.execute(
                "SELECT COUNT(*) FROM memory_revisions WHERE cascade_id = ?",
                (seed.cascade_id,),
            ).fetchone()[0]
            self.assertEqual(revision_count, len(expected_ids) + 1)

            for memory_id in expected_ids:
                row = conn.execute(
                    "SELECT version, tags_json FROM episodic_memories WHERE id = ?",
                    (memory_id,),
                ).fetchone()
                self.assertEqual(row["version"], 2)
                self.assertIn("riconsolidato", row["tags_json"])
                revisions = conn.execute(
                    """
                    SELECT revision_number, cascade_id, previous_snapshot_json,
                           new_snapshot_json
                    FROM memory_revisions
                    WHERE memory_id = ? ORDER BY revision_number
                    """,
                    (memory_id,),
                ).fetchall()
                self.assertEqual(len(revisions), 2)
                self.assertEqual(revisions[1]["revision_number"], 2)
                self.assertEqual(revisions[1]["cascade_id"], seed.cascade_id)
                self.assertIn(
                    "critica ricevuta", revisions[1]["new_snapshot_json"]
                )

            real_memory = conn.execute(
                "SELECT version, description FROM episodic_memories WHERE id = 'mem_0004'"
            ).fetchone()
            self.assertEqual(real_memory["version"], 1)
            self.assertNotIn("riconsolidato", real_memory["description"])
            self.assertEqual(
                conn.execute(
                    "SELECT access_level FROM episodic_memories WHERE id = 'mem_0002'"
                ).fetchone()["access_level"],
                "repressed",
            )

        history = self.service.history()
        self.assertEqual(len(history["changes"]), 1)
        self.assertEqual(history["changes"][0]["cascade_id"], seed.cascade_id)
        self.assertEqual(len(history["changes"][0]["trait_changes"]), 2)

        actions = {item["item_id"]: item["action"] for item in result["semantic_items"]}
        self.assertEqual(actions["sem_0001"], "protected")
        self.assertEqual(actions["sem_0005"], "protected")
        self.assertEqual(actions["sem_0006"], "reflect")

        with self.service.semantic.db.semantic() as conn:
            verified_metadata = conn.execute(
                "SELECT metadata_json FROM semantic_items WHERE id = 'sem_0001'"
            ).fetchone()[0]
            provided_metadata = conn.execute(
                "SELECT metadata_json FROM semantic_items WHERE id = 'sem_0005'"
            ).fetchone()[0]
            opinion_metadata = conn.execute(
                "SELECT metadata_json FROM semantic_items WHERE id = 'sem_0006'"
            ).fetchone()[0]
        self.assertNotIn("cascade_reflections", verified_metadata)
        self.assertNotIn("cascade_reflections", provided_metadata)
        self.assertIn(seed.cascade_id, opinion_metadata)

    def test_apply_is_idempotent(self) -> None:
        seed = self._load_seed()
        first = self.service.apply(seed, reindex=False)
        self.assertGreater(first["counts"]["updated_memories"], 0)

        second = self.service.apply(seed, reindex=False)
        self.assertFalse(second["created_seed"])
        self.assertEqual(second["counts"]["updated_memories"], 0)
        self.assertEqual(
            second["counts"]["skipped_memories"],
            first["counts"]["updated_memories"],
        )
        self.assertEqual(second["counts"]["semantic_reflections"], 0)
        self.assertEqual(second["trait_changes_applied"], [])
        self.assertTrue(second["traits_already_applied"])
        self.assertEqual(len(self.service.history()["changes"]), 1)

    def test_seed_conflict_and_validation(self) -> None:
        seed = self._load_seed()
        self.service.apply(seed, reindex=False)

        changed = self._load_seed(title="Un altro titolo")
        with self.assertRaises(ConflictError):
            self.service.apply(changed, reindex=False)

        with self.assertRaises(ValidationError):
            self._load_seed(
                seed_id="future_seed",
                event_time="2026-12-01T10:00:00+01:00",
            )

        with self.assertRaises(ValidationError):
            self._load_seed(
                seed_id="before_birth",
                event_time="2000-01-01T10:00:00+01:00",
            )

        unknown_trait = self._load_seed(
            seed_id="unknown_trait",
            trait_impacts={"tratto_inesistente": 1},
        )
        with self.assertRaises(ValidationError):
            self.service.preview(unknown_trait)

    def test_rewriter_hook_receives_context(self) -> None:
        class HookRewriter:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def rewrite(self, seed, memory, context):
                self.calls.append(
                    {
                        "memory_id": memory["id"],
                        "seed_id": context["seed_object"].seed_id,
                        "cascade_id": context["cascade_id"],
                    }
                )
                return {
                    "description": f"[hook] {memory['title']}",
                    "tags": list(memory["tags"]) + ["hook"],
                }

        seed = self._load_seed()
        hook = HookRewriter()
        service = EvolutionService(self.root, as_of=AS_OF, rewriter=hook)
        result = service.apply(seed, reindex=False)

        self.assertEqual(len(hook.calls), result["counts"]["updated_memories"])
        self.assertTrue(
            all(
                call["seed_id"] == "test_rejection_2024"
                and call["cascade_id"] == seed.cascade_id
                for call in hook.calls
            )
        )
        with service.memory.db.timeline() as conn:
            descriptions = [
                row["description"]
                for row in conn.execute(
                    "SELECT description FROM episodic_memories WHERE tags_json LIKE '%hook%'"
                )
            ]
        self.assertTrue(descriptions)
        self.assertTrue(all(item.startswith("[hook]") for item in descriptions))


if __name__ == "__main__":
    unittest.main()
