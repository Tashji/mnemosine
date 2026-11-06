"""Seed esterni, evoluzione caratteriale e riconsolidamento a cascata.

La Fase 5 introduce un percorso amministrativo deterministico:

* un seed viene inserito nella timeline come ricordo ``admin_seed``;
* le regole configurate in ``identity.personality.evolution`` aggiornano i
  tratti psicologici in base a intensità, valenza e tag del seed;
* tutti i ricordi simulati successivi vengono riconsolidati alla luce del
  nuovo evento, con una revisione tracciabile per ogni modifica;
* le conoscenze semantiche collegate vengono annotate, ma i fatti verificati e
  i contenuti forniti dall'utente restano intatti.

Il modulo non chiama servizi LLM. Il riconsolidamento usa un rewriter
deterministico, sostituibile tramite il protocollo ``MemoryRewriter`` quando
sarà disponibile un provider linguistico.

Uso:
    python -m agent_memory.evolution --seed seed.json
    python -m agent_memory.evolution --seed seed.json --apply
    python -m agent_memory.evolution --seed seed.json --json
    python -m agent_memory.evolution --history
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import ConfigStore, now_iso
from .db import decode_memory
from .errors import (
    AgentMemoryError,
    ConfigError,
    ConflictError,
    ValidationError,
)
from .retrieval import RetrievalService
from .semantic import SemanticService
from .service import (
    MemoryService,
    _insert_episodic,
    _object,
    _optional_str,
    _require_bool,
    _require_datetime,
    _require_int,
    _require_number,
    _require_str,
    _string_list,
    _update_episodic,
)

EVOLUTION_VERSION = "v1"
HISTORY_FILENAME = "personality_history.json"
DEFAULT_MAX_MEMORIES = 200
DEFAULT_MAX_TRAIT_DELTA = 2
MUTABLE_KNOWLEDGE_TYPES = {"opinion", "hypothesis", "uncertain"}
PROTECTED_KNOWLEDGE_TYPES = {"verified_fact", "user_provided"}
SEED_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _slug(value: str) -> str:
    cleaned = "".join(char if char.isalnum() else "_" for char in value.lower())
    return "_".join(part for part in cleaned.split("_") if part) or "seed"


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _tone_for(seed: Seed) -> str:
    if seed.valence <= -0.3:
        return "un peso più amaro"
    if seed.valence >= 0.3:
        return "una luce più chiara"
    return "un significato più incerto"


def _metric(
    mapping: dict[str, Any], key: str, default: float, low: float, high: float
) -> float:
    value = mapping.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"emotional_state.{key}: atteso un numero.")
    return _clamp(float(value), low, high)


@dataclass(frozen=True)
class Seed:
    """Evento amministrativo che innesca il riconsolidamento."""

    seed_id: str
    event_time: str
    title: str
    description: str
    importance: int
    place: str | None
    people: tuple[str, ...]
    tags: tuple[str, ...]
    emotional_state: dict[str, Any]
    consequences: str | None
    is_simulated: bool
    trait_impacts: dict[str, float]
    max_memories: int
    memory_id: str
    cascade_id: str

    @property
    def event_date(self) -> str:
        return self.event_time[:10]

    @property
    def valence(self) -> float:
        return _metric(self.emotional_state, "valence", 0.0, -1.0, 1.0)

    @property
    def arousal(self) -> float:
        return _metric(self.emotional_state, "arousal", 0.5, 0.0, 1.0)

    @property
    def intensity(self) -> int:
        value = self.emotional_state.get("intensity", self.importance)
        return _require_int(value, "emotional_state.intensity", 1, 10)

    @property
    def source_ref(self) -> str:
        return f"seed:{self.seed_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_id": self.seed_id,
            "memory_id": self.memory_id,
            "cascade_id": self.cascade_id,
            "source_ref": self.source_ref,
            "event_time": self.event_time,
            "event_date": self.event_date,
            "title": self.title,
            "description": self.description,
            "importance": self.importance,
            "place": self.place,
            "people": list(self.people),
            "tags": list(self.tags),
            "emotional_state": copy.deepcopy(self.emotional_state),
            "consequences": self.consequences,
            "is_simulated": self.is_simulated,
            "trait_impacts": dict(self.trait_impacts),
            "max_memories": self.max_memories,
        }


class MemoryRewriter(Protocol):
    """Interfaccia per un motore di riscrittura deterministico o LLM."""

    def rewrite(
        self,
        seed: dict[str, Any],
        memory: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Restituisce un patch episodico limitato ai campi modificabili."""
        ...


