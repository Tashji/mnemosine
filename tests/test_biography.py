"""Test della Fase 4: biografia procedurale e filtro di anacronismo."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from agent_memory.biography import (
    BiographyService,
    age_on,
    classify_world_event,
    comprehension_threshold,
)
from agent_memory.service import MemoryService

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 10, 24)


class BiographyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copytree(PROJECT_ROOT / "config", self.root / "config")
        shutil.copytree(
            PROJECT_ROOT / "data",
            self.root / "data",
            ignore=shutil.ignore_patterns("models", "indexes"),
        )
        self._remove_generated_biography()
        self.memory = MemoryService(self.root)
        self.service = BiographyService(self.root, as_of=AS_OF)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- utilità ------------------------------------------------------------

    def _remove_generated_biography(self) -> None:
        db_path = self.root / "data" / "timeline_episodic.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                "DELETE FROM episodic_memories WHERE id LIKE 'bio_%'"
            )
            conn.commit()

    def _all_memories(self) -> list[dict[str, object]]:
        with self.memory.db.timeline() as conn:
            rows = conn.execute(
                "SELECT * FROM episodic_memories ORDER BY event_date, id"
            ).fetchall()
        return [dict(row) for row in rows]

    def _memory_by_source_ref(self, source_ref: str) -> dict[str, object] | None:
        with self.memory.db.timeline() as conn:
            row = conn.execute(
                "SELECT * FROM episodic_memories WHERE source_ref = ?",
                (source_ref,),
            ).fetchone()
        return dict(row) if row is not None else None

    def _world_event(self, event_id: str) -> dict[str, object]:
        return next(
            event
            for event in self.service.world_history["events"]
            if event["id"] == event_id
        )

    # -- piano --------------------------------------------------------------

    def test_plan_matches_macro_era_density(self) -> None:
        plan = self.service.plan()

        self.assertEqual(len(plan.generated), 35)
        self.assertEqual(plan.existing_total, 5)
        self.assertEqual(plan.total_after, 40)
        self.assertEqual(
            plan.by_era(),
            {
                "infanzia": {
                    "target": 3,
                    "existing": 1,
                    "new": 2,
                    "total_after": 3,
                },
                "fanciullezza": {
                    "target": 7,
                    "existing": 0,
                    "new": 7,
                    "total_after": 7,
                },
                "adolescenza": {
                    "target": 12,
                    "existing": 2,
                    "new": 10,
                    "total_after": 12,
                },
                "eta_adulta": {
                    "target": 18,
                    "existing": 2,
                    "new": 16,
                    "total_after": 18,
                },
            },
        )

        self.assertEqual(
            [event.event_date.year for event in plan.generated],
            sorted(event.event_date.year for event in plan.generated),
        )
        self.assertTrue(all(event.event_date <= AS_OF for event in plan.generated))
        self.assertEqual(
            len({event.memory_id for event in plan.generated}),
            len(plan.generated),
        )
        self.assertEqual(
            len({event.source_ref for event in plan.generated}),
            len(plan.generated),
        )

    def test_plan_is_deterministic(self) -> None:
        first = self.service.plan()
        second = BiographyService(self.root, as_of=AS_OF).plan()

        self.assertEqual(
            [
                (event.memory_id, event.event_time, event.title)
                for event in first.generated
            ],
            [
                (event.memory_id, event.event_time, event.title)
                for event in second.generated
            ],
        )

    def test_identity_is_used_without_placeholders(self) -> None:
        plan = self.service.plan()
        text = " ".join(
            " ".join(
                [
                    event.title,
                    event.description,
                    event.place or "",
                    event.consequences or "",
                ]
            )
            for event in plan.generated
        )
        self.assertNotIn("{", text)
        self.assertNotIn("}", text)
        self.assertIn("Licata", text)
        self.assertIn("Catania", text)

    # -- anacronismo --------------------------------------------------------

    def test_anachronism_filter_classification(self) -> None:
        birth = self.service.birth_date
        cases = {
            "wh_2001_09_11_attacks": "context_only",
            "wh_2004_12_26_tsunami": "context_only",
            "wh_2005_04_02_john_paul_ii": "context_only",
            "wh_2008_09_15_lehman": "direct",
            "wh_2011_03_11_fukushima": "direct",
            "wh_2020_03_09_covid_lockdown": "direct",
        }
        for event_id, expected in cases.items():
            with self.subTest(event_id=event_id):
                status, age, threshold = classify_world_event(
                    self._world_event(event_id), birth
                )
                self.assertEqual(status, expected)
                if expected == "context_only":
                    self.assertLess(age, threshold)
                else:
                    self.assertGreaterEqual(age, threshold)

        before_birth = {
            "id": "synthetic_before",
            "date": "2000-01-01",
            "category": "geopolitica",
            "comprehension_threshold_years": 3,
        }
        status, _age, threshold = classify_world_event(before_birth, birth)
        self.assertEqual(status, "before_birth")
        self.assertEqual(threshold, 7)

        complex_event = {
            "id": "synthetic_complex",
            "date": "2006-01-01",
            "category": "geopolitica",
            "comprehension_threshold_years": 2,
        }
        status, age, threshold = classify_world_event(complex_event, birth)
        self.assertEqual(status, "context_only")
        self.assertEqual(age, 4)
        self.assertEqual(threshold, 7)
        self.assertEqual(comprehension_threshold(complex_event), 7)

    def test_context_only_events_are_never_written(self) -> None:
        plan = self.service.plan()

        statuses = {
            decision.event_id: decision.status
            for decision in plan.world_events
        }
        self.assertEqual(statuses["wh_2001_09_11_attacks"], "context_only")
        self.assertEqual(statuses["wh_2004_12_26_tsunami"], "context_only")
        self.assertEqual(statuses["wh_2005_04_02_john_paul_ii"], "context_only")
        self.assertEqual(statuses["wh_2020_03_09_covid_lockdown"], "covered")

        self.service.apply(plan)
        for event_id in (
            "wh_2001_09_11_attacks",
            "wh_2004_12_26_tsunami",
            "wh_2005_04_02_john_paul_ii",
        ):
            self.assertIsNone(self._memory_by_source_ref(event_id))

    # -- applicazione -------------------------------------------------------

    def test_apply_is_idempotent_and_preserves_existing_memories(self) -> None:
        before = self.memory.get_memory("mem_0001")
        self.assertIsNotNone(before)

        first = self.service.apply()
        self.assertEqual(first["created"], 35)
        self.assertEqual(first["skipped"], 0)
        self.assertEqual(first["total"], 40)

        after = self.memory.get_memory("mem_0001")
        self.assertEqual(after["title"], before["title"])
        self.assertEqual(after["description"], before["description"])

        with self.memory.db.timeline() as conn:
            repressed = conn.execute(
                "SELECT access_level FROM episodic_memories WHERE id = 'mem_0002'"
            ).fetchone()
        self.assertEqual(repressed["access_level"], "repressed")
        self.assertIsNone(self.memory.get_memory("mem_0002"))

        second = self.service.apply()
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["total"], 40)

        rows = [
            row for row in self._all_memories() if row["id"].startswith("bio_")
        ]
        self.assertEqual(len(rows), 35)
        self.assertTrue(
            all(row["access_level"] == "conscious" for row in rows)
        )
        self.assertTrue(all(row["is_simulated"] == 1 for row in rows))
        self.assertTrue(
            all(
                row["source"] in {"generated", "world_event"}
                for row in rows
            )
        )
        self.assertEqual(
            sum(1 for row in rows if row["source"] == "world_event"), 9
        )
        self.assertEqual(
            sum(1 for row in rows if row["source"] == "generated"), 26
        )

    def test_world_event_distinguishes_fact_and_reaction(self) -> None:
        self.service.apply()
        row = self._memory_by_source_ref("wh_2008_09_15_lehman")
        self.assertIsNotNone(row)
        self.assertEqual(row["source"], "world_event")
        self.assertEqual(row["is_simulated"], 1)
        self.assertIn("Fatto storico:", row["description"])
        self.assertIn("Reazione simulata:", row["description"])
        self.assertIn("fatto_storico", row["tags_json"])
        self.assertIn("reazione_simulata", row["tags_json"])

    def test_existing_world_event_is_not_duplicated(self) -> None:
        self.service.apply()
        with self.memory.db.timeline() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM episodic_memories WHERE source_ref = ?",
                ("wh_2020_03_09_covid_lockdown",),
            ).fetchone()[0]
        self.assertEqual(count, 1)
        row = self._memory_by_source_ref("wh_2020_03_09_covid_lockdown")
        self.assertEqual(row["id"], "mem_0003")

    def test_direct_world_events_respect_threshold(self) -> None:
        plan = self.service.plan()
        for event in plan.generated:
            if event.world_event_id is None:
                continue
            world_event = self._world_event(event.world_event_id)
            self.assertGreaterEqual(
                age_on(self.service.birth_date, event.event_date),
                comprehension_threshold(world_event),
            )

    def test_trait_conditioning_changes_negative_memory(self) -> None:
        plan = self.service.plan()
        rejection = next(
            event
            for event in plan.generated
            if event.key == "adult_rejection"
        )
        self.assertIn("Ci ho rimuginato per giorni", rejection.description)
        self.assertGreaterEqual(rejection.emotional_state["intensity"], 8)

    def test_cli_preview_and_apply(self) -> None:
        from agent_memory.biography import main

        preview = main(
            [
                "--root",
                str(self.root),
                "--as-of",
                AS_OF.isoformat(),
            ]
        )
        self.assertEqual(preview, 0)
        self.assertEqual(len(self._all_memories()), 5)

        applied = main(
            [
                "--root",
                str(self.root),
                "--as-of",
                AS_OF.isoformat(),
                "--apply",
                "--json",
            ]
        )
        self.assertEqual(applied, 0)
        self.assertEqual(len(self._all_memories()), 40)


if __name__ == "__main__":
    unittest.main()
