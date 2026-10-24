"""Test della dashboard amministrativa locale."""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from agent_memory.dashboard import DashboardData, create_server
from agent_memory.evolution import EvolutionService

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 10, 24)


class DashboardFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copytree(PROJECT_ROOT / "config", self.root / "config")
        shutil.copytree(
            PROJECT_ROOT / "data",
            self.root / "data",
            ignore=shutil.ignore_patterns("models", "indexes"),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _apply_seed(self) -> None:
        seed_path = self.root / "seed_dashboard.json"
        seed_path.write_text(
            json.dumps(
                {
                    "seed_id": "dashboard_test_seed",
                    "event_time": "2026-10-21T12:00:00+02:00",
                    "title": "Un rifiuto importante",
                    "description": (
                        "Una persona importante ha criticato il progetto a cui "
                        "l'agente lavorava da settimane."
                    ),
                    "tags": ["rifiuto", "critica"],
                    "emotional_state": {
                        "label": "umiliazione",
                        "valence": -0.8,
                        "arousal": 0.7,
                        "intensity": 8,
                    },
                    "cascade": {"max_memories": 50},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        service = EvolutionService(self.root, as_of=AS_OF)
        service.apply(service.load_seed(seed_path), reindex=False)


class DashboardDataTests(DashboardFixture):
    def test_overview_and_timeline_visibility(self) -> None:
        data = DashboardData(self.root)
        overview = data.overview()

        self.assertEqual(overview["identity"]["full_name"], "Alessandro Vitale")
        self.assertEqual(overview["counts"]["memories"], 40)
        self.assertEqual(overview["counts"]["conscious"], 39)
        self.assertEqual(overview["counts"]["repressed"], 1)
        self.assertEqual(overview["counts"]["identity_traits"], 4)
        self.assertEqual(overview["counts"]["shadow_traits"], 5)
        self.assertEqual(len(overview["recent_memories"]), 5)

        visible = data.timeline(limit=100)
        self.assertEqual(visible["count"], 39)
        self.assertEqual(visible["total"], 40)
        self.assertNotIn("mem_0002", [memory["id"] for memory in visible["memories"]])

        hidden = data.timeline(include_hidden=True, limit=100)
        self.assertEqual(hidden["count"], 40)
        repressed = [
            memory for memory in hidden["memories"] if memory["access_level"] == "repressed"
        ]
        self.assertEqual([memory["id"] for memory in repressed], ["mem_0002"])

        filtered = data.timeline(access="repressed", limit=100)
        self.assertEqual(filtered["count"], 1)
        self.assertEqual(filtered["memories"][0]["id"], "mem_0002")

    def test_traits_semantic_and_memory_detail(self) -> None:
        data = DashboardData(self.root)
        traits = data.traits()
        self.assertEqual(len(traits["traits"]), 4)
        self.assertEqual(len(traits["shadow"]["traits"]), 5)
        self.assertFalse(traits["shadow"]["access_policy"]["conscious_direct_read"])

        semantic = data.semantic_items(status="active")
        self.assertEqual(semantic["count"], 9)
        hypotheses = data.semantic_items(knowledge_type="hypothesis")
        self.assertTrue(hypotheses["items"])
        self.assertTrue(all(item["knowledge_type"] == "hypothesis" for item in hypotheses["items"]))

        detail = data.memory_detail("mem_0005")
        self.assertEqual(detail["memory"]["id"], "mem_0005")
        self.assertGreaterEqual(len(detail["revisions"]), 1)
        self.assertTrue(detail["links"] or detail["semantic_links"])

    def test_evolution_history_after_seed(self) -> None:
        self._apply_seed()
        data = DashboardData(self.root).evolution()

        self.assertGreaterEqual(data["history"]["count"], 1)
        self.assertGreaterEqual(len(data["cascades"]), 1)
        self.assertEqual(data["seeds"][0]["source"], "admin_seed")
        self.assertTrue(data["recent_revisions"])
        cascade = data["cascades"][0]
        self.assertGreaterEqual(cascade["memories"], 1)
        self.assertGreaterEqual(cascade["memory_revisions"], 1)


class DashboardHTTPTests(DashboardFixture):
    def setUp(self) -> None:
        super().setUp()
        self.server = create_server(
            self.root,
            host="127.0.0.1",
            port=0,
            quiet=True,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        super().tearDown()

    def _get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        try:
            with urllib.request.urlopen(self.base_url + path, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_static_assets_and_security_headers(self) -> None:
        status, headers, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"Mnemosine", body)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])

        status, headers, body = self._get("/static/app.js")
        self.assertEqual(status, 200)
        self.assertIn("text/javascript", headers["Content-Type"])
        self.assertIn(b"loadTimeline", body)

        status, _headers, body = self._get("/static/../config/identity.json")
        self.assertEqual(status, 404)
        self.assertIn(b"non trovato", body.lower())

    def test_read_only_api(self) -> None:
        status, _headers, body = self._get("/api/overview")
        self.assertEqual(status, 200)
        overview = json.loads(body)
        self.assertEqual(overview["counts"]["memories"], 40)

        status, _headers, body = self._get("/api/timeline")
        self.assertEqual(status, 200)
        timeline = json.loads(body)
        self.assertEqual(timeline["count"], 39)
        self.assertNotIn("mem_0002", [item["id"] for item in timeline["memories"]])

        status, _headers, body = self._get("/api/timeline?include_hidden=1")
        self.assertEqual(status, 200)
        hidden = json.loads(body)
        self.assertEqual(hidden["count"], 40)

        status, _headers, body = self._get("/api/memory/mem_0005")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["memory"]["id"], "mem_0005")

        status, _headers, body = self._get("/api/traits")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["shadow"]["traits"]), 5)

        status, _headers, body = self._get("/api/semantic?status=all")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["count"], 9)

        status, _headers, body = self._get("/api/evolution")
        self.assertEqual(status, 200)
        evolution = json.loads(body)
        self.assertIn("seeds", evolution)
        self.assertIn("cascades", evolution)

    def test_bad_requests_are_reported(self) -> None:
        status, _headers, body = self._get("/api/memory/missing")
        self.assertEqual(status, 404)
        self.assertIn("non trovato", json.loads(body)["error"].lower())

        status, _headers, body = self._get("/api/timeline?source=inventata")
        self.assertEqual(status, 400)
        self.assertIn("source", json.loads(body)["error"])

        status, _headers, body = self._get("/api/semantic?type=inventata")
        self.assertEqual(status, 400)
        self.assertIn("knowledge_type", json.loads(body)["error"])


if __name__ == "__main__":
    unittest.main()
