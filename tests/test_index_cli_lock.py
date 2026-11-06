"""Test del lock condiviso nel comando index."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from agent_memory import profile as profile_module
from tests.support import copy_profile


class IndexCliLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "profile"
        copy_profile(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_cli_sync_respects_profile_lock(self) -> None:
        from agent_memory.index import main as index_main

        with profile_module._profile_lock(self.root):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                blocked = index_main(["--root", str(self.root), "--sync"])
            self.assertEqual(blocked, 1)
            self.assertIn("già in corso", stderr.getvalue())

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                timed_out = index_main(
                    [
                        "--root",
                        str(self.root),
                        "--rebuild",
                        "--wait-timeout",
                        "0.1",
                    ]
                )
            self.assertEqual(timed_out, 1)
            self.assertIn("Timeout di", stderr.getvalue())

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = index_main(["--root", str(self.root), "--status"])
            self.assertIn(status, (0, 1))
            self.assertNotIn("in corso", stderr.getvalue())

    def test_wait_flags_rejected_for_read_only_checks(self) -> None:
        from agent_memory.index import main as index_main

        with self.assertRaises(SystemExit) as caught:
            index_main(
                [
                    "--root",
                    str(self.root),
                    "--status",
                    "--wait-timeout",
                    "1",
                ]
            )
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
