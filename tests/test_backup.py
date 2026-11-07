"""Test di backup, verifica, ripristino e retention del profilo."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_memory.backup import BackupService
from agent_memory.config import ConfigStore
from agent_memory.db import Database
from agent_memory.errors import ConfigError
from tests.support import copy_profile, set_database_version


class BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        copy_profile(self.root)
        self.service = BackupService(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_create_and_verify_include_data_but_exclude_private_areas(self) -> None:
        secrets = self.root / "data" / "secrets"
        secrets.mkdir(parents=True, exist_ok=True)
        (secrets / "token.txt").write_text("non-deve-entrare", encoding="utf-8")
        models = self.root / "data" / "models"
        models.mkdir(parents=True, exist_ok=True)
        (models / "model.bin").write_bytes(b"cache")

        created = self.service.create(reason="test", keep=5)
        self.assertTrue(created["name"].startswith("backup-"))
        manifest = json.loads(
            (Path(created["path"]) / "manifest.json").read_text(encoding="utf-8")
        )
        paths = {item["path"] for item in manifest["entries"]}
        self.assertIn("config/identity.json", paths)
        self.assertIn("data/timeline_episodic.db", paths)
        self.assertNotIn("data/secrets/token.txt", paths)
        self.assertNotIn("data/models/model.bin", paths)
        self.assertIn("data/secrets", manifest["excluded"])
        self.assertIn("data/models", manifest["excluded"])

        report = self.service.verify(created["name"])
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["databases"], 4)

    def test_checksum_tampering_is_detected(self) -> None:
        created = self.service.create(reason="test", keep=5)
        target = Path(created["path"]) / "config" / "persona.md"
        target.write_text("contenuto alterato", encoding="utf-8")
        report = self.service.verify(created["name"])
        self.assertFalse(report["ok"])
        self.assertTrue(any("Checksum" in error for error in report["errors"]))

    def test_restore_requires_confirmation_and_creates_a_safety_backup(self) -> None:
        persona = self.root / "config" / "persona.md"
        original = persona.read_text(encoding="utf-8")
        created = self.service.create(reason="test", keep=5)
        persona.write_text("modificato dopo il backup", encoding="utf-8")

        with self.assertRaises(ConfigError):
            self.service.restore(created["name"], confirmation="NO")

        result = self.service.restore(created["name"], confirmation="RIPRISTINA")
        self.assertEqual(persona.read_text(encoding="utf-8"), original)
        self.assertTrue(result["safety_backup"])
        reasons = {item.get("reason") for item in self.service.list()}
        self.assertIn("pre-restore", reasons)

    def test_prune_keeps_only_the_requested_number(self) -> None:
        self.service.create(reason="uno", include_files=False, keep=10)
        self.service.create(reason="due", include_files=False, keep=10)
        result = self.service.prune(1)
        self.assertEqual(len(result["kept"]), 1)
        self.assertEqual(len(result["removed"]), 1)
        self.assertEqual(len(self.service.list()), 1)

    def test_opening_an_old_database_creates_a_pre_migration_backup(self) -> None:
        set_database_version(self.root, "1.0")
        with Database(self.root).timeline():
            pass
        reasons = [item.get("reason") for item in self.service.list()]
        self.assertTrue(
            any(str(reason).startswith("pre-migration:") for reason in reasons),
            reasons,
        )

    def test_invalid_backup_name_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            self.service.verify("../profilo")
        with self.assertRaises(ConfigError):
            self.service.verify("backup-inesistente")


class BackupConfigurationTests(unittest.TestCase):
    def test_config_store_is_not_modified_by_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copy_profile(root)
            before = ConfigStore(root).get("identity")
            BackupService(root).create(reason="test", include_files=False)
            self.assertEqual(ConfigStore(root).get("identity"), before)


if __name__ == "__main__":
    unittest.main()
