"""Registro progressivo delle persone incontrate.

Le relazioni fidate vivono in ``social_graph.json``; qui si accumulano i
profili parziali di chi è stato incontrato senza entrare nel grafo. Ogni
informazione nasce come *candidato* e solo una conferma esplicita la rende
un fatto. I dati sensibili restano candidati finché l'amministratore non
li conferma dalla dashboard.

Il file è separato dagli altri documenti di configurazione: un profilo
creato prima di questa funzione non ha ``acquaintances.json`` e il
registro viene creato al primo utilizzo.
"""

from __future__ import annotations

import copy
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import project_root
from .errors import ConfigError, ValidationError
from .migrations import write_json_atomic

SCHEMA_VERSION = 1
FILENAME = "acquaintances.json"
MAX_PEOPLE = 500
MAX_FACTS = 200
MAX_TEXT = 500
MAX_NOTES = 2000

PERSON_STATUSES = ("unknown", "acquaintance", "known")
FACT_STATUSES = ("candidate", "confirmed", "rejected")

CATEGORY_LABELS: dict[str, str] = {
    "nickname": "Nickname",
    "name": "Nome",
    "city": "Città",
    "age": "Età",
    "work": "Lavoro",
    "education": "Formazione",
    "appearance": "Aspetto",
    "interests": "Interessi",
    "values": "Valori",
    "habits": "Abitudini",
    "relationship": "Relazioni",
    "health": "Salute",
    "religion": "Religione",
    "politics": "Politica",
    "orientation": "Orientamento",
    "address": "Indirizzo",
    "contact": "Contatti",
    "other": "Altro",
}

SENSITIVE_CATEGORIES = frozenset(
    {"health", "religion", "politics", "orientation", "address", "contact"}
)

# Categorie usate per la barra di completezza della scheda.
CORE_CATEGORIES = (
    "nickname",
    "name",
    "city",
    "age",
    "work",
    "appearance",
    "interests",
    "values",
    "habits",
)

_PERSON_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, CATEGORY_LABELS["other"])


def is_sensitive(category: str) -> bool:
    return category in SENSITIVE_CATEGORIES


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(
    value: Any,
    label: str,
    *,
    maximum: int = MAX_TEXT,
    required: bool = False,
) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"{label}: attesa una stringa.")
    cleaned = value.strip()
    if required and not cleaned:
        raise ValidationError(f"{label}: obbligatorio.")
    if len(cleaned) > maximum:
        raise ValidationError(f"{label}: massimo {maximum} caratteri.")
    if "\x00" in cleaned:
        raise ValidationError(f"{label}: carattere non valido.")
    return cleaned


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return (slug or "persona")[:60]


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("Confidenza: attesa un numero tra 0 e 1.")
    number = float(value)
    if not 0 <= number <= 1:
        raise ValidationError("Confidenza: usa un numero tra 0 e 1.")
    return round(number, 2)


