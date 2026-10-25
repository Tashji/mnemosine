"""Test dell'esecutore che consuma un impulso e consegna il messaggio.

I test lavorano su una copia temporanea del profilo e fissano l'orologio: la
whitelist e i flag vengono accesi qui, così ogni condizione è verificata da sola.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_memory import heartbeat
from agent_memory.errors import AccessDeniedError, ConfigError
from agent_memory.executor import (
    MAX_MESSAGE_CHARS,
    MessageExecutor,
    WhatsAppPolicy,
    delivered_messages,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-10-25T15:00"
CREATED = "2026-10-25T14:30:00+01:00"
MARCO = {"user_id": "user_marco", "display_name": "Marco"}


class ExecutorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copytree(PROJECT_ROOT / "config", self.root / "config")
        shutil.copytree(
            PROJECT_ROOT / "data",
            self.root / "data",
            ignore=shutil.ignore_patterns("models", "indexes", "dashboard_tls"),
        )
        shutil.rmtree(self.root / "data" / "outbox", ignore_errors=True)
        self.enable_whatsapp()
        self.allow_delivery()
        self.set_impulse()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- strumenti ----------------------------------------------------------

    def read_json(self, name: str) -> dict[str, Any]:
        return json.loads((self.root / "config" / f"{name}.json").read_text("utf-8"))

    def write_json(self, name: str, data: dict[str, Any]) -> None:
        (self.root / "config" / f"{name}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def executor(self, at: str = NOW) -> MessageExecutor:
        return MessageExecutor(self.root, at=datetime.fromisoformat(at))

    def enable_whatsapp(
        self,
        *,
        enabled: bool = True,
        contacts: list[dict[str, str]] | None = None,
        quiet: tuple[str, str] = ("00:00", "07:00"),
        max_per_day: int = 3,
    ) -> None:
        data = self.read_json("integrations")
        data["whatsapp"] = {
            "enabled": enabled,
            "allowed_contacts": (
                contacts
                if contacts is not None
                else [{**MARCO, "phone": "+39 333 1234567"}]
            ),
            "quiet_hours": {"start": quiet[0], "end": quiet[1]},
            "max_per_day": max_per_day,
        }
        self.write_json("integrations", data)

    def allow_delivery(self, *, flag: bool = True, maintenance: bool = False) -> None:
        state = self.read_json("current_state")
        flags = state.get("flags") or {}
        flags["whatsapp_sending_enabled"] = flag
        flags["maintenance_mode"] = maintenance
        state["flags"] = flags
        state["availability"] = {
            "accepting_messages": True,
            "response_style": "normale",
            "notes": "",
        }
        self.write_json("current_state", state)

    def set_impulse(
        self,
        *,
        trigger: str = "relazione",
        target: dict[str, Any] | None = None,
        channel: str = "whatsapp",
        created_at: str = CREATED,
    ) -> None:
        state = self.read_json("current_state")
        hb = state.get("heartbeat") or {}
        hb["pending_impulse"] = {
            "trigger": trigger,
            "target": MARCO if target is None else target,
            "context": {"days_since_contact": 6.0, "trust": 74},
            "created_at": created_at,
            "delivery": {
                "channel": channel,
                "authorized": True,
                "note": "prova",
            },
            "requires_message": True,
        }
        hb["spontaneous_message_pending"] = True
        state["heartbeat"] = hb
        self.write_json("current_state", state)

    def clear_impulse(self) -> None:
        state = self.read_json("current_state")
        hb = state.get("heartbeat") or {}
        hb["pending_impulse"] = None
        hb["spontaneous_message_pending"] = False
        state["heartbeat"] = hb
        self.write_json("current_state", state)

    def write_outbox(self, records: list[dict[str, Any]]) -> None:
        path = self.root / "data" / "outbox" / "messages.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def blocked_reasons(self, text: str = "Ciao Marco, come stai?") -> list[str]:
        with self.assertRaises(AccessDeniedError) as ctx:
            self.executor().deliver(text)
        return str(ctx.exception)

    # -- autorizzazione -----------------------------------------------------

    def test_ready_impulse_is_authorized(self) -> None:
        report = self.executor().authorize("  Ciao Marco, come stai?  ")
        self.assertTrue(report["allowed"])
        self.assertEqual(report["reasons"], [])
        self.assertEqual(report["channel"], "whatsapp")
        self.assertEqual(report["target"]["user_id"], "user_marco")
        self.assertEqual(report["text"], "Ciao Marco, come stai?")
        self.assertEqual(report["text_length"], len("Ciao Marco, come stai?"))
        self.assertTrue(all(item["ok"] for item in report["checks"]))

    def test_missing_impulse_is_blocked(self) -> None:
        self.clear_impulse()
        self.assertIn("nessun impulso in attesa", self.blocked_reasons())

    def test_flag_off_blocks_delivery(self) -> None:
        self.allow_delivery(flag=False)
        message = self.blocked_reasons()
        self.assertIn("integrazione whatsapp spenta", message)
        self.assertIsNotNone(self.executor().pending_impulse())

    def test_disabled_integration_blocks_delivery(self) -> None:
        self.enable_whatsapp(enabled=False)
        self.assertIn("integrazione whatsapp spenta", self.blocked_reasons())

    def test_contact_outside_whitelist_is_blocked(self) -> None:
        self.enable_whatsapp(
            contacts=[{"user_id": "user_giulia", "display_name": "Giulia"}]
        )
        self.assertIn("whitelist", self.blocked_reasons())

    def test_contact_unknown_to_social_graph_is_blocked(self) -> None:
        self.enable_whatsapp(
            contacts=[{"user_id": "user_sconosciuto", "display_name": "X"}]
        )
        self.set_impulse(target={"user_id": "user_sconosciuto", "display_name": "X"})
        message = self.blocked_reasons()
        self.assertIn("grafo delle relazioni", message)

    def test_browser_impulse_cannot_be_delivered(self) -> None:
        self.set_impulse(trigger="noia", target=None, channel="browser")
        message = self.blocked_reasons()
        self.assertIn("non consegna messaggi", message)

    def test_maintenance_mode_blocks_delivery(self) -> None:
        self.allow_delivery(maintenance=True)
        self.assertIn("manutenzione attiva", self.blocked_reasons())

    def test_quiet_hours_block_delivery(self) -> None:
        self.set_impulse(created_at="2026-10-25T02:30:00+01:00")
        executor = self.executor("2026-10-25T03:00")
        report = executor.authorize("Ciao Marco")
        self.assertFalse(report["allowed"])
        self.assertIn("finestra di silenzio 00:00-07:00", report["reasons"])

    def test_daily_limit_counts_only_today(self) -> None:
        self.enable_whatsapp(max_per_day=1)
        self.write_outbox(
            [
                {"delivered_at": "2026-10-24T18:00:00+01:00", "text": "ieri"},
                {"delivered_at": "2026-10-25T09:00:00+01:00", "text": "oggi"},
            ]
        )
        reasons = self.blocked_reasons()
        self.assertIn("limite di 1 consegne al giorno", reasons)

    def test_stale_impulse_is_refused(self) -> None:
        self.set_impulse(created_at="2026-10-25T01:00:00+01:00")
        self.assertIn("troppo vecchio", self.blocked_reasons())

    def test_future_impulse_is_refused(self) -> None:
        self.set_impulse(created_at="2026-10-25T18:00:00+01:00")
        self.assertIn("datato nel futuro", self.blocked_reasons())

    def test_text_is_validated(self) -> None:
        executor = self.executor()
        for text in ("", "   ", 42, "x" * (MAX_MESSAGE_CHARS + 1), "a\x00b"):
            with self.subTest(text=text):
                report = executor.authorize(text)
                self.assertFalse(report["allowed"])
                details = [item["detail"] for item in report["checks"]]
                self.assertTrue(any(d.startswith("testo:") for d in details))
        report = executor.authorize("x" * MAX_MESSAGE_CHARS)
        self.assertTrue(report["allowed"])

    # -- consegna -----------------------------------------------------------

    def test_delivery_writes_outbox_and_closes_impulse(self) -> None:
        result = self.executor().deliver("Ciao Marco, come stai?")
        self.assertTrue(result["delivered"])
        self.assertTrue(result["message_id"].startswith("msg_"))
        self.assertEqual(result["channel"], "whatsapp")
        self.assertEqual(result["transport"], "outbox")
        self.assertEqual(result["target"]["phone"], "+39 333 1234567")
        self.assertEqual(result["trigger"], "relazione")
        self.assertEqual(result["text"], "Ciao Marco, come stai?")

        records = delivered_messages(self.root)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["message_id"], result["message_id"])
        self.assertEqual(records[0]["target"]["user_id"], "user_marco")

        state = self.read_json("current_state")
        hb = state["heartbeat"]
        self.assertIsNone(hb["pending_impulse"])
        self.assertFalse(hb["spontaneous_message_pending"])
        self.assertEqual(hb["last_decision"], "impulso_consegnato")
        self.assertEqual(hb["last_delivery_id"], result["message_id"])
        self.assertEqual(hb["last_delivered_at"], result["delivered_at"])

    def test_second_delivery_is_blocked(self) -> None:
        self.executor().deliver("Ciao Marco")
        self.assertIn("nessun impulso in attesa", self.blocked_reasons())

    def test_write_failure_keeps_the_impulse(self) -> None:
        outbox = self.root / "data" / "outbox" / "messages.jsonl"
        outbox.mkdir(parents=True)
        with self.assertRaises(ConfigError):
            self.executor().deliver("Ciao Marco")
        self.assertIsNotNone(self.executor().pending_impulse())

    def test_outbox_ignores_broken_lines(self) -> None:
        path = self.root / "data" / "outbox" / "messages.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"message_id": "msg_1", "delivered_at": "2026-10-25T09:00:00+01:00"}\n'
            "non-json\n"
            "\n",
            encoding="utf-8",
        )
        self.assertEqual(len(delivered_messages(self.root)), 1)

    # -- configurazione -----------------------------------------------------

    def test_policy_rejects_invalid_documents(self) -> None:
        cases = [
            {"enabled": "sì"},
            {"allowed_contacts": [{"display_name": "senza id"}]},
            {
                "allowed_contacts": [
                    {"user_id": "user_marco"},
                    {"user_id": "user_marco"},
                ]
            },
            {"max_per_day": True},
            {"max_per_day": 25},
            {"quiet_hours": {"start": "25:00", "end": "07:00"}},
        ]
        for patch in cases:
            with self.subTest(patch=patch):
                self.enable_whatsapp()
                data = self.read_json("integrations")
                data["whatsapp"].update(patch)
                self.write_json("integrations", data)
                with self.assertRaises(ConfigError):
                    WhatsAppPolicy.load(self.root)

    def test_missing_integrations_file_means_disabled(self) -> None:
        (self.root / "config" / "integrations.json").unlink()
        policy = WhatsAppPolicy.load(self.root)
        self.assertFalse(policy.enabled)
        self.assertEqual(policy.allowed_contacts, ())
        self.assertIn("integrazione whatsapp spenta", self.blocked_reasons())

    # -- CLI ----------------------------------------------------------------

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = heartbeat.main(["--root", str(self.root), "--at", NOW, *argv])
        return code, out.getvalue(), err.getvalue()

    def test_cli_deliver_blocked_reports_reason(self) -> None:
        self.allow_delivery(flag=False)
        code, _out, err = self.run_cli("--deliver", "Ciao Marco")
        self.assertEqual(code, 1)
        self.assertIn("Consegna bloccata", err)
        self.assertIn("integrazione whatsapp spenta", err)
        self.assertIsNotNone(self.executor().pending_impulse())

    def test_cli_deliver_writes_the_message(self) -> None:
        code, out, err = self.run_cli("--deliver", "Ciao Marco, come stai?")
        self.assertEqual(code, 0, err)
        self.assertIn("Messaggio consegnato a Marco (user_marco)", out)
        self.assertIn("outbox", out)
        self.assertEqual(len(delivered_messages(self.root)), 1)

    def test_cli_deliver_and_interaction_are_exclusive(self) -> None:
        code, _out, err = self.run_cli("--deliver", "Ciao", "--interaction", "1")
        self.assertEqual(code, 1)
        self.assertIn("non entrambi", err)


if __name__ == "__main__":
    unittest.main()
