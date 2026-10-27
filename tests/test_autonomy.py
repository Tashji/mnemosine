from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from agent_memory.autonomy import AutonomyService
from agent_memory.browser import BrowserController
from agent_memory.errors import ValidationError
from tests.test_browser import FakeBackend
from tests.test_operational import OperationalTests


class AutonomyTests(OperationalTests):
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
