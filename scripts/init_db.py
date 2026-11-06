#!/usr/bin/env python3
"""Bootstrap di configurazione, database e archivi della memoria.

Il comando:
  1. carica e valida i cinque file JSON in config/;
  2. applica gli schemi in agent_memory/schemas/ ai quattro database in data/;
  3. inserisce ricordi episodici, voci dell'ombra e conoscenze semantiche;
  4. verifica i riferimenti incrociati tra JSON, timeline, diario e semantica.

L'indice vettoriale FAISS non viene costruito qui: dopo il bootstrap esegui
``python -m agent_memory.index --rebuild`` (richiede il modello di embedding).

Uso:
    python scripts/init_db.py
    python scripts/init_db.py --reset   # ricrea i database da zero
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
SCHEMA_DIR = ROOT / "agent_memory" / "schemas"
DATA_DIR = ROOT / "data"

TIMELINE_DB = DATA_DIR / "timeline_episodic.db"
SHADOW_DB = DATA_DIR / "shadow_diary.db"
SEMANTIC_DB = DATA_DIR / "semantic_memory.db"
VECTOR_DB = DATA_DIR / "vector_index.db"
INDEX_DIR = DATA_DIR / "indexes"

CONFIG_FILES = {
    "identity": "identity.json",
    "shadow_traits": "shadow_traits.json",
    "current_state": "current_state.json",
    "world_history": "world_history.json",
    "social_graph": "social_graph.json",
}

SEED_TIMESTAMP = "2026-10-24T15:00:00+02:00"


class ValidationError(Exception):
    """Errore di validazione dei dati di configurazione o dei riferimenti."""


# ---------------------------------------------------------------------------
# Validazione dei JSON
# ---------------------------------------------------------------------------


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise ValidationError(f"File non trovato: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"JSON non valido in {path}: riga {exc.lineno}, colonna {exc.colno} ({exc.msg})"
        ) from exc


def require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{path}: atteso un oggetto JSON.")
    return value


def require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValidationError(f"{path}: attesa una lista JSON.")
    return value


def require_keys(obj: dict[str, Any], keys: list[str], path: str) -> None:
    for key in keys:
        if key not in obj:
            raise ValidationError(f"{path}: chiave obbligatoria mancante '{key}'.")


def require_number(value: Any, low: float, high: float, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{path}: atteso un numero, trovato {type(value).__name__}.")
    if not low <= float(value) <= high:
        raise ValidationError(f"{path}: valore {value} fuori intervallo [{low}, {high}].")


def require_iso_date(value: Any, path: str) -> None:
    if not isinstance(value, str):
        raise ValidationError(f"{path}: attesa una data ISO 8601 come stringa.")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{path}: data non valida '{value}'.") from exc


def require_iso_datetime(value: Any, path: str) -> None:
    if not isinstance(value, str):
        raise ValidationError(f"{path}: atteso un timestamp ISO 8601 come stringa.")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{path}: timestamp non valido '{value}'.") from exc


def validate_identity(identity: dict[str, Any]) -> None:
    require_keys(
        identity,
        ["schema_version", "identity_id", "personal_data", "family", "personality"],
        "identity",
    )
    personal = require_object(identity["personal_data"], "identity.personal_data")
    require_keys(
        personal,
        [
            "first_name",
            "last_name",
            "birth_date",
            "birth_place",
            "timezone",
        ],
        "identity.personal_data",
    )
    require_iso_date(personal["birth_date"], "identity.personal_data.birth_date")
    require_object(personal["birth_place"], "identity.personal_data.birth_place")

    family = require_object(identity["family"], "identity.family")
    require_keys(family, ["parents", "siblings"], "identity.family")
    require_list(family["parents"], "identity.family.parents")
    require_list(family["siblings"], "identity.family.siblings")
    if len(family["parents"]) < 1:
        raise ValidationError("identity.family.parents: almeno un genitore richiesto.")

    personality = require_object(identity["personality"], "identity.personality")
    require_keys(personality, ["model", "scale", "traits"], "identity.personality")
    traits = require_object(personality["traits"], "identity.personality.traits")
    expected_traits = [
        "introversion_extraversion",
        "empathy_detachment",
        "logic_impulsivity",
        "resilience_sensitivity",
    ]
    for trait_id in expected_traits:
        if trait_id not in traits:
            raise ValidationError(
                f"identity.personality.traits: manca la dimensione '{trait_id}'."
            )
        trait = require_object(
            traits[trait_id], f"identity.personality.traits.{trait_id}"
        )
        require_keys(trait, ["value", "left", "right"], f"identity.personality.traits.{trait_id}")
        require_number(
            trait["value"],
            1,
            10,
            f"identity.personality.traits.{trait_id}.value",
        )


def validate_shadow_traits(shadow: dict[str, Any]) -> None:
    require_keys(
        shadow,
        [
            "schema_version",
            "identity_id",
            "access_policy",
            "traits",
            "internal_conflicts",
            "traumas",
        ],
        "shadow_traits",
    )
    access = require_object(shadow["access_policy"], "shadow_traits.access_policy")
    if access.get("conscious_direct_read") is not False:
        raise ValidationError(
            "shadow_traits.access_policy.conscious_direct_read deve essere false."
        )
    if access.get("rag_exposure") != "never_by_default":
        raise ValidationError(
            "shadow_traits.access_policy.rag_exposure deve essere 'never_by_default'."
        )

    traits = require_list(shadow["traits"], "shadow_traits.traits")
    if not traits:
        raise ValidationError("shadow_traits.traits: almeno un tratto richiesto.")
    seen: set[str] = set()
    for index, item in enumerate(traits):
        path = f"shadow_traits.traits[{index}]"
        trait = require_object(item, path)
        require_keys(
            trait,
            [
                "id",
                "name",
                "intensity",
                "conscious_awareness",
                "triggers",
                "defense_mechanisms",
                "behavioral_rules",
                "safety",
            ],
            path,
        )
        if trait["id"] in seen:
            raise ValidationError(f"{path}.id duplicato: {trait['id']}")
        seen.add(trait["id"])
        require_number(trait["intensity"], 1, 10, f"{path}.intensity")
        require_number(trait["conscious_awareness"], 0, 1, f"{path}.conscious_awareness")
        require_list(trait["triggers"], f"{path}.triggers")
        require_list(trait["defense_mechanisms"], f"{path}.defense_mechanisms")
        require_list(trait["behavioral_rules"], f"{path}.behavioral_rules")
        require_object(trait["safety"], f"{path}.safety")
        for trigger_index, trigger in enumerate(trait["triggers"]):
            trigger_path = f"{path}.triggers[{trigger_index}]"
            trigger_obj = require_object(trigger, trigger_path)
            require_keys(trigger_obj, ["id", "description", "weight"], trigger_path)
            require_number(trigger_obj["weight"], 0, 1, f"{trigger_path}.weight")

    conflicts = require_list(shadow["internal_conflicts"], "shadow_traits.internal_conflicts")
    for index, item in enumerate(conflicts):
        conflict = require_object(item, f"shadow_traits.internal_conflicts[{index}]")
        require_keys(
            conflict,
            ["id", "between", "description"],
            f"shadow_traits.internal_conflicts[{index}]",
        )

    traumas = require_list(shadow["traumas"], "shadow_traits.traumas")
    for index, item in enumerate(traumas):
        trauma = require_object(item, f"shadow_traits.traumas[{index}]")
        require_keys(
            trauma,
            ["id", "linked_memory_id", "intensity", "repressed"],
            f"shadow_traits.traumas[{index}]",
        )
        require_number(trauma["intensity"], 1, 10, f"shadow_traits.traumas[{index}].intensity")
        if not isinstance(trauma["repressed"], bool):
            raise ValidationError(
                f"shadow_traits.traumas[{index}].repressed deve essere booleano."
            )


def validate_current_state(state: dict[str, Any]) -> None:
    require_keys(
        state,
        [
            "schema_version",
            "state_id",
            "identity_id",
            "updated_at",
            "operational_status",
            "clock",
            "activity",
            "emotion",
            "mental_load",
            "mental_load_thresholds",
            "routine",
            "sleep",
            "availability",
            "heartbeat",
            "flags",
        ],
        "current_state",
    )
    require_iso_datetime(state["updated_at"], "current_state.updated_at")
    require_number(state["mental_load"], 0, 100, "current_state.mental_load")
    thresholds = require_object(
        state["mental_load_thresholds"], "current_state.mental_load_thresholds"
    )
    require_keys(thresholds, ["soft", "overload"], "current_state.mental_load_thresholds")
    if thresholds["soft"] >= thresholds["overload"]:
        raise ValidationError(
            "current_state.mental_load_thresholds: 'soft' deve essere minore di 'overload'."
        )
    for key in [
        "clock",
        "activity",
        "emotion",
        "routine",
        "sleep",
        "availability",
        "heartbeat",
        "flags",
    ]:
        require_object(state[key], f"current_state.{key}")


def validate_world_history(world: dict[str, Any]) -> None:
    require_keys(
        world,
        ["schema_version", "dataset_id", "identity_id", "birth_date", "events"],
        "world_history",
    )
    require_iso_date(world["birth_date"], "world_history.birth_date")
    events = require_list(world["events"], "world_history.events")
    if not events:
        raise ValidationError("world_history.events: almeno un evento richiesto.")
    seen: set[str] = set()
    for index, item in enumerate(events):
        path = f"world_history.events[{index}]"
        event = require_object(item, path)
        require_keys(
            event,
            [
                "id",
                "date",
                "title",
                "description",
                "category",
                "global_relevance",
                "italy_relevance",
                "comprehension_threshold_years",
                "potential_personal_impact",
                "sources",
            ],
            path,
        )
        if event["id"] in seen:
            raise ValidationError(f"{path}.id duplicato: {event['id']}")
        seen.add(event["id"])
        require_iso_date(event["date"], f"{path}.date")
        require_number(event["global_relevance"], 0, 10, f"{path}.global_relevance")
        require_number(event["italy_relevance"], 0, 10, f"{path}.italy_relevance")
        require_number(
            event["comprehension_threshold_years"],
            1,
            18,
            f"{path}.comprehension_threshold_years",
        )
        require_list(event["sources"], f"{path}.sources")


def validate_social_graph(social: dict[str, Any]) -> None:
    require_keys(
        social,
        ["schema_version", "identity_id", "scales", "people"],
        "social_graph",
    )
    people = require_list(social["people"], "social_graph.people")
    if not people:
        raise ValidationError("social_graph.people: almeno una persona richiesta.")
    seen: set[str] = set()
    for index, item in enumerate(people):
        path = f"social_graph.people[{index}]"
        person = require_object(item, path)
        require_keys(
            person,
            [
                "user_id",
                "display_name",
                "trust",
                "intimacy",
                "interaction_history",
            ],
            path,
        )
        if person["user_id"] in seen:
            raise ValidationError(f"{path}.user_id duplicato: {person['user_id']}")
        seen.add(person["user_id"])
        require_number(person["trust"], 0, 100, f"{path}.trust")
        require_number(person["intimacy"], 0, 100, f"{path}.intimacy")
        history = require_list(person["interaction_history"], f"{path}.interaction_history")
        for history_index, entry in enumerate(history):
            entry_path = f"{path}.interaction_history[{history_index}]"
            entry_obj = require_object(entry, entry_path)
            require_keys(
                entry_obj,
                ["timestamp", "channel", "summary", "sentiment"],
                entry_path,
            )
            require_iso_datetime(entry_obj["timestamp"], f"{entry_path}.timestamp")
            if entry_obj.get("memory_id") is not None and not isinstance(
                entry_obj["memory_id"], str
            ):
                raise ValidationError(f"{entry_path}.memory_id deve essere stringa o null.")


def load_and_validate_configs() -> dict[str, Any]:
    configs: dict[str, Any] = {}
    for key, filename in CONFIG_FILES.items():
        configs[key] = load_json(CONFIG_DIR / filename)

    identity = require_object(configs["identity"], "identity")
    shadow = require_object(configs["shadow_traits"], "shadow_traits")
    state = require_object(configs["current_state"], "current_state")
    world = require_object(configs["world_history"], "world_history")
    social = require_object(configs["social_graph"], "social_graph")

    validate_identity(identity)
    validate_shadow_traits(shadow)
    validate_current_state(state)
    validate_world_history(world)
    validate_social_graph(social)

    identity_id = identity["identity_id"]
    for name, document in [
        ("shadow_traits", shadow),
        ("current_state", state),
        ("world_history", world),
        ("social_graph", social),
    ]:
        if document.get("identity_id") != identity_id:
            raise ValidationError(
                f"{name}.identity_id ({document.get('identity_id')!r}) non corrisponde "
                f"a identity.identity_id ({identity_id!r})."
            )

    if world.get("birth_date") != identity["personal_data"]["birth_date"]:
        raise ValidationError(
            "world_history.birth_date non corrisponde a identity.personal_data.birth_date."
        )

    return configs


# ---------------------------------------------------------------------------
# Dati di esempio
# ---------------------------------------------------------------------------


EPISODIC_MEMORIES: list[dict[str, Any]] = [
    {
        "id": "mem_0001",
        "event_time": "2007-06-10T16:30:00+02:00",
        "title": "La caduta dalla bicicletta",
        "description": (
            "A sei anni, mentre imparava ad andare in bicicletta nel cortile di casa a Licata, "
            "è caduto e si è sbucciato un ginocchio. Il padre lo ha aiutato a rialzarsi. "
            "Da quel momento è diventato più prudente quando prova qualcosa di nuovo."
        ),
        "place": "Licata, cortile di casa",
        "people": ["fam_father"],
        "emotional_state": {
            "label": "paura",
            "valence": -0.5,
            "arousal": 0.7,
            "intensity": 6,
        },
        "importance": 6,
        "consequences": (
            "Maggiore prudenza nelle attività fisiche nuove; tendenza a controllare due volte "
            "prima di agire."
        ),
        "source": "admin_seed",
        "source_ref": "seed_bike_fall_2007",
        "reliability": 0.8,
        "is_simulated": 1,
        "access_level": "conscious",
        "access_reason": None,
        "is_consolidated": 1,
        "tags": ["infanzia", "famiglia", "paura", "prudenza"],
    },
    {
        "id": "mem_0002",
        "event_time": "2019-05-17T10:15:00+02:00",
        "title": "La presentazione andata male",
        "description": (
            "Durante l'ultimo anno di liceo, una presentazione di filosofia davanti alla classe "
            "è andata male: voce tremante, frasi confuse, risate di alcuni compagni. "
            "L'episodio è stato vissuto con vergogna e umiliazione."
        ),
        "place": "Licata, Liceo Scientifico",
        "people": ["compagni_di_classe", "professore_filosofia"],
        "emotional_state": {
            "label": "vergogna",
            "valence": -0.8,
            "arousal": 0.8,
            "intensity": 8,
        },
        "importance": 8,
        "consequences": (
            "Evita le presentazioni pubbliche, prepara ossessivamente i materiali e teme "
            "il giudizio degli altri."
        ),
        "source": "generated",
        "source_ref": "biography_generator_v1",
        "reliability": 0.7,
        "is_simulated": 1,
        "access_level": "repressed",
        "access_reason": (
            "Evento traumatico rimosso dalla memoria conscia; i dettagli "
            "sono nel diario dell'ombra."
        ),
        "is_consolidated": 1,
        "tags": ["adolescenza", "scuola", "vergogna", "trauma", "presentazioni"],
    },
    {
        "id": "mem_0003",
        "event_time": "2020-03-15T09:00:00+01:00",
        "title": "Il primo giorno di lockdown",
        "description": (
            "Il giorno dopo il suo diciannovesimo compleanno, l'Italia entra nel lockdown per "
            "il COVID-19. La scuola passa alla didattica a distanza. In casa il clima è teso "
            "ma ordinato; l'agente trascorre molto tempo nella sua stanza, tra lezioni online "
            "e serie TV."
        ),
        "place": "Licata, casa di famiglia",
        "people": ["fam_father", "fam_mother", "fam_sister"],
        "emotional_state": {
            "label": "inquietudine",
            "valence": -0.3,
            "arousal": 0.4,
            "intensity": 6,
        },
        "importance": 7,
        "consequences": (
            "Consolida abitudini solitarie e una certa ambivalenza verso la vita sociale: "
            "il lockdown allevia la pressione sociale ma aumenta la solitudine."
        ),
        "source": "world_event",
        "source_ref": "wh_2020_03_09_covid_lockdown",
        "reliability": 0.9,
        "is_simulated": 1,
        "access_level": "conscious",
        "access_reason": None,
        "is_consolidated": 1,
        "tags": [
            "covid-19",
            "lockdown",
            "adolescenza",
            "didattica_a_distanza",
            "isolamento",
        ],
    },
    {
        "id": "mem_0004",
        "event_time": "2026-10-18T21:40:00+02:00",
        "title": "Conversazione con Marco su Python e MCP",
        "description": (
            "Marco ha scritto per chiedere un parere su un progetto Python. La conversazione "
            "si è spostata su MCP, memoria degli agenti e sul modo in cui i ricordi possono "
            "essere recuperati. L'agente ha spiegato con entusiasmo la differenza tra memoria "
            "episodica e semantica."
        ),
        "place": "Catania, chat privata",
        "people": ["user_marco"],
        "emotional_state": {
            "label": "entusiasmo",
            "valence": 0.7,
            "arousal": 0.6,
            "intensity": 6,
        },
        "importance": 5,
        "consequences": (
            "Rafforza il legame con Marco e l'interesse per i sistemi di memoria per agenti."
        ),
        "source": "real_interaction",
        "source_ref": "chat_2026_10_18_marco_001",
        "reliability": 1.0,
        "is_simulated": 0,
        "access_level": "conscious",
        "access_reason": None,
        "is_consolidated": 0,
        "tags": ["marco", "python", "mcp", "programmazione", "amicizia"],
    },
    {
        "id": "mem_0005",
        "event_time": "2026-10-20T22:10:00+02:00",
        "title": "Serata gaming con Davide",
        "description": (
            "Serata di gioco cooperativo online con Davide. Hanno perso due partite, ma la "
            "conversazione è stata leggera e piacevole. L'agente ha apprezzato il fatto di "
            "non dover parlare di sé."
        ),
        "place": "Catania, camera, gioco online",
        "people": ["user_davide"],
        "emotional_state": {
            "label": "rilassato",
            "valence": 0.6,
            "arousal": 0.3,
            "intensity": 4,
        },
        "importance": 3,
        "consequences": (
            "Rende più saldo il rapporto con Davide; conferma il gaming come attività di "
            "decompressione."
        ),
        "source": "routine",
        "source_ref": "routine_2026_10_20_gaming",
        "reliability": 0.9,
        "is_simulated": 1,
        "access_level": "conscious",
        "access_reason": None,
        "is_consolidated": 0,
        "tags": ["davide", "gaming", "amicizia_online", "routine"],
    },
]


MEMORY_LINKS: list[dict[str, Any]] = [
    {
        "from_memory_id": "mem_0002",
        "to_memory_id": "mem_0003",
        "relation_type": "thematic",
        "strength": 0.6,
        "note": (
            "La vergogna della presentazione si intreccia con l'isolamento e la didattica "
            "a distanza del lockdown."
        ),
    },
    {
        "from_memory_id": "mem_0004",
        "to_memory_id": "mem_0005",
        "relation_type": "associated",
        "strength": 0.4,
        "note": (
            "Due interazioni sociali recenti, entrambe preferite in contesti a bassa pressione."
        ),
    },
]


SHADOW_ENTRIES: list[dict[str, Any]] = [
    {
        "id": "shd_0001",
        "entry_time": "2019-05-17T20:00:00+02:00",
        "entry_type": "repressed_memory",
        "title": "La vergogna della presentazione",
        "content": (
            "La scena della presentazione è rimasta intatta nel diario latente: la voce che "
            "trema, le risate di alcuni compagni, il silenzio del professore. Nella memoria "
            "conscia resta solo una generica riluttanza verso le presentazioni pubbliche."
        ),
        "linked_memory_id": "mem_0002",
        "linked_trait_id": "shadow_fear_rejection",
        "trigger": "Presentazione davanti alla classe",
        "emotions": {
            "label": "vergogna",
            "valence": -0.9,
            "arousal": 0.8,
            "intensity": 9,
        },
        "intensity": 9,
        "defense_mechanism": "repression",
        "conscious_manifestation": (
            "Evita le presentazioni pubbliche e prepara i materiali in modo ossessivo."
        ),
        "access_level": "latent",
        "source": "generated",
        "source_ref": "biography_generator_v1",
    },
    {
        "id": "shd_0002",
        "entry_time": "2026-10-18T22:05:00+02:00",
        "entry_type": "projection",
        "title": "Il successo di Marco",
        "content": (
            "Marco ha parlato con entusiasmo di un progetto che sta funzionando. Una parte "
            "dell'agente ha interpretato questo entusiasmo come una forma di superiorità. "
            "La reazione è stata un commento leggermente distaccato, poi corretto con una "
            "domanda tecnica."
        ),
        "linked_memory_id": "mem_0004",
        "linked_trait_id": "shadow_envy",
        "trigger": "Racconto di un successo personale da parte di un coetaneo",
        "emotions": {
            "label": "invidia",
            "valence": -0.4,
            "arousal": 0.5,
            "intensity": 6,
        },
        "intensity": 5,
        "defense_mechanism": "projection",
        "conscious_manifestation": (
            "Nel diario conscio l'episodio è stato registrato come 'Marco era un po' "
            "arrogante', senza riconoscere l'invidia."
        ),
        "access_level": "latent",
        "source": "real_interaction",
        "source_ref": "chat_2026_10_18_marco_001",
    },
    {
        "id": "shd_0003",
        "entry_time": "2026-10-20T23:00:00+02:00",
        "entry_type": "latent_reaction",
        "title": "Il bisogno di non parlare di sé",
        "content": (
            "Durante la serata con Davide, l'agente ha evitato diverse domande personali. "
            "La sensazione era di sollievo, ma anche di distanza. Il bisogno di leggerezza "
            "nasconde la paura di essere giudicato."
        ),
        "linked_memory_id": "mem_0005",
        "linked_trait_id": "shadow_insecurity",
        "trigger": "Domande personali in un contesto informale",
        "emotions": {
            "label": "sollievo",
            "valence": 0.3,
            "arousal": 0.3,
            "intensity": 4,
        },
        "intensity": 4,
        "defense_mechanism": "avoidance",
        "conscious_manifestation": (
            "L'agente si descrive come 'uno che preferisce le serate leggere'."
        ),
        "access_level": "latent",
        "source": "real_interaction",
        "source_ref": "voice_chat_2026_10_20_davide",
    },
]


SHADOW_TRIGGER_LOG: list[dict[str, Any]] = [
    {
        "trait_id": "shadow_fear_rejection",
        "triggered_at": "2019-05-17T10:15:00+02:00",
        "trigger_description": "Presentazione davanti alla classe",
        "intensity": 9,
        "defense_mechanism": "repression",
        "response_summary": (
            "Rimozione dell'evento dalla memoria conscia; evitamento delle presentazioni."
        ),
        "related_entry_id": "shd_0001",
        "resolved": 0,
    },
    {
        "trait_id": "shadow_envy",
        "triggered_at": "2026-10-18T21:50:00+02:00",
        "trigger_description": "Successo di Marco",
        "intensity": 5,
        "defense_mechanism": "projection",
        "response_summary": (
            "Commento distaccato; interpretazione del comportamento altrui come arroganza."
        ),
        "related_entry_id": "shd_0002",
        "resolved": 1,
    },
    {
        "trait_id": "shadow_insecurity",
        "triggered_at": "2026-10-20T22:30:00+02:00",
        "trigger_description": "Domande personali durante la serata gaming",
        "intensity": 4,
        "defense_mechanism": "avoidance",
        "response_summary": "Spostamento della conversazione su temi leggeri.",
        "related_entry_id": "shd_0003",
        "resolved": 1,
    },
]


# ---------------------------------------------------------------------------
# Inserimento nei database
# ---------------------------------------------------------------------------


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def memory_row(memory: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": memory["id"],
        "event_time": memory["event_time"],
        "event_date": memory["event_time"][:10],
        "title": memory["title"],
        "description": memory["description"],
        "place": memory.get("place"),
        "people_json": json_text(memory.get("people", [])),
        "emotional_state_json": json_text(memory.get("emotional_state", {})),
        "importance": memory["importance"],
        "consequences": memory.get("consequences"),
        "source": memory["source"],
        "source_ref": memory.get("source_ref"),
        "reliability": memory.get("reliability", 1.0),
        "is_simulated": memory.get("is_simulated", 0),
        "access_level": memory.get("access_level", "conscious"),
        "access_reason": memory.get("access_reason"),
        "is_consolidated": memory.get("is_consolidated", 0),
        "tags_json": json_text(memory.get("tags", [])),
        "embedding_ref": memory.get("embedding_ref"),
        "embedding_model": memory.get("embedding_model"),
        "embedding_updated_at": memory.get("embedding_updated_at"),
        "version": memory.get("version", 1),
        "previous_version_id": memory.get("previous_version_id"),
        "created_at": SEED_TIMESTAMP,
        "updated_at": SEED_TIMESTAMP,
    }


def shadow_row(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry["id"],
        "entry_time": entry["entry_time"],
        "created_at": SEED_TIMESTAMP,
        "updated_at": SEED_TIMESTAMP,
        "entry_type": entry["entry_type"],
        "title": entry["title"],
        "content": entry["content"],
        "linked_memory_id": entry.get("linked_memory_id"),
        "linked_trait_id": entry.get("linked_trait_id"),
        "trigger": entry.get("trigger"),
        "emotions_json": json_text(entry.get("emotions", {})),
        "intensity": entry["intensity"],
        "defense_mechanism": entry.get("defense_mechanism", "none"),
        "conscious_manifestation": entry.get("conscious_manifestation"),
        "access_level": entry.get("access_level", "latent"),
        "source": entry["source"],
        "source_ref": entry.get("source_ref"),
        "version": entry.get("version", 1),
        "previous_version_id": entry.get("previous_version_id"),
    }


def semantic_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "title": item["title"],
        "content": item["content"],
        "category": item["category"],
        "knowledge_type": item["knowledge_type"],
        "source": item["source"],
        "source_ref": item.get("source_ref"),
        "reliability": item.get("reliability", 0.5),
        "importance": item.get("importance", 5),
        "status": item.get("status", "active"),
        "valid_from": item.get("valid_from"),
        "valid_to": item.get("valid_to"),
        "tags_json": json_text(item.get("tags", [])),
        "metadata_json": json_text(item.get("metadata", {})),
        "embedding_model": item.get("embedding_model"),
        "embedding_updated_at": item.get("embedding_updated_at"),
        "version": item.get("version", 1),
        "previous_version_id": item.get("previous_version_id"),
        "created_at": SEED_TIMESTAMP,
        "updated_at": SEED_TIMESTAMP,
    }


MEMORY_COLUMNS = [
    "id",
    "event_time",
    "event_date",
    "title",
    "description",
    "place",
    "people_json",
    "emotional_state_json",
    "importance",
    "consequences",
    "source",
    "source_ref",
    "reliability",
    "is_simulated",
    "access_level",
    "access_reason",
    "is_consolidated",
    "tags_json",
    "embedding_ref",
    "embedding_model",
    "embedding_updated_at",
    "version",
    "previous_version_id",
    "created_at",
    "updated_at",
]

SEMANTIC_COLUMNS = [
    "id",
    "title",
    "content",
    "category",
    "knowledge_type",
    "source",
    "source_ref",
    "reliability",
    "importance",
    "status",
    "valid_from",
    "valid_to",
    "tags_json",
    "metadata_json",
    "embedding_model",
    "embedding_updated_at",
    "version",
    "previous_version_id",
    "created_at",
    "updated_at",
]


SHADOW_COLUMNS = [
    "id",
    "entry_time",
    "created_at",
    "updated_at",
    "entry_type",
    "title",
    "content",
    "linked_memory_id",
    "linked_trait_id",
    "trigger",
    "emotions_json",
    "intensity",
    "defense_mechanism",
    "conscious_manifestation",
    "access_level",
    "source",
    "source_ref",
    "version",
    "previous_version_id",
]


SEMANTIC_ITEMS: list[dict[str, Any]] = [
    {
        "id": "sem_0001",
        "title": "Una bicicletta è un veicolo a due ruote",
        "content": (
            "Una bicicletta è un veicolo generalmente dotato di due ruote, "
            "azionato da pedali e utilizzato per il trasporto o per attività sportive."
        ),
        "category": "trasporti",
        "knowledge_type": "verified_fact",
        "source": "admin_seed",
        "source_ref": "seed_enciclopedia_bicicletta",
        "reliability": 0.99,
        "importance": 5,
        "tags": ["bicicletta", "veicoli", "trasporti"],
        "metadata": {"origin": "phase3_seed"},
    },
    {
        "id": "sem_0002",
        "title": "Python è un linguaggio di programmazione",
        "content": (
            "Python è un linguaggio di programmazione interpretato, dinamico e "
            "multiparadigma, molto usato per sviluppo web, automazione e analisi dati."
        ),
        "category": "informatica",
        "knowledge_type": "verified_fact",
        "source": "admin_seed",
        "source_ref": "seed_enciclopedia_python",
        "reliability": 0.99,
        "importance": 6,
        "tags": ["python", "programmazione", "informatica"],
        "metadata": {"origin": "phase3_seed"},
    },
    {
        "id": "sem_0003",
        "title": "SQLite è un DBMS relazionale embedded",
        "content": (
            "SQLite è un sistema di gestione di database relazionali embedded: "
            "memorizza i dati in un singolo file e non richiede un server separato."
        ),
        "category": "informatica",
        "knowledge_type": "verified_fact",
        "source": "admin_seed",
        "source_ref": "seed_enciclopedia_sqlite",
        "reliability": 0.98,
        "importance": 6,
        "tags": ["sqlite", "database", "informatica"],
        "metadata": {"origin": "phase3_seed"},
    },
    {
        "id": "sem_0004",
        "title": "Model Context Protocol (MCP)",
        "content": (
            "Il Model Context Protocol è un protocollo aperto per collegare modelli "
            "linguistici a strumenti, risorse e fonti dati in modo standardizzato."
        ),
        "category": "informatica",
        "knowledge_type": "verified_fact",
        "source": "admin_seed",
        "source_ref": "seed_mcp_protocol",
        "reliability": 0.95,
        "importance": 6,
        "tags": ["mcp", "protocolli", "agenti", "informatica"],
        "metadata": {"origin": "phase3_seed"},
    },
    {
        "id": "sem_0005",
        "title": "Marco sta lavorando a un progetto Python",
        "content": (
            "Durante la conversazione del 18 ottobre 2026, Marco ha raccontato di "
            "essere impegnato in un progetto Python universitario e ha chiesto un "
            "parere su come organizzare il codice."
        ),
        "category": "persone",
        "knowledge_type": "user_provided",
        "source": "conversation",
        "source_ref": "chat_2026_10_18_marco_001",
        "reliability": 0.7,
        "importance": 4,
        "tags": ["marco", "python", "progetto", "università"],
        "metadata": {"origin": "phase3_seed"},
    },
    {
        "id": "sem_0006",
        "title": "Il gaming cooperativo come decompressione",
        "content": (
            "L'agente tende a preferire il gaming cooperativo perché riduce la "
            "pressione sociale e permette di condividere un'attività senza dover "
            "parlare troppo di sé."
        ),
        "category": "preferenze",
        "knowledge_type": "opinion",
        "source": "reflection",
        "source_ref": "seed_self_observation_gaming",
        "reliability": 0.6,
        "importance": 4,
        "tags": ["gaming", "amicizia", "decompressione", "preferenze"],
        "metadata": {"origin": "phase3_seed"},
    },
    {
        "id": "sem_0007",
        "title": "Un modello multilingue è preferibile per l'italiano",
        "content": (
            "Per la memoria semantica in italiano è ragionevole preferire un modello "
            "di embedding multilingue a un modello addestrato soltanto sull'inglese."
        ),
        "category": "informatica",
        "knowledge_type": "hypothesis",
        "source": "inference",
        "source_ref": "seed_embedding_hypothesis",
        "reliability": 0.5,
        "importance": 5,
        "tags": ["embedding", "italiano", "multilingue"],
        "metadata": {"origin": "phase3_seed"},
    },
    {
        "id": "sem_0008",
        "title": "Il progetto di Marco usa SQLite",
        "content": (
            "Ipotesi iniziale: il progetto Python di Marco potrebbe usare SQLite per "
            "la persistenza locale dei dati. Non è stato confermato esplicitamente."
        ),
        "category": "persone",
        "knowledge_type": "uncertain",
        "source": "inference",
        "source_ref": "seed_marco_project_hypothesis",
        "reliability": 0.3,
        "importance": 3,
        "tags": ["marco", "sqlite", "progetto"],
        "metadata": {"origin": "phase3_seed"},
    },
    {
        "id": "sem_0009",
        "title": "Un modello solo inglese è sufficiente per la memoria italiana",
        "content": (
            "Ipotesi alternativa: un modello di embedding addestrato soltanto "
            "sull'inglese potrebbe essere sufficiente anche per testi italiani brevi."
        ),
        "category": "informatica",
        "knowledge_type": "hypothesis",
        "source": "inference",
        "source_ref": "seed_embedding_alternative",
        "reliability": 0.3,
        "importance": 4,
        "tags": ["embedding", "inglese", "italiano"],
        "metadata": {"origin": "phase3_seed"},
    },
]


SEMANTIC_ITEM_LINKS: list[dict[str, Any]] = [
    {
        "from_item_id": "sem_0002",
        "to_item_id": "sem_0004",
        "relation_type": "related_to",
        "strength": 0.8,
        "note": "Python e MCP sono entrambi temi tecnici ricorrenti nelle conversazioni.",
    },
    {
        "from_item_id": "sem_0003",
        "to_item_id": "sem_0002",
        "relation_type": "related_to",
        "strength": 0.6,
        "note": "SQLite è usato spesso come persistenza in progetti Python.",
    },
    {
        "from_item_id": "sem_0007",
        "to_item_id": "sem_0004",
        "relation_type": "related_to",
        "strength": 0.6,
        "note": "La scelta del modello di embedding riguarda l'archivio semantico degli agenti.",
    },
    {
        "from_item_id": "sem_0007",
        "to_item_id": "sem_0009",
        "relation_type": "contradicts",
        "strength": 0.9,
        "note": (
            "Le due ipotesi sono in tensione; nessuna delle due viene sovrascritta "
            "automaticamente. La contraddizione resta esplicita."
        ),
    },
]


SEMANTIC_EPISODIC_LINKS: list[dict[str, Any]] = [
    {
        "semantic_id": "sem_0001",
        "memory_id": "mem_0001",
        "relation_type": "related_to",
        "strength": 0.8,
        "note": "Il concetto generale di bicicletta è collegato alla caduta del 2007.",
    },
    {
        "semantic_id": "sem_0002",
        "memory_id": "mem_0004",
        "relation_type": "related_to",
        "strength": 0.7,
        "note": "La conversazione con Marco ha riguardato Python e MCP.",
    },
    {
        "semantic_id": "sem_0005",
        "memory_id": "mem_0004",
        "relation_type": "derived_from",
        "strength": 0.8,
        "note": "La conoscenza su Marco deriva dalla conversazione del 18 ottobre.",
    },
    {
        "semantic_id": "sem_0006",
        "memory_id": "mem_0005",
        "relation_type": "related_to",
        "strength": 0.7,
        "note": "La serata gaming con Davide è un esempio della preferenza per il cooperativo.",
    },
]


def insert_memories(conn: sqlite3.Connection) -> None:
    placeholders = ", ".join("?" for _ in MEMORY_COLUMNS)
    columns = ", ".join(MEMORY_COLUMNS)
    sql = f"INSERT OR IGNORE INTO episodic_memories ({columns}) VALUES ({placeholders})"

    for memory in EPISODIC_MEMORIES:
        row = memory_row(memory)
        conn.execute(sql, tuple(row[column] for column in MEMORY_COLUMNS))
        conn.execute(
            """
            INSERT OR IGNORE INTO memory_revisions (
                memory_id, revision_number, changed_at, changed_by,
                change_reason, previous_snapshot_json, new_snapshot_json, cascade_id
            ) VALUES (?, 1, ?, ?, ?, NULL, ?, NULL)
            """,
            (
                memory["id"],
                SEED_TIMESTAMP,
                "seed_process",
                "Creazione del ricordo di esempio della Fase 1.",
                json.dumps(row, ensure_ascii=False, indent=2),
            ),
        )

    for link in MEMORY_LINKS:
        conn.execute(
            """
            INSERT OR IGNORE INTO memory_links (
                from_memory_id, to_memory_id, relation_type,
                strength, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                link["from_memory_id"],
                link["to_memory_id"],
                link["relation_type"],
                link["strength"],
                link.get("note"),
                SEED_TIMESTAMP,
            ),
        )

    conn.commit()


