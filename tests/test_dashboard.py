"""Test della dashboard amministrativa locale."""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

from agent_memory.dashboard import DashboardData, create_server
from agent_memory.evolution import EvolutionService
from agent_memory.files import FileSandbox
from agent_memory.heartbeat import HeartbeatEngine

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

    def test_overview_contacts_expose_last_interaction_and_count(self) -> None:
        overview = DashboardData(self.root).overview()

        contacts = overview["contacts"]
        self.assertEqual(
            [contact["user_id"] for contact in contacts],
            ["user_davide", "user_marco", "user_giulia"],
        )
        davide = contacts[0]
        self.assertEqual(davide["display_name"], "Davide")
        self.assertEqual(davide["interaction_count"], 23)
        self.assertEqual(
            davide["last_interaction_at"], "2026-10-20T22:10:00+02:00"
        )
        self.assertEqual(
            davide["last_interaction_summary"],
            "Serata gaming cooperativo con voce.",
        )
        self.assertEqual(davide["trust"], 63)
        self.assertEqual(davide["intimacy"], 44)

    def test_relations_expose_full_interaction_history(self) -> None:
        relations = DashboardData(self.root).relations()

        self.assertEqual(relations["count"], 3)
        self.assertEqual(relations["total_interactions"], 48)
        marco = next(
            contact
            for contact in relations["contacts"]
            if contact["user_id"] == "user_marco"
        )
        self.assertEqual(marco["display_name"], "Marco")
        self.assertEqual(marco["interaction_count"], 18)
        self.assertEqual(
            [entry["timestamp"] for entry in marco["interactions"]],
            ["2026-10-18T21:40:00+02:00", "2026-10-02T19:10:00+02:00"],
        )
        latest = marco["interactions"][0]
        self.assertEqual(latest["channel"], "chat")
        self.assertEqual(latest["memory_id"], "mem_0004")
        self.assertEqual(latest["sentiment"], "positivo")
        self.assertIn("Python", latest["summary"])
        self.assertEqual(
            marco["preferences"],
            ["programmazione", "videogiochi", "serie TV"],
        )
        self.assertTrue(marco["confirmed_facts"])
        self.assertTrue(marco["boundaries"])
        self.assertTrue(marco["notes"])

    def test_overview_exposes_heartbeat_outbox_and_files(self) -> None:
        data = DashboardData(self.root)
        initial = data.overview()

        self.assertIn("heartbeat", initial["state"])
        self.assertEqual(initial["outbox"]["count"], 0)
        self.assertEqual(initial["files"]["count"], 0)
        self.assertFalse((self.root / "files").exists())

        FileSandbox(self.root).write_file("appunti.txt", "promemoria")
        outbox_dir = self.root / "data" / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        (outbox_dir / "messages.jsonl").write_text(
            json.dumps(
                {
                    "message_id": "msg_test01",
                    "delivered_at": "2026-10-26T11:00:00+01:00",
                    "channel": "whatsapp",
                    "trigger": "relazione",
                    "target": {
                        "user_id": "user_marco",
                        "display_name": "Marco",
                    },
                    "text": "Ciao Marco, come procede?",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        refreshed = data.overview()
        self.assertEqual(refreshed["counts"]["files"], 1)
        self.assertEqual(refreshed["files"]["entries"][0]["name"], "appunti.txt")
        self.assertEqual(refreshed["counts"]["outbox_messages"], 1)
        self.assertEqual(refreshed["outbox"]["recent"][0]["target_name"], "Marco")
        self.assertEqual(
            refreshed["outbox"]["recent"][0]["text"],
            "Ciao Marco, come procede?",
        )

    def test_recorded_interaction_refreshes_relations_history(self) -> None:
        data = DashboardData(self.root)
        data.relations()

        at = datetime.fromisoformat("2026-10-26T10:00:00+01:00")
        HeartbeatEngine(self.root, at=at).record_interaction(
            user_id="user_giulia", summary="Aggiornamento sul progetto di tesi."
        )

        relations = data.relations()
        giulia = next(
            contact
            for contact in relations["contacts"]
            if contact["user_id"] == "user_giulia"
        )
        self.assertEqual(giulia["interaction_count"], 8)
        self.assertEqual(len(giulia["interactions"]), 2)
        self.assertEqual(
            giulia["interactions"][0]["timestamp"],
            "2026-10-26T10:00:00+01:00",
        )
        self.assertEqual(
            giulia["interactions"][0]["summary"],
            "Aggiornamento sul progetto di tesi.",
        )

    def test_recorded_interaction_refreshes_contact_in_overview(self) -> None:
        data = DashboardData(self.root)
        data.overview()  # crea i servizi: la lettura successiva non deve usare cache

        at = datetime.fromisoformat("2026-10-26T10:00:00+01:00")
        HeartbeatEngine(self.root, at=at).record_interaction(
            user_id="user_giulia", summary="Aggiornamento sul progetto di tesi."
        )

        contacts = data.overview()["contacts"]
        giulia = next(
            contact for contact in contacts if contact["user_id"] == "user_giulia"
        )
        self.assertEqual(contacts[0]["user_id"], "user_giulia")
        self.assertEqual(giulia["interaction_count"], 8)
        self.assertEqual(
            giulia["last_interaction_at"], "2026-10-26T10:00:00+01:00"
        )
        self.assertEqual(
            giulia["last_interaction_summary"],
            "Aggiornamento sul progetto di tesi.",
        )

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
        self.assertIn(b"loadRelations", body)
        self.assertIn(b"openRelation", body)

        status, _headers, body = self._get("/static/../config/identity.json")
        self.assertEqual(status, 404)
        self.assertIn(b"non trovato", body.lower())

    def test_read_only_api(self) -> None:
        status, _headers, body = self._get("/api/overview")
        self.assertEqual(status, 200)
        overview = json.loads(body)
        self.assertEqual(overview["counts"]["memories"], 40)
        self.assertEqual(len(overview["contacts"]), 3)
        self.assertEqual(overview["contacts"][0]["user_id"], "user_davide")

        status, _headers, body = self._get("/api/relations")
        self.assertEqual(status, 200)
        relations = json.loads(body)
        self.assertEqual(relations["count"], 3)
        self.assertEqual(relations["total_interactions"], 48)
        self.assertEqual(relations["contacts"][0]["user_id"], "user_davide")
        self.assertEqual(
            len(relations["contacts"][0]["interactions"]), 1
        )

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
