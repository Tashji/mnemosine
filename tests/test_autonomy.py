from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from agent_memory.autonomy import AutonomyService
from agent_memory.browser import BrowserController
from agent_memory.errors import ValidationError
from tests import test_operational
from tests.test_browser import FakeBackend


class AutonomyTests(test_operational.OperationalTests):
    def setUp(self):
        super().setUp()
        integrations = self.root / "config" / "integrations.json"
        integrations.write_text(json.dumps({"browser": {"enabled": True, "interaction_enabled": True,
            "allowed_origins": ["https://example.com"], "interaction_origins": ["https://example.com"]}}))
        self.backend = FakeBackend(text="Operazione completata")
        self.browser = BrowserController(self.root, backend=self.backend)
        self.service = AutonomyService(self.root, self.browser)
        self.config.update_current_state({"flags": {"maintenance_mode": False}, "heartbeat": {
            "pending_impulse": {"trigger": "noia", "created_at": self.service._now().isoformat()},
            "spontaneous_message_pending": True}})
        self.resolver = patch("agent_memory.browser._default_resolver", return_value=["93.184.216.34"])
        self.resolver.start()
        self.addCleanup(self.resolver.stop)

    def test_claim_act_verify_and_action_retry(self):
        run = self.service.claim("https://example.com")
        self.assertEqual(self.backend.opened, [])
        run_id = run["id"]
        first = self.service.act(run_id, "open-1", "open", {"url": "https://example.com"})
        self.assertEqual(first["state"], "awaiting_verification")
        again = self.service.act(run_id, "open-1", "open", {"url": "https://example.com"})
        self.assertEqual(first, again)
        self.assertEqual(len(self.backend.opened), 1)
        self.assertFalse(self.service.verify(run_id, "non presente")["ui_verified"])
        self.assertTrue(self.service.verify(run_id, "completata")["ui_verified"])
        self.assertIsNone(self.config.get("current_state")["heartbeat"]["pending_impulse"])
        self.assertFalse(self.service.status(run_id)["remote_delivery_verified"])

    def test_abort_preserves_completed_state(self):
        run_id = self.service.claim("https://example.com")["id"]
        self.service.act(run_id, "open", "open", {"url": "https://example.com"})
        self.assertTrue(self.service.verify(run_id, "completata")["ui_verified"])
        result = self.service.abort(run_id)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(self.service.status(run_id)["state"], "completed")
        self.assertFalse(result["remote_delivery_verified"])

    def test_interrupted_action_never_retried(self):
        run_id = self.service.claim("https://example.com")["id"]
        with patch.object(self.browser, "open", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.service.act(run_id, "lost", "open", {"url": "https://example.com"})
        restarted = AutonomyService(self.root, self.browser)
        result = restarted.act(run_id, "lost", "open", {"url": "https://example.com"})
        self.assertFalse(result["retry_allowed"])
        self.assertEqual(restarted.status(run_id)["state"], "uncertain")
        with self.assertRaises(ValidationError):
            restarted.act(run_id, "different", "open", {"url": "https://example.com"})
        self.assertEqual(self.backend.opened, [])

    def test_maintenance_revocation_and_new_impulse(self):
        run_id = self.service.claim("https://example.com")["id"]
        self.config.update_current_state({"flags": {"maintenance_mode": True}})
        with self.assertRaises(ValidationError):
            self.service.act(run_id, "no", "open", {"url": "https://example.com"})
        self.config.update_current_state({"flags": {"maintenance_mode": False}})
        self.service.act(run_id, "open", "open", {"url": "https://example.com"})
        self.config.update_current_state({"heartbeat": {"pending_impulse": {"created_at": "new"}}})
        with self.assertRaises(ValidationError):
            self.service.verify(run_id, "completata")
        self.assertEqual(self.config.get("current_state")["heartbeat"]["pending_impulse"]["created_at"], "new")

    def test_revoked_consent_blocks_effect_and_abort_keeps_impulse(self):
        run_id = self.service.claim("https://example.com")["id"]
        self.service.act(run_id, "open", "open", {"url": "https://example.com"})
        path = self.root / "config" / "integrations.json"
        document = json.loads(path.read_text())
        document["browser"]["interaction_origins"] = []
        path.write_text(json.dumps(document))
        from agent_memory.errors import BrowserBlockedError
        with self.assertRaises(BrowserBlockedError):
            self.service.act(run_id, "send", "press", {"key": "Enter"})
        self.assertEqual(self.backend.pressed, [])
        self.service.abort(run_id)
        self.assertIsNotNone(self.config.get("current_state")["heartbeat"]["pending_impulse"])

    def _interrupt(self):
        run_id = self.service.claim("https://example.com")["id"]
        with patch.object(self.browser, "open", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.service.act(run_id, "lost", "open", {"url": "https://example.com"})
        return run_id

    def test_administrative_review_preserves_receipt_and_never_retries(self):
        run_id = self._interrupt()
        summary = self.service.review_summary()["runs"][0]
        self.assertEqual(summary["id"], run_id)
        self.assertTrue(summary["review_required"])
        self.assertEqual(summary["last_action"]["action"], "open")
        for confirmation, note in (("no", "Verificato"), ("HO VERIFICATO", ""), ("HO VERIFICATO", "x" * 1001)):
            with self.assertRaises(ValidationError):
                self.service.review_uncertain(run_id, confirmation, note)
        result = self.service.review_uncertain(run_id, "HO VERIFICATO", "Verificato manualmente")
        self.assertTrue(result["impulse_cleared"])
        self.assertFalse(result["remote_delivery_verified"])
        self.assertIsNone(self.config.get("current_state")["heartbeat"]["pending_impulse"])
        self.assertEqual(self.service.status(run_id)["state"], "blocked")
        self.assertFalse(self.service.act(run_id, "lost", "open", {"url": "https://example.com"})["retry_allowed"])
        with self.assertRaises(ValidationError):
            self.service.act(run_id, "new", "open", {"url": "https://example.com"})
        with self.assertRaises(ValidationError):
            self.service.review_uncertain(run_id, "HO VERIFICATO", "Di nuovo")
        summary = self.service.review_summary()["runs"][0]
        self.assertEqual(summary["review"]["note"], "Verificato manualmente")
        self.assertFalse(summary["review_required"])
        self.assertEqual(self.backend.opened, [])

    def test_review_preserves_new_impulse_and_aborted_uncertainty(self):
        run_id = self._interrupt()
        self.service.status(run_id)
        self.service.abort(run_id)
        new = {"trigger": "new", "created_at": self.service._now().isoformat()}
        self.config.update_current_state({"heartbeat": {"pending_impulse": new}})
        with self.assertRaises(ValidationError):
            self.service.claim("https://example.com")
        result = self.service.review_uncertain(run_id, "HO VERIFICATO", "Controllato")
        self.assertFalse(result["impulse_cleared"])
        self.assertEqual(self.config.get("current_state")["heartbeat"]["pending_impulse"], new)
        self.assertTrue(self.service.claim("https://example.com")["available"])

    def test_review_rejects_active_action_and_keeps_state(self):
        from agent_memory.profile import _profile_lock
        run_id = self._interrupt()
        with _profile_lock(self.root), self.assertRaises(ValidationError):
            self.service.review_uncertain(run_id, "HO VERIFICATO", "Non ancora")
        self.assertEqual(self.service.status(run_id)["state"], "uncertain")

    def test_review_rolls_back_as_one_transaction(self):
        run_id = self._interrupt()
        before = self.config.get("current_state")["heartbeat"]["pending_impulse"]
        with patch.object(self.service.config, "update_current_state", side_effect=OSError("disk failure")), self.assertRaises(OSError):
            self.service.review_uncertain(run_id, "HO VERIFICATO", "Controllato")
        self.assertEqual(self.config.get("current_state")["heartbeat"]["pending_impulse"], before)
        self.assertTrue(self.service.review_summary()["runs"][0]["review_required"])
        self.assertTrue(self.service.review_uncertain(run_id, "HO VERIFICATO", "Controllato")["impulse_cleared"])

    def test_review_rejects_action_that_is_actually_running(self):
        import threading
        started, finish = threading.Event(), threading.Event()
        run_id = self.service.claim("https://example.com")["id"]
        errors = []
        def slow_open(**kwargs):
            started.set()
            if not finish.wait(5):
                raise RuntimeError("test timeout")
            return self.browser._navigate(kwargs["url"], max_chars=6000)
        def act():
            try:
                self.service.act(run_id, "slow", "open", {"url": "https://example.com"})
            except (RuntimeError, ValidationError) as exc:
                errors.append(exc)
        with patch.object(self.browser, "open", side_effect=slow_open):
            thread = threading.Thread(target=act)
            thread.start()
            try:
                self.assertTrue(started.wait(5))
                summary = self.service.review_summary()["runs"][0]
                self.assertTrue(summary["review_required"])
                with self.assertRaises(ValidationError):
                    self.service.review_uncertain(run_id, "HO VERIFICATO", "Controllato")
            finally:
                finish.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.service.status(run_id)["state"], "awaiting_verification")
        with self.assertRaises(ValidationError):
            self.service.review_uncertain(run_id, "HO VERIFICATO", "Controllato")

    def test_review_summary_does_not_include_action_arguments(self):
        run_id = self.service.claim("https://example.com")["id"]
        with self.config.transaction() as conn:
            conn.execute("INSERT INTO autonomous_actions(run_id,id,payload,result) VALUES (?, ?, ?, NULL)",
                         (run_id, "sensitive", json.dumps({"action": "fill", "arguments": {"value": "PASSWORD_SEGRETA"}})))
            conn.execute("UPDATE autonomous_runs SET state='running' WHERE id=?", (run_id,))
        self.assertNotIn("PASSWORD_SEGRETA", json.dumps(self.service.review_summary()))

    def test_review_expired_and_blocked_runs_without_repeating_effects(self):
        for state in ("expired", "blocked"):
            with self.subTest(state=state):
                impulse = {"trigger": state, "created_at": self.service._now().isoformat()}
                self.config.update_current_state({"heartbeat": {"pending_impulse": impulse, "spontaneous_message_pending": True}})
                run_id = self.service.claim("https://example.com")["id"]
                if state == "expired":
                    with self.config.transaction() as conn:
                        conn.execute("UPDATE autonomous_runs SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?", (run_id,))
                else:
                    self.service.act(run_id, "opened", "open", {"url": "https://example.com"})
                    self.service.abort(run_id)
                count = len(self.backend.opened)
                summary = next(r for r in self.service.review_summary()["runs"] if r["id"] == run_id)
                self.assertEqual(summary["review_reason"], state)
                result = self.service.review_uncertain(run_id, "HO VERIFICATO", "Controllato")
                self.assertTrue(result["impulse_cleared"])
                self.assertEqual(self.service.status(run_id)["state"], "blocked")
                self.assertFalse(self.config.get("current_state")["heartbeat"]["spontaneous_message_pending"])
                self.assertEqual(len(self.backend.opened), count)
                with self.assertRaises(ValidationError):
                    self.service.act(run_id, "new-action", "open", {"url": "https://example.com"})
                if state == "blocked":
                    self.service.act(run_id, "opened", "open", {"url": "https://example.com"})
                    self.assertEqual(len(self.backend.opened), count)

    def test_summary_prioritizes_expired_run_over_newer_ready_runs(self):
        run_id = self.service.claim("https://example.com")["id"]
        with self.config.transaction() as conn:
            conn.execute("UPDATE autonomous_runs SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?", (run_id,))
            for index in range(12):
                conn.execute("INSERT INTO autonomous_runs(id, impulse, state, url, created_at) VALUES (?, ?, ?, ?, ?)",
                             (f"complete-{index}", json.dumps({"unique": index}), "completed", "https://example.com", self.service._now().isoformat()))
        runs = self.service.review_summary(limit=1)["runs"]
        self.assertEqual(runs[0]["id"], run_id)
        self.assertEqual(runs[0]["review_reason"], "expired")

    def test_review_stale_run_keeps_replacement_impulse(self):
        run_id = self.service.claim("https://example.com")["id"]
        with self.config.transaction() as conn:
            conn.execute("UPDATE autonomous_runs SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?", (run_id,))
        new = {"trigger": "replacement", "created_at": self.service._now().isoformat()}
        self.config.update_current_state({"heartbeat": {"pending_impulse": new}})
        self.assertFalse(self.service.review_uncertain(run_id, "HO VERIFICATO", "Verificato")["impulse_cleared"])
        self.assertEqual(self.config.get("current_state")["heartbeat"]["pending_impulse"], new)
        self.assertTrue(self.service.claim("https://example.com")["available"])

    def test_review_rejects_live_and_completed_runs(self):
        run_id = self.service.claim("https://example.com")["id"]
        with self.assertRaises(ValidationError):
            self.service.review_uncertain(run_id, "HO VERIFICATO", "Controllato")
        self.service.act(run_id, "open", "open", {"url": "https://example.com"})
        self.service.verify(run_id, "completata")
        with self.assertRaises(ValidationError):
            self.service.review_uncertain(run_id, "HO VERIFICATO", "Controllato")

    def test_expiry_and_step_limit(self):
        run_id = self.service.claim("https://example.com")["id"]
        with self.config.transaction() as conn:
            conn.execute("UPDATE autonomous_runs SET steps=20 WHERE id=?", (run_id,))
        with self.assertRaises(ValidationError):
            self.service.act(run_id, "over", "open", {"url": "https://example.com"})
        with self.config.transaction() as conn:
            conn.execute("UPDATE autonomous_runs SET steps=0, created_at='2000-01-01T00:00:00+00:00' WHERE id=?", (run_id,))
        with self.assertRaises(ValidationError):
            self.service.act(run_id, "expired", "open", {"url": "https://example.com"})


if __name__ == "__main__":
    unittest.main()
