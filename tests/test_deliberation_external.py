"""Test della Fase 4: intenti esterni files, WhatsApp e browser.

Gli intenti vengono eseguiti solo con ``requires_confirmation`` falso. Un
intento bloccato non applica effetti e non tocca i tool; un errore chiude
l'episodio come bloccato, senza ritentare l'azione. I test lavorano su una
copia temporanea del profilo demo.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from agent_memory.autonomy import AutonomyService
from agent_memory.browser import BrowserController
from agent_memory.config import ConfigStore
from agent_memory.deliberation import DeliberationService
from agent_memory.executor import delivered_messages
from tests.support import copy_profile
from tests.test_browser import FakeBackend

NOW = datetime(2026, 10, 24, 16, 5, tzinfo=ZoneInfo("Europe/Rome"))
MARCO = "user_marco"


class ExternalIntentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        copy_profile(self.root)
        self.store = ConfigStore(self.root)
        self.store.update_current_state(
            {
                "flags": {"life_engine_enabled": True},
                "heartbeat": {
                    "pending_impulse": None,
                    "spontaneous_message_pending": False,
                },
            }
        )
        self._hungry()
        self.resolver = mock.patch(
            "agent_memory.browser._default_resolver",
            return_value=["93.184.216.34"],
        )
        self.resolver.start()
        self.addCleanup(self.resolver.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- strumenti ----------------------------------------------------------

    def _service(self, at: datetime = NOW) -> DeliberationService:
        return DeliberationService(self.root, at=at)

    def _hungry(self) -> None:
        self.store.update_current_state(
            {"life": {"needs": {"hunger": {"value": 82}}}}
        )

    def _open(self, at: datetime = NOW) -> dict:
        opened = self._service(at).tick()
        self.assertTrue(opened["opened"], opened)
        return opened

    def _decide(self, intent: dict, at: datetime = NOW) -> dict:
        return self._service(at).decide({"decision": "intent", "intent": intent})

    def _intent(self, **overrides) -> dict:
        base = {
            "title": "Annotare l'idea",
            "goal": "Salvare un appunto utile",
            "why_now": "Ho appena pensato a qualcosa.",
            "risk": "low",
        }
        base.update(overrides)
        return base

    def _enable_whatsapp(self) -> None:
        path = self.root / "config" / "integrations.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["whatsapp"] = {
            "enabled": True,
            "allowed_contacts": [
                {
                    "user_id": MARCO,
                    "display_name": "Marco",
                    "phone": "+39 333 1234567",
                }
            ],
            "quiet_hours": {"start": "00:00", "end": "07:00"},
            "max_per_day": 3,
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.store.update_current_state(
            {
                "flags": {"whatsapp_sending_enabled": True},
                "availability": {"accepting_messages": True},
            },
            allow_whatsapp_sending=True,
        )

    def _enable_browser(self, origins: list[str] | None = None) -> None:
        path = self.root / "config" / "integrations.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["browser"] = {
            "enabled": True,
            "allowed_origins": origins or ["https://example.org"],
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # -- files --------------------------------------------------------------

    def test_files_intent_writes_to_the_sandbox(self) -> None:
        self._open()
        result = self._decide(
            self._intent(
                tool="files",
                target="appunti/idea.txt",
                arguments={"content": "Idea per la cena."},
                expected_effects={"hunger": -10},
                requires_confirmation=False,
            )
        )
        outcome = result["episode"]["outcome"]
        self.assertTrue(outcome["applied"])
        self.assertFalse(outcome["simulated"])
        self.assertEqual(outcome["tool"], "files")
        self.assertEqual(outcome["execution"]["path"], "appunti/idea.txt")
        self.assertTrue(outcome["execution"]["created"])
        self.assertEqual(
            (self.root / "files" / "appunti" / "idea.txt").read_text(
                encoding="utf-8"
            ),
            "Idea per la cena.",
        )
        state = self.store.get("current_state")
        self.assertEqual(state["activity"]["label"], "Annotare l'idea")
        self.assertEqual(state["life"]["stream"][-1]["kind"], "intent_files")
        self.assertEqual(state["life"]["needs"]["hunger"]["value"], 72)

    def test_files_intent_requires_confirmation_and_applies_no_effects(self) -> None:
        self._open()
        result = self._decide(
            self._intent(
                tool="files",
                target="appunti/idea.txt",
                arguments={"content": "Idea per la cena."},
                expected_effects={"hunger": -10},
            )
        )
        outcome = result["episode"]["outcome"]
        self.assertFalse(outcome["applied"])
        self.assertTrue(outcome["blocked"])
        self.assertTrue(outcome["confirmation_required"])
        self.assertEqual(outcome["effects"], {})
        self.assertFalse((self.root / "files").exists())
        state = self.store.get("current_state")
        self.assertEqual(state["life"]["needs"]["hunger"]["value"], 82)
        self.assertEqual(state["life"]["stream"][-1]["kind"], "intent_blocked")

    # -- whatsapp -----------------------------------------------------------

    def test_whatsapp_intent_is_delivered_through_the_outbox(self) -> None:
        self._enable_whatsapp()
        self._open()
        result = self._decide(
            self._intent(
                title="Scrivere a Marco",
                goal="Riprendere i contatti",
                tool="whatsapp",
                target=MARCO,
                arguments={"text": "Ciao Marco, come stai?"},
                requires_confirmation=False,
            )
        )
        outcome = result["episode"]["outcome"]
        self.assertTrue(outcome["applied"])
        self.assertFalse(outcome["simulated"])
        self.assertEqual(outcome["tool"], "whatsapp")
        self.assertEqual(outcome["execution"]["recipient"], "Marco")
        self.assertNotIn("phone", json.dumps(outcome))
        messages = delivered_messages(self.root)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["trigger"], "life_intent")
        self.assertEqual(messages[0]["intent_id"], result["episode"]["decision"]["intent"]["intent_id"])
        self.assertEqual(messages[0]["text"], "Ciao Marco, come stai?")
        state = self.store.get("current_state")
        self.assertIsNone(state["heartbeat"]["pending_impulse"])
        self.assertEqual(state["life"]["stream"][-1]["kind"], "intent_whatsapp")

    def test_whatsapp_intent_respects_the_existing_guards(self) -> None:
        self._enable_whatsapp()
        self.store.update_current_state(
            {"flags": {"whatsapp_sending_enabled": False}}
        )
        self._open()
        result = self._decide(
            self._intent(
                title="Scrivere a Marco",
                goal="Riprendere i contatti",
                tool="whatsapp",
                target=MARCO,
                arguments={"text": "Ciao Marco, come stai?"},
                expected_effects={"hunger": -10},
                requires_confirmation=False,
            )
        )
        outcome = result["episode"]["outcome"]
        self.assertFalse(outcome["applied"])
        self.assertTrue(outcome["blocked"])
        self.assertIn("whatsapp spenta", outcome["error"])
        self.assertEqual(delivered_messages(self.root), [])
        state = self.store.get("current_state")
        self.assertEqual(state["life"]["needs"]["hunger"]["value"], 82)

    # -- browser ------------------------------------------------------------

    def test_browser_intent_queues_an_impulse_claimable_by_autonomy(self) -> None:
        self._enable_browser()
        now = datetime.now(ZoneInfo("Europe/Rome"))
        self._open(at=now)
        result = self._decide(
            self._intent(
                title="Leggere le notizie",
                goal="Informarmi su quello che succede",
                tool="browser",
                target="https://example.org/notizie",
                requires_confirmation=False,
            ),
            at=now,
        )
        outcome = result["episode"]["outcome"]
        self.assertTrue(outcome["applied"])
        self.assertFalse(outcome["simulated"])
        self.assertEqual(outcome["tool"], "browser")
        self.assertTrue(outcome["execution"]["queued"])
        impulse = self.store.get("current_state")["heartbeat"]["pending_impulse"]
        self.assertEqual(impulse["trigger"], "life_intent")
        self.assertEqual(impulse["delivery"]["channel"], "browser")
        self.assertEqual(impulse["delivery"]["url"], "https://example.org/notizie")

        controller = BrowserController(self.root, backend=FakeBackend())
        claimed = AutonomyService(self.root, controller).claim(
            "https://example.org/notizie"
        )
        self.assertTrue(claimed["available"])
        self.assertEqual(claimed["url"], "https://example.org/notizie")

    def test_browser_intent_outside_the_whitelist_is_blocked(self) -> None:
        self._enable_browser()
        self._open()
        result = self._decide(
            self._intent(
                title="Leggere un sito",
                goal="Informarmi",
                tool="browser",
                target="https://evil.example.net/",
                expected_effects={"hunger": -10},
                requires_confirmation=False,
            )
        )
        outcome = result["episode"]["outcome"]
        self.assertFalse(outcome["applied"])
        self.assertTrue(outcome["blocked"])
        self.assertIn("whitelist", outcome["error"])
        state = self.store.get("current_state")
        self.assertIsNone(state["heartbeat"]["pending_impulse"])
        self.assertEqual(state["life"]["needs"]["hunger"]["value"], 82)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
