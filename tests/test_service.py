"""Test dei servizi di memoria della Fase 2.

I test lavorano su una copia temporanea di configurazioni e database: non
modificano mai gli artefatti del progetto.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_memory.errors import AgentMemoryError, ConfigError, NotFoundError, ValidationError
from agent_memory.service import MemoryService, ShadowService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TempProjectTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        shutil.copytree(PROJECT_ROOT / "config", self.root / "config")
        shutil.copytree(
            PROJECT_ROOT / "data",
            self.root / "data",
            ignore=shutil.ignore_patterns("models", "indexes"),
        )
        self.memory = MemoryService(self.root)
        self.shadow = ShadowService(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class ConfigAndServiceTests(TempProjectTestCase):
    def test_identity_and_state_are_loaded(self) -> None:
        identity = self.memory.get_identity()
        self.assertEqual(identity["identity_id"], "character_001")
        self.assertEqual(identity["personal_data"]["full_name"], "Alessandro Vitale")

        state = self.memory.get_current_state()
        self.assertEqual(state["mental_load"], 37)
        self.assertFalse(state["flags"]["shadow_direct_read_enabled"])

    def test_update_current_state_merges_and_persists(self) -> None:
        updated = self.memory.update_current_state(
            {"mental_load": 55, "emotion": {"label": "stanco"}}
        )
        self.assertEqual(updated["mental_load"], 55)
        self.assertEqual(updated["emotion"]["label"], "stanco")
        self.assertEqual(updated["emotion"]["valence"], 0.35)
        self.assertEqual(updated["identity_id"], "character_001")

        with (self.root / "config" / "current_state.json").open(encoding="utf-8") as handle:
            persisted = json.load(handle)
        self.assertEqual(persisted["mental_load"], 55)
        self.assertEqual(persisted["emotion"]["label"], "stanco")

    def test_update_current_state_rejects_invalid_values_and_shadow_flag(self) -> None:
        with self.assertRaises(ValidationError):
            self.memory.update_current_state({"mental_load": 120})
        with self.assertRaises(ValidationError):
            self.memory.update_current_state({"identity_id": "other"})
        with self.assertRaises(ValidationError):
            self.memory.update_current_state(
                {"flags": {"shadow_direct_read_enabled": True}}
            )

    def test_update_identity_is_internal_and_preserves_id(self) -> None:
        updated = self.memory.update_identity(
            {"personal_data": {"first_name": "Alex"}}
        )
        self.assertEqual(updated["personal_data"]["first_name"], "Alex")
        self.assertEqual(updated["identity_id"], "character_001")

        with self.assertRaises(AgentMemoryError):
            self.memory.update_identity(
                {"personal_data": {"birth_date": "2000-01-01"}}
            )

    def test_shadow_policy_is_fail_closed(self) -> None:
        path = self.root / "config" / "shadow_traits.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["access_policy"]["conscious_direct_read"] = True
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        with self.assertRaises(ConfigError):
            MemoryService(self.root)
        with self.assertRaises(ConfigError):
            ShadowService(self.root)

    def test_world_history_filters(self) -> None:
        events = self.memory.get_world_history(
            start_date="2020-01-01", end_date="2021-12-31"
        )
        self.assertTrue(events)
        self.assertTrue(all("2020-01-01" <= event["date"] <= "2021-12-31" for event in events))

        relevant = self.memory.get_world_history(min_italy_relevance=9)
        self.assertTrue(relevant)
        self.assertTrue(all(event["italy_relevance"] >= 9 for event in relevant))

        with self.assertRaises(ValidationError):
            self.memory.get_world_history(
                start_date="2024-01-01", end_date="2020-01-01"
            )

    def test_social_graph_and_person(self) -> None:
        graph = self.memory.get_social_graph()
        self.assertEqual(len(graph["people"]), 3)
        marco = self.memory.get_person("user_marco")
        self.assertIsNotNone(marco)
        assert marco is not None
        self.assertEqual(marco["trust"], 74)
        self.assertIsNone(self.memory.get_person("user_unknown"))


class EpisodicMemoryTests(TempProjectTestCase):
    def test_conscious_view_excludes_repressed_memory(self) -> None:
        ids = {memory["id"] for memory in self.memory.list_memories(limit=100)}
        self.assertEqual(ids, {"mem_0001", "mem_0003", "mem_0004", "mem_0005"})
        self.assertIsNone(self.memory.get_memory("mem_0002"))
        self.assertEqual(self.memory.get_memory_links("mem_0002"), [])

    def test_search_and_filters(self) -> None:
        self.assertEqual(
            [memory["id"] for memory in self.memory.search_memories("Marco")],
            ["mem_0004"],
        )
        self.assertEqual(
            [memory["id"] for memory in self.memory.search_memories(tag="lockdown")],
            ["mem_0003"],
        )
        self.assertEqual(
            [memory["id"] for memory in self.memory.search_memories(person="user_davide")],
            ["mem_0005"],
        )
        self.assertEqual(
            {memory["id"] for memory in self.memory.search_memories(source="real_interaction")},
            {"mem_0004"},
        )
        self.assertEqual(
            [memory["id"] for memory in self.memory.search_memories(min_importance=7)],
            ["mem_0003"],
        )
        ascending = self.memory.search_memories(order="asc", limit=1)
        self.assertEqual(ascending[0]["id"], "mem_0001")
        self.assertEqual(self.memory.search_memories(limit=1)[0]["id"], "mem_0005")

    def test_create_memory_and_revision(self) -> None:
        memory = self.memory.create_memory(
            title="Un pomeriggio di studio",
            description="Ha studiato per due ore, poi è uscito per una passeggiata.",
            importance=4,
            source="generated",
            event_time="2026-10-24T18:00:00+02:00",
            place="Catania, casa",
            people=["user_marco"],
            tags=["studio", "passeggiata"],
        )
        self.assertTrue(memory["id"].startswith("mem_"))
        self.assertEqual(memory["event_date"], "2026-10-24")
        self.assertEqual(memory["access_level"], "conscious")
        self.assertEqual(memory["is_simulated"], 1)
        self.assertEqual(memory["tags"], ["studio", "passeggiata"])

        with self.memory.db.timeline() as conn:
            revision = conn.execute(
                "SELECT revision_number, changed_by FROM memory_revisions WHERE memory_id = ?",
                (memory["id"],),
            ).fetchone()
        self.assertIsNotNone(revision)
        assert revision is not None
        self.assertEqual(revision["revision_number"], 1)
        self.assertEqual(revision["changed_by"], "memory_service")

        self.assertIsNotNone(self.memory.get_memory(memory["id"]))

    def test_create_memory_validates_source_and_simulation(self) -> None:
        with self.assertRaises(ValidationError):
            self.memory.create_memory(
                title="Interazione falsa",
                description="Non dovrebbe essere accettata.",
                source="real_interaction",
                is_simulated=True,
            )
        with self.assertRaises(ValidationError):
            self.memory.create_memory(
                title="Generato non simulato",
                description="Non dovrebbe essere accettato.",
                source="generated",
                is_simulated=False,
            )
        with self.assertRaises(ValidationError):
            self.memory.create_memory(
                title="Importanza errata",
                description="Non dovrebbe essere accettata.",
                importance=11,
            )

    def test_links_only_between_conscious_memories(self) -> None:
        link = self.memory.add_memory_link(
            "mem_0004", "mem_0005", "thematic", 0.7, "Test"
        )
        self.assertEqual(link["relation_type"], "thematic")
        with self.assertRaises(AgentMemoryError):
            self.memory.add_memory_link("mem_0004", "mem_0005", "thematic")
        with self.assertRaises(NotFoundError):
            self.memory.add_memory_link("mem_0004", "mem_0002", "thematic")

        links = self.memory.get_memory_links("mem_0004")
        self.assertTrue(any(item["to_memory_id"] == "mem_0005" for item in links))


class ShadowServiceTests(TempProjectTestCase):
    def test_shadow_can_read_latent_memory_but_public_service_cannot(self) -> None:
        self.assertIsNone(self.memory.get_memory("mem_0002"))
        latent = self.shadow.get_episodic_memory("mem_0002")
        self.assertIsNotNone(latent)
        assert latent is not None
        self.assertEqual(latent["access_level"], "repressed")

    def test_shadow_traits_and_entries_are_available_internally(self) -> None:
        traits = self.shadow.get_shadow_traits()
        self.assertFalse(traits["access_policy"]["conscious_direct_read"])
        self.assertEqual(len(traits["traits"]), 5)
        self.assertEqual(len(self.shadow.list_traits()), 5)
        self.assertEqual(len(self.shadow.list_shadow_entries()), 3)
        self.assertIsNotNone(self.shadow.get_trait("shadow_envy"))
        self.assertIsNone(self.shadow.get_trait("shadow_unknown"))

    def test_create_shadow_entry_with_revision(self) -> None:
        entry = self.shadow.create_shadow_entry(
            entry_type="latent_reaction",
            title="Una reazione trattenuta",
            content="Ha evitato di rispondere a una domanda personale.",
            intensity=4,
            source="reflection",
            linked_memory_id="mem_0004",
            linked_trait_id="shadow_insecurity",
            emotions={"label": "diffidenza", "valence": -0.2, "arousal": 0.3},
        )
        self.assertTrue(entry["id"].startswith("shd_"))
        self.assertEqual(entry["access_level"], "latent")
        self.assertEqual(entry["emotions"]["label"], "diffidenza")
        self.assertIsNotNone(self.shadow.get_shadow_entry(entry["id"]))

        with self.shadow.db.shadow() as conn:
            revision = conn.execute(
                "SELECT revision_number, changed_by FROM shadow_revisions WHERE entry_id = ?",
                (entry["id"],),
            ).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) FROM shadow_entries WHERE id = ?", (entry["id"],)
            ).fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(revision["revision_number"], 1)
        self.assertEqual(revision["changed_by"], "shadow_engine")

    def test_create_latent_episodic_memory(self) -> None:
        memory = self.shadow.create_episodic_memory(
            title="Ricordo latente di test",
            description="Un ricordo non visibile al server MCP.",
            importance=6,
            source="generated",
            event_time="2026-10-24T19:00:00+02:00",
            access_level="latent",
        )
        self.assertEqual(memory["access_level"], "latent")
        self.assertIsNone(self.memory.get_memory(memory["id"]))
        self.assertIsNotNone(self.shadow.get_episodic_memory(memory["id"]))

        with self.assertRaises(ValidationError):
            self.shadow.create_episodic_memory(
                title="Represso senza motivo",
                description="Non dovrebbe essere accettato.",
                source="generated",
                access_level="repressed",
            )

    def test_record_trigger_is_idempotent(self) -> None:
        first = self.shadow.record_trigger(
            trait_id="shadow_envy",
            trigger_description="Test trigger",
            intensity=3,
            triggered_at="2026-10-24T18:30:00+02:00",
            defense_mechanism="rationalization",
        )
        second = self.shadow.record_trigger(
            trait_id="shadow_envy",
            trigger_description="Test trigger",
            intensity=3,
            triggered_at="2026-10-24T18:30:00+02:00",
            defense_mechanism="rationalization",
        )
        self.assertEqual(first["id"], second["id"])
        with self.shadow.db.shadow() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM shadow_trigger_log WHERE trigger_description = ?",
                ("Test trigger",),
            ).fetchone()[0]
        self.assertEqual(count, 1)

        with self.assertRaises(ValidationError):
            self.shadow.record_trigger(
                trait_id="shadow_unknown",
                trigger_description="Non valido",
                intensity=3,
            )

    def test_health_reports_both_databases(self) -> None:
        health = self.memory.health()
        self.assertTrue(health["ok"])
        self.assertTrue(health["databases"]["timeline_episodic"]["ok"])
        self.assertTrue(health["databases"]["shadow_diary"]["ok"])
        shadow_health = self.shadow.health()
        self.assertTrue(shadow_health["ok"])
        self.assertEqual(shadow_health["trait_count"], 5)
        self.assertFalse(shadow_health["access_policy"]["conscious_direct_read"])
