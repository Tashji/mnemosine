"""Test del versioning e delle migrazioni automatiche degli schemi.

I test lavorano su copie temporanee del profilo demo: nessun file del progetto
viene modificato. Le migrazioni future vengono simulate aggiungendo passi alle
mappe di ``agent_memory.migrations`` e togliendoli alla fine del test.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_memory import migrations as migr
from agent_memory import profile
from agent_memory.config import ConfigStore
from agent_memory.db import TIMELINE_TABLES, Database
from agent_memory.errors import ConfigError, DatabaseUnavailable
from agent_memory.migrations import migrate_json_document, stored_database_version
from agent_memory.service import MemoryService

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MigrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copytree(PROJECT_ROOT / "config", self.root / "config")
        shutil.copytree(
            PROJECT_ROOT / "data",
            self.root / "data",
            ignore=shutil.ignore_patterns("models", "indexes", "dashboard_tls"),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def db_path(self, database: str = "timeline_episodic") -> Path:
        return self.root / "data" / f"{database}.db"

    def json_path(self, name: str = "identity") -> Path:
        return self.root / "config" / f"{name}.json"

    def read_json(self, name: str = "identity") -> dict:
        return json.loads(self.json_path(name).read_text(encoding="utf-8"))

    def write_json(self, data: dict, name: str = "identity") -> None:
        self.json_path(name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def drop_schema_meta(self, database: str = "timeline_episodic") -> None:
        conn = sqlite3.connect(self.db_path(database))
        conn.execute("DROP TABLE IF EXISTS schema_meta")
        conn.commit()
        conn.close()


class SqliteMigrationTests(MigrationTestCase):
    def test_current_database_is_already_migrated(self) -> None:
        health = Database(self.root).health()["timeline_episodic"]
        self.assertEqual(health["schema_supported"], 3)
        with Database(self.root).timeline() as conn:
            self.assertEqual(stored_database_version(conn), 3)
        self.assertFalse(Database(self.root).health()["timeline_episodic"]["needs_migration"])

    def test_v2_autonomy_rows_survive_review_migration(self) -> None:
        with Database(self.root).timeline() as conn:
            conn.execute("INSERT INTO autonomous_runs(id, impulse, state, url, created_at) VALUES (?, ?, ?, ?, ?)",
                         ("legacy-run", '{"trigger":"test"}', "uncertain", "https://example.org", "2026-01-01"))
            conn.execute("INSERT INTO autonomous_actions(run_id, id, payload) VALUES (?, ?, ?)",
                         ("legacy-run", "legacy-action", '{"action":"click"}'))
            conn.execute("DROP TABLE autonomous_reviews")
            conn.execute("UPDATE schema_meta SET value='2.0' WHERE key='schema_version'")
            conn.commit()
        with Database(self.root).timeline() as conn:
            self.assertEqual(stored_database_version(conn), 3)
            self.assertEqual(conn.execute("SELECT state FROM autonomous_runs WHERE id='legacy-run'").fetchone()[0], "uncertain")
            self.assertIsNone(conn.execute("SELECT result FROM autonomous_actions WHERE id='legacy-action'").fetchone()[0])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM autonomous_reviews").fetchone()[0], 0)

    def test_database_without_version_is_stamped_without_touching_rows(self) -> None:
        conn = sqlite3.connect(self.db_path())
        before = conn.execute("SELECT COUNT(*) FROM episodic_memories").fetchone()[0]
        conn.close()
        self.drop_schema_meta()

        with Database(self.root).timeline() as opened:
            self.assertEqual(stored_database_version(opened), 3)
            after = opened.execute("SELECT COUNT(*) FROM episodic_memories").fetchone()[0]

        self.assertEqual(before, after)

    def test_empty_database_is_rebuilt_from_package_schema(self) -> None:
        self.db_path().unlink()
        sqlite3.connect(self.db_path()).close()

        with Database(self.root).timeline() as opened:
            tables = {
                row[0]
                for row in opened.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                )
            }
            self.assertEqual(stored_database_version(opened), 3)

        self.assertLessEqual(TIMELINE_TABLES, tables)

    def test_missing_table_at_current_version_is_reported(self) -> None:
        conn = sqlite3.connect(self.db_path())
        conn.execute("DROP TABLE memory_links")
        conn.commit()
        conn.close()

        with (
            self.assertRaises(DatabaseUnavailable) as ctx,
            Database(self.root).timeline(),
        ):
            pass
        self.assertIn("tabelle mancanti", str(ctx.exception))

    def test_database_from_a_newer_version_is_refused(self) -> None:
        conn = sqlite3.connect(self.db_path())
        conn.execute(
            "UPDATE schema_meta SET value = '9.0' WHERE key = 'schema_version'"
        )
        conn.commit()
        conn.close()

        with (
            self.assertRaises(DatabaseUnavailable) as ctx,
            Database(self.root).timeline(),
        ):
            pass
        self.assertIn("più recente", str(ctx.exception))

    def test_health_reports_pending_migration_without_writing(self) -> None:
        self.drop_schema_meta()

        health = Database(self.root).health()["timeline_episodic"]
        self.assertIsNone(health["schema_version"])
        self.assertTrue(health["needs_migration"])

        conn = sqlite3.connect(self.db_path())
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("SELECT value FROM schema_meta").fetchall()
        conn.close()

    def test_migration_steps_run_in_order(self) -> None:
        calls: list[int] = []

        def step_two(conn: sqlite3.Connection) -> None:
            calls.append(2)
            conn.execute("ALTER TABLE episodic_memories ADD COLUMN probe_two TEXT")

        def step_three(conn: sqlite3.Connection) -> None:
            calls.append(3)
            conn.execute("ALTER TABLE episodic_memories ADD COLUMN probe_three TEXT")

        with (
            patch.dict(
                migr.DATABASE_SCHEMAS,
                {"timeline_episodic": ("timeline_episodic.sql", 3)},
            ),
            patch.dict(
                migr.SQLITE_MIGRATIONS["timeline_episodic"],
                {2: step_two, 3: step_three},
                clear=True,
            ),
            Database(self.root).timeline() as conn,
        ):
            self.assertEqual(calls, [2, 3])
            self.assertEqual(stored_database_version(conn), 3)
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(episodic_memories)")
            }
            self.assertIn("probe_two", columns)
            self.assertIn("probe_three", columns)

    def test_failed_step_keeps_the_previous_version(self) -> None:
        def broken(conn: sqlite3.Connection) -> None:
            raise sqlite3.OperationalError("colonna già presente")

        with (
            patch.dict(
                migr.DATABASE_SCHEMAS,
                {"timeline_episodic": ("timeline_episodic.sql", 2)},
            ),
            patch.dict(
                migr.SQLITE_MIGRATIONS["timeline_episodic"],
                {2: broken},
                clear=True,
            ),
            self.assertRaises(DatabaseUnavailable) as ctx,
            Database(self.root).timeline(),
        ):
            pass

        self.assertIn("rimasto a 1.0", str(ctx.exception))
        conn = sqlite3.connect(self.db_path())
        stored = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(stored, "1.0")

    def test_process_that_arrives_second_does_not_repeat_the_work(self) -> None:
        applied: list[int] = []

        with (
            patch.dict(
                migr.DATABASE_SCHEMAS,
                {"timeline_episodic": ("timeline_episodic.sql", 2)},
            ),
            patch.dict(
                migr.SQLITE_MIGRATIONS["timeline_episodic"],
                {2: lambda conn: applied.append(2)},
                clear=True,
            ),
            # Il primo lettura vede il profilo vecchio, la seconda (sotto lock)
            # vede che un altro processo ha già migrato.
            patch.object(migr, "stored_database_version", side_effect=[1, 2]),
            Database(self.root).timeline() as conn,
        ):
            self.assertEqual(applied, [])
            self.assertFalse(conn.in_transaction)

    def test_missing_step_is_reported_as_incomplete_package(self) -> None:
        with (
            patch.dict(
                migr.DATABASE_SCHEMAS,
                {"timeline_episodic": ("timeline_episodic.sql", 4)},
            ),
            self.assertRaises(DatabaseUnavailable) as ctx,
            Database(self.root).timeline(),
        ):
            pass
        self.assertIn("manca il passo di migrazione", str(ctx.exception))

    def test_created_database_is_stamped_at_current_version(self) -> None:
        path = self.root / "data" / "probe.db"
        profile._create_database(path, "timeline_episodic")

        conn = sqlite3.connect(path)
        stored = stored_database_version(conn)
        conn.close()
        self.assertEqual(stored, migr.DATABASE_SCHEMAS["timeline_episodic"][1])


class LegacyProfileTests(MigrationTestCase):
    def test_profile_without_any_marker_keeps_working(self) -> None:
        for database in migr.DATABASE_SCHEMAS:
            self.drop_schema_meta(database)

        service = MemoryService(self.root)
        memories = service.list_memories(limit=3)
        self.assertEqual(len(memories), 3)

        # Ogni database viene migrato al primo uso, non tutti insieme.
        conn = sqlite3.connect(self.db_path("timeline_episodic"))
        self.assertEqual(stored_database_version(conn), 3)
        conn.close()

        database = Database(self.root)
        with database.shadow(), database.semantic(), database.vector():
            pass
        for name in ("shadow_diary", "semantic_memory", "vector_index"):
            conn = sqlite3.connect(self.db_path(name))
            self.assertEqual(stored_database_version(conn), 1)
            conn.close()


class JsonMigrationTests(MigrationTestCase):
    def test_current_documents_are_not_rewritten(self) -> None:
        with patch("agent_memory.config.write_json_atomic") as writer:
            configs = ConfigStore(self.root).load_all()

        writer.assert_not_called()
        self.assertEqual(configs["identity"]["schema_version"], "1.0")

    def test_missing_version_is_stamped_and_persisted(self) -> None:
        data = self.read_json()
        del data["schema_version"]
        self.write_json(data)

        configs = ConfigStore(self.root).load_all()

        self.assertEqual(configs["identity"]["schema_version"], "1.0")
        self.assertEqual(self.read_json()["schema_version"], "1.0")

    def test_invalid_legacy_document_is_not_rewritten(self) -> None:
        data = self.read_json()
        del data["schema_version"]
        del data["personal_data"]
        self.write_json(data)
        before = self.json_path().read_bytes()

        with self.assertRaises(ConfigError):
            ConfigStore(self.root).load_all()

        self.assertEqual(self.json_path().read_bytes(), before)

    def test_document_from_a_newer_version_is_refused(self) -> None:
        data = self.read_json()
        data["schema_version"] = "9.0"
        self.write_json(data)
        before = self.json_path().read_bytes()

        with self.assertRaises(ConfigError) as ctx:
            ConfigStore(self.root).load_all()

        self.assertIn("più recente", str(ctx.exception))
        self.assertEqual(self.json_path().read_bytes(), before)

    def test_social_history_migration_preserves_existing_entries(self) -> None:
        document = self.read_json("social_graph")
        document["schema_version"] = "1.0"
        migrated, changed = migrate_json_document("social_graph", document)
        self.assertTrue(changed)
        self.assertEqual(migrated["schema_version"], "2.0")
        self.assertEqual(migrated["people"], document["people"])

    def test_current_state_v1_gains_the_heartbeat_settings(self) -> None:
        data = self.read_json("current_state")
        data["schema_version"] = "1.0"
        data["heartbeat"] = {
            "last_run_at": "2026-10-24T15:00:00+02:00",
            "next_run_at": "2026-10-24T15:30:00+02:00",
            "last_decision": "continuare_l_attivita",
            "spontaneous_message_pending": False,
        }
        self.write_json(data, "current_state")

        configs = ConfigStore(self.root).load_all()

        self.assertEqual(configs["current_state"]["schema_version"], "2.0")
        heartbeat_state = configs["current_state"]["heartbeat"]
        self.assertEqual(heartbeat_state["last_decision"], "continuare_l_attivita")
        self.assertEqual(heartbeat_state["last_run_at"], "2026-10-24T15:00:00+02:00")
        self.assertEqual(heartbeat_state["interval_minutes"], 30)
        self.assertEqual(heartbeat_state["max_impulses_per_day"], 3)
        self.assertIsNone(heartbeat_state["pending_impulse"])

        on_disk = self.read_json("current_state")
        self.assertEqual(on_disk["schema_version"], "2.0")
        self.assertEqual(on_disk["heartbeat"]["cooldown_minutes"], 180)

    def test_migration_ladder_updates_the_document(self) -> None:
        document = ConfigStore(self.root).get("identity")

        def step_two(data: dict) -> dict:
            updated = dict(data)
            updated["migration_probe"] = True
            return updated

        with (
            patch.dict(migr.JSON_DOCUMENTS, {"identity": 2}),
            patch.dict(
                migr.JSON_MIGRATIONS["identity"], {2: step_two}, clear=True
            ),
        ):
            migrated, changed = migrate_json_document("identity", document)

        self.assertTrue(changed)
        self.assertTrue(migrated["migration_probe"])
        self.assertEqual(migrated["schema_version"], "2.0")
        self.assertNotIn("migration_probe", document)

    def test_missing_json_step_is_reported(self) -> None:
        document = ConfigStore(self.root).get("identity")
        with (
            patch.dict(migr.JSON_DOCUMENTS, {"identity": 2}),
            self.assertRaises(ConfigError) as ctx,
        ):
            migrate_json_document("identity", document)
        self.assertIn("manca il passo di migrazione", str(ctx.exception))

    def test_invalid_version_is_reported(self) -> None:
        document = ConfigStore(self.root).get("identity")
        document["schema_version"] = "non una versione"
        with self.assertRaises(ConfigError) as ctx:
            migrate_json_document("identity", document)
        self.assertIn("non valida", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
