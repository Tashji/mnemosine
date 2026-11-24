"""Test del ciclo autonomo: testo del modello e decisione WhatsApp."""

from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from agent_memory.autonomy_loop import (
    AutonomyLoop,
    build_whatsapp_decision,
    clean_message,
    prompt_situation,
)
from agent_memory.deliberation import validate_decision


class AutonomyLoopTests(unittest.TestCase):
    def test_clean_message_removes_fences_quotes_and_prefix(self) -> None:
        self.assertEqual(
            clean_message('```\n"Messaggio: Ciao Angelo, come stai?"\n```'),
            "Ciao Angelo, come stai?",
        )
        self.assertEqual(clean_message("  Ciao   Angelo  "), "Ciao Angelo")
        self.assertEqual(clean_message(None), "")

    def test_clean_message_truncates_at_a_word_boundary(self) -> None:
        text = clean_message("parola " * 200)
        self.assertLessEqual(len(text), 701)
        self.assertTrue(text.endswith("…"))
        self.assertNotIn("  ", text)

    def test_prompt_situation_omits_unknowns_and_internal_data(self) -> None:
        report = {
            "now": {"local_time": "2026-11-24T16:00:00+01:00", "day": "martedì"},
            "activity": {"label": "Tempo libero", "location": "casa"},
            "relationships": [
                {"display_name": "Angelo", "closeness": "molto stretto"}
            ],
            "body": {"social": 50, "energy": 70},
            "unknowns": ["cosa c'è in casa", "meteo"],
            "affordances": {"tools": ["browser"]},
        }
        text = prompt_situation(report)
        self.assertIn("Ora:", text)
        self.assertIn("Tempo libero", text)
        self.assertIn("Angelo", text)
        self.assertNotIn("meteo", text)
        self.assertNotIn("Non sai", text)
        self.assertNotIn("social", text)

    def test_decision_is_valid_and_authorizes_whatsapp(self) -> None:
        episode = {
            "episode_id": "del_abc123",
            "reason": "need_social",
        }
        decision = build_whatsapp_decision(
            episode=episode,
            user_id="test_angelo",
            display_name="Angelo",
            text="Ciao Angelo, come stai?",
        )
        normalized = validate_decision(decision)
        intent = normalized["intent"]
        self.assertEqual(intent["tool"], "whatsapp")
        self.assertEqual(intent["target"], "test_angelo")
        self.assertEqual(intent["arguments"]["text"], "Ciao Angelo, come stai?")
        self.assertFalse(intent["requires_confirmation"])
        self.assertEqual(intent["expected_effects"], {"social": 5})
        self.assertEqual(intent["risk"], "low")
        self.assertEqual(normalized["next_review_minutes"], 60)

    def test_decision_accepts_a_generated_message_without_phone(self) -> None:
        decision = build_whatsapp_decision(
            episode={"episode_id": "del_x", "reason": "exploration"},
            user_id="test_angelo",
            display_name="Angelo",
            text="Ciao Angelo, ti va di raccontarmi come procede?",
        )
        raw = str(decision)
        self.assertNotIn("+39", raw)
        self.assertEqual(decision["intent"]["target"], "test_angelo")

    def test_uncertain_attempt_counts_towards_the_daily_limit(self) -> None:
        class Status:
            def entries(self) -> dict[str, dict[str, str]]:
                return {
                    "msg_ok": {
                        "state": "delivered_ui",
                        "updated_at": "2026-11-24T15:00:00+01:00",
                    },
                    "msg_uncertain": {
                        "state": "uncertain",
                        "updated_at": "2026-11-24T16:00:00+01:00",
                    },
                    "msg_blocked": {
                        "state": "blocked",
                        "updated_at": "2026-11-24T16:10:00+01:00",
                    },
                }

        class Transport:
            now = datetime(2026, 11, 24, 17, 0, tzinfo=ZoneInfo("Europe/Rome"))
            status = Status()

        loop = AutonomyLoop.__new__(AutonomyLoop)
        self.assertEqual(loop._attempts_today(Transport()), 2)


if __name__ == "__main__":
    unittest.main()
