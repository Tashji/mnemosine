"""Test delle Fasi 2-3: salienza, episodi, effetti e riflessione."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from agent_memory.config import ConfigStore
from agent_memory.db import Database
from agent_memory.deliberation import (
    DeliberationService,
    evaluate_salience,
    validate_decision,
)
from agent_memory.errors import NotFoundError, ValidationError
from tests.support import copy_profile

NOW = datetime(2026, 10, 24, 16, 5, tzinfo=ZoneInfo("Europe/Rome"))


class DeliberationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        copy_profile(self.root)
        self.store = ConfigStore(self.root)
        self.store.update_current_state(
            {"flags": {"life_engine_enabled": True}}
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _service(self, at: datetime = NOW) -> DeliberationService:
        return DeliberationService(self.root, at=at)

    def _hungry(self) -> None:
        self.store.update_current_state(
            {"life": {"needs": {"hunger": {"value": 82}}}}
        )

    def _count(self, table: str) -> int:
        with Database(self.root).timeline() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def test_disabled_engine_does_not_open_or_write(self) -> None:
        self.store.update_current_state(
            {"flags": {"life_engine_enabled": False}}
        )
        self._hungry()
        result = self._service().tick()
        self.assertFalse(result["opened"])
        self.assertEqual(result["reason"], "life_engine_disabled")
        self.assertEqual(self._count("deliberation_episodes"), 0)

    def test_tick_opens_only_with_salience_and_opens_once(self) -> None:
        quiet = self._service().tick()
        self.assertFalse(quiet["opened"])
        self.assertEqual(quiet["reason"], "not_salient")

        self._hungry()
        opened = self._service().tick()
        self.assertTrue(opened["opened"])
        self.assertEqual(opened["salience"]["reason"], "need_hunger")
        self.assertEqual(opened["episode"]["state"], "open")
        self.assertEqual(self._count("deliberation_episodes"), 1)
        self.assertEqual(
            self.store.get("current_state")["deliberation"]["state"], "open"
        )

        status = self._service().status()
        self.assertEqual(status["active"]["episode_id"], opened["episode"]["episode_id"])
        self.assertEqual(status["today"], 1)

        again = self._service().tick()
        self.assertFalse(again["opened"])
        self.assertEqual(again["reason"], "episode_active")
        self.assertEqual(self._count("deliberation_episodes"), 1)

    def test_continue_closes_the_episode_and_starts_cooldown(self) -> None:
        self._hungry()
        opened = self._service().tick()
        episode_id = opened["episode"]["episode_id"]

        result = self._service().decide(
            {
                "decision": "continue",
                "rationale": "Sto già facendo qualcosa.",
                "confidence": 0.8,
            }
        )
        self.assertTrue(result["accepted"])
        self.assertEqual(result["episode"]["state"], "closed")
        self.assertEqual(result["episode"]["decision"]["decision"], "continue")
        state = self.store.get("current_state")
        self.assertEqual(state["deliberation"]["decision"], "continue")
        self.assertTrue(result["episode"]["outcome"]["applied"])
        self.assertEqual(state["life"]["stream"][-1]["kind"], "continue")
        self.assertIn("Programmazione al PC", state["life"]["stream"][-1]["text"])
        with Database(self.root).timeline() as conn:
            events = [
                row[0]
                for row in conn.execute(
                    "SELECT type FROM deliberation_events "
                    "WHERE episode_id=? ORDER BY id",
                    (episode_id,),
                )
            ]
        self.assertEqual(events, ["opened", "decided"])

        cooldown = self._service().tick()
        self.assertFalse(cooldown["opened"])
        self.assertEqual(cooldown["reason"], "cooldown")

        later = self._service(NOW + timedelta(minutes=91)).tick()
        self.assertTrue(later["opened"])

    def test_unstructured_requires_activity_and_attention(self) -> None:
        with self.assertRaises(ValidationError):
            validate_decision({"decision": "do_nothing"})
        with self.assertRaises(ValidationError):
            validate_decision({"decision": "intent"})
        with self.assertRaises(ValidationError):
            validate_decision(
                {"decision": "unstructured", "unstructured": {}}
            )
        with self.assertRaises(ValidationError):
            validate_decision(
                {
                    "decision": "unstructured",
                    "unstructured": {
                        "activity": "Divano",
                        "attention": "video",
                        "duration_minutes": 0,
                    },
                }
            )

        self._hungry()
        self._service().tick()
        before = self.store.get("current_state")
        result = self._service().decide(
            {
                "decision": "unstructured",
                "unstructured": {
                    "activity": "Sdraiato sul divano",
                    "attention": "video",
                    "mental_content": "Pensa a Giulia.",
                    "duration_minutes": 30,
                    "expected_effects": {"stress": -8, "energy": 3},
                },
            }
        )
        self.assertTrue(result["accepted"])
        decision = result["episode"]["decision"]
        self.assertEqual(decision["unstructured"]["activity"], "Sdraiato sul divano")
        self.assertEqual(decision["unstructured"]["duration_minutes"], 30)
        state = self.store.get("current_state")
        self.assertEqual(state["activity"]["label"], "Sdraiato sul divano")
        self.assertEqual(state["life"]["mode"], "unstructured")
        self.assertEqual(state["life"]["attention"], "video")
        self.assertEqual(state["life"]["mental_content"], "Pensa a Giulia.")
        self.assertEqual(state["life"]["stream"][-1]["kind"], "unstructured")
        effects = result["episode"]["outcome"]["effects"]
        self.assertEqual(effects["stress"]["delta"], -8)
        self.assertEqual(
            state["life"]["needs"]["stress"]["value"],
            effects["stress"]["after"],
        )
        self.assertGreater(state["emotion"]["valence"], before["emotion"]["valence"])

    def test_internal_intent_updates_state_loop_and_stream(self) -> None:
        self.store.update_current_state(
            {
                "life": {
                    "open_loops": [
                        {
                            "id": "loop_1",
                            "title": "Preparare la cena",
                            "status": "active",
                            "priority": 70,
                            "last_touched_at": (NOW - timedelta(days=4)).isoformat(),
                        }
                    ]
                }
            }
        )
        self._hungry()
        self._service().tick()
        result = self._service().decide(
            {
                "decision": "intent",
                "intent": {
                    "title": "Preparare uno spuntino",
                    "goal": "Mangiare qualcosa per far scendere la fame",
                    "why_now": "La fame è alta e c'è una pausa.",
                    "tool": "none",
                    "target": "loop_1",
                    "steps": ["Andare in cucina", "Preparare qualcosa"],
                    "expected_effects": {"hunger": -25, "energy": 4},
                    "risk": "low",
                    "requires_confirmation": False,
                },
            }
        )
        self.assertTrue(result["accepted"])
        intent = result["episode"]["decision"]["intent"]
        self.assertTrue(intent["intent_id"].startswith("int_del_"))
        self.assertEqual(intent["tool"], "none")
        self.assertEqual(result["episode"]["outcome"]["tool"], "none")
        self.assertTrue(result["episode"]["outcome"]["simulated"])
        self.assertEqual(result["episode"]["outcome"]["loop_id"], "loop_1")
        state = self.store.get("current_state")
        self.assertEqual(state["activity"]["label"], "Preparare uno spuntino")
        self.assertEqual(state["life"]["mode"], "intent")
        self.assertEqual(state["life"]["attention"], "Preparare uno spuntino")
        self.assertEqual(
            state["life"]["open_loops"][0]["last_touched_at"], NOW.isoformat()
        )
        self.assertEqual(state["life"]["stream"][-1]["kind"], "intent")
        self.assertIn("simulato", state["life"]["stream"][-1]["text"])

    def test_internal_intent_validation_rejects_external_tools(self) -> None:
        base = {
            "title": "Controllare la posta",
            "goal": "Leggere i messaggi importanti",
            "why_now": "È passato tempo.",
        }
        normalized = validate_decision(
            {"decision": "intent", "intent": {**base, "tool": "none"}}
        )
        self.assertEqual(normalized["intent"]["tool"], "none")
        for tool in ("browser", "files", "whatsapp"):
            with self.assertRaises(ValidationError):
                validate_decision(
                    {"decision": "intent", "intent": {**base, "tool": tool}}
                )
        with self.assertRaises(ValidationError):
            validate_decision({"decision": "intent", "intent": {"title": "Solo"}})

    def test_expired_episode_uses_continue_without_a_decision(self) -> None:
        self._hungry()
        opened = self._service().tick()
        episode_id = opened["episode"]["episode_id"]

        result = self._service(NOW + timedelta(minutes=16)).expire()
        self.assertEqual(result["count"], 1)
        with Database(self.root).timeline() as conn:
            row = conn.execute(
                "SELECT state, decision_json, outcome_json "
                "FROM deliberation_episodes WHERE id=?",
                (episode_id,),
            ).fetchone()
        self.assertEqual(row["state"], "expired")
        decision = json.loads(row["decision_json"])
        self.assertEqual(decision["decision"], "continue")
        outcome = json.loads(row["outcome_json"])
        self.assertTrue(outcome["applied"])
        self.assertTrue(outcome["simulated"])
        self.assertTrue(outcome["expired"])
        state = self.store.get("current_state")
        self.assertEqual(state["deliberation"]["state"], "expired")
        self.assertEqual(state["life"]["stream"][-1]["kind"], "continue")

    def test_expired_without_safe_activity_uses_rest(self) -> None:
        self._hungry()
        self._service().tick()
        self.store.update_current_state({"activity": {"label": ""}})

        result = self._service(NOW + timedelta(minutes=16)).expire()
        self.assertEqual(result["count"], 1)
        decision = result["expired"][0]["decision"]
        self.assertEqual(decision["decision"], "unstructured")
        self.assertEqual(decision["unstructured"]["activity"], "Riposo")
        state = self.store.get("current_state")
        self.assertEqual(state["activity"]["label"], "Riposo")
        self.assertEqual(state["life"]["mode"], "unstructured")
        self.assertEqual(state["life"]["stream"][-1]["kind"], "unstructured")

    def test_gate_blocks_sleep_maintenance_and_uncertain_actions(self) -> None:
        self._hungry()
        self.store.update_current_state({"sleep": {"is_sleeping": True}})
        self.assertEqual(self._service().tick()["reason"], "sleep")

        self.store.update_current_state(
            {"sleep": {"is_sleeping": False}, "flags": {"maintenance_mode": True}}
        )
        self.assertEqual(self._service().tick()["reason"], "maintenance")

        self.store.update_current_state({"flags": {"maintenance_mode": False}})
        with Database(self.root).timeline() as conn:
            conn.execute(
                "INSERT INTO autonomous_runs(id, impulse, state, url, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("uncertain-run", "{}", "uncertain", "https://example.org", NOW.isoformat()),
            )
            conn.commit()
        self.assertEqual(self._service().tick()["reason"], "uncertain_action")

    def test_status_without_episodes_is_readable(self) -> None:
        status = self._service().status()
        self.assertTrue(status["storage"])
        self.assertIsNone(status["active"])
        self.assertIsNone(status["last"])
        self.assertEqual(status["today"], 0)

    def test_decide_without_an_open_episode_is_rejected(self) -> None:
        with self.assertRaises(NotFoundError):
            self._service().decide({"decision": "continue"})


class SalienceTests(unittest.TestCase):
    def test_needs_loops_and_exploration_are_deterministic(self) -> None:
        quiet = {
            "body": {
                "energy": 60,
                "hunger": 50,
                "social": 50,
                "curiosity": 50,
                "stress": 40,
            },
            "open_loops": [],
            "affordances": {"time_budget_minutes": 85},
        }
        self.assertFalse(evaluate_salience(quiet, now=NOW)["salient"])
        self.assertEqual(
            evaluate_salience(quiet, now=NOW, seed=4)["reason"], "exploration"
        )
        self.assertFalse(evaluate_salience(quiet, now=NOW, seed=6)["salient"])

        hungry = {**quiet, "body": {**quiet["body"], "hunger": 82}}
        self.assertEqual(
            evaluate_salience(hungry, now=NOW)["reason"], "need_hunger"
        )

        due = {
            **quiet,
            "open_loops": [
                {
                    "id": "loop_1",
                    "title": "Finire il progetto",
                    "priority": 70,
                    "due_at": "2026-10-25T10:00:00+02:00",
                }
            ],
        }
        self.assertEqual(evaluate_salience(due, now=NOW)["reason"], "loop_due")


if __name__ == "__main__":
    unittest.main()
