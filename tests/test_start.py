"""Test del primo avvio guidato e della sua delega alla dashboard."""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_memory import start
from agent_memory.profile import CONFIG_FILENAMES, DATABASE_FILES


def make_ready_profile(root: Path) -> None:
    """Crea la struttura minima di un profilo già configurato."""

    (root / "config").mkdir(parents=True)
    for name in CONFIG_FILENAMES.values():
        (root / "config" / name).write_text("{}", encoding="utf-8")
    (root / "data").mkdir()
    for name in DATABASE_FILES:
        (root / "data" / name).write_bytes(b"")


class DefaultRootTests(unittest.TestCase):
    def test_default_is_per_user_and_outside_the_repository(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(start.PROFILE_ENV, None)
            root = start.default_profile_root()
        self.assertEqual(root, Path.home() / ".mnemosine" / "profile")
        self.assertNotIn("agent_memory", root.as_posix())

    def test_environment_override_wins(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {start.PROFILE_ENV: tmp}),
        ):
            self.assertEqual(start.default_profile_root(), Path(tmp))

    def test_blank_environment_override_is_ignored(self) -> None:
        with mock.patch.dict(os.environ, {start.PROFILE_ENV: "   "}):
            self.assertEqual(
                start.default_profile_root(), Path.home() / ".mnemosine" / "profile"
            )


class FirstRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "profilo"
        self.calls: list[list[str]] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, argv: list[str]) -> tuple[int, str]:
        def fake_dashboard(argv: list[str]) -> int:
            self.calls.append(argv)
            return 0

        output = io.StringIO()
        with (
            mock.patch.object(start, "dashboard_main", fake_dashboard),
            contextlib.redirect_stdout(output),
        ):
            code = start.main(argv)
        return code, output.getvalue()

    def test_missing_profile_opens_the_wizard(self) -> None:
        code, output = self._run(["--root", str(self.root), "--no-open"])
        self.assertEqual(code, 0)
        self.assertIn("Prima configurazione", output)
        self.assertIn(str(self.root), output)
        self.assertIn("/setup", output)
        self.assertEqual(len(self.calls), 1)
        argv = self.calls[0]
        self.assertEqual(argv[argv.index("--root") + 1], str(self.root.resolve()))
        self.assertNotIn("--open", argv)

    def test_browser_opens_by_default(self) -> None:
        _code, _output = self._run(["--root", str(self.root)])
        self.assertIn("--open", self.calls[0])

    def test_ready_profile_reopens_the_dashboard(self) -> None:
        make_ready_profile(self.root)
        code, output = self._run(["--root", str(self.root), "--no-open"])
        self.assertEqual(code, 0)
        self.assertIn("già configurato", output)
        self.assertNotIn("Prima configurazione", output)
        self.assertEqual(len(self.calls), 1)

    def test_default_root_used_when_missing(self) -> None:
        with mock.patch.dict(os.environ, {start.PROFILE_ENV: str(self.root)}):
            _code, output = self._run(["--no-open"])
        self.assertIn(str(self.root), output)
        self.assertEqual(self.calls[0][1], str(self.root.resolve()))

    def test_flags_are_forwarded(self) -> None:
        _code, _output = self._run(
            ["--root", str(self.root), "--host", "0.0.0.0", "--port", "9000",
             "--auth", "--https"]
        )
        argv = self.calls[0]
        self.assertEqual(argv[argv.index("--host") + 1], "0.0.0.0")
        self.assertEqual(argv[argv.index("--port") + 1], "9000")
        self.assertIn("--auth", argv)
        self.assertIn("--https", argv)
        self.assertIn("--open", argv)

    def test_exit_code_is_propagated(self) -> None:
        def failing_dashboard(argv: list[str]) -> int:
            return 3

        with (
            mock.patch.object(start, "dashboard_main", failing_dashboard),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(start.main(["--root", str(self.root)]), 3)


class ServerHintTests(unittest.TestCase):
    def test_server_points_to_the_wizard_when_profile_is_missing(self) -> None:
        from agent_memory import server

        with tempfile.TemporaryDirectory() as tmp:
            errors = io.StringIO()
            with (
                contextlib.redirect_stderr(errors),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = server.main(["--root", tmp, "--check"])
        self.assertEqual(code, 1)
        self.assertIn("mnemosine-start", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
