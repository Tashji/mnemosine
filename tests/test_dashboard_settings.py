"""Test delle impostazioni amministrative della dashboard."""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from agent_memory import heartbeat
from agent_memory.dashboard import create_server
from agent_memory.dashboard_auth import DashboardAuth
from agent_memory.dashboard_settings import DashboardSettings
from agent_memory.errors import ConfigError, ValidationError
from agent_memory.executor import delivered_messages
from agent_memory.profile import PROFILE_STATE_MISSING, PROJECT_ROOT, profile_state

PASSWORD = "correct horse battery"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _copy_profile(root: Path) -> None:
    shutil.copytree(PROJECT_ROOT / "config", root / "config")
    shutil.copytree(
        PROJECT_ROOT / "data",
        root / "data",
        ignore=shutil.ignore_patterns("models", "indexes"),
    )


class SettingsUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "profilo"
        _copy_profile(self.root)
        self.settings = DashboardSettings(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_snapshot_exposes_effective_policy_and_profile(self) -> None:
        session = self.root / "data" / "browser" / "session.json"
        session.parent.mkdir(parents=True, exist_ok=True)
        session.write_text("{}", encoding="utf-8")

        snapshot = self.settings.snapshot()

        self.assertFalse(snapshot["browser"]["enabled"])
        self.assertEqual(snapshot["browser"]["allowed_origins"], [])
        self.assertTrue(snapshot["browser"]["session_saved"])
        self.assertFalse(snapshot["whatsapp"]["enabled"])
        self.assertEqual(snapshot["whatsapp"]["quiet_hours"]["start"], "00:00")
        self.assertEqual(snapshot["behavior"]["heartbeat"]["interval_minutes"], 30)
        self.assertTrue(snapshot["profile"]["can_archive"])
        self.assertEqual(snapshot["profile"]["full_name"], "Alessandro Vitale")
        self.assertEqual(snapshot["profile"]["backup_dir"], str(self.root / "backups"))

    def test_browser_roundtrip_and_validation(self) -> None:
        saved = self.settings.save_browser(
            {
                "enabled": True,
                "interaction_enabled": True,
                "service_workers": True,
                "user_agent": "Mozilla/5.0 Chrome/130",
                "allowed_origins": ["example.org", "https://*.wikipedia.org"],
                "navigation_timeout_seconds": 20,
            }
        )

        self.assertTrue(saved["browser"]["enabled"])
        self.assertTrue(saved["browser"]["interaction_enabled"])
        self.assertTrue(saved["browser"]["service_workers"])
        self.assertEqual(
            saved["browser"]["allowed_origins"],
            ["example.org", "https://*.wikipedia.org"],
        )
        self.assertEqual(saved["browser"]["navigation_timeout_seconds"], 20.0)

        with self.assertRaises(ValidationError):
            self.settings.save_browser(
                {
                    "enabled": False,
                    "interaction_enabled": True,
                    "service_workers": False,
                    "user_agent": None,
                    "allowed_origins": [],
                    "navigation_timeout_seconds": 20,
                }
            )
        with self.assertRaises(ConfigError):
            self.settings.save_browser(
                {
                    "enabled": True,
                    "interaction_enabled": False,
                    "service_workers": False,
                    "user_agent": None,
                    "allowed_origins": ["http://example.org"],
                    "navigation_timeout_seconds": 20,
                }
            )
        with self.assertRaises(ConfigError):
            self.settings.save_browser(
                {
                    "enabled": True,
                    "interaction_enabled": False,
                    "service_workers": False,
                    "user_agent": "",
                    "allowed_origins": [],
                    "navigation_timeout_seconds": 20,
                }
            )

    def test_whatsapp_contacts_and_duplicate_rejected(self) -> None:
        saved = self.settings.save_whatsapp(
            {
                "enabled": True,
                "allowed_contacts": [
                    {
                        "user_id": "marco",
                        "display_name": "Marco",
                        "phone": "+39 333 111",
                    }
                ],
                "quiet_hours": {"start": "22:30", "end": "07:15"},
                "max_per_day": 2,
            }
        )
        self.assertTrue(saved["whatsapp"]["enabled"])
        self.assertEqual(saved["whatsapp"]["allowed_contacts"][0]["user_id"], "marco")
        self.assertEqual(saved["whatsapp"]["max_per_day"], 2)

        with self.assertRaises(ValidationError):
            self.settings.save_whatsapp(
                {
                    "enabled": False,
                    "allowed_contacts": [
                        {"user_id": "marco"},
                        {"user_id": "marco"},
                    ],
                    "quiet_hours": {"start": "22:30", "end": "07:15"},
                    "max_per_day": 2,
                }
            )
        with self.assertRaises(ConfigError):
            self.settings.save_whatsapp(
                {
                    "enabled": False,
                    "allowed_contacts": [],
                    "quiet_hours": {"start": "25:00", "end": "07:15"},
                    "max_per_day": 2,
                }
            )

    def test_behavior_updates_heartbeat_and_keeps_locked_flags(self) -> None:
        saved = self.settings.save_behavior(
            {
                "flags": {
                    "visual_generation_enabled": True,
                    "shadow_engine_enabled": False,
                    "maintenance_mode": True,
                },
                "heartbeat": {
                    "interval_minutes": 45,
                    "cooldown_minutes": 60,
                    "max_impulses_per_day": 2,
                },
            }
        )
        self.assertTrue(saved["behavior"]["flags"]["visual_generation_enabled"])
        self.assertFalse(saved["behavior"]["flags"]["shadow_engine_enabled"])
        self.assertTrue(saved["behavior"]["flags"]["maintenance_mode"])
        self.assertEqual(saved["behavior"]["heartbeat"]["interval_minutes"], 45)
        self.assertEqual(saved["behavior"]["heartbeat"]["max_impulses_per_day"], 2)

        with self.assertRaises(ValidationError):
            self.settings.save_behavior(
                {
                    "flags": {"whatsapp_sending_enabled": True},
                    "heartbeat": {},
                }
            )
        with self.assertRaises(ValidationError):
            self.settings.save_behavior(
                {
                    "flags": {"whatsapp_sending_enabled": True},
                    "confirmation": "no",
                    "heartbeat": {},
                }
            )
        with self.assertRaises(ValidationError):
            self.settings.save_behavior(
                {
                    "flags": {},
                    "heartbeat": {"interval_minutes": 0},
                }
            )

        enabled = self.settings.save_behavior(
            {
                "flags": {"whatsapp_sending_enabled": True},
                "confirmation": "ABILITA",
                "heartbeat": {},
            }
        )
        self.assertTrue(enabled["behavior"]["flags"]["whatsapp_sending_enabled"])

        disabled = self.settings.save_behavior(
            {
                "flags": {"whatsapp_sending_enabled": False},
                "heartbeat": {},
            }
        )
        self.assertFalse(disabled["behavior"]["flags"]["whatsapp_sending_enabled"])

    def test_archive_moves_profile_and_preserves_shared_data(self) -> None:
        tls = self.root / "data" / "dashboard_tls"
        tls.mkdir(parents=True, exist_ok=True)
        (tls / "key.pem").write_text("chiave", encoding="utf-8")
        models = self.root / "data" / "models"
        models.mkdir(parents=True, exist_ok=True)
        (models / "cache.bin").write_text("cache", encoding="utf-8")

        result = self.settings.archive_profile("ELIMINA")

        archive = Path(result["archive"])
        self.assertFalse((self.root / "config").exists())
        self.assertEqual(profile_state(self.root), PROFILE_STATE_MISSING)
        self.assertTrue((archive / "config" / "identity.json").is_file())
        self.assertTrue((archive / "data" / "timeline_episodic.db").is_file())
        self.assertEqual((tls / "key.pem").read_text(encoding="utf-8"), "chiave")
        self.assertEqual((models / "cache.bin").read_text(encoding="utf-8"), "cache")
        self.assertEqual(result["redirect"], "/setup")

    def test_archive_requires_confirmation_and_refuses_project_root(self) -> None:
        with self.assertRaises(ValidationError):
            self.settings.archive_profile("elimina")
        with self.assertRaises(ConfigError):
            DashboardSettings(PROJECT_ROOT).archive_profile("ELIMINA")


class SettingsHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "profilo"
        _copy_profile(self.root)
        self.auth = DashboardAuth(PASSWORD)
        self.server = create_server(
            self.root,
            host="127.0.0.1",
            port=0,
            quiet=True,
            auth=self.auth,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.opener = urllib.request.build_opener(NoRedirect)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    def _request(
        self,
        path: str,
        *,
        data: bytes | None = None,
        cookie: str | None = None,
        content_type: str = "application/json",
    ):
        headers = {"Origin": self.base_url}
        if cookie:
            headers["Cookie"] = cookie
        if data is not None:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method="POST" if data is not None else "GET",
        )
        try:
            response = self.opener.open(request, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        self.addCleanup(response.close)
        return response

    def _login(self) -> str:
        response = self._request("/login")
        self.assertEqual(response.status, 200)
        page = response.read().decode("utf-8")
        match = re.search(r'name="csrf" value="([^"]+)"', page)
        self.assertIsNotNone(match)
        data = urllib.parse.urlencode(
            {"csrf": match.group(1), "password": PASSWORD, "next": "/"}
        ).encode()
        response = self._request(
            "/login",
            data=data,
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status, 303)
        return response.headers["Set-Cookie"].split(";", 1)[0]

    def _csrf(self, cookie: str) -> str:
        response = self._request("/api/session", cookie=cookie)
        self.assertEqual(response.status, 200)
        return json.loads(response.read())["csrf"]

    def _post_json(self, path: str, payload: dict, cookie: str):
        return self._request(
            path,
            data=json.dumps(payload).encode("utf-8"),
            cookie=cookie,
        )

    def _set_pending_impulse(self, user_id: str, display_name: str) -> None:
        path = self.root / "config" / "current_state.json"
        state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        state["flags"]["maintenance_mode"] = False
        state["availability"] = {
            "accepting_messages": True,
            "response_style": "normale",
            "notes": "",
        }
        state["heartbeat"]["pending_impulse"] = {
            "trigger": "relazione",
            "target": {"user_id": user_id, "display_name": display_name},
            "context": {"days_since_contact": 6.0, "trust": 74},
            "created_at": "2026-10-25T14:30:00+01:00",
            "delivery": {"channel": "whatsapp", "authorized": True, "note": "prova"},
            "requires_message": True,
        }
        state["heartbeat"]["spontaneous_message_pending"] = True
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def test_settings_require_auth_and_csrf(self) -> None:
        self.assertEqual(self._request("/api/settings").status, 401)
        self.assertEqual(
            self._post_json(
                "/api/settings/browser",
                {"enabled": False},
                "",
            ).status,
            401,
        )

        cookie = self._login()
        response = self._request("/api/settings", cookie=cookie)
        self.assertEqual(response.status, 200)
        self.assertIn("browser", json.loads(response.read()))

        response = self._post_json(
            "/api/settings/browser",
            {
                "enabled": False,
                "interaction_enabled": False,
                "service_workers": False,
                "user_agent": None,
                "allowed_origins": [],
                "navigation_timeout_seconds": 30,
            },
            cookie,
        )
        self.assertEqual(response.status, 400)

        response = self._post_json(
            "/api/settings/browser",
            {
                "csrf": self._csrf(cookie),
                "enabled": True,
                "interaction_enabled": False,
                "service_workers": False,
                "user_agent": None,
                "allowed_origins": ["example.org"],
                "navigation_timeout_seconds": 30,
            },
            cookie,
        )
        self.assertEqual(response.status, 200)
        self.assertTrue(json.loads(response.read())["browser"]["enabled"])

    def test_archive_from_api_returns_to_setup(self) -> None:
        cookie = self._login()
        response = self._post_json(
            "/api/profile/archive",
            {"csrf": self._csrf(cookie), "confirmation": "no"},
            cookie,
        )
        self.assertEqual(response.status, 400)

        response = self._post_json(
            "/api/profile/archive",
            {"csrf": self._csrf(cookie), "confirmation": "ELIMINA"},
            cookie,
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read())["redirect"], "/setup")
        self.assertEqual(profile_state(self.root), PROFILE_STATE_MISSING)

        response = self._request("/", cookie=cookie)
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/setup")

        response = self._request("/setup", cookie=cookie)
        self.assertEqual(response.status, 200)

    def test_sending_requires_confirmation_and_can_be_disabled(self) -> None:
        cookie = self._login()
        response = self._post_json(
            "/api/settings/behavior",
            {
                "csrf": self._csrf(cookie),
                "flags": {"whatsapp_sending_enabled": True},
                "heartbeat": {},
            },
            cookie,
        )
        self.assertEqual(response.status, 400)
        self.assertIn("ABILITA", response.read().decode("utf-8"))

        response = self._post_json(
            "/api/settings/behavior",
            {
                "csrf": self._csrf(cookie),
                "flags": {"whatsapp_sending_enabled": True},
                "confirmation": "ABILITA",
                "heartbeat": {},
            },
            cookie,
        )
        self.assertEqual(response.status, 200)
        self.assertTrue(
            json.loads(response.read())["behavior"]["flags"][
                "whatsapp_sending_enabled"
            ]
        )

        response = self._post_json(
            "/api/settings/behavior",
            {
                "csrf": self._csrf(cookie),
                "flags": {"whatsapp_sending_enabled": False},
                "heartbeat": {},
            },
            cookie,
        )
        self.assertEqual(response.status, 200)
        self.assertFalse(
            json.loads(response.read())["behavior"]["flags"][
                "whatsapp_sending_enabled"
            ]
        )

    def test_active_sending_delivers_to_outbox_with_contacts_and_limit(self) -> None:
        cookie = self._login()
        response = self._post_json(
            "/api/settings/whatsapp",
            {
                "csrf": self._csrf(cookie),
                "enabled": True,
                "allowed_contacts": [
                    {
                        "user_id": "user_marco",
                        "display_name": "Marco",
                        "phone": "+39 333 1234567",
                    }
                ],
                "quiet_hours": {"start": "00:00", "end": "07:00"},
                "max_per_day": 1,
            },
            cookie,
        )
        self.assertEqual(response.status, 200, response.read().decode("utf-8"))

        response = self._post_json(
            "/api/settings/behavior",
            {
                "csrf": self._csrf(cookie),
                "flags": {"whatsapp_sending_enabled": True},
                "confirmation": "ABILITA",
                "heartbeat": {},
            },
            cookie,
        )
        self.assertEqual(response.status, 200, response.read().decode("utf-8"))

        state_path = self.root / "config" / "current_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertTrue(state["flags"]["whatsapp_sending_enabled"])

        def deliver(text: str) -> tuple[int, str, str]:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = heartbeat.main(
                    [
                        "--root",
                        str(self.root),
                        "--at",
                        "2026-10-25T15:00",
                        "--deliver",
                        text,
                    ]
                )
            return code, out.getvalue(), err.getvalue()

        # Giulia esiste nel grafo delle relazioni ma non è nella whitelist.
        self._set_pending_impulse("user_giulia", "Giulia")
        code, _out, err = deliver("Ciao Giulia")
        self.assertEqual(code, 1)
        self.assertIn("whitelist", err)
        self.assertEqual(delivered_messages(self.root), [])

        # Con il flag attivo e Marco autorizzato la consegna arriva nell'outbox.
        self._set_pending_impulse("user_marco", "Marco")
        code, out, err = deliver("Ciao Marco, come stai?")
        self.assertEqual(code, 0, err)
        self.assertIn("Messaggio consegnato a Marco", out)

        records = delivered_messages(self.root)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["target"]["user_id"], "user_marco")
        self.assertEqual(records[0]["target"]["phone"], "+39 333 1234567")
        self.assertEqual(records[0]["text"], "Ciao Marco, come stai?")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIsNone(state["heartbeat"]["pending_impulse"])
        self.assertFalse(state["heartbeat"]["spontaneous_message_pending"])

        # La seconda consegna dello stesso giorno supera il limite impostato.
        self._set_pending_impulse("user_marco", "Marco")
        code, _out, err = deliver("Di nuovo ciao")
        self.assertEqual(code, 1)
        self.assertIn("limite di 1 consegne al giorno", err)
        self.assertEqual(len(delivered_messages(self.root)), 1)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIsNotNone(state["heartbeat"]["pending_impulse"])


if __name__ == "__main__":
    unittest.main()