def insert_shadow_entries(conn: sqlite3.Connection) -> None:
    placeholders = ", ".join("?" for _ in SHADOW_COLUMNS)
    columns = ", ".join(SHADOW_COLUMNS)
    sql = f"INSERT OR IGNORE INTO shadow_entries ({columns}) VALUES ({placeholders})"

    for entry in SHADOW_ENTRIES:
        row = shadow_row(entry)
        conn.execute(sql, tuple(row[column] for column in SHADOW_COLUMNS))
        conn.execute(
            """
            INSERT OR IGNORE INTO shadow_revisions (
                entry_id, revision_number, changed_at, changed_by,
                change_reason, previous_snapshot_json, new_snapshot_json, cascade_id
            ) VALUES (?, 1, ?, ?, ?, NULL, ?, NULL)
            """,
            (
                entry["id"],
                SEED_TIMESTAMP,
                "seed_process",
                "Creazione della voce latente di esempio della Fase 1.",
                json.dumps(row, ensure_ascii=False, indent=2),
            ),
        )

    for trigger in SHADOW_TRIGGER_LOG:
        conn.execute(
            """
            INSERT OR IGNORE INTO shadow_trigger_log (
                trait_id, triggered_at, trigger_description, intensity,
                defense_mechanism, response_summary, related_entry_id, resolved
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trigger["trait_id"],
                trigger["triggered_at"],
                trigger["trigger_description"],
                trigger["intensity"],
                trigger["defense_mechanism"],
                trigger["response_summary"],
                trigger["related_entry_id"],
                trigger["resolved"],
            ),
        )

    conn.commit()


def insert_semantic(conn: sqlite3.Connection) -> None:
    placeholders = ", ".join("?" for _ in SEMANTIC_COLUMNS)
    columns = ", ".join(SEMANTIC_COLUMNS)
    sql = f"INSERT OR IGNORE INTO semantic_items ({columns}) VALUES ({placeholders})"

    for item in SEMANTIC_ITEMS:
        row = semantic_row(item)
        conn.execute(sql, tuple(row[column] for column in SEMANTIC_COLUMNS))
        conn.execute(
            """
            INSERT OR IGNORE INTO semantic_revisions (
                item_id, revision_number, changed_at, changed_by,
                change_reason, previous_snapshot_json, new_snapshot_json, cascade_id
            ) VALUES (?, 1, ?, ?, ?, NULL, ?, NULL)
            """,
            (
                item["id"],
                SEED_TIMESTAMP,
                "seed_process",
                "Creazione della conoscenza di esempio della Fase 3.",
                json.dumps(row, ensure_ascii=False, indent=2),
            ),
        )

    for link in SEMANTIC_ITEM_LINKS:
        conn.execute(
            """
            INSERT OR IGNORE INTO semantic_item_links (
                from_item_id, to_item_id, relation_type,
                strength, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                link["from_item_id"],
                link["to_item_id"],
                link["relation_type"],
                link["strength"],
                link.get("note"),
                SEED_TIMESTAMP,
            ),
        )

    for link in SEMANTIC_EPISODIC_LINKS:
        conn.execute(
            """
            INSERT OR IGNORE INTO semantic_episodic_links (
                semantic_id, memory_id, relation_type,
                strength, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                link["semantic_id"],
                link["memory_id"],
                link["relation_type"],
                link["strength"],
                link.get("note"),
                SEED_TIMESTAMP,
            ),
        )

    conn.commit()


# ---------------------------------------------------------------------------
# Verifica dei riferimenti incrociati
# ---------------------------------------------------------------------------


def validate_references(
    configs: dict[str, Any],
    timeline: sqlite3.Connection,
    shadow: sqlite3.Connection,
    semantic: sqlite3.Connection,
) -> None:
    shadow_traits = configs["shadow_traits"]
    world_history = configs["world_history"]
    social_graph = configs["social_graph"]

    memory_ids = {
        row["id"] for row in timeline.execute("SELECT id FROM episodic_memories")
    }
    trait_ids = {trait["id"] for trait in shadow_traits["traits"]}
    world_ids = {event["id"] for event in world_history["events"]}
    shadow_ids = {row["id"] for row in shadow.execute("SELECT id FROM shadow_entries")}

    for person in social_graph["people"]:
        for entry in person["interaction_history"]:
            memory_id = entry.get("memory_id")
            if memory_id is not None and memory_id not in memory_ids:
                raise ValidationError(
                    f"social_graph.{person['user_id']}: memory_id '{memory_id}' "
                    "non esiste nella timeline episodica."
                )

    for trait in shadow_traits["traits"]:
        for memory_id in trait.get("linked_memory_ids", []):
            if memory_id not in memory_ids:
                raise ValidationError(
                    f"shadow_traits.{trait['id']}: linked_memory_id '{memory_id}' "
                    "non esiste nella timeline episodica."
                )

    for trauma in shadow_traits["traumas"]:
        memory_id = trauma["linked_memory_id"]
        if memory_id not in memory_ids:
            raise ValidationError(
                f"shadow_traits.traumas.{trauma['id']}: linked_memory_id '{memory_id}' "
                "non esiste nella timeline episodica."
            )
        row = timeline.execute(
            "SELECT access_level FROM episodic_memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if trauma["repressed"] and row is not None and row["access_level"] != "repressed":
            raise ValidationError(
                f"shadow_traits.traumas.{trauma['id']}: il trauma è represso ma il ricordo "
                f"'{memory_id}' non ha access_level='repressed'."
            )

    for entry in shadow.execute(
        "SELECT id, linked_memory_id, linked_trait_id FROM shadow_entries"
    ):
        if entry["linked_memory_id"] and entry["linked_memory_id"] not in memory_ids:
            raise ValidationError(
                f"shadow_entries.{entry['id']}: linked_memory_id "
                f"'{entry['linked_memory_id']}' non esiste nella timeline episodica."
            )
        if entry["linked_trait_id"] and entry["linked_trait_id"] not in trait_ids:
            raise ValidationError(
                f"shadow_entries.{entry['id']}: linked_trait_id "
                f"'{entry['linked_trait_id']}' non esiste in shadow_traits."
            )

    for trigger in shadow.execute(
        "SELECT id, trait_id, related_entry_id FROM shadow_trigger_log"
    ):
        if trigger["trait_id"] not in trait_ids:
            raise ValidationError(
                f"shadow_trigger_log.{trigger['id']}: trait_id "
                f"'{trigger['trait_id']}' non esiste in shadow_traits."
            )
        if trigger["related_entry_id"] not in shadow_ids:
            raise ValidationError(
                f"shadow_trigger_log.{trigger['id']}: related_entry_id "
                f"'{trigger['related_entry_id']}' non esiste nel diario dell'ombra."
            )

    for row in timeline.execute(
        "SELECT id, source_ref FROM episodic_memories WHERE source_ref IS NOT NULL"
    ):
        source_ref = row["source_ref"]
        if source_ref.startswith("wh_") and source_ref not in world_ids:
            raise ValidationError(
                f"episodic_memories.{row['id']}: source_ref '{source_ref}' non esiste "
                "in world_history."
            )

    semantic_ids = {
        row["id"] for row in semantic.execute("SELECT id FROM semantic_items")
    }
    for row in semantic.execute(
        "SELECT id, previous_version_id FROM semantic_items"
    ):
        previous = row["previous_version_id"]
        if previous is not None and previous not in semantic_ids:
            raise ValidationError(
                f"semantic_items.{row['id']}: previous_version_id '{previous}' "
                "non esiste nell'archivio semantico."
            )

    for link in semantic.execute(
        "SELECT id, from_item_id, to_item_id FROM semantic_item_links"
    ):
        if link["from_item_id"] not in semantic_ids:
            raise ValidationError(
                f"semantic_item_links.{link['id']}: from_item_id "
                f"'{link['from_item_id']}' inesistente."
            )
        if link["to_item_id"] not in semantic_ids:
            raise ValidationError(
                f"semantic_item_links.{link['id']}: to_item_id "
                f"'{link['to_item_id']}' inesistente."
            )

    for link in semantic.execute(
        "SELECT id, semantic_id, memory_id FROM semantic_episodic_links"
    ):
        if link["semantic_id"] not in semantic_ids:
            raise ValidationError(
                f"semantic_episodic_links.{link['id']}: semantic_id "
                f"'{link['semantic_id']}' inesistente."
            )
        memory = timeline.execute(
            "SELECT access_level FROM episodic_memories WHERE id = ?",
            (link["memory_id"],),
        ).fetchone()
        if memory is None:
            raise ValidationError(
                f"semantic_episodic_links.{link['id']}: memory_id "
                f"'{link['memory_id']}' inesistente."
            )
        if memory["access_level"] != "conscious":
            raise ValidationError(
                f"semantic_episodic_links.{link['id']}: il ricordo "
                f"'{link['memory_id']}' non è conscio e non può essere collegato "
                "dalla memoria semantica."
            )


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def remove_database_files() -> None:
    for path in [TIMELINE_DB, SHADOW_DB, SEMANTIC_DB, VECTOR_DB]:
        for candidate in [
            path,
            Path(f"{path}-wal"),
            Path(f"{path}-shm"),
            Path(f"{path}-journal"),
        ]:
            if candidate.exists():
                candidate.unlink()
    if INDEX_DIR.exists():
        for candidate in INDEX_DIR.glob("*"):
            if candidate.is_file():
                candidate.unlink()


def open_database(path: Path, schema_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap dei database della memoria (Fasi 1 e 3)."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Elimina e ricrea i database e gli indici in data/ prima di "
            "inserire i dati. Il modello di embedding in data/models non viene toccato."
        ),
    )
    args = parser.parse_args()

    try:
        configs = load_and_validate_configs()
        print("Validazione JSON: OK")

        if args.reset:
            remove_database_files()

        DATA_DIR.mkdir(parents=True, exist_ok=True)

        timeline = open_database(TIMELINE_DB, SCHEMA_DIR / "timeline_episodic.sql")
        shadow = open_database(SHADOW_DB, SCHEMA_DIR / "shadow_diary.sql")
        semantic = open_database(
            SEMANTIC_DB, SCHEMA_DIR / "semantic_memory.sql"
        )
        vector = open_database(VECTOR_DB, SCHEMA_DIR / "vector_index.sql")

        try:
            insert_memories(timeline)
            insert_shadow_entries(shadow)
            insert_semantic(semantic)
            validate_references(configs, timeline, shadow, semantic)

            checks = {
                "timeline": timeline.execute("PRAGMA integrity_check").fetchone()[0],
                "shadow": shadow.execute("PRAGMA integrity_check").fetchone()[0],
                "semantic": semantic.execute("PRAGMA integrity_check").fetchone()[0],
                "vector": vector.execute("PRAGMA integrity_check").fetchone()[0],
            }
            if any(value != "ok" for value in checks.values()):
                raise ValidationError(
                    "Integrity check fallita: "
                    + ", ".join(f"{name}={value}" for name, value in checks.items())
                )

            print("Validazione riferimenti: OK")
            print(
                "timeline_episodic.db: "
                f"{table_count(timeline, 'episodic_memories')} ricordi, "
                f"{table_count(timeline, 'memory_links')} collegamenti, "
                f"{table_count(timeline, 'memory_revisions')} revisioni"
            )
            print(
                "shadow_diary.db: "
                f"{table_count(shadow, 'shadow_entries')} voci, "
                f"{table_count(shadow, 'shadow_revisions')} revisioni, "
                f"{table_count(shadow, 'shadow_trigger_log')} trigger"
            )
            semantic_links = table_count(
                semantic, "semantic_item_links"
            ) + table_count(semantic, "semantic_episodic_links")
            print(
                "semantic_memory.db: "
                f"{table_count(semantic, 'semantic_items')} conoscenze, "
                f"{table_count(semantic, 'semantic_revisions')} revisioni, "
                f"{semantic_links} collegamenti"
            )
            print(
                "vector_index.db: pronto "
                f"({table_count(vector, 'vector_entries')} mapping)"
            )
            print(
                "Embedding: alla prima installazione esegui "
                "`python -m agent_memory.index --rebuild`; per aggiornamenti usa "
                "`python -m agent_memory.index --sync`."
            )
        finally:
            timeline.close()
            shadow.close()
            semantic.close()
            vector.close()

        return 0
    except ValidationError as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"ERRORE SQLite: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERRORE file: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
