"""Test del TLS locale e dell'accesso HTTPS della dashboard."""

from __future__ import annotations

import os
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from agent_memory.dashboard import create_server
from agent_memory.dashboard_auth import DashboardAuth
from agent_memory.dashboard_tls import ensure_dashboard_tls
from agent_memory.errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "correct horse battery"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DashboardTLSTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.tls_dir = self.root / "tls"
        shutil.copytree(PROJECT_ROOT / "config", self.root / "config")
        shutil.copytree(
            PROJECT_ROOT / "data",
            self.root / "data",
            ignore=shutil.ignore_patterns("models", "indexes"),
        )
        self.auth = DashboardAuth(PASSWORD)
        self.server = create_server(
            self.root,
            host="127.0.0.1",
            port=0,
            quiet=True,
            auth=self.auth,
            https=True,
            tls_dir=self.tls_dir,
            tls_sans=("192.0.2.10", "dashboard.test"),
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base_url = f"https://127.0.0.1:{self.server.server_address[1]}"
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.opener = urllib.request.build_opener(
            NoRedirect,
            urllib.request.HTTPSHandler(context=context),
        )

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
    ):
        headers = {}
        if cookie:
            headers["Cookie"] = cookie
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

    def test_certificate_is_generated_and_scoped(self) -> None:
        certificate = self.tls_dir / "cert.pem"
        key = self.tls_dir / "key.pem"
        self.assertTrue(certificate.is_file())
        self.assertTrue(key.is_file())
        if os.name == "posix":
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.tls_dir.stat().st_mode & 0o777, 0o700)
        self.assertIsNotNone(self.server.tls)

        openssl = shutil.which("openssl")
        if openssl is None:
            self.skipTest("OpenSSL non disponibile per ispezionare il certificato.")
        result = subprocess.run(
            [
                openssl,
                "x509",
                "-noout",
                "-ext",
                "subjectAltName",
                "-in",
                str(certificate),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DNS:dashboard.test", result.stdout)
        self.assertIn("IP Address:192.0.2.10", result.stdout)
        self.assertIn("DNS:localhost", result.stdout)
        self.assertIn("IP Address:127.0.0.1", result.stdout)

        before = certificate.read_bytes()
        again = ensure_dashboard_tls(
            self.tls_dir, extra_names=("192.0.2.10", "dashboard.test")
        )
        self.assertEqual(again.certificate_path, certificate)
        self.assertEqual(certificate.read_bytes(), before)

        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ConfigError):
            ensure_dashboard_tls(
                Path(tmp) / "invalid", extra_names=("bad name",)
            )

    def test_https_auth_session_cookie_is_secure(self) -> None:
        response = self._request("/api/overview")
        self.assertEqual(response.status, 401)

        response = self._request("/login")
        self.assertEqual(response.status, 200)
        page = response.read().decode("utf-8")
        match = re.search(r'name="csrf" value="([^"]+)"', page)
        self.assertIsNotNone(match)
        data = urllib.parse.urlencode(
            {"csrf": match.group(1), "password": PASSWORD, "next": "/"}
        ).encode()
        response = self._request("/login", data=data)
        self.assertEqual(response.status, 303)
        set_cookie = response.headers["Set-Cookie"]
        self.assertIn("Secure", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        cookie = set_cookie.split(";", 1)[0]

        response = self._request("/api/overview", cookie=cookie)
        self.assertEqual(response.status, 200)

        response = self._request("/api/session", cookie=cookie)
        self.assertEqual(response.status, 200)
        self.assertIn(b'"auth_enabled": true', response.read())

        response = self._request("/logout", data=b"", cookie=cookie)
        self.assertEqual(response.status, 303)
        self.assertIn("Secure", response.headers["Set-Cookie"])

        response = self._request("/api/overview", cookie=cookie)
        self.assertEqual(response.status, 401)


if __name__ == "__main__":
    unittest.main()
