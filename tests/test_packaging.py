"""Test del packaging: metadati, entry point e dati inclusi nel pacchetto."""

from __future__ import annotations

import importlib
import os
import re
import tempfile
import tomllib
import unittest
from pathlib import Path

from agent_memory import profile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
PACKAGE_DIR = Path(profile.__file__).resolve().parent


def _distribution_name(spec: str) -> str:
    """Estrae il nome normalizzato da una voce di requirements o pyproject."""

    name = re.split(r"[<>=!~;\[ ]", spec.strip(), maxsplit=1)[0]
    return name.lower().replace("_", "-")


class PackagingMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    def test_project_section_is_complete(self) -> None:
        project = self.data["project"]
        self.assertEqual(project["name"], "mnemosine")
        self.assertTrue(project["version"])
        self.assertEqual(project["readme"], "README.MD")
        self.assertTrue(project["description"])
        self.assertTrue(project["requires-python"].startswith(">="))

    def test_build_backend_is_setuptools(self) -> None:
        build = self.data["build-system"]
        self.assertEqual(build["build-backend"], "setuptools.build_meta")
        self.assertTrue(any("setuptools" in item for item in build["requires"]))

    def test_runtime_dependencies_match_requirements(self) -> None:
        declared = {
            _distribution_name(spec) for spec in self.data["project"]["dependencies"]
        }
        required = {
            _distribution_name(line)
            for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        self.assertEqual(declared, required)

    def test_browser_extra_matches_browser_requirements(self) -> None:
        extra = self.data["project"]["optional-dependencies"]["browser"]
        browser_requirements = {
            _distribution_name(line)
            for line in (PROJECT_ROOT / "requirements-browser.txt")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        self.assertEqual({_distribution_name(item) for item in extra}, browser_requirements)

    def test_console_scripts_resolve_to_callables(self) -> None:
        scripts = self.data["project"]["scripts"]
        self.assertIn("mnemosine", scripts)
        for name, target in scripts.items():
            with self.subTest(script=name):
                module_name, _, attribute = target.partition(":")
                self.assertTrue(module_name and attribute, target)
                module = importlib.import_module(module_name)
                self.assertTrue(callable(getattr(module, attribute)))

    def test_package_data_covers_runtime_assets(self) -> None:
        packages = self.data["tool"]["setuptools"]["packages"]
        self.assertEqual(packages, ["agent_memory"])
        patterns = self.data["tool"]["setuptools"]["package-data"]["agent_memory"]
        self.assertIn("dashboard_assets/*", patterns)
        self.assertIn("schemas/*", patterns)


class ProfileRootGuidanceTests(unittest.TestCase):
    def test_missing_root_explains_how_to_fix_it(self) -> None:
        """Senza profilo il messaggio deve dire cosa passare a --root."""

        from agent_memory.config import ConfigStore
        from agent_memory.errors import ConfigError

        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaises(ConfigError) as caught,
        ):
            ConfigStore(Path(tmp) / "vuoto").get("identity")
        message = str(caught.exception)
        self.assertIn("--root", message)
        self.assertIn("config/", message)


class PackagedDataTests(unittest.TestCase):
    def test_schemas_are_inside_the_package(self) -> None:
        self.assertEqual(profile.SCHEMA_DIR, PACKAGE_DIR / "schemas")
        for name in (
            "timeline_episodic.sql",
            "shadow_diary.sql",
            "semantic_memory.sql",
            "vector_index.sql",
        ):
            with self.subTest(schema=name):
                self.assertTrue(profile._schema_path(name).is_file())

    def test_legacy_schemas_directory_is_gone(self) -> None:
        self.assertFalse((PROJECT_ROOT / "schemas").exists())

    def test_dashboard_assets_are_inside_the_package(self) -> None:
        assets = PACKAGE_DIR / "dashboard_assets"
        for name in ("index.html", "login.html", "setup.html", "app.js", "styles.css"):
            with self.subTest(asset=name):
                self.assertTrue((assets / name).is_file())

    def test_schema_resolution_is_independent_of_cwd(self) -> None:
        """La creazione del profilo non deve dipendere dalla working directory."""

        self.assertTrue(profile.SCHEMA_DIR.is_relative_to(PACKAGE_DIR))
        previous = os.getcwd()
        try:
            os.chdir(tempfile.gettempdir())
            self.assertTrue(profile._schema_path("timeline_episodic.sql").is_file())
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
