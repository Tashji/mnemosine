"""Test del carico cognitivo dinamico e del ciclo Heartbeat.

I test lavorano su copie temporanee del profilo demo e fissano l'orologio con
``--at``: nessuna dipendenza dall'ora reale, nessuna scrittura nel progetto.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_memory import heartbeat
from agent_memory.config import ConfigStore
from agent_memory.errors import ValidationError
from agent_memory.heartbeat import (
    HEARTBEAT_DEFAULTS,
    HeartbeatEngine,
    apply_interactions,
    recover_load,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-10-25T15:00"


class HeartbeatTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copytree(PROJECT_ROOT / "config", self.root / "config")
        shutil.copytree(
            PROJECT_ROOT / "data",
            self.root / "data",
            ignore=shutil.ignore_patterns("models", "indexes", "dashboard_tls"),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- strumenti ----------------------------------------------------------

    def read_json(self, name: str) -> dict[str, Any]:
        return ConfigStore(self.root).get(name)

    def write_json(self, name: str, data: dict[str, Any]) -> None:
        ConfigStore(self.root).write(name, data)

    def patch_state(self, **fields: Any) -> None:
        state = self.read_json("current_state")
        state.update(fields)
        self.write_json("current_state", state)

    def patch_heartbeat(self, **fields: Any) -> None:
        state = self.read_json("current_state")
        heartbeat_state = state.get("heartbeat")
        if not isinstance(heartbeat_state, dict):
            heartbeat_state = {}
        heartbeat_state.update(fields)
        state["heartbeat"] = heartbeat_state
        self.write_json("current_state", state)

    def set_people(self, people: list[dict[str, Any]]) -> None:
        graph = self.read_json("social_graph")
        graph["people"] = people
        self.write_json("social_graph", graph)

    def plan_day(self, start: str = "13:00") -> None:
        state = self.read_json("current_state")
        state["routine"] = {
            "date": "2026-10-25",
            "mode": "weekday",
            "day_label": "domenica",
            "next_transition_at": "2026-10-25T18:00:00+01:00",
            "planned_activities": [
                {
                    "id": "act_probe",
                    "time": start,
                    "label": "Lettura",
                    "category": "reading",
                    "location": "camera",
                    "details": "Legge senza fretta.",
                },
                {
                    "id": "act_late",
                    "time": "18:00",
                    "label": "Cena",
                    "category": "meal",
                    "location": "cucina",
                    "details": "Cena.",
                },
            ],
        }
        self.write_json("current_state", state)

    def add_memory(self, event_date: str, title: str, importance: int) -> str:
        path = self.root / "data" / "timeline_episodic.db"
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            row = dict(
                conn.execute("SELECT * FROM episodic_memories LIMIT 1").fetchone()
            )
            row.update(
                {
                    "id": "mem_probe",
                    "event_time": f"{event_date}T12:00:00+02:00",
                    "event_date": event_date,
                    "title": title,
                    "importance": importance,
                    "access_level": "conscious",
                }
            )
            columns = ", ".join(row)
            placeholders = ", ".join(f":{key}" for key in row)
            conn.execute(
                f"INSERT INTO episodic_memories ({columns}) VALUES ({placeholders})",
                row,
            )
            conn.commit()
        finally:
            conn.close()
        return "mem_probe"

    def engine(self, at: str = NOW) -> HeartbeatEngine:
        return HeartbeatEngine(self.root, at=datetime.fromisoformat(at))


class LoadDynamicsTests(unittest.TestCase):
    def test_each_complexity_has_its_weight(self) -> None:
        self.assertEqual(apply_interactions(10, 1, "leggera"), 12)
        self.assertEqual(apply_interactions(10, 1, "normale"), 14)
        self.assertEqual(apply_interactions(10, 2, "complessa"), 24)

    def test_load_never_exceeds_one_hundred(self) -> None:
        self.assertEqual(apply_interactions(95, 5, "complessa"), 100)

    def test_unknown_complexity_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            apply_interactions(10, 1, "impossibile")

    def test_invalid_message_count_is_refused(self) -> None:
        for value in (0, -1, 101):
            with self.subTest(messages=value), self.assertRaises(ValidationError):
                apply_interactions(10, value, "normale")

    def test_sleep_recovers_faster_than_waking(self) -> None:
        self.assertEqual(recover_load(80, 60, sleeping=False), 78)
        self.assertEqual(recover_load(80, 60, sleeping=True), 65)

    def test_a_full_night_resets_the_load(self) -> None:
        self.assertEqual(recover_load(90, 400, sleeping=True), 0)

    def test_zero_minutes_keeps_the_load(self) -> None:
        self.assertEqual(recover_load(42, 0, sleeping=False), 42)


class HeartbeatTickTests(HeartbeatTestCase):
    def test_tick_without_triggers_keeps_working(self) -> None:
        self.set_people([])
        self.plan_day(start="14:30")
        result = self.engine().preview()

        self.assertEqual(result["decision"], "continuare_l_attivita")
        self.assertIsNone(result["impulse"])
        self.assertEqual(result["next_run_at"], "2026-10-25T15:30:00+01:00")

    def test_anniversary_becomes_an_impulse(self) -> None:
        self.set_people([])
        self.plan_day(start="14:30")
        memory_id = self.add_memory("2019-10-25", "Un giorno importante", 8)

        result = self.engine().preview()

        self.assertEqual(result["decision"], "impulso")
        impulse = result["impulse"]
        self.assertEqual(impulse["trigger"], "ricorrenza")
        self.assertEqual(impulse["context"]["memory_id"], memory_id)
        self.assertEqual(impulse["context"]["years_ago"], 7)
        self.assertEqual(impulse["delivery"]["channel"], "browser")
        self.assertFalse(impulse["delivery"]["authorized"])

    def test_long_silence_from_a_trusted_person_becomes_an_impulse(self) -> None:
        self.plan_day(start="14:30")
        result = self.engine().preview()

        self.assertEqual(result["decision"], "impulso")
        impulse = result["impulse"]
        self.assertEqual(impulse["trigger"], "relazione")
        self.assertEqual(impulse["target"]["display_name"], "Marco")
        self.assertGreaterEqual(impulse["context"]["days_since_contact"], 3)
        self.assertEqual(impulse["delivery"]["channel"], "whatsapp")
        self.assertFalse(impulse["delivery"]["authorized"])

    def test_boredom_from_a_long_activity_becomes_an_impulse(self) -> None:
        self.set_people([])
        self.plan_day(start="13:00")
        result = self.engine().preview()

        self.assertEqual(result["decision"], "impulso")
        impulse = result["impulse"]
        self.assertEqual(impulse["trigger"], "noia")
        self.assertEqual(impulse["context"]["activity"], "Lettura")
        self.assertGreaterEqual(impulse["context"]["minutes"], 90)

    def test_night_consolidates_and_sends_nothing(self) -> None:
        self.patch_state(mental_load=90)
        result = self.engine("2026-10-25T03:00").preview()

        self.assertTrue(result["sleeping"])
        self.assertEqual(result["decision"], "consolidamento")
        self.assertIsNone(result["impulse"])
        self.assertFalse(result["state"]["heartbeat"]["spontaneous_message_pending"])
        self.assertEqual(result["mental_load_after"], 0)
        self.assertEqual(result["state"]["availability"]["response_style"], "assente")
        self.assertEqual(
            result["state"]["sleep"]["last_consolidation_at"],
            "2026-10-25T03:00:00+01:00",
        )

    def test_cooldown_blocks_a_second_impulse(self) -> None:
        self.plan_day(start="14:30")
        self.patch_heartbeat(last_impulse_at="2026-10-25T14:30:00+01:00")

        result = self.engine().preview()

        self.assertEqual(result["decision"], "continuare_l_attivita")
        self.assertIsNone(result["impulse"])

    def test_daily_cap_blocks_new_impulses(self) -> None:
        self.plan_day(start="14:30")
        self.patch_heartbeat(impulses_today={"date": "2026-10-25", "count": 3})

        result = self.engine().preview()

        self.assertEqual(result["decision"], "continuare_l_attivita")
        self.assertIsNone(result["impulse"])

    def test_overload_stops_impulses_and_shortens_answers(self) -> None:
        self.plan_day(start="14:30")
        self.patch_state(mental_load=88)
        self.patch_heartbeat(last_run_at="2026-10-25T14:55:00+01:00")

        result = self.engine().preview()

        self.assertEqual(result["decision"], "riposo_cognitivo")
        self.assertIsNone(result["impulse"])
        self.assertEqual(result["state"]["availability"]["response_style"], "breve")

    def test_maintenance_mode_stops_impulses(self) -> None:
        self.plan_day(start="14:30")
        state = self.read_json("current_state")
        state["flags"]["maintenance_mode"] = True
        self.write_json("current_state", state)

        result = self.engine().preview()

        self.assertEqual(result["decision"], "manutenzione")
        self.assertIsNone(result["impulse"])

    def test_pending_impulse_is_not_lost_by_a_quiet_tick(self) -> None:
        pending = {"trigger": "noia", "created_at": "2026-10-25T14:00:00+01:00"}
        self.plan_day(start="14:30")
        self.patch_heartbeat(
            pending_impulse=pending,
            spontaneous_message_pending=True,
        )

        result = self.engine().preview()

        self.assertEqual(result["decision"], "impulso_in_attesa")
        self.assertIsNone(result["impulse"])
        self.assertEqual(result["state"]["heartbeat"]["pending_impulse"], pending)
        self.assertTrue(result["state"]["heartbeat"]["spontaneous_message_pending"])


class HeartbeatWriteTests(HeartbeatTestCase):
    def test_apply_persists_and_is_idempotent(self) -> None:
        self.set_people([])
        self.plan_day(start="14:30")
        first = self.engine().apply()
        self.assertTrue(first["applied"])

        state = self.read_json("current_state")
        self.assertEqual(state["mental_load"], first["mental_load_after"])
        self.assertEqual(state["heartbeat"]["last_decision"], "continuare_l_attivita")
        self.assertEqual(state["heartbeat"]["interval_minutes"], 30)
        self.assertIn("impulses_today", state["heartbeat"])

        second = self.engine().apply()
        self.assertFalse(second["applied"])

    def test_record_interaction_updates_load_and_timestamp(self) -> None:
        state = self.read_json("current_state")
        before = state["mental_load"]

        result = self.engine().record_interaction(messages=2, complexity="complessa")

        self.assertEqual(result["mental_load"], min(100, before + 14))
        state = self.read_json("current_state")
        self.assertEqual(state["mental_load"], result["mental_load"])
        self.assertEqual(
            state["heartbeat"]["last_interaction_at"], "2026-10-25T15:00:00+01:00"
        )

    def test_history_survives_more_than_one_hundred_interactions(self) -> None:
        for index in range(105):
            self.engine().record_interaction(user_id="user_marco", summary=f"entry {index}")
        graph = self.read_json("social_graph")
        marco = next(p for p in graph["people"] if p["user_id"] == "user_marco")
        store = ConfigStore(self.root).operational
        self.assertEqual(store.history("user_marco")["total"], 107)
        self.assertEqual(marco["interaction_count"], 123)
        entries = [e for offset in (0, 100) for e in store.history("user_marco", offset=offset, limit=100)["entries"]]
        self.assertTrue(any(e["summary"] == "entry 0" for e in entries))
        self.assertEqual(ConfigStore(self.root).operational.history("user_marco")["total"], 107)

    def test_record_interaction_updates_the_relationship(self) -> None:
        result = self.engine().record_interaction(
            user_id="user_marco", summary="Conversazione serale"
        )

        self.assertEqual(result["relationship"]["user_id"], "user_marco")
        self.assertEqual(
            result["relationship"]["last_interaction_at"],
            "2026-10-25T15:00:00+01:00",
        )
        graph = self.read_json("social_graph")
        marco = next(person for person in graph["people"] if person["user_id"] == "user_marco")
        self.assertEqual(marco["interaction_count"], 19)
        self.assertEqual(marco["last_interaction_at"], "2026-10-25T15:00:00+01:00")
        latest = ConfigStore(self.root).operational.history("user_marco")["entries"][0]
        self.assertEqual(latest["summary"], "Conversazione serale")
        self.assertEqual(
            latest["timestamp"],
            "2026-10-25T15:00:00+01:00",
        )

    def test_invalid_or_hidden_memory_does_not_change_profile(self) -> None:
        engine = self.engine()
        before = engine.config.load_all()
        for memory_id in ("missing_memory", "mem_0002"):
            with self.subTest(memory_id=memory_id), self.assertRaises(ValidationError):
                engine.record_interaction(user_id="user_marco", memory_id=memory_id)
            self.assertEqual(engine.config.load_all(), before)
        with self.assertRaises(ValidationError):
            engine.record_interaction(memory_id="mem_0004")
        self.assertEqual(engine.config.load_all(), before)

    def test_unknown_contact_does_not_change_state(self) -> None:
        state_before = self.read_json("current_state")
        graph_before = self.read_json("social_graph")

        with self.assertRaises(ValidationError):
            self.engine().record_interaction(user_id="user_sconosciuto")

        self.assertEqual(self.read_json("current_state"), state_before)
        self.assertEqual(self.read_json("social_graph"), graph_before)

    def test_interaction_restarts_the_silence_window(self) -> None:
        self.set_people(
            [
                {
                    "user_id": "user_marco",
                    "display_name": "Marco",
                    "trust": 74,
                    "intimacy": 61,
                    "first_interaction_at": "2024-10-05T18:20:00+02:00",
                    "last_interaction_at": "2026-10-18T21:40:00+02:00",
                    "interaction_count": 18,
                }
            ]
        )
        self.plan_day(start="14:30")

        before = self.engine().preview()
        self.assertEqual(before["impulse"]["trigger"], "relazione")

        self.engine().record_interaction(user_id="user_marco")
        after = self.engine().preview()
        self.assertIsNone(after["impulse"])
        self.assertEqual(after["decision"], "continuare_l_attivita")

        later = self.engine("2026-10-29T15:00").preview()
        self.assertEqual(later["impulse"]["trigger"], "relazione")
        self.assertEqual(later["impulse"]["target"]["user_id"], "user_marco")

    def test_interaction_pushes_availability_over_the_soft_threshold(self) -> None:
        self.patch_state(mental_load=58)

        result = self.engine().record_interaction(messages=1, complexity="normale")

        self.assertEqual(result["mental_load"], 62)
        self.assertEqual(result["availability"]["response_style"], "ridotta")

    def test_acknowledge_impulse_clears_the_queue(self) -> None:
        self.plan_day(start="14:30")
        self.engine().apply()

        heartbeat_state = self.engine().acknowledge_impulse(note="scritto a Marco")

        self.assertIsNone(heartbeat_state["pending_impulse"])
        self.assertFalse(heartbeat_state["spontaneous_message_pending"])
        self.assertEqual(heartbeat_state["last_decision"], "impulso_gestito")
        self.assertEqual(heartbeat_state["last_handling_note"], "scritto a Marco")

    def test_defaults_cover_the_new_profile_fields(self) -> None:
        state = self.read_json("current_state")
        self.assertLessEqual(set(HEARTBEAT_DEFAULTS), set(state["heartbeat"]))


class HeartbeatCommandTests(HeartbeatTestCase):
    def run_cli(self, *args: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = heartbeat.main(["--root", str(self.root), *args])
        return code, buffer.getvalue()

    def test_json_preview(self) -> None:
        self.plan_day(start="14:30")
        code, output = self.run_cli("--at", NOW, "--json")

        self.assertEqual(code, 0)
        result = json.loads(output)
        self.assertIn("decision", result)
        self.assertIn("mental_load_after", result)

    def test_interaction_from_command_line(self) -> None:
        code, output = self.run_cli("--at", NOW, "--interaction", "2")

        self.assertEqual(code, 0)
        self.assertIn("Interazione registrata", output)
        self.assertEqual(self.read_json("current_state")["mental_load"], 45)

    def test_interaction_from_command_line_updates_the_relationship(self) -> None:
        code, output = self.run_cli(
            "--at",
            NOW,
            "--interaction",
            "1",
            "--user-id",
            "user_marco",
            "--summary",
            "Ciao Marco",
            "--channel",
            "whatsapp",
            "--sentiment",
            "positivo",
            "--memory-id",
            "mem_0004",
        )

        self.assertEqual(code, 0)
        self.assertIn("Relazione aggiornata: Marco", output)
        graph = self.read_json("social_graph")
        marco = next(person for person in graph["people"] if person["user_id"] == "user_marco")
        self.assertEqual(marco["interaction_count"], 19)
        latest = ConfigStore(self.root).operational.history("user_marco")["entries"][0]
        self.assertEqual(latest["summary"], "Ciao Marco")
        self.assertEqual(latest["channel"], "whatsapp")
        self.assertEqual(latest["sentiment"], "positivo")
        self.assertEqual(latest["memory_id"], "mem_0004")

    def test_loop_stops_after_the_requested_ticks(self) -> None:
        self.set_people([])
        self.plan_day(start="14:30")
        code, output = self.run_cli("--loop", "--interval", "30", "--max-ticks", "1")

        self.assertEqual(code, 0)
        # In modalità --loop vale l'orologio reale: si controlla che il battito
        # sia avvenuto e sia stato scritto, non quale decisione abbia preso.
        self.assertEqual(len(output.strip().splitlines()), 1)
        self.assertIn("carico", output)
        state = self.read_json("current_state")
        self.assertIsNotNone(state["heartbeat"]["last_run_at"])


if __name__ == "__main__":
    unittest.main()
