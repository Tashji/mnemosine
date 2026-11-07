"""Test della vista Vita e del comando amministrativo del motore."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_memory.config import ConfigStore
from agent_memory.dashboard import DashboardData
from agent_memory.dashboard_settings import DashboardSettings
from agent_memory.errors import ValidationError
from tests.support import copy_profile


class DashboardLifeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        copy_profile(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_life_view_is_read_only_and_bounded(self) -> None:
        before = ConfigStore(self.root).get("current_state")
        data = DashboardData(self.root).life()

        self.assertFalse(data["enabled"])
        self.assertFalse(data["status"]["storage"] is False)
        self.assertEqual(
            data["report"]["activity"]["label"], "Programmazione al PC"
        )
        self.assertIn("hunger", data["report"]["body"])
        self.assertIn("stream", data["report"])
        serialized = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("shadow_traits", serialized)
        self.assertNotIn("repressed", serialized)
        self.assertEqual(before, ConfigStore(self.root).get("current_state"))

    def test_life_engine_toggle_requires_explicit_confirmation(self) -> None:
        settings = DashboardSettings(self.root)

        with self.assertRaises(ValidationError):
            settings.set_life_engine(True, "")
        with self.assertRaises(ValidationError):
            settings.set_life_engine(True, "ABILITA")
        self.assertFalse(
            ConfigStore(self.root)
            .get("current_state")["flags"].get("life_engine_enabled", False)
        )

        self.assertTrue(settings.set_life_engine(True, "ABILITO")["enabled"])
        self.assertTrue(
            ConfigStore(self.root)
            .get("current_state")["flags"]["life_engine_enabled"]
        )

        with self.assertRaises(ValidationError):
            settings.set_life_engine(False, "ABILITO")
        self.assertTrue(
            ConfigStore(self.root)
            .get("current_state")["flags"]["life_engine_enabled"]
        )

        self.assertFalse(settings.set_life_engine(False, "DISATTIVA")["enabled"])
        self.assertFalse(
            ConfigStore(self.root)
            .get("current_state")["flags"].get("life_engine_enabled", False)
        )


if __name__ == "__main__":
    unittest.main()