class AcquaintanceStore:
    """Lettura e scrittura atomiche del registro delle persone incontrate."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root else project_root()
        self.path = self.root / "config" / FILENAME
        self._lock = threading.RLock()

    # -- documento ---------------------------------------------------------

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "people": []}

    def _validate(self, document: Any) -> dict[str, Any]:
        if not isinstance(document, dict):
            raise ConfigError(f"{FILENAME}: atteso un oggetto JSON.")
        people = document.get("people")
        if not isinstance(people, list):
            raise ConfigError(f"{FILENAME}.people: attesa una lista.")
        seen: set[str] = set()
        for index, person in enumerate(people):
            path = f"{FILENAME}.people[{index}]"
            if not isinstance(person, dict):
                raise ConfigError(f"{path}: atteso un oggetto JSON.")
            person_id = person.get("person_id")
            if not isinstance(person_id, str) or not _PERSON_ID_PATTERN.match(person_id):
                raise ConfigError(f"{path}.person_id: identificativo non valido.")
            if person_id in seen:
                raise ConfigError(f"{path}.person_id: duplicato.")
            seen.add(person_id)
            if not isinstance(person.get("display_name"), str) or not person[
                "display_name"
            ].strip():
                raise ConfigError(f"{path}.display_name: obbligatorio.")
            if person.get("status") not in PERSON_STATUSES:
                raise ConfigError(f"{path}.status: valore non valido.")
            facts = person.get("facts")
            if not isinstance(facts, list):
                raise ConfigError(f"{path}.facts: attesa una lista.")
            fact_ids: set[str] = set()
            for fact_index, fact in enumerate(facts):
                fact_path = f"{path}.facts[{fact_index}]"
                if not isinstance(fact, dict):
                    raise ConfigError(f"{fact_path}: atteso un oggetto JSON.")
                fact_id = fact.get("id")
                if not isinstance(fact_id, str) or not fact_id:
                    raise ConfigError(f"{fact_path}.id: obbligatorio.")
                if fact_id in fact_ids:
                    raise ConfigError(f"{fact_path}.id: duplicato.")
                fact_ids.add(fact_id)
                category = fact.get("category")
                if category not in CATEGORY_LABELS:
                    raise ConfigError(f"{fact_path}.category: categoria non valida.")
                if not isinstance(fact.get("value"), str) or not fact["value"].strip():
                    raise ConfigError(f"{fact_path}.value: obbligatorio.")
                if fact.get("status") not in FACT_STATUSES:
                    raise ConfigError(f"{fact_path}.status: stato non valido.")
        return document

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"Impossibile leggere {self.path}: {exc}") from exc
        return self._validate(document)

    def _write(self, document: dict[str, Any]) -> None:
        candidate = copy.deepcopy(document)
        candidate["schema_version"] = SCHEMA_VERSION
        candidate["updated_at"] = _now()
        candidate = self._validate(candidate)
        write_json_atomic(self.path, candidate)

    # -- lettura -----------------------------------------------------------

    def read(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._read())

    def list_people(self) -> list[dict[str, Any]]:
        people = self.read()["people"]
        people.sort(
            key=lambda person: (
                str(person.get("last_seen_at") or ""),
                person["display_name"].lower(),
            ),
            reverse=True,
        )
        return people

    def get(self, person_id: str) -> dict[str, Any] | None:
        target = _text(person_id, "person_id", maximum=80, required=True)
        for person in self.read()["people"]:
            if person["person_id"] == target:
                return person
        return None

    # -- scrittura ---------------------------------------------------------

    @staticmethod
    def _new_id(people: list[dict[str, Any]], channel: str, handle: str) -> str:
        prefix = _slug(channel or "persona")
        base = f"{prefix}_{_slug(handle)}"[:80]
        existing = {person["person_id"] for person in people}
        if base not in existing:
            return base
        counter = 2
        while f"{base}_{counter}" in existing:
            counter += 1
        return f"{base}_{counter}"

    @staticmethod
    def _locate(
        people: list[dict[str, Any]],
        person_id: str,
        channel: str,
        handle: str,
        display_name: str,
    ) -> dict[str, Any] | None:
        if person_id:
            for person in people:
                if person["person_id"] == person_id:
                    return person
        if handle:
            for person in people:
                for identity in person.get("identities", []):
                    if (
                        isinstance(identity, dict)
                        and str(identity.get("handle", "")).casefold()
                        == handle.casefold()
                        and (
                            not channel
                            or str(identity.get("channel", "")).casefold()
                            == channel.casefold()
                        )
                    ):
                        return person
        if display_name:
            for person in people:
                if person["display_name"].casefold() == display_name.casefold():
                    return person
        return None

    @staticmethod
    def _add_fact(
        person: dict[str, Any],
        category: str,
        value: str,
        confidence: float,
        source: str,
        now: str,
    ) -> dict[str, Any]:
        facts = person["facts"]
        for fact in facts:
            if (
                fact["category"] == category
                and fact["value"].casefold() == value.casefold()
            ):
                fact["confidence"] = max(
                    float(fact.get("confidence") or 0), confidence
                )
                if source:
                    fact["source"] = source
                fact["observed_at"] = now
                fact["sensitive"] = bool(fact.get("sensitive")) or is_sensitive(
                    category
                )
                if fact["status"] == "rejected":
                    fact["status"] = "candidate"
                    fact["confirmed_at"] = None
                return fact
        if len(facts) >= MAX_FACTS:
            raise ValidationError(
                f"Troppi fatti per questa persona (massimo {MAX_FACTS})."
            )
        fact = {
            "id": f"fact_{uuid.uuid4().hex[:10]}",
            "category": category,
            "value": value,
            "status": "candidate",
            "sensitive": is_sensitive(category),
            "confidence": confidence,
            "source": source,
            "observed_at": now,
            "confirmed_at": None,
        }
        facts.append(fact)
        return fact

    def create(
        self,
        display_name: str,
        *,
        channel: str = "",
        handle: str = "",
        notes: str = "",
        person_id: str = "",
    ) -> dict[str, Any]:
        name = _text(display_name, "Nickname", maximum=80, required=True)
        channel = _text(channel, "Canale", maximum=40)
        handle = _text(handle, "Handle", maximum=120)
        notes = _text(notes, "Note", maximum=MAX_NOTES)
        requested_id = _text(person_id, "person_id", maximum=80)
        if requested_id and not _PERSON_ID_PATTERN.match(requested_id):
            raise ValidationError(
                "person_id: usa lettere, numeri, trattino o underscore (massimo 80)."
            )
        now = _now()
        with self._lock:
            document = self._read()
            people = document["people"]
            if len(people) >= MAX_PEOPLE:
                raise ValidationError(
                    f"Registro pieno: massimo {MAX_PEOPLE} persone."
                )
            if requested_id and any(p["person_id"] == requested_id for p in people):
                raise ValidationError(
                    f"Esiste già una persona con person_id {requested_id!r}."
                )
            new_id = requested_id or self._new_id(
                people, channel, handle or name
            )
            person = {
                "person_id": new_id,
                "display_name": name,
                "status": "unknown",
                "notes": notes,
                "channels": [channel] if channel else [],
                "identities": (
                    [{"channel": channel, "handle": handle or name}]
                    if channel or handle
                    else []
                ),
                "first_seen_at": now,
                "last_seen_at": now,
                "observation_count": 1,
                "facts": [],
            }
            people.append(person)
            self._write(document)
            return copy.deepcopy(person)

    def observe(
        self,
        *,
        person_id: str = "",
        display_name: str = "",
        channel: str = "",
        handle: str = "",
        category: str = "",
        value: str = "",
        confidence: float = 0.4,
        source: str = "",
    ) -> dict[str, Any]:
        person_id = _text(person_id, "person_id", maximum=80)
        if person_id and not _PERSON_ID_PATTERN.match(person_id):
            raise ValidationError("person_id: identificativo non valido.")
        display_name = _text(display_name, "Nickname", maximum=80)
        channel = _text(channel, "Canale", maximum=40)
        handle = _text(handle, "Handle", maximum=120)
        category = _text(category, "Categoria", maximum=40)
        value = _text(value, "Valore", maximum=MAX_TEXT)
        source = _text(source, "Fonte", maximum=500)
        if category and category not in CATEGORY_LABELS:
            raise ValidationError(f"Categoria non valida: {category}.")
        if category and not value:
            raise ValidationError(
                "Valore: obbligatorio quando indichi una categoria."
            )
        score = _confidence(confidence)
        now = _now()
        with self._lock:
            document = self._read()
            people = document["people"]
            person = self._locate(
                people, person_id, channel, handle, display_name
            )
            if person is None:
                if not person_id and not display_name and not handle:
                    raise ValidationError(
                        "Serve un person_id, un nickname o un handle."
                    )
                if len(people) >= MAX_PEOPLE:
                    raise ValidationError(
                        f"Registro pieno: massimo {MAX_PEOPLE} persone."
                    )
                name = display_name or handle or person_id
                person = {
                    "person_id": person_id
                    or self._new_id(people, channel, handle or name),
                    "display_name": name,
                    "status": "unknown",
                    "notes": "",
                    "channels": [channel] if channel else [],
                    "identities": (
                        [{"channel": channel, "handle": handle or name}]
                        if channel or handle
                        else []
                    ),
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "observation_count": 1,
                    "facts": [],
                }
                people.append(person)
            else:
                person["last_seen_at"] = now
                person["observation_count"] = (
                    int(person.get("observation_count") or 0) + 1
                )
            if channel or handle:
                identity = {
                    "channel": channel,
                    "handle": handle or display_name or person["display_name"],
                }
                identities = person.setdefault("identities", [])
                if identity not in identities:
                    identities.append(identity)
                channels = person.setdefault("channels", [])
                if channel and channel not in channels:
                    channels.append(channel)
            if category:
                self._add_fact(person, category, value, score, source, now)
            self._write(document)
            return copy.deepcopy(person)

    def update_person(
        self,
        person_id: str,
        *,
        display_name: str | None = None,
        status: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        target = _text(person_id, "person_id", maximum=80, required=True)
        with self._lock:
            document = self._read()
            person = next(
                (p for p in document["people"] if p["person_id"] == target),
                None,
            )
            if person is None:
                raise ValidationError(f"Persona non trovata: {person_id}.")
            if display_name is not None:
                person["display_name"] = _text(
                    display_name, "Nickname", maximum=80, required=True
                )
            if status is not None:
                if status not in PERSON_STATUSES:
                    raise ValidationError("Stato: valore non valido.")
                person["status"] = status
            if notes is not None:
                person["notes"] = _text(notes, "Note", maximum=MAX_NOTES)
            person["updated_at"] = _now()
            self._write(document)
            return copy.deepcopy(person)

    def set_fact_status(
        self,
        person_id: str,
        fact_id: str,
        status: str,
        *,
        allow_sensitive: bool = False,
    ) -> dict[str, Any]:
        target = _text(person_id, "person_id", maximum=80, required=True)
        identifier = _text(fact_id, "fact_id", maximum=80, required=True)
        if status not in FACT_STATUSES:
            raise ValidationError("Stato del fatto non valido.")
        with self._lock:
            document = self._read()
            person = next(
                (p for p in document["people"] if p["person_id"] == target),
                None,
            )
            if person is None:
                raise ValidationError(f"Persona non trovata: {person_id}.")
            fact = next(
                (f for f in person["facts"] if f["id"] == identifier), None
            )
            if fact is None:
                raise ValidationError(f"Fatto non trovato: {fact_id}.")
            if (
                fact.get("sensitive")
                and status == "confirmed"
                and not allow_sensitive
            ):
                raise ValidationError(
                    "Un dato sensibile si conferma solo dalla dashboard."
                )
            fact["status"] = status
            fact["confirmed_at"] = _now() if status == "confirmed" else None
            self._write(document)
            return copy.deepcopy(fact)

    def remove_fact(self, person_id: str, fact_id: str) -> None:
        target = _text(person_id, "person_id", maximum=80, required=True)
        identifier = _text(fact_id, "fact_id", maximum=80, required=True)
        with self._lock:
            document = self._read()
            person = next(
                (p for p in document["people"] if p["person_id"] == target),
                None,
            )
            if person is None:
                raise ValidationError(f"Persona non trovata: {person_id}.")
            before = len(person["facts"])
            person["facts"] = [
                fact for fact in person["facts"] if fact["id"] != identifier
            ]
            if len(person["facts"]) == before:
                raise ValidationError(f"Fatto non trovato: {fact_id}.")
            self._write(document)

    def remove(self, person_id: str) -> None:
        target = _text(person_id, "person_id", maximum=80, required=True)
        with self._lock:
            document = self._read()
            before = len(document["people"])
            document["people"] = [
                person
                for person in document["people"]
                if person["person_id"] != target
            ]
            if len(document["people"]) == before:
                raise ValidationError(f"Persona non trovata: {person_id}.")
            self._write(document)


def _decorate(person: dict[str, Any]) -> dict[str, Any]:
    """Aggiunge etichette e conteggi usati dalla dashboard."""

    result = copy.deepcopy(person)
    counts = {"candidate": 0, "confirmed": 0, "rejected": 0}
    confirmed_categories: set[str] = set()
    for fact in result.get("facts", []):
        fact["category_label"] = category_label(fact["category"])
        status = fact.get("status")
        if status in counts:
            counts[status] += 1
        if status == "confirmed":
            confirmed_categories.add(fact["category"])
    covered = {"nickname"} if result.get("display_name") else set()
    covered |= confirmed_categories
    result["fact_counts"] = counts
    result["completeness"] = round(
        len(covered & set(CORE_CATEGORIES)) / len(CORE_CATEGORIES) * 100
    )
    return result


def _decorated_people(store: AcquaintanceStore) -> list[dict[str, Any]]:
    return [_decorate(person) for person in store.list_people()]


def categories_document() -> list[dict[str, Any]]:
    return [
        {
            "id": category,
            "label": label,
            "sensitive": is_sensitive(category),
        }
        for category, label in CATEGORY_LABELS.items()
    ]


class AcquaintanceService:
    """Facciata usata dalla dashboard autenticata."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root else project_root()
        self.store = AcquaintanceStore(self.root)

    def snapshot(self) -> dict[str, Any]:
        people = _decorated_people(self.store)
        return {
            "people": people,
            "count": len(people),
            "categories": categories_document(),
            "statuses": [
                {"id": status, "label": label}
                for status, label in (
                    ("unknown", "Sconosciuto"),
                    ("acquaintance", "Conoscente"),
                    ("known", "Conosciuto"),
                )
            ],
        }

    def person(self, person_id: str) -> dict[str, Any] | None:
        person = self.store.get(person_id)
        return _decorate(person) if person is not None else None

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = payload.get("action")
        if action == "create":
            person = self.store.create(
                payload.get("display_name"),
                channel=payload.get("channel", ""),
                handle=payload.get("handle", ""),
                notes=payload.get("notes", ""),
            )
            return {"saved": True, "action": action, "person": _decorate(person)}
        if action == "update":
            person = self.store.update_person(
                payload.get("person_id"),
                display_name=payload.get("display_name"),
                status=payload.get("status"),
                notes=payload.get("notes"),
            )
            return {"saved": True, "action": action, "person": _decorate(person)}
        if action == "observe":
            person = self.store.observe(
                person_id=payload.get("person_id", ""),
                display_name=payload.get("display_name", ""),
                channel=payload.get("channel", ""),
                handle=payload.get("handle", ""),
                category=payload.get("category", ""),
                value=payload.get("value", ""),
                confidence=payload.get("confidence", 0.4),
                source=payload.get("source", ""),
            )
            return {"saved": True, "action": action, "person": _decorate(person)}
        if action == "fact_status":
            fact = self.store.set_fact_status(
                payload.get("person_id"),
                payload.get("fact_id"),
                payload.get("status"),
                allow_sensitive=True,
            )
            return {"saved": True, "action": action, "fact": fact}
        if action == "fact_remove":
            self.store.remove_fact(
                payload.get("person_id"), payload.get("fact_id")
            )
            return {"saved": True, "action": action}
        if action == "remove":
            if payload.get("confirmation") != "RIMUOVI":
                raise ValidationError("Scrivi RIMUOVI per confermare.")
            self.store.remove(payload.get("person_id"))
            return {"saved": True, "action": action}
        raise ValidationError("Azione non valida per il registro delle persone.")
