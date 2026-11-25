"""Test della vista Manutenzione: backup, coerenza e stato dell'indice."""

from __future__ import annotations

import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from agent_memory.dashboard import create_server
from agent_memory.dashboard_auth import DashboardAuth
from agent_memory.dashboard_maintenance import MaintenanceService
from agent_memory.errors import ValidationError
from tests.support import copy_profile

PASSWORD = "correct horse battery"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class MaintenanceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "profilo"
        copy_profile(self.root)
        self.service = MaintenanceService(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_snapshot_reports_databases_schema_and_index(self) -> None:
        snapshot = self.service.snapshot()
        self.assertEqual(snapshot["root"], str(self.root.resolve()))
        self.assertTrue(snapshot["backups"]["ok"])
        databases = {item["name"]: item for item in snapshot["databases"]}
        self.assertEqual(len(databases), 4)
        for item in databases.values():
            self.assertTrue(item["exists"], item["name"])
            self.assertTrue(item["schema_version"], item["name"])
            self.assertIsInstance(item["bytes"], int)
        self.assertIn("indexes", snapshot["index"])
        self.assertEqual(snapshot["restore_confirmation"], "RIPRISTINA")

    def test_backup_create_and_verify(self) -> None:
        created = self.service.run_action({"action": "create"})
        name = created["result"]["name"]
        self.assertTrue(name.startswith("backup-"))
        self.assertEqual(len(created["backups"]["items"]), 1)

        verified = self.service.run_action({"action": "verify", "name": name})
        self.assertTrue(verified["result"]["ok"], verified["result"])

    def test_restore_is_refused_from_the_dashboard(self) -> None:
        name = self.service.run_action({"action": "create"})["result"]["name"]
        with self.assertRaises(ValidationError) as caught:
            self.service.run_action(
                {"action": "restore", "name": name, "confirmation": "RIPRISTINA"}
            )
        self.assertIn("terminale", str(caught.exception))

    def test_unknown_action_and_missing_name_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.run_action({"action": "format"})
        with self.assertRaises(ValidationError):
            self.service.run_action({"action": "verify"})

    def test_check_reads_the_profile_without_writing_backups(self) -> None:
        report = self.service.run_action({"action": "check"})
        self.assertIn("report", report)
        self.assertIn("checks", report["report"])
        self.assertTrue(report["report"]["ok"], report["report"].get("checks"))
        self.assertEqual(self.service.snapshot()["backups"]["items"], [])


class MaintenanceHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "profilo"
        copy_profile(self.root)
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
            response = self.opener.open(request, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        self.addCleanup(response.close)
        return response

    def _login(self) -> str:
        page = self._request("/login").read().decode("utf-8")
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
        return json.loads(response.read())["csrf"]

    def test_page_contains_the_maintenance_view(self) -> None:
        cookie = self._login()
        page = self._request("/", cookie=cookie).read().decode("utf-8")
        self.assertIn('data-view="maintenance"', page)
        self.assertIn('id="view-maintenance"', page)

    def test_api_snapshot_and_backup_action(self) -> None:
        cookie = self._login()
        response = self._request("/api/maintenance", cookie=cookie)
        self.assertEqual(response.status, 200)
        snapshot = json.loads(response.read())
        self.assertIn("backups", snapshot)
        self.assertIn("databases", snapshot)

        csrf = self._csrf(cookie)
        response = self._request(
            "/api/maintenance/action",
            cookie=cookie,
            data=json.dumps({"action": "create", "csrf": csrf}).encode(),
        )
        self.assertEqual(response.status, 200)
        created = json.loads(response.read())
        self.assertTrue(created["result"]["name"].startswith("backup-"))

        response = self._request(
            "/api/maintenance/action",
            cookie=cookie,
            data=json.dumps(
                {
                    "action": "check",
                    "csrf": self._csrf(cookie),
                }
            ).encode(),
        )
        self.assertEqual(response.status, 200)
        self.assertIn("checks", json.loads(response.read())["report"])

    def test_maintenance_api_requires_a_session(self) -> None:
        response = self._request("/api/maintenance")
        self.assertIn(response.status, {302, 303, 401})
        self.assertNotEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
