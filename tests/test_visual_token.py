"""Test del comando che salva e verifica il token Replicate."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from agent_memory import visual_token
from agent_memory.errors import ConfigError
from agent_memory.visual import resolve_token, token_path

TOKEN = "r8_testtoken0123456789"


class TokenStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_store_token_creates_private_file_and_resolves_it(self) -> None:
        path = visual_token.store_token(self.root, TOKEN)

        self.assertEqual(path, self.root / "data" / "secrets" / "replicate_token")
        self.assertEqual(path.read_text(encoding="utf-8").strip(), TOKEN)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(resolve_token(self.root), (TOKEN, "file"))

    def test_store_token_refuses_invalid_values(self) -> None:
        for value in ("", "   ", "short", "token con spazi", "x" * 501):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                visual_token.store_token(self.root, value)
        self.assertFalse(token_path(self.root).exists())

    def test_store_token_refuses_a_symlink_target(self) -> None:
        secrets_dir = self.root / "data" / "secrets"
        secrets_dir.mkdir(parents=True, mode=0o700)
        elsewhere = self.root / "altrove.txt"
        elsewhere.write_text("vecchio\n", encoding="utf-8")
        token_path(self.root).symlink_to(elsewhere)

        with self.assertRaises(ConfigError):
            visual_token.store_token(self.root, TOKEN)
        self.assertEqual(elsewhere.read_text(encoding="utf-8"), "vecchio\n")

    def test_remove_token_is_idempotent(self) -> None:
        visual_token.store_token(self.root, TOKEN)

        self.assertTrue(visual_token.remove_token(self.root))
        self.assertFalse(visual_token.remove_token(self.root))
        self.assertFalse(token_path(self.root).exists())


class Response:
    def __init__(self, body: bytes = b'{"username": "demo"}', status: int = 200) -> None:
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size: int) -> bytes:
        return self._body


class CheckTokenTests(unittest.TestCase):
    def test_check_uses_only_the_account_endpoint_with_a_bearer_header(self) -> None:
        captured: dict[str, object] = {}

        class Opener:
            def open(self, request, timeout):
                captured["url"] = request.full_url
                captured["auth"] = request.get_header("Authorization")
                captured["timeout"] = timeout
                return Response()

        with patch.object(visual_token, "build_opener", return_value=Opener()):
            result = visual_token.check_token(TOKEN)

        self.assertEqual(result, {"valid": True, "status": 200, "username": "demo"})
        self.assertEqual(captured["url"], "https://api.replicate.com/v1/account")
        self.assertEqual(captured["auth"], f"Bearer {TOKEN}")
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_http_errors_are_reported_without_the_token(self) -> None:
        with patch.object(visual_token, "build_opener") as builder:
            builder.return_value.open.side_effect = HTTPError(
                "https://api.replicate.com/v1/account", 401, "Unauthorized", None, None
            )
            result = visual_token.check_token(TOKEN)

        self.assertEqual(result, {"valid": False, "status": 401})
        self.assertNotIn(TOKEN, json.dumps(result))


class MainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_main_stores_the_token_from_hidden_input(self) -> None:
        out = io.StringIO()
        with (
            patch.object(visual_token.getpass, "getpass", side_effect=[TOKEN, TOKEN]),
            redirect_stdout(out),
        ):
            code = visual_token.main(["--root", str(self.root)])

        self.assertEqual(code, 0)
        self.assertEqual(token_path(self.root).read_text(encoding="utf-8").strip(), TOKEN)
        self.assertNotIn(TOKEN, out.getvalue())

    def test_main_rejects_a_mismatched_confirmation(self) -> None:
        err = io.StringIO()
        with (
            patch.object(visual_token.getpass, "getpass", side_effect=[TOKEN, TOKEN + "x"]),
            redirect_stderr(err),
            redirect_stdout(io.StringIO()),
        ):
            code = visual_token.main(["--root", str(self.root)])

        self.assertEqual(code, 1)
        self.assertFalse(token_path(self.root).exists())
        self.assertIn("non coincidono", err.getvalue())
        self.assertNotIn(TOKEN, err.getvalue())

    def test_check_without_a_token_explains_how_to_configure_it(self) -> None:
        err = io.StringIO()
        with (
            patch.dict("os.environ", {}, clear=True),
            redirect_stderr(err),
            redirect_stdout(io.StringIO()),
        ):
            code = visual_token.main(["--root", str(self.root), "--check"])

        self.assertEqual(code, 1)
        self.assertIn("mnemosine-visual-token", err.getvalue())

    def test_check_reports_a_valid_token_in_json(self) -> None:
        visual_token.store_token(self.root, TOKEN)
        out = io.StringIO()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(
                visual_token,
                "check_token",
                return_value={"valid": True, "status": 200, "username": "demo"},
            ),
            redirect_stdout(out),
        ):
            code = visual_token.main(["--root", str(self.root), "--check", "--json"])

        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["source"], "file")
        self.assertNotIn(TOKEN, out.getvalue())


if __name__ == "__main__":
    unittest.main()
