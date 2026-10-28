"""Test HTTP della sezione Aspetto della dashboard."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

from PIL import Image

from agent_memory.dashboard import create_server
from agent_memory.dashboard_auth import DashboardAuth
from agent_memory.profile import PROJECT_ROOT

PASSWORD = "correct horse battery"
OUTPUT = BytesIO()
Image.new("RGB", (2, 2), (20, 40, 60)).save(OUTPUT, format="PNG")
PNG = OUTPUT.getvalue()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class VisualDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "profile"
        shutil.copytree(PROJECT_ROOT / "config", self.root / "config")
        shutil.copytree(
            PROJECT_ROOT / "data",
            self.root / "data",
            ignore=shutil.ignore_patterns("models", "indexes", "dashboard_tls"),
        )
        self.server = create_server(
            self.root, host="127.0.0.1", port=0, quiet=True,
            auth=DashboardAuth(PASSWORD),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.opener = urllib.request.build_opener(NoRedirect)
        self.cookie = ""
        self.cookie = self._login()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def _request(self, path, *, data=None, headers=None):
        request_headers = {"Origin": self.base}
        if headers:
            request_headers.update(headers)
        if self.cookie:
            request_headers["Cookie"] = self.cookie
        request = urllib.request.Request(
            self.base + path, data=data, headers=request_headers,
            method="POST" if data is not None else "GET",
        )
        try:
            response = self.opener.open(request, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        self.addCleanup(response.close)
        return response

    def _login(self):
        response = self._request("/login")
        page = response.read().decode()
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        response = self._request(
            "/login",
            data=(f"csrf={csrf}&password={PASSWORD}&next=%2F").encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        return response.headers["Set-Cookie"].split(";", 1)[0]

    def _csrf(self):
        response = self._request("/api/session")
        self.assertEqual(response.status, 200)
        return json.loads(response.read())["csrf"]

    def _post(self, path, payload):
        return self._request(
            path,
            data=json.dumps({**payload, "csrf": self._csrf()}).encode(),
            headers={"Content-Type": "application/json"},
        )

    def test_authenticated_visual_page_and_profile_update(self):
        response = self._request("/")
        self.assertEqual(response.status, 200)
        self.assertIn(b"data-view=\"visual\"", response.read())
        response = self._request("/api/visual/status")
        self.assertEqual(response.status, 200)
        self.assertFalse(json.loads(response.read())["enabled"])
        response = self._post("/api/visual/profile", {
            "profile": {"body": "corporatura atletica", "fixed_seed": 41},
            "enabled": False,
        })
        self.assertEqual(response.status, 200)
        saved = json.loads(response.read())
        self.assertEqual(saved["profile"]["body"], "corporatura atletica")
        self.assertEqual(saved["profile"]["fixed_seed"], 41)

    def test_reference_upload_and_generation_require_csrf_and_consent(self):
        unauthorized = urllib.request.Request(
            self.base + "/api/visual/status", headers={"Origin": self.base}
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.opener.open(unauthorized, timeout=10)
        self.assertEqual(error.exception.code, 401)
        error.exception.close()

        csrf = self._csrf()
        response = self._request(
            "/api/visual/reference", data=PNG,
            headers={"Content-Type": "image/png", "X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status, 200)
        self.assertTrue(json.loads(response.read())["reference_available"])
        response = self._post("/api/visual/profile", {
            "profile": {"body": "corporatura media"}, "enabled": True,
        })
        self.assertEqual(response.status, 400)
        self.assertIn("GENERA", json.loads(response.read())["error"])
        response = self._post("/api/visual/profile", {
            "profile": {"body": "corporatura media"}, "enabled": True,
            "confirmation": "GENERA",
        })
        self.assertEqual(response.status, 200)
        response = self._post("/api/visual/generate", {
            "scene": "in giardino", "consent_to_external_processing": False,
        })
        self.assertEqual(response.status, 400)
        response = self._post("/api/visual/generate", {
            "scene": "in giardino", "consent_to_external_processing": True,
        })
        self.assertEqual(response.status, 500)
        self.assertIn("Provider non configurato", json.loads(response.read())["error"])
