"""Test dell'autenticazione locale della dashboard."""

from __future__ import annotations

import http.client
import os
import re
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from agent_memory.dashboard import _same_origin, create_server
from agent_memory.dashboard_auth import DashboardAuth, resolve_auth
from agent_memory.errors import ConfigError, ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "correct horse battery"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DashboardAuthUnitTests(unittest.TestCase):
    def test_password_and_session_lifecycle(self) -> None:
        clock = [1000.0]
        auth = DashboardAuth(
            PASSWORD,
            session_ttl=60,
            now=lambda: clock[0],
        )

        self.assertTrue(auth.verify_password(PASSWORD))
        self.assertFalse(auth.verify_password("sbagliata"))
        self.assertFalse(auth.verify_password(""))

        token = auth.issue_session()
        self.assertTrue(auth.validate_session(token))
        clock[0] += 61
        self.assertFalse(auth.validate_session(token))

    def test_login_tokens_are_single_use_and_expire(self) -> None:
        clock = [2000.0]
        auth = DashboardAuth(PASSWORD, now=lambda: clock[0])
        token = auth.create_login_token()

        self.assertTrue(auth.consume_login_token(token))
        self.assertFalse(auth.consume_login_token(token))

        expired = auth.create_login_token()
        clock[0] += 601
        self.assertFalse(auth.consume_login_token(expired))

    def test_login_rate_limit(self) -> None:
        auth = DashboardAuth(PASSWORD, max_login_attempts=2)
        self.assertTrue(auth.allow_login("client"))
        auth.register_failure("client")
        auth.register_failure("client")
        self.assertFalse(auth.allow_login("client"))
        self.assertTrue(auth.allow_login("altro-client"))
        auth.reset_failures("client")
        self.assertTrue(auth.allow_login("client"))

    def test_password_policy(self) -> None:
        with self.assertRaises(ValidationError):
            DashboardAuth("corta")
        with self.assertRaises(ValidationError):
            DashboardAuth(" " * 7)

    def test_resolve_auth_generated_and_disabled(self) -> None:
        disabled, generated = resolve_auth(require_auth=False, environ={})
        self.assertIsNone(disabled)
        self.assertIsNone(generated)

        auth, generated = resolve_auth(require_auth=True, environ={})
        self.assertIsNotNone(auth)
        self.assertIsNotNone(generated)
        self.assertTrue(auth.verify_password(generated))

    def test_resolve_auth_from_environment_and_file(self) -> None:
        auth, generated = resolve_auth(
            require_auth=False,
            environ={"AGENT_MEMORY_DASHBOARD_PASSWORD": PASSWORD},
        )
        self.assertIsNotNone(auth)
        self.assertIsNone(generated)
        self.assertTrue(auth.verify_password(PASSWORD))

        with tempfile.TemporaryDirectory() as tmp:
            password_path = Path(tmp) / "dashboard.pass"
            password_path.write_text(PASSWORD + "\n", encoding="utf-8")
            password_path.chmod(0o600)
            auth, generated = resolve_auth(
                require_auth=False,
                password_file=password_path,
                environ={},
            )
            self.assertIsNotNone(auth)
            self.assertIsNone(generated)
            self.assertTrue(auth.verify_password(PASSWORD))

            if os.name == "posix":
                password_path.chmod(0o644)
                with self.assertRaises(ConfigError):
                    resolve_auth(
                        require_auth=False,
                        password_file=password_path,
                        environ={},
                    )

    def test_same_origin_normalizes_ports_and_proxy_host(self) -> None:
        self.assertTrue(_same_origin(None, "127.0.0.1:3000"))
        self.assertTrue(_same_origin("https://preview.example", "preview.example"))
        self.assertTrue(
            _same_origin("https://preview.example", "preview.example:443")
        )
        self.assertTrue(
            _same_origin(
                "https://preview.example",
                "127.0.0.1:3000",
                "preview.example",
            )
        )
        self.assertTrue(
            _same_origin(
                "https://preview.example",
                "127.0.0.1:3000",
                "other.example, preview.example",
            )
        )
        self.assertFalse(
            _same_origin("https://preview.example", "127.0.0.1:3000")
        )
        self.assertFalse(_same_origin("https://preview.example", "preview.example:8443"))
        self.assertFalse(_same_origin("http://evil.example", "preview.example"))
        self.assertFalse(_same_origin("null", "preview.example"))
        self.assertTrue(
            _same_origin(
                "null",
                "preview.example",
                None,
                (),
                True,
            )
        )
        self.assertFalse(_same_origin("https://[malformed", "preview.example"))
        self.assertTrue(
            _same_origin(
                "https://preview.example",
                "127.0.0.1:3000",
                None,
                ("https://preview.example",),
            )
        )
        self.assertFalse(
            _same_origin(
                "https://evil.example",
                "127.0.0.1:3000",
                None,
                ("https://preview.example",),
            )
        )

    def test_resolve_auth_rejects_two_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            password_path = Path(tmp) / "dashboard.pass"
            password_path.write_text(PASSWORD, encoding="utf-8")
            password_path.chmod(0o600)
            with self.assertRaises(ConfigError):
                resolve_auth(
                    require_auth=False,
                    password_file=password_path,
                    environ={"AGENT_MEMORY_DASHBOARD_PASSWORD": PASSWORD},
                )


class DashboardAuthHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
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
            trusted_origins=("https://preview.example",),
            allow_null_origin=True,
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
        origin: str | None = None,
        forwarded_host: str | None = None,
    ):
        headers = {}
        if cookie:
            headers["Cookie"] = cookie
        if origin:
            headers["Origin"] = origin
        if forwarded_host:
            headers["X-Forwarded-Host"] = forwarded_host
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

    def _login_page(self) -> tuple[str, str]:
        response = self._request("/login")
        self.assertEqual(response.status, 200)
        page = response.read().decode("utf-8")
        match = re.search(r'name="csrf" value="([^"]+)"', page)
        self.assertIsNotNone(match)
        return page, match.group(1)

    def test_login_session_and_logout(self) -> None:
        response = self._request("/")
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/login?next=%2F")

        response = self._request("/api/overview")
        self.assertEqual(response.status, 401)

        response = self._request("/api/session")
        self.assertEqual(response.status, 200)
        self.assertIn(b'"auth_enabled": true', response.read())

        _page, csrf = self._login_page()
        data = urllib.parse.urlencode(
            {"csrf": csrf, "password": PASSWORD, "next": "/timeline"}
        ).encode()
        response = self._request("/login", data=data)
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/timeline")
        set_cookie = response.headers["Set-Cookie"]
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Strict", set_cookie)
        cookie = set_cookie.split(";", 1)[0]

        response = self._request("/api/overview", cookie=cookie)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read()[:1], b"{")

        response = self._request("/logout", data=b"", cookie=cookie)
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/login")
        self.assertIn("Max-Age=0", response.headers["Set-Cookie"])

        response = self._request("/api/overview", cookie=cookie)
        self.assertEqual(response.status, 401)

    def test_wrong_password_and_invalid_csrf(self) -> None:
        _page, csrf = self._login_page()
        data = urllib.parse.urlencode(
            {"csrf": csrf, "password": "sbagliata", "next": "/"}
        ).encode()
        response = self._request("/login", data=data)
        self.assertEqual(response.status, 401)
        self.assertNotIn("Set-Cookie", response.headers)

        _page, csrf = self._login_page()
        data = urllib.parse.urlencode(
            {"csrf": "token-inventato", "password": PASSWORD, "next": "/"}
        ).encode()
        response = self._request("/login", data=data)
        self.assertEqual(response.status, 400)

    def test_open_redirect_is_sanitized(self) -> None:
        _page, csrf = self._login_page()
        data = urllib.parse.urlencode(
            {"csrf": csrf, "password": PASSWORD, "next": "//evil.example"}
        ).encode()
        response = self._request("/login", data=data)
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/")

        _page, csrf = self._login_page()
        data = urllib.parse.urlencode(
            {"csrf": csrf, "password": PASSWORD, "next": "/%2Fevil.example"}
        ).encode()
        response = self._request("/login", data=data)
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/")

    def test_cross_origin_post_is_rejected(self) -> None:
        _page, csrf = self._login_page()
        data = urllib.parse.urlencode(
            {"csrf": csrf, "password": PASSWORD, "next": "/"}
        ).encode()
        response = self._request(
            "/login",
            data=data,
            origin="http://evil.example",
        )
        self.assertEqual(response.status, 403)

    def test_login_accepts_chunked_form(self) -> None:
        _page, csrf = self._login_page()
        payload = urllib.parse.urlencode(
            {"csrf": csrf, "password": PASSWORD, "next": "/"}
        ).encode("utf-8")
        conn = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_address[1], timeout=10
        )
        try:
            conn.request(
                "POST",
                "/login",
                body=iter([payload]),
                headers={
                    "Origin": self.base_url,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                encode_chunked=True,
            )
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, 303)
            self.assertEqual(response.getheader("Location"), "/")
        finally:
            conn.close()

    def test_login_accepts_null_origin_when_enabled(self) -> None:
        _page, csrf = self._login_page()
        data = urllib.parse.urlencode(
            {"csrf": csrf, "password": PASSWORD, "next": "/"}
        ).encode()
        response = self._request("/login", data=data, origin="null")
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/")

    def test_login_accepts_explicit_trusted_origin(self) -> None:
        _page, csrf = self._login_page()
        data = urllib.parse.urlencode(
            {"csrf": csrf, "password": PASSWORD, "next": "/"}
        ).encode()
        response = self._request(
            "/login",
            data=data,
            origin="https://preview.example",
        )
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/")

    def test_login_accepts_forwarded_host_from_reverse_proxy(self) -> None:
        _page, csrf = self._login_page()
        data = urllib.parse.urlencode(
            {"csrf": csrf, "password": PASSWORD, "next": "/"}
        ).encode()
        response = self._request(
            "/login",
            data=data,
            origin="https://preview.example",
            forwarded_host="preview.example",
        )
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/")

    def test_login_template_is_not_public(self) -> None:
        response = self._request("/static/login.html")
        self.assertEqual(response.status, 404)
        response = self._request("/static/styles.css")
        self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
