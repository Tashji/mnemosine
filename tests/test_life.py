"""Test dello stato di vita e del rapporto di situazione della Fase 1."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agent_memory.config import ConfigStore
from agent_memory.errors import ValidationError
from agent_memory.service import MemoryService, ShadowService
from agent_memory.situation import SituationBuilder, render_situation
from tests.support import copy_profile

NOW = datetime(2026, 10, 24, 16, 5, tzinfo=ZoneInfo("Europe/Rome"))


class LifeSituationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        copy_profile(self.root)
        self.store = ConfigStore(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _report(self) -> dict:
        return SituationBuilder(self.root, at=NOW).build()

    def test_legacy_profile_gets_a_read_only_default(self) -> None:
        before = self.store.get("current_state")
        report = self._report()

        self.assertEqual(before, self.store.get("current_state"))
        self.assertNotIn("life", self.store.get("current_state"))
        self.assertEqual(report["situation_version"], "1.0")
        self.assertEqual(report["generated_at"], NOW.isoformat())
        self.assertEqual(report["activity"]["mode"], "routine")
        self.assertEqual(report["activity"]["label"], "Programmazione al PC")
        self.assertEqual(report["body"]["energy"], 63)
        self.assertEqual(report["body"]["stress"], 37)
        self.assertEqual(report["body"]["hunger"], 50)
        self.assertEqual(report["affordances"]["time_budget_minutes"], 85)
        self.assertEqual(report["open_loops"], [])
        self.assertEqual(report["stream"], [])
        self.assertEqual(report["privacy"], {
            "shadow_included": False,
            "hidden_memories_included": False,
            "secrets_included": False,
        })
        self.assertIn("cosa c'è in casa", report["unknowns"])
        self.assertIn("messaggi non letti", report["unknowns"])
        self.assertIn("meteo", report["unknowns"])

        marco = next(
            person
            for person in report["relationships"]
            if person["user_id"] == "user_marco"
        )
        self.assertEqual(marco["display_name"], "Marco")
        self.assertIn("closeness", marco)
        self.assertNotIn("trust", marco)
        self.assertNotIn("intimacy", marco)
        self.assertEqual(marco["last_contact"], "5 giorni fa")

        text = render_situation(report)
        self.assertIn("Sono le 16:05 di sabato.", text)
        self.assertIn("Marco", text)
        self.assertIn("Cosa vuoi fare adesso?", text)
        self.assertNotIn("SEGRETO", text)
        self.assertNotIn("shadow", text.casefold())

    def test_persisted_life_drives_the_report(self) -> None:
        life = {
            "mode": "unstructured",
            "needs": {
                "energy": {"value": 40, "trend": "down"},
                "hunger": {"value": 65, "trend": "up"},
                "social": {"value": 55},
                "curiosity": {"value": 30},
                "stress": {"value": 45},
            },
            "open_loops": [
                {
                    "id": "loop_parser",
                    "kind": "project",
                    "title": "Finire il piccolo progetto Python",
                    "status": "active",
                    "priority": 70,
                    "due_at": "2026-10-25T18:00:00+01:00",
                    "next_step": "Chiudere il parser",
                    "last_touched_at": "2026-10-23T18:20:00+02:00",
                    "visibility": "conscious",
                }
            ],
            "environment": {
                "location": "soggiorno",
                "tools": ["browser", "files"],
                "known_resources": ["divano", "pc"],
                "unknowns": ["contenuto del frigo"],
            },
            "attention": "video",
            "mental_content": "Pensa a Giulia.",
            "stream": [
                {
                    "at": "2026-10-24T15:50:00+02:00",
                    "kind": "posture",
                    "text": "Si sdraia sul divano.",
                },
                {
                    "at": "2026-10-24T16:01:00+02:00",
                    "kind": "reverie",
                    "text": "Ripensa a Giulia.",
                },
            ],
        }
        activity = {
            "id": "act_divano",
            "label": "Sdraiato sul divano",
            "category": "leisure",
            "location": "soggiorno",
            "started_at": "2026-10-24T15:50:00+02:00",
            "expected_end_at": None,
        }
        self.store.update_current_state({"activity": activity, "life": life})

        report = self._report()
        self.assertEqual(report["activity"]["mode"], "unstructured")
        self.assertEqual(report["activity"]["label"], "Sdraiato sul divano")
        self.assertEqual(report["body"]["hunger"], 65)
        self.assertEqual(report["body"]["stress"], 45)
        self.assertEqual(report["stream"][-1]["text"], "Ripensa a Giulia.")
        self.assertEqual(len(report["open_loops"]), 1)
        loop = report["open_loops"][0]
        self.assertEqual(loop["id"], "loop_parser")
        self.assertEqual(loop["last_touched_at"], "2026-10-23T18:20:00+02:00")
        self.assertEqual(report["affordances"]["known_resources"], ["divano", "pc"])
        self.assertEqual(report["affordances"]["location"], "soggiorno")
        self.assertIn("contenuto del frigo", report["unknowns"])

        text = render_situation(report)
        self.assertIn("Sei sdraiato sul divano.", text)
        self.assertIn("Ripensa a Giulia.", text)
        self.assertIn("Loop aperti:", text)
        self.assertIn("Finire il piccolo progetto Python", text)

        saved = self.store.get("current_state")
        self.assertEqual(saved["life"]["attention"], "video")
        self.assertEqual(saved["life"]["mental_content"], "Pensa a Giulia.")

    def test_limits_are_enforced_on_read(self) -> None:
        loops = [
            {
                "id": f"loop_{index}",
                "title": f"Loop {index}",
                "status": "active",
                "priority": index,
            }
            for index in range(1, 21)
        ]
        loops[18]["priority"] = 20
        loops[18]["due_at"] = "2026-10-24T18:00:00+02:00"
        loops[19]["priority"] = 20
        loops[19]["due_at"] = "2026-10-25T18:00:00+01:00"
        stream = [
            {
                "at": f"2026-10-24T15:{index:02d}:00+02:00",
                "kind": "stream",
                "text": f"Voce {index}",
            }
            for index in range(8)
        ]
        self.store.update_current_state(
            {"life": {"open_loops": loops, "stream": stream}}
        )

        report = self._report()
        self.assertEqual(len(report["open_loops"]), 8)
        self.assertEqual(report["open_loops"][0]["id"], "loop_19")
        self.assertEqual(len(report["stream"]), 8)
        self.assertEqual(report["stream"][0]["text"], "Voce 0")

    def test_partial_patch_is_valid_and_completed_on_read(self) -> None:
        saved = self.store.update_current_state(
            {"life": {"attention": "video"}}
        )
        self.assertEqual(saved["life"]["attention"], "video")

        report = self._report()
        self.assertEqual(report["body"]["energy"], 63)
        self.assertEqual(report["activity"]["mode"], "routine")

    def test_invalid_life_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.store.update_current_state({"life": {"mode": "vegetale"}})
        with self.assertRaises(ValidationError):
            self.store.update_current_state(
                {"life": {"needs": {"hunger": {"value": 101}}}}
            )
        with self.assertRaises(ValidationError):
            self.store.update_current_state(
                {
                    "life": {
                        "open_loops": [
                            {
                                "id": "loop_latent",
                                "title": "Non deve passare",
                                "visibility": "latent",
                            }
                        ]
                    }
                }
            )

    def test_latent_memory_is_never_in_the_report(self) -> None:
        MemoryService(self.root).create_memory(
            title="Visibile",
            description="Un ricordo conscio.",
            importance=8,
            event_time="2026-10-24T15:30:00+02:00",
            memory_id="mem_visible",
        )
        ShadowService(self.root).create_episodic_memory(
            title="SEGRETO",
            description="Un ricordo latente.",
            importance=9,
            event_time="2026-10-24T15:31:00+02:00",
            memory_id="mem_secret",
        )

        report = self._report()
        texts = [event["text"] for event in report["recent_events"]]
        self.assertIn("Visibile", texts)
        self.assertNotIn("SEGRETO", texts)
        self.assertNotIn("SEGRETO", render_situation(report))

    def test_report_does_not_create_a_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "profile"
            copy_profile(root, include_data=False)
            report = SituationBuilder(root, at=NOW).build()
            self.assertEqual(report["recent_events"], [])
            self.assertFalse((root / "data").exists())

    def test_same_instant_is_deterministic(self) -> None:
        first = self._report()
        second = self._report()
        self.assertEqual(first, second)
        self.assertEqual(first["situation_id"], second["situation_id"])


if __name__ == "__main__":
    unittest.main()
