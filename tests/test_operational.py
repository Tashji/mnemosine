"""Verifiche su copie isolate del profilo, nessuna azione su siti esterni."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from agent_memory.config import ConfigStore
from agent_memory.errors import ValidationError
from agent_memory.heartbeat import HeartbeatEngine

ROOT = Path(__file__).resolve().parents[1]


class OperationalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / "config", self.root / "config")
        shutil.copytree(ROOT / "data", self.root / "data", ignore=shutil.ignore_patterns("models", "indexes", "browser", "dashboard_tls"))
        self.config = ConfigStore(self.root)
        self.config.load_all()

    def engine(self):
        from datetime import datetime
        return HeartbeatEngine(self.root, at=datetime(2026, 10, 25, 15))

    def test_import_once_and_no_history_expansion(self):
        graph = self.config.get("social_graph")
        self.assertNotIn("interaction_history", graph["people"][0])
        self.assertEqual(self.config.operational.history("user_marco")["total"], 2)
        ConfigStore(self.root).load_all()
        self.assertEqual(self.config.operational.history("user_marco")["total"], 2)

    def test_idempotent_and_conflicting_retry(self):
        first = self.engine().record_interaction(user_id="user_marco", interaction_id="req-1", summary="ciao")
        again = self.engine().record_interaction(user_id="user_marco", interaction_id="req-1", summary="ciao")
        self.assertEqual(first, again)
        self.assertEqual(self.config.operational.history("user_marco")["total"], 3)
        with self.assertRaises(ValidationError):
            self.engine().record_interaction(user_id="user_marco", interaction_id="req-1", summary="diverso")

    def test_failure_rolls_back_both_documents_and_history(self):
        engine = self.engine()
        before = engine.config.load_all()
        with patch.object(engine.config, "update_current_state", side_effect=OSError("disk failure")), self.assertRaises(OSError):
            engine.record_interaction(user_id="user_marco", interaction_id="failed")
        self.assertEqual(engine.config.load_all(), before)
        self.assertEqual(engine.config.operational.history("user_marco")["total"], 2)
        self.engine().record_interaction(user_id="user_marco", interaction_id="failed")
        self.assertEqual(engine.config.operational.history("user_marco")["total"], 3)

    def test_concurrent_calls_do_not_lose_load_or_counts(self):
        before = self.config.get("current_state")["mental_load"]
        def call(i):
            return self.engine().record_interaction(user_id="user_marco", interaction_id=f"parallel-{i}")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(call, range(4)))
        self.assertEqual(self.config.get("current_state")["mental_load"], min(100, before + 16))
        self.assertEqual(self.config.operational.history("user_marco")["total"], 6)

    def test_failed_export_does_not_turn_committed_request_into_failure(self):
        with patch("agent_memory.migrations.write_json_atomic", side_effect=OSError("read only export")), self.assertWarns(RuntimeWarning):
            first = self.engine().record_interaction(user_id="user_marco", interaction_id="export-failed")
        self.assertEqual(self.engine().record_interaction(user_id="user_marco", interaction_id="export-failed"), first)
        self.assertEqual(self.config.operational.history("user_marco")["total"], 3)

    def test_uncommitted_interaction_is_rolled_back_after_process_exit(self):
        import subprocess
        import sys
        code = '''
import os, sys
from agent_memory.config import ConfigStore
c=ConfigStore(sys.argv[1])
with c.transaction() as conn:
    c.update_current_state({"mental_load": 99})
    conn.execute("INSERT INTO interaction_events(event_id,user_id,timestamp,entry) VALUES ('crash','user_marco','','{}')")
    os._exit(23)
'''
        before = self.config.get("current_state")
        result = subprocess.run([sys.executable, "-c", code, str(self.root)], check=False)
        self.assertEqual(result.returncode, 23)
        self.assertEqual(ConfigStore(self.root).get("current_state"), before)
        self.assertEqual(self.config.operational.history("user_marco")["total"], 2)

    def test_sql_pagination_and_export_is_not_authoritative(self):
        for i in range(105):
            self.engine().record_interaction(user_id="user_marco", interaction_id=f"page-{i}")
        pages = [self.config.operational.history("user_marco", offset=i) for i in (0, 50, 100)]
        self.assertEqual([len(p["entries"]) for p in pages], [50, 50, 7])
        (self.root / "config" / "current_state.json").write_text('{}')
        self.assertIn("mental_load", ConfigStore(self.root).get("current_state"))
