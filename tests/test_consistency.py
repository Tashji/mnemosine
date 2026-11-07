"""Test dei controlli di coerenza temporale e semantica."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_memory.consistency import ConsistencyChecker
from tests.support import copy_profile


class ConsistencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        copy_profile(self.root)
        self.checker = ConsistencyChecker(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _check(self, report: dict, name: str) -> dict:
        return next(item for item in report["checks"] if item["name"] == name)

    def _timeline(self) -> sqlite3.Connection:
        return sqlite3.connect(self.root / "data" / "timeline_episodic.db")

    def test_clean_profile_has_no_errors(self) -> None:
        report = self.checker.run()
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["error_count"], 0)

    def test_invalid_timestamp_is_reported_without_the_content(self) -> None:
        conn = self._timeline()
        try:
            conn.execute(
                "UPDATE episodic_memories SET event_time = "
                "'2026-01-01T99:99:99+00:00', event_date = '2026-01-01' "
                "WHERE id = (SELECT id FROM episodic_memories LIMIT 1)"
            )
            conn.execute(
                "UPDATE episodic_memories SET title = 'SEGRETO-DA-NON-STAMPARE' "
                "WHERE id = (SELECT id FROM episodic_memories LIMIT 1)"
            )
            conn.commit()
        finally:
            conn.close()
        report = self.checker.run()
        check = self._check(report, "timeline:temporal")
        self.assertFalse(check["ok"])
        self.assertTrue(any("event_time" in error for error in check["errors"]))
        self.assertNotIn("SEGRETO-DA-NON-STAMPARE", json.dumps(report))

    def test_orphan_semantic_link_is_reported(self) -> None:
        path = self.root / "data" / "semantic_memory.db"
        conn = sqlite3.connect(path)
        try:
            semantic_id = conn.execute(
                "SELECT id FROM semantic_items LIMIT 1"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO semantic_episodic_links "
                "(semantic_id, memory_id, relation_type, strength, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    semantic_id,
                    "mem_inesistente",
                    "related_to",
                    0.5,
                    "2026-01-01T00:00:00+00:00",
                ),
            )
            conn.commit()
        finally:
            conn.close()
        report = self.checker.run()
        check = self._check(report, "semantica:temporal")
        self.assertTrue(
            any("memory_id orfano" in error for error in check["errors"]),
            check,
        )

    def test_missing_index_file_is_an_error_when_the_directory_exists(self) -> None:
        (self.root / "data" / "indexes").mkdir(parents=True, exist_ok=True)
        report = self.checker.run()
        check = self._check(report, "vettoriale")
        self.assertTrue(
            any("episodic.faiss" in error for error in check["errors"]),
            check,
        )

    def test_malformed_outbox_is_reported_without_contents(self) -> None:
        outbox = self.root / "data" / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        (outbox / "messages.jsonl").write_text(
            '{"message_id": "ok", "text": "SEGRETO"}\nnot-json\n',
            encoding="utf-8",
        )
        report = self.checker.run()
        check = self._check(report, "outbox")
        self.assertFalse(check["ok"])
        self.assertTrue(any("JSON non valido" in error for error in check["errors"]))
        self.assertNotIn("SEGRETO", json.dumps(report))

    def test_missing_identity_id_is_reported(self) -> None:
        path = self.root / "config" / "identity.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document.pop("identity_id", None)
        path.write_text(json.dumps(document), encoding="utf-8")
        report = self.checker.run()
        check = self._check(report, "configurazione")
        self.assertTrue(
            any("identity_id" in error for error in check["errors"]),
            check,
        )


if __name__ == "__main__":
    unittest.main()
