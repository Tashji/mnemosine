"""Test del comando di creazione di un profilo vuoto."""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from agent_memory import profile as profile_module
from agent_memory.biography import BiographyService
from agent_memory.config import ConfigStore
from agent_memory.dashboard import DashboardData
from agent_memory.profile import PROJECT_ROOT, main


class ProfileInitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "profile"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *extra: str) -> tuple[int, str, str]:
        argv = [
            "init",
            "--root",
            str(self.root),
            "--first-name",
            "Marco",
            "--last-name",
            "Rossi",
            "--birth-date",
            "2003-05-12",
            "--birth-city",
            "Roma",
            "--birth-province",
            "Roma",
            "--birth-region",
            "Lazio",
            *extra,
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def _count(self, filename: str, table: str) -> int:
        conn = sqlite3.connect(self.root / "data" / filename)
        try:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            conn.close()

    def test_init_creates_valid_empty_profile(self) -> None:
        code, stdout, stderr = self._run(
            "--father-name",
            "Luca",
            "--mother-name",
            "Anna",
            "--sibling-name",
            "Sara",
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("Profilo creato:", stdout)
        self.assertIn("Configurazione: 5 file validati", stdout)
        self.assertIn("biography --root", stdout)

        configs = ConfigStore(self.root).load_all()
        identity = configs["identity"]
        self.assertEqual(identity["identity_id"], "character_marco_rossi_2003")
        self.assertEqual(identity["personal_data"]["full_name"], "Marco Rossi")
        self.assertEqual(identity["personal_data"]["birth_date"], "2003-05-12")
        self.assertEqual(
            [parent["first_name"] for parent in identity["family"]["parents"]],
            ["Luca", "Anna"],
        )
        self.assertEqual(identity["family"]["siblings"][0]["first_name"], "Sara")
        self.assertEqual(
            configs["world_history"]["birth_date"], "2003-05-12"
        )
        self.assertEqual(configs["world_history"]["events"], [])

        self.assertEqual(self._count("timeline_episodic.db", "episodic_memories"), 0)
        self.assertEqual(self._count("shadow_diary.db", "shadow_entries"), 0)
        self.assertEqual(self._count("semantic_memory.db", "semantic_items"), 0)
        self.assertEqual(self._count("vector_index.db", "vector_entries"), 0)
        self.assertTrue((self.root / "data" / "indexes").is_dir())
        self.assertEqual(
            [path.name for path in Path(self._tmp.name).iterdir()], ["profile"]
        )

    def test_init_preserves_unrelated_files_in_existing_directory(self) -> None:
        self.root.mkdir()
        notes = self.root / "notes.txt"
        notes.write_text("keep", encoding="utf-8")

        code, _stdout, stderr = self._run()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(notes.read_text(encoding="utf-8"), "keep")
        identity = ConfigStore(self.root).get("identity")
        self.assertEqual(identity["personal_data"]["full_name"], "Marco Rossi")
        self.assertEqual(
            [path.name for path in Path(self._tmp.name).iterdir()], ["profile"]
        )

    def test_lock_blocks_concurrent_init_on_same_root(self) -> None:
        with profile_module._profile_lock(self.root):
            code, _stdout, stderr = self._run()
        self.assertEqual(code, 2)
        self.assertIn("già in corso", stderr)
        self.assertFalse(self.root.exists())

        code, _stdout, stderr = self._run()
        self.assertEqual(code, 0, stderr)

    def test_lock_is_per_profile_root(self) -> None:
        other = Path(self._tmp.name) / "other"
        with profile_module._profile_lock(other):
            code, _stdout, stderr = self._run()
        self.assertEqual(code, 0, stderr)
        self.assertTrue((self.root / "config" / "identity.json").is_file())

    def test_lock_is_released_after_cancelled_creation(self) -> None:
        with patch(
            "agent_memory.profile._create_database",
            side_effect=KeyboardInterrupt,
        ):
            code, _stdout, _stderr = self._run()
        self.assertEqual(code, 130)

        code, _stdout, stderr = self._run()
        self.assertEqual(code, 0, stderr)

    def test_wait_blocks_until_lock_is_released(self) -> None:
        locked = threading.Event()
        release = threading.Event()

        def hold_lock() -> None:
            with profile_module._profile_lock(self.root):
                locked.set()
                release.wait(5)

        holder = threading.Thread(target=hold_lock, daemon=True)
        holder.start()
        self.assertTrue(locked.wait(5))

        result: dict[str, object] = {}
        waiting = threading.Event()
        real_sleep = time.sleep

        def fake_sleep(seconds: float) -> None:
            waiting.set()
            real_sleep(0.01)

        def run_init() -> None:
            result["code"], result["stdout"], result["stderr"] = self._run(
                "--wait"
            )

        runner = threading.Thread(target=run_init, daemon=True)
        with patch("agent_memory.profile.time.sleep", side_effect=fake_sleep):
            runner.start()
            try:
                self.assertTrue(waiting.wait(5))
                self.assertTrue(runner.is_alive())
            finally:
                release.set()
                holder.join(5)
                runner.join(5)

        self.assertFalse(holder.is_alive())
        self.assertFalse(runner.is_alive())
        self.assertEqual(result["code"], 0, result["stderr"])
        self.assertIn("In attesa del lock", result["stderr"])
        self.assertTrue((self.root / "config" / "identity.json").is_file())

    def test_wait_can_be_cancelled_with_ctrl_c(self) -> None:
        with (
            profile_module._profile_lock(self.root),
            patch(
                "agent_memory.profile.time.sleep",
                side_effect=KeyboardInterrupt,
            ),
        ):
            code, _stdout, stderr = self._run("--wait")
        self.assertEqual(code, 130)
        self.assertIn("Creazione annullata", stderr)
        self.assertFalse(self.root.exists())

        code, _stdout, stderr = self._run()
        self.assertEqual(code, 0, stderr)

    def test_wait_timeout_fails_while_lock_is_held(self) -> None:
        with profile_module._profile_lock(self.root):
            code, _stdout, stderr = self._run("--wait-timeout", "0.2")
        self.assertEqual(code, 2)
        self.assertIn("Timeout di 0.2 secondi", stderr)
        self.assertIn("--wait-timeout", stderr)
        self.assertFalse(self.root.exists())

        code, _stdout, stderr = self._run()
        self.assertEqual(code, 0, stderr)

    def test_wait_timeout_implies_wait_and_succeeds(self) -> None:
        locked = threading.Event()
        release = threading.Event()

        def hold_lock() -> None:
            with profile_module._profile_lock(self.root):
                locked.set()
                release.wait(5)

        holder = threading.Thread(target=hold_lock, daemon=True)
        holder.start()
        self.assertTrue(locked.wait(5))
        timer = threading.Timer(0.2, release.set)
        timer.start()
        try:
            code, _stdout, stderr = self._run("--wait-timeout", "5")
        finally:
            timer.cancel()
            release.set()
            holder.join(5)

        self.assertEqual(code, 0, stderr)
        self.assertIn("In attesa del lock", stderr)
        self.assertIn("timeout 5s", stderr)
        self.assertTrue((self.root / "config" / "identity.json").is_file())

    def test_wait_timeout_requires_positive_seconds(self) -> None:
        for value in ("0", "-1", "inf", "nan", "non-un-numero"):
            with self.subTest(value=value):
                with self.assertRaises(SystemExit) as caught:
                    self._run("--wait-timeout", value)
                self.assertEqual(caught.exception.code, 2)

    def test_interactive_prompts_create_profile(self) -> None:
        responses = [
            "Marco",
            "Rossi",
            "2003-05-12",
            "Roma",
            "Roma",
            "Lazio",
            "Italia",
            "IT",
            "italiana",
            "maschio",
            "",
            "Roma",
            "Roma",
            "Lazio",
            "Europe/Rome",
            "Luca",
            "Anna",
            "Sara",
            "sorella",
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("builtins.input", side_effect=responses),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = main(["init", "--interactive", "--root", str(self.root)])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Creazione guidata del profilo", stdout.getvalue())

        identity = ConfigStore(self.root).get("identity")
        self.assertEqual(identity["personal_data"]["full_name"], "Marco Rossi")
        self.assertEqual(identity["personal_data"]["gender"], "maschio")
        self.assertEqual(
            identity["personal_data"]["residence"]["city"], "Roma"
        )
        self.assertEqual(
            [parent["first_name"] for parent in identity["family"]["parents"]],
            ["Luca", "Anna"],
        )
        self.assertEqual(identity["family"]["siblings"][0]["first_name"], "Sara")

    def test_interactive_uses_default_root_and_defaults(self) -> None:
        responses = [
            "Marco",
            "Rossi",
            "2003-05-12",
            "Roma",
            "",
            "",
            "Italia",
            "IT",
            "italiana",
            "",
            "",
            "",
            "",
            "",
            "Europe/Rome",
            "",
            "",
            "",
            "",
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            contextlib.chdir(self._tmp.name),
            patch("builtins.input", side_effect=responses),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = main(["init", "--interactive"])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Profilo creato:", stdout.getvalue())

        root = Path(self._tmp.name) / "profiles" / "marco_rossi"
        identity = ConfigStore(root).get("identity")
        self.assertEqual(identity["personal_data"]["full_name"], "Marco Rossi")
        self.assertEqual(identity["personal_data"]["gender"], "non specificato")
        self.assertEqual(identity["family"]["parents"], [])

    def test_interactive_eof_cancels_without_creating_files(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("builtins.input", side_effect=["Marco", EOFError()]),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = main(["init", "--interactive", "--root", str(self.root)])
        self.assertEqual(code, 130)
        self.assertIn("Creazione annullata", stderr.getvalue())
        self.assertFalse(self.root.exists())

    def test_interactive_keyboard_interrupt_cancels_without_creating_files(
        self,
    ) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch(
                "builtins.input",
                side_effect=["Marco", "Rossi", KeyboardInterrupt()],
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = main(["init", "--interactive", "--root", str(self.root)])
        self.assertEqual(code, 130)
        self.assertIn("Creazione annullata", stderr.getvalue())
        self.assertFalse(self.root.exists())

    def test_interactive_reprompts_invalid_birth_date(self) -> None:
        responses = [
            "Marco",
            "Rossi",
            "non-una-data",
            "2003-05-12",
            "Roma",
            "",
            "",
            "Italia",
            "IT",
            "italiana",
            "",
            "",
            "Roma",
            "",
            "",
            "Europe/Rome",
            "",
            "",
            "",
        ]
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("builtins.input", side_effect=responses),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = main(["init", "--interactive", "--root", str(self.root)])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("Data non valida", stdout.getvalue())
        identity = ConfigStore(self.root).get("identity")
        self.assertEqual(identity["personal_data"]["birth_date"], "2003-05-12")

    def test_init_refuses_existing_profile_without_force(self) -> None:
        code, _stdout, stderr = self._run()
        self.assertEqual(code, 0, stderr)

        code, _stdout, stderr = self._run()
        self.assertEqual(code, 2)
        self.assertIn("esiste già", stderr)
        self.assertIn("--force", stderr)

    def test_force_recreates_empty_profile(self) -> None:
        code, _stdout, stderr = self._run()
        self.assertEqual(code, 0, stderr)

        identity_path = self.root / "config" / "identity.json"
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        identity["personal_data"]["first_name"] = "Vecchio"
        identity_path.write_text(json.dumps(identity), encoding="utf-8")
        marker = self.root / "data" / "indexes" / "marker.faiss"
        marker.write_bytes(b"old")
        models = self.root / "data" / "models" / "keep.bin"
        models.parent.mkdir(parents=True, exist_ok=True)
        models.write_bytes(b"model")
        tls_key = self.root / "data" / "dashboard_tls" / "key.pem"
        tls_key.parent.mkdir(parents=True, exist_ok=True)
        tls_key.write_bytes(b"key")

        code, _stdout, stderr = self._run("--force")
        self.assertEqual(code, 0, stderr)
        refreshed = json.loads(identity_path.read_text(encoding="utf-8"))
        self.assertEqual(refreshed["personal_data"]["first_name"], "Marco")
        self.assertFalse(marker.exists())
        self.assertEqual(models.read_bytes(), b"model")
        self.assertEqual(tls_key.read_bytes(), b"key")
        self.assertEqual(
            [path.name for path in Path(self._tmp.name).iterdir()], ["profile"]
        )
        self.assertEqual(self._count("timeline_episodic.db", "episodic_memories"), 0)

    def test_interrupted_staging_leaves_no_partial_profile(self) -> None:
        with patch(
            "agent_memory.profile._create_database",
            side_effect=KeyboardInterrupt,
        ):
            code, _stdout, stderr = self._run()
        self.assertEqual(code, 130)
        self.assertIn("Creazione annullata", stderr)
        self.assertFalse(self.root.exists())
        self.assertEqual(list(Path(self._tmp.name).iterdir()), [])

    def test_interrupted_commit_leaves_no_profile(self) -> None:
        with patch(
            "agent_memory.profile._rename_directory",
            side_effect=KeyboardInterrupt,
        ):
            code, _stdout, stderr = self._run()
        self.assertEqual(code, 130)
        self.assertIn("Creazione annullata", stderr)
        self.assertFalse(self.root.exists())
        self.assertEqual(list(Path(self._tmp.name).iterdir()), [])

    def test_interrupted_force_restores_previous_profile(self) -> None:
        code, _stdout, stderr = self._run()
        self.assertEqual(code, 0, stderr)

        identity_path = self.root / "config" / "identity.json"
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        identity["personal_data"]["first_name"] = "Vecchio"
        identity_path.write_text(json.dumps(identity), encoding="utf-8")
        marker = self.root / "data" / "indexes" / "marker.faiss"
        marker.write_bytes(b"old")
        models = self.root / "data" / "models" / "keep.bin"
        models.parent.mkdir(parents=True, exist_ok=True)
        models.write_bytes(b"model")

        original_install = profile_module._install_file
        calls = 0

        def fail_during_install(source: Path, destination: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise KeyboardInterrupt
            original_install(source, destination)

        with patch(
            "agent_memory.profile._install_file",
            side_effect=fail_during_install,
        ):
            code, _stdout, stderr = self._run("--force")
        self.assertEqual(code, 130)
        self.assertIn("Creazione annullata", stderr)
        restored = json.loads(identity_path.read_text(encoding="utf-8"))
        self.assertEqual(restored["personal_data"]["first_name"], "Vecchio")
        self.assertTrue(marker.exists())
        self.assertEqual(models.read_bytes(), b"model")
        self.assertEqual(
            [path.name for path in Path(self._tmp.name).iterdir()], ["profile"]
        )

    def test_dry_run_does_not_write(self) -> None:
        code, stdout, stderr = self._run("--dry-run")
        self.assertEqual(code, 0, stderr)
        self.assertIn("Dry-run: profilo pianificato", stdout)
        self.assertFalse(self.root.exists())

    def test_profile_can_be_populated_by_biography(self) -> None:
        code, _stdout, stderr = self._run()
        self.assertEqual(code, 0, stderr)

        result = BiographyService(self.root, as_of=date(2026, 10, 24)).apply()
        self.assertGreater(result["created"], 0)
        self.assertEqual(result["total"], result["created"])

        overview = DashboardData(self.root).overview()
        self.assertEqual(overview["identity"]["full_name"], "Marco Rossi")
        self.assertEqual(overview["counts"]["memories"], result["total"])
        self.assertGreater(len(overview["recent_memories"]), 0)

    def test_refuses_to_initialize_the_project_root(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(
                [
                    "init",
                    "--root",
                    str(PROJECT_ROOT),
                    "--first-name",
                    "Marco",
                    "--last-name",
                    "Rossi",
                    "--birth-date",
                    "2003-05-12",
                    "--birth-city",
                    "Roma",
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn("directory separata", stderr.getvalue())

    def test_future_birth_date_is_rejected(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            self._run("--birth-date", "2999-01-01")
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
