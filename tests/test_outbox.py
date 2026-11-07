"""Test del trasporto outbox → WhatsApp Web.

Il backend reale richiede uno schermo e una sessione autenticata, quindi i test
usano un backend finto: verificano il ciclo di stato, il lock, i gate e la
privacy del registro, non i selettori di WhatsApp.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

from agent_memory.backup import BackupService
from agent_memory.errors import (
    ConfigError,
    OutboxBlockedError,
    OutboxBusyError,
    OutboxUncertainError,
)
from agent_memory.outbox import (
    DeliveryStatusStore,
    OutboxLock,
    OutboxTransport,
)
from agent_memory import outbox as outbox_module
from tests.support import copy_profile

NOW = "2026-10-25T15:00"
DELIVERED = "2026-10-25T14:50:00+01:00"
MARCO = {"user_id": "user_marco", "display_name": "Marco"}


class FakeBackend:
    def __init__(
        self,
        *,
        result: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, str]] = []
        self.preflights = 0
        self.closed = 0

    def preflight(self) -> None:
        self.preflights += 1

    def deliver(self, phone: str, text: str) -> dict[str, Any]:
        self.calls.append((phone, text))
        if self.error is not None:
            raise self.error
        return self.result if self.result is not None else {
            "ui_confirmed": True,
            "remote_delivery_verified": False,
        }

    def close(self) -> None:
        self.closed += 1


class OutboxTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        copy_profile(self.root)
        shutil.rmtree(self.root / "data" / "outbox", ignore_errors=True)
        audit = self.root / "data" / "logs" / "audit.jsonl"
        if audit.exists():
            audit.unlink()
        self.enable_whatsapp()
        self.allow_delivery()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- strumenti ----------------------------------------------------------

    def read_json(self, name: str) -> dict[str, Any]:
        return json.loads(
            (self.root / "config" / f"{name}.json").read_text("utf-8")
        )

    def write_json(self, name: str, data: dict[str, Any]) -> None:
        (self.root / "config" / f"{name}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def enable_whatsapp(
        self,
        *,
        contacts: list[dict[str, Any]] | None = None,
        max_per_day: int = 3,
        quiet: tuple[str, str] = ("00:00", "07:00"),
    ) -> None:
        data = self.read_json("integrations")
        data["whatsapp"] = {
            "enabled": True,
            "allowed_contacts": (
                contacts
                if contacts is not None
                else [{**MARCO, "phone": "+39 333 1234567"}]
            ),
            "quiet_hours": {"start": quiet[0], "end": quiet[1]},
            "max_per_day": max_per_day,
        }
        self.write_json("integrations", data)

    def allow_delivery(
        self, *, flag: bool = True, maintenance: bool = False
    ) -> None:
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

    def record(self, message_id: str = "msg_test0001", **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "message_id": message_id,
            "delivered_at": DELIVERED,
            "channel": "whatsapp",
            "transport": "outbox",
            "target": {
                "user_id": "user_marco",
                "display_name": "Marco",
                "phone": "+39 333 1234567",
            },
            "trigger": "relazione",
            "impulse": None,
            "text": "Ciao Marco, come stai?",
        }
        base.update(overrides)
        return base

    def write_records(self, *records: dict[str, Any]) -> None:
        path = self.root / "data" / "outbox" / "messages.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def audit_events(self) -> list[dict[str, Any]]:
        path = self.root / "data" / "logs" / "audit.jsonl"
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def transport(
        self, backend: FakeBackend | None = None, at: str = NOW
    ) -> OutboxTransport:
        return OutboxTransport(
            self.root, backend=backend, at=datetime.fromisoformat(at)
        )

    def status_path(self) -> Path:
        return self.root / "data" / "outbox" / "status.json"

    def read_status(self) -> dict[str, Any]:
        return json.loads(self.status_path().read_text(encoding="utf-8"))

    # -- invio --------------------------------------------------------------

    def test_send_marks_delivered_ui_and_uses_policy_phone(self) -> None:
        # Il telefono nel record è vecchio: vale sempre quello della whitelist.
        self.write_records(
            self.record(target={
                "user_id": "user_marco",
                "display_name": "Marco",
                "phone": "+39 000 0000000",
            })
        )
        backend = FakeBackend()
        report = self.transport(backend).run()
        self.assertEqual(report["sent"], 1)
        self.assertEqual(report["pending"], 1)
        self.assertEqual(backend.calls, [("393331234567", "Ciao Marco, come stai?")])
        entry = self.read_status()["messages"]["msg_test0001"]
        self.assertEqual(entry["state"], "delivered_ui")
        self.assertEqual(entry["attempts"], 1)
        self.assertFalse(entry["remote_delivery_verified"])
        self.assertEqual(backend.closed, 1)

    def test_second_run_does_not_resend(self) -> None:
        self.write_records(self.record())
        first = self.transport(FakeBackend()).run()
        self.assertEqual(first["sent"], 1)
        second_backend = FakeBackend()
        second = self.transport(second_backend).run()
        self.assertEqual(second["sent"], 0)
        self.assertEqual(second["pending"], 0)
        self.assertEqual(second_backend.calls, [])
        self.assertEqual(
            self.read_status()["messages"]["msg_test0001"]["attempts"], 1
        )

    def test_ui_not_confirmed_is_uncertain_and_stops(self) -> None:
        self.write_records(
            self.record("msg_a"), self.record("msg_b", text="Secondo messaggio")
        )
        backend = FakeBackend(result={"ui_confirmed": False})
        report = self.transport(backend).run(limit=2)
        self.assertEqual(report["uncertain"], 1)
        self.assertEqual(report["sent"], 0)
        self.assertEqual(report["stopped"], "uncertain")
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(
            self.read_status()["messages"]["msg_a"]["state"], "uncertain"
        )

    def test_unknown_error_is_uncertain_and_never_retried(self) -> None:
        self.write_records(self.record())
        report = self.transport(FakeBackend(error=RuntimeError("boom"))).run()
        self.assertEqual(report["uncertain"], 1)
        entry = self.read_status()["messages"]["msg_test0001"]
        self.assertEqual(entry["state"], "uncertain")
        self.assertEqual(entry["reason"], "unexpected_error")

        backend = FakeBackend()
        second = self.transport(backend).run()
        self.assertEqual(second["uncertain"], 0)
        self.assertEqual(backend.calls, [])

    def test_backend_blocked_error_is_retryable(self) -> None:
        self.write_records(self.record())
        first = self.transport(
            FakeBackend(error=OutboxBlockedError("sessione scaduta"))
        ).run()
        self.assertEqual(first["blocked"], 1)
        self.assertEqual(
            self.read_status()["messages"]["msg_test0001"]["state"], "blocked"
        )

        backend = FakeBackend()
        second = self.transport(backend).run()
        self.assertEqual(second["sent"], 1)
        self.assertEqual(backend.calls[0][0], "393331234567")
        self.assertEqual(
            self.read_status()["messages"]["msg_test0001"]["attempts"], 2
        )

    def test_explicit_uncertain_error_is_not_retried(self) -> None:
        self.write_records(self.record())
        report = self.transport(
            FakeBackend(error=OutboxUncertainError("forse partito"))
        ).run()
        self.assertEqual(report["uncertain"], 1)
        entry = self.read_status()["messages"]["msg_test0001"]
        self.assertEqual(entry["reason"], "effect_uncertain")

    def test_interrupted_sending_becomes_uncertain_without_sending(self) -> None:
        self.write_records(self.record())
        store = DeliveryStatusStore(self.status_path())
        store.update(
            "msg_test0001",
            "sending",
            datetime.fromisoformat(NOW),
            attempt=True,
        )
        backend = FakeBackend()
        report = self.transport(backend).run()
        self.assertEqual(report["recovered"], 1)
        self.assertEqual(report["pending"], 0)
        self.assertEqual(backend.calls, [])
        self.assertEqual(
            self.read_status()["messages"]["msg_test0001"]["reason"], "interrupted"
        )

    def test_limit_sends_oldest_first(self) -> None:
        self.write_records(
            self.record("msg_new", delivered_at="2026-10-25T14:00:00+01:00", text="nuovo"),
            self.record("msg_old", delivered_at="2026-10-25T10:00:00+01:00", text="vecchio"),
        )
        backend = FakeBackend()
        report = self.transport(backend).run(limit=1)
        self.assertEqual(report["sent"], 1)
        self.assertEqual(report["pending"], 2)
        self.assertEqual(backend.calls[0][1], "vecchio")
        # Solo i record ispezionati hanno una voce; l'assenza vale "pending".
        self.assertNotIn("msg_new", self.read_status()["messages"])
        self.assertEqual(backend.calls[0][0], "393331234567")

    # -- gate globali -------------------------------------------------------

    def test_sending_flag_off_stops_before_browser(self) -> None:
        self.allow_delivery(flag=False)
        self.write_records(self.record())
        backend = FakeBackend()
        report = self.transport(backend).run()
        self.assertEqual(report["stopped"], "sending_disabled")
        self.assertEqual(report["blocked"], 0)
        self.assertEqual(backend.preflights, 0)
        self.assertEqual(backend.calls, [])

    def test_maintenance_stops(self) -> None:
        self.allow_delivery(maintenance=True)
        self.write_records(self.record())
        backend = FakeBackend()
        report = self.transport(backend).run()
        self.assertEqual(report["stopped"], "maintenance")
        self.assertEqual(backend.preflights, 0)

    def test_quiet_hours_stop(self) -> None:
        self.write_records(
            self.record(delivered_at="2026-10-25T02:30:00+01:00")
        )
        backend = FakeBackend()
        report = self.transport(backend, at="2026-10-25T03:00").run()
        self.assertEqual(report["stopped"], "quiet_hours")
        self.assertEqual(backend.calls, [])

    def test_daily_limit_counts_ui_deliveries_today(self) -> None:
        self.enable_whatsapp(max_per_day=1)
        now = datetime.fromisoformat(NOW)
        DeliveryStatusStore(self.status_path()).update(
            "msg_seed", "delivered_ui", now, remote_delivery_verified=False
        )
        self.write_records(self.record())
        backend = FakeBackend()
        report = self.transport(backend).run()
        self.assertEqual(report["stopped"], "daily_limit")
        self.assertEqual(backend.calls, [])

    def test_yesterday_delivery_does_not_count_today(self) -> None:
        self.enable_whatsapp(max_per_day=1)
        now = datetime.fromisoformat(NOW)
        DeliveryStatusStore(self.status_path()).update(
            "msg_seed",
            "delivered_ui",
            now - timedelta(days=1),
            remote_delivery_verified=False,
        )
        self.write_records(self.record())
        backend = FakeBackend()
        report = self.transport(backend).run()
        self.assertEqual(report["sent"], 1)

    # -- validazione del record --------------------------------------------

    def test_contact_outside_whitelist_is_blocked(self) -> None:
        self.enable_whatsapp(
            contacts=[{"user_id": "user_giulia", "display_name": "Giulia", "phone": "+39 333 7654321"}]
        )
        self.write_records(self.record())
        backend = FakeBackend()
        report = self.transport(backend).run()
        self.assertEqual(report["blocked"], 1)
        self.assertEqual(backend.calls, [])
        self.assertEqual(
            self.read_status()["messages"]["msg_test0001"]["reason"],
            "contact_not_allowed",
        )

    def test_missing_phone_is_blocked(self) -> None:
        self.enable_whatsapp(contacts=[{**MARCO}])
        self.write_records(self.record())
        report = self.transport(FakeBackend()).run()
        self.assertEqual(report["blocked"], 1)
        self.assertEqual(
            self.read_status()["messages"]["msg_test0001"]["reason"],
            "phone_missing",
        )

    def test_invalid_phone_is_blocked(self) -> None:
        self.enable_whatsapp(contacts=[{**MARCO, "phone": "123"}])
        self.write_records(self.record())
        self.transport(FakeBackend()).run()
        self.assertEqual(
            self.read_status()["messages"]["msg_test0001"]["reason"],
            "phone_invalid",
        )

    def test_stale_and_future_messages_are_blocked(self) -> None:
        self.write_records(
            self.record("msg_stale", delivered_at="2026-10-23T14:50:00+01:00"),
            self.record("msg_future", delivered_at="2026-10-25T16:00:00+01:00"),
        )
        report = self.transport(FakeBackend()).run(limit=2)
        self.assertEqual(report["blocked"], 2)
        status = self.read_status()["messages"]
        self.assertEqual(status["msg_stale"]["reason"], "stale")
        self.assertEqual(status["msg_future"]["reason"], "future_timestamp")

    def test_malformed_records_are_blocked_without_sending(self) -> None:
        records = [
            self.record("msg_channel", channel="browser"),
            self.record("msg_transport", transport="files"),
            self.record("msg_text", text=""),
            self.record("msg_target", target=None),
            self.record("msg_time", delivered_at=None),
        ]
        self.write_records(*records)
        backend = FakeBackend()
        report = self.transport(backend).run(limit=5)
        self.assertEqual(report["blocked"], 5)
        self.assertEqual(backend.calls, [])
        reasons = {
            key: value["reason"]
            for key, value in self.read_status()["messages"].items()
        }
        self.assertEqual(reasons["msg_channel"], "channel")
        self.assertEqual(reasons["msg_transport"], "transport")
        self.assertEqual(reasons["msg_text"], "text_missing")
        self.assertEqual(reasons["msg_target"], "target_missing")
        self.assertEqual(reasons["msg_time"], "timestamp_missing")

    # -- lock e registro ----------------------------------------------------

    def test_lock_prevents_concurrent_runs(self) -> None:
        self.write_records(self.record())
        lock_path = self.root / "data" / "outbox" / ".transport.lock"
        backend = FakeBackend()
        with OutboxLock(lock_path):
            with self.assertRaises(OutboxBusyError):
                self.transport(backend).run()
        self.assertEqual(backend.calls, [])

    def test_backup_ignores_lock_while_transport_holds_it(self) -> None:
        self.write_records(self.record())
        lock_path = self.root / "data" / "outbox" / ".transport.lock"
        with OutboxLock(lock_path):
            created = BackupService(self.root).create(reason="test", keep=2)
        backup_root = Path(created["path"])
        manifest = json.loads(
            (backup_root / "manifest.json").read_text(encoding="utf-8")
        )
        paths = {item["path"] for item in manifest["entries"]}
        self.assertIn("data/outbox/messages.jsonl", paths)
        self.assertNotIn("data/outbox/.transport.lock", paths)
        self.assertFalse(
            (backup_root / "data" / "outbox" / ".transport.lock").exists()
        )

    def test_corrupt_status_file_aborts(self) -> None:
        self.write_records(self.record())
        self.status_path().parent.mkdir(parents=True, exist_ok=True)
        self.status_path().write_text("{non-json", encoding="utf-8")
        backend = FakeBackend()
        with self.assertRaises(ConfigError):
            self.transport(backend).run()
        self.assertEqual(backend.calls, [])

    def test_status_and_audit_contain_no_text_phone_or_name(self) -> None:
        self.write_records(self.record())
        self.transport(FakeBackend()).run()
        for path in (self.status_path(), self.root / "data" / "logs" / "audit.jsonl"):
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("Ciao Marco", raw)
            self.assertNotIn("333", raw)
            self.assertNotIn("user_marco", raw)
            self.assertNotIn("Marco", raw)

        events = [
            item
            for item in self.audit_events()
            if item.get("action") == "whatsapp.transport"
        ]
        self.assertEqual(events[-1]["outcome"], "ok")
        self.assertEqual(events[-1]["message_id"], "msg_test0001")
        self.assertEqual(events[-1]["transport"], "whatsapp_web")

    @unittest.skipIf(os.name == "nt", "permessi POSIX non applicabili")
    def test_status_file_has_restricted_permissions(self) -> None:
        self.write_records(self.record())
        self.transport(FakeBackend()).run()
        mode = stat.S_IMODE(self.status_path().stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_status_report_is_safe_and_does_not_send(self) -> None:
        self.write_records(self.record())
        backend = FakeBackend()
        transport = self.transport(backend)
        report = transport.status_report()
        self.assertEqual(report["counts"]["total"], 1)
        self.assertEqual(report["counts"]["pending"], 1)
        self.assertIsNone(report["gate"])
        self.assertTrue(report["messages"][0]["phone_configured"])
        self.assertEqual(backend.calls, [])
        raw = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("Ciao Marco", raw)
        self.assertNotIn("333", raw)
        self.assertNotIn("user_marco", raw)

    # -- CLI ----------------------------------------------------------------

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = outbox_module.main(
                ["--root", str(self.root), "--at", NOW, *argv]
            )
        return code, out.getvalue(), err.getvalue()

    def test_cli_check_prints_state_without_sending(self) -> None:
        self.write_records(self.record())
        backend = FakeBackend()
        with mock.patch(
            "agent_memory.outbox._build_backend", return_value=backend
        ):
            code, out, err = self.run_cli("--check")
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertEqual(report["counts"]["pending"], 1)
        self.assertEqual(backend.calls, [])
        self.assertNotIn("Ciao Marco", out)
        self.assertNotIn("333", out)

    def test_cli_send_uses_backend_and_reports(self) -> None:
        self.write_records(self.record())
        backend = FakeBackend()
        with mock.patch(
            "agent_memory.outbox._build_backend", return_value=backend
        ):
            code, out, err = self.run_cli("--send")
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertEqual(report["sent"], 1)
        self.assertEqual(len(backend.calls), 1)
        self.assertNotIn("Ciao Marco", out)

    def test_cli_exit_code_two_on_uncertain(self) -> None:
        self.write_records(self.record())
        backend = FakeBackend(error=RuntimeError("boom"))
        with mock.patch(
            "agent_memory.outbox._build_backend", return_value=backend
        ):
            code, out, _err = self.run_cli("--send")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["uncertain"], 1)

    def test_cli_reports_lock_busy_as_error(self) -> None:
        self.write_records(self.record())
        lock_path = self.root / "data" / "outbox" / ".transport.lock"
        with OutboxLock(lock_path):
            code, _out, err = self.run_cli("--send")
        self.assertEqual(code, 1)
        self.assertIn("consegna è già in corso", err)


if __name__ == "__main__":
    unittest.main()
