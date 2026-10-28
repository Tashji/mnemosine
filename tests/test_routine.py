"""Test della Fase 6: routine giornaliera e stato corrente."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from agent_memory.errors import ValidationError
from agent_memory.routine import RoutineEngine, main
from tests.support import copy_profile


class RoutineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        copy_profile(self.root, include_data=False)
        self.state_path = self.root / "config" / "current_state.json"
        self.identity_path = self.root / "config" / "identity.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- utilità ------------------------------------------------------------

    def _engine(self, at: str) -> RoutineEngine:
        return RoutineEngine(self.root, at=datetime.fromisoformat(at))

    def _read_json(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, data: dict[str, object]) -> None:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _planned_by_time(self, result: dict[str, object], clock: str) -> dict[str, str]:
        planned = result["planned_activities"]
        assert isinstance(planned, list)
        return next(item for item in planned if item["time"] == clock)

    # -- piano --------------------------------------------------------------

    def test_weekend_plan_selects_current_activity_and_transition(self) -> None:
        result = self._engine("2026-10-25T15:00:00+01:00").preview()

        self.assertEqual(result["mode"], "weekend")
        self.assertEqual(result["day_label"], "domenica")
        self.assertFalse(result["is_sleep_window"])

        planned = result["planned_activities"]
        self.assertIsInstance(planned, list)
        self.assertEqual(len(planned), 9)
        times = [item["time"] for item in planned]
        self.assertEqual(times, sorted(times))

        current = self._planned_by_time(result, "15:00")
        self.assertEqual(result["activity"]["id"], current["id"])
        self.assertEqual(
            result["activity"]["started_at"], "2026-10-25T15:00:00+01:00"
        )
        self.assertEqual(
            result["next_transition_at"], "2026-10-25T17:30:00+01:00"
        )
        self.assertIn(
            result["activity"]["category"],
            {"solitary_focus", "gaming", "reading", "outdoor", "leisure"},
        )

    def test_weekday_plan_uses_study_and_work(self) -> None:
        result = self._engine("2026-10-26T10:00:00+01:00").preview()

        self.assertEqual(result["mode"], "weekday")
        self.assertEqual(result["day_label"], "lunedì")
        labels = [item["label"] for item in result["planned_activities"]]
        self.assertIn("Studio", labels)
        self.assertIn("Lavoro al PC", labels)
        self.assertEqual(
            result["activity"]["started_at"], "2026-10-26T08:30:00+01:00"
        )
        self.assertEqual(
            result["next_transition_at"], "2026-10-26T13:00:00+01:00"
        )

    def test_plan_is_reused_for_the_same_day(self) -> None:
        first = self._engine("2026-10-25T10:00:00+01:00").apply()
        second = self._engine("2026-10-25T15:00:00+01:00").preview()

        self.assertEqual(
            [item["id"] for item in first["planned_activities"]],
            [item["id"] for item in second["planned_activities"]],
        )

    def test_missing_interests_fall_back_to_free_time(self) -> None:
        identity = self._read_json(self.identity_path)
        identity["interests"] = []
        self._write_json(self.identity_path, identity)

        result = self._engine("2026-10-25T15:00:00+01:00").preview()

        labels = [item["label"] for item in result["planned_activities"]]
        self.assertIn("Tempo libero", labels)
        self.assertFalse(result["is_sleep_window"])

    # -- sonno --------------------------------------------------------------

    def test_sleep_window_disables_availability(self) -> None:
        result = self._engine("2026-10-25T02:30:00+02:00").preview()

        self.assertTrue(result["is_sleep_window"])
        self.assertEqual(result["activity"]["id"], "act_sleep")
        self.assertEqual(
            result["activity"]["started_at"], "2026-10-25T00:00:00+02:00"
        )
        self.assertEqual(
            result["next_transition_at"], "2026-10-25T07:00:00+01:00"
        )
        state = result["state"]
        self.assertEqual(state["operational_status"], "sleeping")
        self.assertTrue(state["clock"]["is_sleep_window"])
        self.assertTrue(state["sleep"]["is_sleeping"])
        self.assertFalse(state["availability"]["accepting_messages"])

    def test_sleep_window_can_cross_midnight(self) -> None:
        state = self._read_json(self.state_path)
        sleep = state["sleep"]
        assert isinstance(sleep, dict)
        sleep["window"] = {"start": "23:00", "end": "06:00"}
        self._write_json(self.state_path, state)

        result = self._engine("2026-10-26T00:30:00+01:00").preview()

        self.assertTrue(result["is_sleep_window"])
        self.assertEqual(
            result["activity"]["started_at"], "2026-10-25T23:00:00+01:00"
        )
        self.assertEqual(
            result["next_transition_at"], "2026-10-26T06:00:00+01:00"
        )

    def test_invalid_sleep_window_is_rejected(self) -> None:
        state = self._read_json(self.state_path)
        sleep = state["sleep"]
        assert isinstance(sleep, dict)
        sleep["window"] = {"start": "25:00", "end": "07:00"}
        self._write_json(self.state_path, state)

        with self.assertRaises(ValidationError):
            self._engine("2026-10-25T15:00:00+01:00")

    # -- scrittura ----------------------------------------------------------

    def test_preview_does_not_write_and_apply_persists(self) -> None:
        before = self.state_path.read_bytes()
        engine = self._engine("2026-10-25T15:00:00+01:00")

        preview = engine.preview()
        self.assertFalse(preview["is_sleep_window"])
        self.assertEqual(self.state_path.read_bytes(), before)

        applied = engine.apply()
        self.assertTrue(applied["applied"])
        after = self._read_json(self.state_path)
        self.assertEqual(after["routine"]["date"], "2026-10-25")
        self.assertEqual(after["routine"]["mode"], "weekend")
        self.assertEqual(
            after["routine"]["planned_activities"],
            preview["planned_activities"],
        )
        self.assertEqual(after["activity"]["id"], preview["activity"]["id"])
        self.assertEqual(
            after["clock"]["local_time"], "2026-10-25T15:00:00+01:00"
        )

    def test_apply_is_idempotent(self) -> None:
        first = self._engine("2026-10-25T15:00:00+01:00").apply()
        self.assertTrue(first["applied"])
        snapshot = self.state_path.read_bytes()

        second = self._engine("2026-10-25T15:00:00+01:00").apply()
        self.assertFalse(second["applied"])
        self.assertEqual(self.state_path.read_bytes(), snapshot)

    # -- CLI ----------------------------------------------------------------

    def test_cli_json_does_not_write(self) -> None:
        before = self.state_path.read_bytes()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                [
                    "--root",
                    str(self.root),
                    "--at",
                    "2026-10-25T15:00:00+01:00",
                    "--json",
                ]
            )

        self.assertEqual(code, 0)
        data = json.loads(output.getvalue())
        self.assertEqual(data["routine_version"], "v1")
        self.assertEqual(data["mode"], "weekend")
        self.assertEqual(self.state_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