class RuleBasedRewriter:
    """Riscrittura deterministica dei ricordi simulati successivi al seed."""

    _TEMPLATES = (
        "Rileggendo questo ricordo dopo «{title}», lo sento attraversato da {tone}.",
        "Dopo «{title}», questo episodio ha assunto {tone}: non riesco a separarlo da quello che è successo.",
        "La memoria di «{title}» è entrata in questo ricordo e gli ha dato {tone}.",
    )

    def rewrite(
        self,
        seed: dict[str, Any],
        memory: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        seed_obj = context["seed_object"]
        tone = _tone_for(seed_obj)
        digest = hashlib.sha256(
            f"{seed_obj.cascade_id}:{memory['id']}".encode()
        ).hexdigest()
        template = self._TEMPLATES[int(digest[:8], 16) % len(self._TEMPLATES)]
        addition = template.format(title=seed_obj.title, tone=tone)

        description = str(memory.get("description", "")).rstrip()
        if description and not description.endswith((".", "!", "?")):
            description += "."
        description = f"{description} {addition}".strip()

        consequences = memory.get("consequences")
        consequence_addition = (
            f"Il riconsolidamento legato a «{seed_obj.title}» ha aggiunto "
            f"{tone} al modo in cui leggo questo episodio."
        )
        if consequences:
            consequences = f"{str(consequences).rstrip()} {consequence_addition}"
        else:
            consequences = consequence_addition

        emotional = dict(memory.get("emotional_state") or {})
        old_valence = _metric(emotional, "valence", 0.0, -1.0, 1.0)
        old_arousal = _metric(emotional, "arousal", 0.5, 0.0, 1.0)
        emotional["valence"] = round(
            _clamp(old_valence + (seed_obj.valence - old_valence) * 0.2, -1.0, 1.0),
            2,
        )
        emotional["arousal"] = round(
            _clamp(old_arousal + (seed_obj.arousal - old_arousal) * 0.15, 0.0, 1.0),
            2,
        )
        old_intensity = _require_int(
            emotional.get("intensity", 5), "emotional_state.intensity", 1, 10
        )
        emotional["intensity"] = max(
            1, min(10, old_intensity + (1 if seed_obj.intensity >= 7 else 0))
        )
        if not emotional.get("label"):
            emotional["label"] = "riconsolidato"

        tags = _unique(list(memory.get("tags") or []) + ["riconsolidato"])
        return {
            "description": description,
            "consequences": consequences,
            "emotional_state": emotional,
            "tags": tags,
            "is_consolidated": True,
        }


def _parse_trait_impacts(value: Any) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError("trait_impacts: atteso un oggetto JSON.")
    result: dict[str, float] = {}
    for trait_id, delta in value.items():
        if not isinstance(trait_id, str) or not trait_id:
            raise ValidationError("trait_impacts: chiave non valida.")
        if isinstance(delta, bool) or not isinstance(delta, (int, float)):
            raise ValidationError(
                f"trait_impacts.{trait_id}: atteso un numero."
            )
        number = float(delta)
        if not -3.0 <= number <= 3.0:
            raise ValidationError(
                f"trait_impacts.{trait_id}: valore {delta} fuori intervallo [-3, 3]."
            )
        result[trait_id] = number
    return result


def _parse_seed(
    data: dict[str, Any],
    identity: dict[str, Any],
    as_of: date,
) -> Seed:
    if not isinstance(data, dict):
        raise ValidationError("Il seed deve essere un oggetto JSON.")
    seed_id = _require_str(data.get("seed_id"), "seed_id")
    if SEED_ID_PATTERN.fullmatch(seed_id) is None:
        raise ValidationError(
            "seed_id: ammessi lettere, numeri, punto, trattino e underscore."
        )
    event_time = _require_datetime(data.get("event_time"), "event_time")
    birth_date = date.fromisoformat(identity["personal_data"]["birth_date"])
    event_date = date.fromisoformat(event_time[:10])
    if event_date < birth_date:
        raise ValidationError("event_time: il seed precede la nascita dell'agente.")
    if event_date > as_of:
        raise ValidationError("event_time: il seed deve essere collocato nel passato.")

    title = _require_str(data.get("title"), "title")
    description = _require_str(data.get("description"), "description")
    importance = _require_int(data.get("importance", 7), "importance", 1, 10)
    place = _optional_str(data.get("place"), "place")
    people = _string_list(data.get("people"), "people")
    tags = _string_list(data.get("tags"), "tags")
    emotional_state = _object(data.get("emotional_state"), "emotional_state")
    if "valence" not in emotional_state:
        emotional_state["valence"] = 0.0
    if "arousal" not in emotional_state:
        emotional_state["arousal"] = 0.5
    if "intensity" not in emotional_state:
        emotional_state["intensity"] = importance
    if "label" not in emotional_state:
        emotional_state["label"] = "evento significativo"
    consequences = _optional_str(data.get("consequences"), "consequences")
    is_simulated = _require_bool(
        data.get("is_simulated", False), "is_simulated"
    )

    cascade_options = data.get("cascade", {})
    if cascade_options is None:
        cascade_options = {}
    if not isinstance(cascade_options, dict):
        raise ValidationError("cascade: atteso un oggetto JSON.")
    max_memories = _require_int(
        cascade_options.get("max_memories", DEFAULT_MAX_MEMORIES),
        "cascade.max_memories",
        1,
        1000,
    )
    trait_impacts = _parse_trait_impacts(data.get("trait_impacts"))

    default_slug = _slug(seed_id)
    memory_id = data.get("memory_id") or f"seed_{default_slug}"
    memory_id = _require_str(memory_id, "memory_id")
    cascade_id = data.get("cascade_id") or f"cascade_{default_slug}"
    cascade_id = _require_str(cascade_id, "cascade_id")

    return Seed(
        seed_id=seed_id,
        event_time=event_time,
        title=title,
        description=description,
        importance=importance,
        place=place,
        people=tuple(people),
        tags=tuple(tags),
        emotional_state=emotional_state,
        consequences=consequences,
        is_simulated=is_simulated,
        trait_impacts=trait_impacts,
        max_memories=max_memories,
        memory_id=memory_id,
        cascade_id=cascade_id,
    )


class EvolutionService:
    """Superficie amministrativa per seed e riconsolidamento."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        as_of: date | None = None,
        rewriter: MemoryRewriter | None = None,
        retrieval: RetrievalService | None = None,
    ):
        self.config = ConfigStore(root)
        self.root = self.config.root
        self.memory = MemoryService(self.root)
        self.semantic = SemanticService(self.root)
        self.retrieval = retrieval or RetrievalService(self.root)
        self.rewriter = rewriter or RuleBasedRewriter()
        self.identity = self.config.get("identity")
        self.as_of = as_of or date.today()
        self.birth_date = date.fromisoformat(
            self.identity["personal_data"]["birth_date"]
        )
        timezone_name = self.identity["personal_data"].get(
            "timezone", "Europe/Rome"
        )
        try:
            self.timezone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            self.timezone = None

    # -- seed e configurazione ----------------------------------------------

    def load_seed(self, path: str | Path) -> Seed:
        path = Path(path).expanduser()
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError as exc:
            raise ValidationError(f"Seed non trovato: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ValidationError(
                f"Seed JSON non valido: riga {exc.lineno}, colonna {exc.colno}."
            ) from exc
        except OSError as exc:
            raise ValidationError(f"Impossibile leggere il seed: {exc}") from exc
        return _parse_seed(data, self.identity, self.as_of)

    @property
    def history_path(self) -> Path:
        return self.root / "config" / HISTORY_FILENAME

    def history(self) -> dict[str, Any]:
        if not self.history_path.exists():
            return {
                "schema_version": "1.0",
                "identity_id": self.identity["identity_id"],
                "changes": [],
            }
        try:
            data = json.loads(self.history_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"Storico della personalità non leggibile: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("changes"), list):
            raise ConfigError("personality_history.json non ha una lista 'changes'.")
        return data

    def _trait_rules(self) -> tuple[list[dict[str, Any]], int]:
        evolution = self.identity.get("personality", {}).get("evolution", {})
        if not isinstance(evolution, dict):
            raise ConfigError("identity.personality.evolution deve essere un oggetto.")
        rules = evolution.get("rules", [])
        if not isinstance(rules, list):
            raise ConfigError("identity.personality.evolution.rules deve essere una lista.")
        max_total = evolution.get(
            "max_total_delta_per_seed", DEFAULT_MAX_TRAIT_DELTA
        )
        max_total = _require_number(
            max_total, "evolution.max_total_delta_per_seed", 0.0, 10.0
        )
        return rules, int(max_total)

    def _rule_matches(self, rule: dict[str, Any], seed: Seed) -> bool:
        if not isinstance(rule, dict):
            raise ConfigError("Ogni regola di evoluzione deve essere un oggetto.")
        intensity = seed.intensity
        if "min_intensity" in rule:
            minimum = _require_int(rule["min_intensity"], "min_intensity", 1, 10)
            if intensity < minimum:
                return False
        if "max_valence" in rule:
            maximum = _require_number(rule["max_valence"], "max_valence", -1.0, 1.0)
            if seed.valence > maximum:
                return False
        if "min_valence" in rule:
            minimum = _require_number(rule["min_valence"], "min_valence", -1.0, 1.0)
            if seed.valence < minimum:
                return False
        tags_any = rule.get("tags_any")
        if tags_any is not None:
            tags = _string_list(tags_any, "tags_any")
            seed_tags = {tag.lower() for tag in seed.tags}
            if not any(tag.lower() in seed_tags for tag in tags):
                return False
        return True

    def _plan_trait_changes(self, seed: Seed) -> list[dict[str, Any]]:
        traits = self.identity.get("personality", {}).get("traits", {})
        if not isinstance(traits, dict):
            raise ConfigError("identity.personality.traits deve essere un oggetto.")
        rules, max_total = self._trait_rules()

        raw: dict[str, float] = defaultdict(float)
        applied_rules: dict[str, list[str]] = defaultdict(list)
        for rule in rules:
            if not self._rule_matches(rule, seed):
                continue
            impacts = rule.get("impacts")
            if not isinstance(impacts, dict) or not impacts:
                raise ConfigError(
                    f"Regola di evoluzione senza impacts: {rule.get('id', '?')}."
                )
            rule_id = str(rule.get("id", "rule"))
            for trait_id, delta in impacts.items():
                if trait_id not in traits:
                    raise ConfigError(
                        f"Regola {rule_id}: tratto sconosciuto '{trait_id}'."
                    )
                if isinstance(delta, bool) or not isinstance(delta, (int, float)):
                    raise ConfigError(
                        f"Regola {rule_id}.{trait_id}: delta non numerico."
                    )
                raw[trait_id] += float(delta)
                applied_rules[trait_id].append(rule_id)

        for trait_id, delta in seed.trait_impacts.items():
            if trait_id not in traits:
                raise ValidationError(
                    f"trait_impacts: tratto sconosciuto '{trait_id}'."
                )
            raw[trait_id] += delta
            applied_rules[trait_id].append("seed")

        changes: list[dict[str, Any]] = []
        remaining = float(max_total)
        for trait_id, trait in traits.items():
            if remaining <= 0:
                break
            delta = raw.get(trait_id, 0.0)
            if abs(delta) < 1e-9:
                continue
            old_value = _require_int(trait.get("value"), f"{trait_id}.value", 1, 10)
            allowed = _clamp(delta, -remaining, remaining)
            new_value = int(_clamp(round(old_value + allowed), 1, 10))
            actual_delta = new_value - old_value
            if actual_delta == 0:
                continue
            changes.append(
                {
                    "trait_id": trait_id,
                    "old_value": old_value,
                    "new_value": new_value,
                    "delta": actual_delta,
                    "rules": list(dict.fromkeys(applied_rules[trait_id])),
                }
            )
            remaining -= abs(actual_delta)
        return changes

    def _apply_traits(
        self, seed: Seed, changes: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], bool]:
        history = self.history()
        if any(
            entry.get("cascade_id") == seed.cascade_id
            for entry in history["changes"]
            if isinstance(entry, dict)
        ):
            return [], True
        if not changes:
            return [], False

        patch = {
            "personality": {
                "traits": {
                    change["trait_id"]: {"value": change["new_value"]}
                    for change in changes
                }
            }
        }
        updated_identity = self.config.update_identity(patch)
        self.identity = updated_identity
        timestamp = now_iso(
            self.identity["personal_data"].get("timezone", "Europe/Rome")
        )
        history["changes"].append(
            {
                "cascade_id": seed.cascade_id,
                "seed_id": seed.seed_id,
                "seed_memory_id": seed.memory_id,
                "changed_at": timestamp,
                "changed_by": "evolution_service",
                "reason": f"Riconsolidamento del seed '{seed.seed_id}'.",
                "trait_changes": copy.deepcopy(changes),
            }
        )
        history["updated_at"] = timestamp
        ConfigStore._atomic_write(self.history_path, history)
        return changes, False

    # -- selezione dei ricordi ----------------------------------------------

    def _seed_row(self, seed: Seed) -> Any | None:
        with self.memory.db.timeline() as conn:
            return conn.execute(
                "SELECT * FROM episodic_memories WHERE source_ref = ? OR id = ?",
                (seed.source_ref, seed.memory_id),
            ).fetchone()

    def _assert_same_seed(self, row: Any, seed: Seed) -> None:
        if row["source"] != "admin_seed":
            raise ConflictError(
                f"L'ID '{seed.memory_id}' è già usato da un ricordo non amministrativo."
            )
        if (
            row["event_time"] != seed.event_time
            or row["title"] != seed.title
            or row["description"] != seed.description
        ):
            raise ConflictError(
                f"Il seed '{seed.seed_id}' esiste già con un contenuto diverso."
            )

    def _affected_rows(self, conn: Any, seed: Seed) -> tuple[list[Any], bool]:
        rows = conn.execute(
            """
            SELECT * FROM conscious_episodic_memories
            WHERE event_time > ?
              AND is_simulated = 1
              AND id <> ?
            ORDER BY event_time ASC, id ASC
            LIMIT ?
            """,
            (seed.event_time, seed.memory_id, seed.max_memories + 1),
        ).fetchall()
        truncated = len(rows) > seed.max_memories
        return list(rows[: seed.max_memories]), truncated

    def _has_cascade_revision(self, conn: Any, memory_id: str, cascade_id: str) -> bool:
        return (
            conn.execute(
                """
                SELECT 1 FROM memory_revisions
                WHERE memory_id = ? AND cascade_id = ?
                LIMIT 1
                """,
                (memory_id, cascade_id),
            ).fetchone()
            is not None
        )

    # -- conoscenze collegate ------------------------------------------------

    def _semantic_candidates(
        self, seed: Seed, memory_ids: list[str]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        warnings: list[str] = []
        ids: set[str] = set()
        if memory_ids:
            placeholders = ", ".join("?" for _ in memory_ids)
            with self.semantic.db.semantic() as conn:
                rows = conn.execute(
                    f"""
                    SELECT DISTINCT semantic_id FROM semantic_episodic_links
                    WHERE memory_id IN ({placeholders})
                    """,
                    tuple(memory_ids),
                ).fetchall()
            ids.update(row["semantic_id"] for row in rows)

        query = f"{seed.title}. {seed.description}"
        semantic_ready = False
        try:
            status = self.retrieval.index.status()
            semantic_ready = bool(
                status.get("indexes", {}).get("semantic", {}).get("ok")
            )
        except AgentMemoryError as exc:
            warnings.append(f"Stato dell'indice non disponibile: {exc}")
        try:
            result = self.retrieval.hybrid_search(
                query,
                mode="hybrid" if semantic_ready else "lexical",
                include_episodic=False,
                include_semantic=True,
                limit=12,
                min_similarity=0.0,
            )
            for entry in result.get("results", []):
                if entry.get("kind") == "semantic":
                    ids.add(str(entry["record_id"]))
            warnings.extend(str(warning) for warning in result.get("warnings", []))
            if not semantic_ready:
                warnings.append(
                    "Indice vettoriale non pronto: usata la ricerca lessicale "
                    "per le conoscenze collegate."
                )
        except AgentMemoryError as exc:
            warnings.append(f"Ricerca semantica non disponibile: {exc}")

        items: list[dict[str, Any]] = []
        for item_id in sorted(ids):
            item = self.semantic.get_item(item_id)
            if item is not None:
                items.append(item)
        return items, warnings

    def _semantic_plan(
        self, seed: Seed, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        plan: list[dict[str, Any]] = []
        for item in items:
            metadata = item.get("metadata") or {}
            reflections = metadata.get("cascade_reflections")
            already = isinstance(reflections, list) and any(
                isinstance(entry, dict)
                and entry.get("cascade_id") == seed.cascade_id
                for entry in reflections
            )
            knowledge_type = item.get("knowledge_type")
            if knowledge_type in PROTECTED_KNOWLEDGE_TYPES:
                action = "protected"
            elif already:
                action = "already_reflected"
            elif knowledge_type in MUTABLE_KNOWLEDGE_TYPES:
                action = "reflect"
            else:
                action = "ignored"
            plan.append(
                {
                    "item_id": item["id"],
                    "title": item.get("title"),
                    "knowledge_type": knowledge_type,
                    "action": action,
                }
            )
        return plan

    def _reflect_semantics(
        self,
        seed: Seed,
        items: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        plan = self._semantic_plan(seed, items)
        plan_by_id = {entry["item_id"]: entry for entry in plan}
        updated: list[dict[str, Any]] = []
        warnings: list[str] = []
        timestamp = now_iso(
            self.identity["personal_data"].get("timezone", "Europe/Rome")
        )
        note = (
            f"Riconsolidamento dopo «{seed.title}»: il ricordo personale "
            f"ha assunto {_tone_for(seed)}."
        )
        for item in items:
            entry = plan_by_id[item["id"]]
            if entry["action"] != "reflect":
                continue
            metadata = copy.deepcopy(item.get("metadata") or {})
            reflections = metadata.get("cascade_reflections")
            if not isinstance(reflections, list):
                reflections = []
            reflections.append(
                {
                    "cascade_id": seed.cascade_id,
                    "seed_id": seed.seed_id,
                    "seed_memory_id": seed.memory_id,
                    "reflected_at": timestamp,
                    "note": note,
                }
            )
            metadata["cascade_reflections"] = reflections
            try:
                updated_item = self.semantic.update_item(
                    item["id"],
                    {"metadata": metadata},
                    changed_by="evolution_service",
                    change_reason=f"Riconsolidamento del seed '{seed.seed_id}'.",
                    cascade_id=seed.cascade_id,
                )
            except AgentMemoryError as exc:
                warnings.append(f"Conoscenza {item['id']} non aggiornata: {exc}")
                continue
            updated.append(
                {
                    "item_id": updated_item["id"],
                    "title": updated_item["title"],
                    "version": updated_item["version"],
                }
            )
        return plan, updated, warnings

    # -- piano e applicazione -----------------------------------------------

    def preview(self, seed: Seed) -> dict[str, Any]:
        trait_changes = self._plan_trait_changes(seed)
        with self.memory.db.timeline() as conn:
            seed_row = conn.execute(
                "SELECT * FROM episodic_memories WHERE source_ref = ? OR id = ?",
                (seed.source_ref, seed.memory_id),
            ).fetchone()
            if seed_row is not None:
                self._assert_same_seed(seed_row, seed)
            seed_exists = seed_row is not None
            affected, truncated = self._affected_rows(conn, seed)

        warnings: list[str] = []
        if truncated:
            warnings.append(
                f"La cascata è limitata a {seed.max_memories} ricordi; "
                "aumenta cascade.max_memories per includerli tutti."
            )
        memory_ids = [row["id"] for row in affected]
        semantic_items, semantic_warnings = self._semantic_candidates(seed, memory_ids)
        warnings.extend(semantic_warnings)
        semantic_plan = self._semantic_plan(seed, semantic_items)

        affected_preview: list[dict[str, Any]] = []
        with self.memory.db.timeline() as conn:
            for affected_row in affected:
                memory = decode_memory(affected_row)
                already = self._has_cascade_revision(conn, memory["id"], seed.cascade_id)
                patch = (
                    {}
                    if already
                    else self.rewriter.rewrite(
                        seed.to_dict(),
                        memory,
                        {
                            "seed_object": seed,
                            "cascade_id": seed.cascade_id,
                            "trait_changes": trait_changes,
                            "related_semantic_items": semantic_items,
                        },
                    )
                )
                affected_preview.append(
                    {
                        "memory_id": memory["id"],
                        "event_date": memory["event_date"],
                        "title": memory["title"],
                        "already_cascaded": already,
                        "changes": patch,
                    }
                )

        return {
            "ok": True,
            "mode": "preview",
            "evolution_version": EVOLUTION_VERSION,
            "seed": seed.to_dict(),
            "seed_exists": seed_exists,
            "trait_changes": trait_changes,
            "affected_memories": affected_preview,
            "semantic_items": semantic_plan,
            "counts": {
                "affected_memories": len(affected_preview),
                "already_cascaded": sum(
                    1 for item in affected_preview if item["already_cascaded"]
                ),
                "trait_changes": len(trait_changes),
                "semantic_reflections": sum(
                    1 for item in semantic_plan if item["action"] == "reflect"
                ),
                "protected_semantic_items": sum(
                    1 for item in semantic_plan if item["action"] == "protected"
                ),
            },
            "warnings": warnings,
        }

    def apply(self, seed: Seed, *, reindex: bool = True) -> dict[str, Any]:
        warnings: list[str] = []
        created_seed = False
        timestamp = now_iso(
            self.identity["personal_data"].get("timezone", "Europe/Rome")
        )

        with self.memory.db.timeline() as conn:
            row = conn.execute(
                "SELECT * FROM episodic_memories WHERE source_ref = ? OR id = ?",
                (seed.source_ref, seed.memory_id),
            ).fetchone()
            if row is None:
                seed_memory = _insert_episodic(
                    conn,
                    event_time=seed.event_time,
                    title=seed.title,
                    description=seed.description,
                    importance=seed.importance,
                    source="admin_seed",
                    place=seed.place,
                    people=list(seed.people),
                    emotional_state=seed.emotional_state,
                    consequences=seed.consequences,
                    source_ref=seed.source_ref,
                    reliability=1.0,
                    is_simulated=seed.is_simulated,
                    access_level="conscious",
                    is_consolidated=True,
                    tags=list(seed.tags),
                    memory_id=seed.memory_id,
                    timestamp=timestamp,
                    changed_by="evolution_service",
                    change_reason=f"Seed esterno '{seed.seed_id}'.",
                    cascade_id=seed.cascade_id,
                )
                created_seed = True
            else:
                self._assert_same_seed(row, seed)
                seed_memory = decode_memory(row)

            affected, truncated = self._affected_rows(conn, seed)
            if truncated:
                warnings.append(
                    f"La cascata è limitata a {seed.max_memories} ricordi; "
                    "aumenta cascade.max_memories per includerli tutti."
                )

            trait_changes = self._plan_trait_changes(seed)
            memory_ids = [item["id"] for item in affected]
            semantic_items, semantic_warnings = self._semantic_candidates(
                seed, memory_ids
            )
            warnings.extend(semantic_warnings)

            updated_memories: list[dict[str, Any]] = []
            skipped_memories: list[dict[str, str]] = []
            for affected_row in affected:
                memory = decode_memory(affected_row)
                if self._has_cascade_revision(conn, memory["id"], seed.cascade_id):
                    skipped_memories.append(
                        {"memory_id": memory["id"], "reason": "già riconsolidato"}
                    )
                    continue
                patch = self.rewriter.rewrite(
                    seed.to_dict(),
                    memory,
                    {
                        "seed_object": seed,
                        "cascade_id": seed.cascade_id,
                        "trait_changes": trait_changes,
                        "related_semantic_items": semantic_items,
                    },
                )
                updated = _update_episodic(
                    conn,
                    memory["id"],
                    patch,
                    timestamp=timestamp,
                    changed_by="evolution_service",
                    change_reason=f"Riconsolidamento del seed '{seed.seed_id}'.",
                    cascade_id=seed.cascade_id,
                )
                updated_memories.append(
                    {
                        "memory_id": updated["id"],
                        "title": updated["title"],
                        "version": updated["version"],
                    }
                )

        applied_traits, traits_already_applied = self._apply_traits(seed, trait_changes)
        semantic_plan, reflected_semantics, semantic_warnings = self._reflect_semantics(
            seed, semantic_items
        )
        warnings.extend(semantic_warnings)

        reindexed: list[dict[str, Any]] = []
        if reindex:
            targets = [("episodic", seed_memory["id"])] + [
                ("episodic", memory["memory_id"]) for memory in updated_memories
            ] + [("semantic", item["item_id"]) for item in reflected_semantics]
            for kind, record_id in targets:
                result = self.retrieval.index_if_ready(kind, record_id)
                status = result.get("status")
                if status in {"indexed", "updated", "unchanged"}:
                    reindexed.append(
                        {"record_id": record_id, "kind": kind, "status": status}
                    )
                elif result.get("reason"):
                    warnings.append(
                        f"Indice {kind} non aggiornato per {record_id}: {result['reason']}"
                    )

        return {
            "ok": True,
            "mode": "apply",
            "evolution_version": EVOLUTION_VERSION,
            "seed": seed.to_dict(),
            "cascade_id": seed.cascade_id,
            "seed_memory": seed_memory,
            "created_seed": created_seed,
            "trait_changes": trait_changes,
            "trait_changes_applied": applied_traits,
            "traits_already_applied": traits_already_applied,
            "updated_memories": updated_memories,
            "skipped_memories": skipped_memories,
            "semantic_items": semantic_plan,
            "semantic_reflections": reflected_semantics,
            "reindexed": reindexed,
            "counts": {
                "updated_memories": len(updated_memories),
                "skipped_memories": len(skipped_memories),
                "trait_changes": len(applied_traits),
                "semantic_reflections": len(reflected_semantics),
                "protected_semantic_items": sum(
                    1 for item in semantic_plan if item["action"] == "protected"
                ),
            },
            "warnings": warnings,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Data non valida: {value!r}. Usa YYYY-MM-DD."
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inserisce un seed e riconsolida la memoria della Fase 5."
    )
    parser.add_argument(
        "--seed",
        type=Path,
        default=None,
        help="File JSON del seed da inserire.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory del progetto (default: quella che contiene il pacchetto).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Applica seed, tratti e cascata. Senza questo flag mostra solo il piano.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Stampa il piano o il risultato in JSON.",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="Stampa lo storico delle modifiche ai tratti psicologici ed esce.",
    )
    parser.add_argument(
        "--as-of",
        type=_parse_date,
        default=None,
        help="Data di riferimento per validare il seed (default: oggi).",
    )
    parser.add_argument(
        "--no-reindex",
        action="store_true",
        help="Non aggiornare l'indice vettoriale dopo il riconsolidamento.",
    )
    return parser


def _print_preview(result: dict[str, Any]) -> None:
    seed = result["seed"]
    print(
        f"Seed {seed['seed_id']}: {seed['title']} ({seed['event_date']}), "
        f"cascata {seed['cascade_id']}."
    )
    print(
        f"Ricordi simulati successivi: {result['counts']['affected_memories']} "
        f"({result['counts']['already_cascaded']} già riconsolidati)."
    )
    if result["trait_changes"]:
        print("Cambiamenti dei tratti:")
        for change in result["trait_changes"]:
            print(
                f"  {change['trait_id']}: {change['old_value']} -> "
                f"{change['new_value']} ({change['delta']:+g})"
            )
    else:
        print("Nessun cambiamento dei tratti.")
    print("Ricordi:")
    for memory in result["affected_memories"]:
        suffix = " [già riconsolidato]" if memory["already_cascaded"] else ""
        print(f"  {memory['event_date']} {memory['memory_id']}: {memory['title']}{suffix}")
    reflect = [item for item in result["semantic_items"] if item["action"] == "reflect"]
    protected = [
        item for item in result["semantic_items"] if item["action"] == "protected"
    ]
    print(f"Conoscenze da annotare: {len(reflect)}; protette: {len(protected)}.")
    for warning in result["warnings"]:
        print(f"Avviso: {warning}")


def _print_apply(result: dict[str, Any]) -> None:
    print(
        f"Seed {result['seed']['seed_id']} applicato con la cascata "
        f"{result['cascade_id']}."
    )
    print(
        f"Seed {'creato' if result['created_seed'] else 'già presente'}; "
        f"ricordi aggiornati: {result['counts']['updated_memories']}; "
        f"saltati: {result['counts']['skipped_memories']}."
    )
    print(f"Tratti modificati: {result['counts']['trait_changes']}.")
    print(f"Conoscenze annotate: {result['counts']['semantic_reflections']}.")
    print(f"Ricordi reindicizzati: {len(result['reindexed'])}.")
    for warning in result["warnings"]:
        print(f"Avviso: {warning}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        service = EvolutionService(args.root, as_of=args.as_of)
        if args.history:
            print(json.dumps(service.history(), ensure_ascii=False, indent=2))
            return 0
        if args.seed is None:
            parser.error("--seed è obbligatorio, oppure usa --history.")
        seed = service.load_seed(args.seed)
        if args.apply:
            result = service.apply(seed, reindex=not args.no_reindex)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                _print_apply(result)
        else:
            result = service.preview(seed)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                _print_preview(result)
        return 0
    except AgentMemoryError as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
