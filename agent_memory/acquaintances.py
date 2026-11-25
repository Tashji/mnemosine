"""Registro progressivo delle persone incontrate.

Le relazioni fidate vivono in ``social_graph.json``; qui si accumulano i
profili parziali di chi è stato incontrato senza entrare nel grafo. Ogni
informazione nasce come *candidato* e solo una conferma esplicita la rende
un fatto. I dati sensibili restano candidati finché l'amministratore non
li conferma dalla dashboard.

Il registro ha tre responsabilità amministrative:

* promuovere una persona a relazione, con una conferma esplicita e senza
  copiare nel grafo i fatti sensibili;
* estrarre candidati da un messaggio incollato, lasciando la revisione
  alla dashboard;
* applicare una policy di lettura per l'agente, la conservazione dei dati
  e l'esportazione del registro.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import ConfigStore, project_root
from .errors import ConfigError, ValidationError
from .migrations import write_json_atomic

SCHEMA_VERSION = 2
FILENAME = "acquaintances.json"
MAX_PEOPLE = 500
MAX_FACTS = 200
MAX_TEXT = 500
MAX_NOTES = 2000
MAX_MESSAGE = 4000
MAX_EXTRACTED = 12
MAX_REVIEW_ITEMS = 200
PROMOTION_CONFIRMATION = "PROMUOVI"
RETENTION_CONFIRMATION = "APPLICA"

PERSON_STATUSES = ("unknown", "acquaintance", "known")
FACT_STATUSES = ("candidate", "confirmed", "rejected")
FACT_ORIGINS = ("manual", "extracted", "imported")
AGENT_VISIBILITIES = ("confirmed", "all")

# La policy vale solo per le letture dell'agente: la dashboard vede sempre
# tutto. I fatti scartati non sono mai visibili all'agente.
DEFAULT_POLICY: dict[str, Any] = {
    "agent_fact_visibility": "confirmed",
    "agent_sensitive_access": False,
    "retention_days": 365,
}

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

_AGE_PATTERN = re.compile(r"\bho\s+(\d{1,3})\s+anni\b", re.IGNORECASE)
_CLAUSE_CUT = re.compile(
    r"\s+(?:da|per|con|ma|ed|e|oppure|quando|perch[eé]|mentre|anche)\s+",
    re.IGNORECASE,
)
_INTENSIFIER = re.compile(
    r"^(?:molto|davvero|tantissimo|parecchio|un\s+sacco)\s+", re.IGNORECASE
)
_LEADING_ARTICLE = re.compile(r"^(?:il|lo|la|i|gli|le|l['’])\s*", re.IGNORECASE)

# Estrazione deterministica: solo dichiarazioni esplicite in prima persona.
# Ogni risultato resta un candidato a bassa confidenza, mai una certezza.
_EXTRACT_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    (
        "name",
        re.compile(
            r"\bmi\s+chiamo\s+([A-Za-zÀ-ÖØ-öø-ÿ'’.-]+"
            r"(?:\s+[A-Za-zÀ-ÖØ-öø-ÿ'’.-]+)?)",
            re.IGNORECASE,
        ),
        0.7,
    ),
    (
        "city",
        re.compile(
            r"\b(?:abito|vivo|risiedo)\s+a\s+([A-Za-zÀ-ÖØ-öø-ÿ'’ .-]{2,60}?)"
            r"(?=[,.!?;\n]|$)",
            re.IGNORECASE,
        ),
        0.6,
    ),
    (
        "city",
        re.compile(
            r"\b(?:sono|vengo)\s+di\s+([A-Za-zÀ-ÖØ-öø-ÿ'’ .-]{2,60}?)"
            r"(?=[,.!?;\n]|$)",
            re.IGNORECASE,
        ),
        0.5,
    ),
    (
        "work",
        re.compile(
            r"\blavoro\s+come\s+([A-Za-zÀ-ÖØ-öø-ÿ'’ .-]{2,80}?)"
            r"(?=[,.!?;\n]|$)",
            re.IGNORECASE,
        ),
        0.7,
    ),
    (
        "work",
        re.compile(
            r"\bfaccio\s+(?:il|la)\s+([A-Za-zÀ-ÖØ-öø-ÿ'’ .-]{2,80}?)"
            r"(?=[,.!?;\n]|$)",
            re.IGNORECASE,
        ),
        0.6,
    ),
    (
        "education",
        re.compile(
            r"\bstudio\s+([A-Za-zÀ-ÖØ-öø-ÿ'’ .-]{2,80}?)"
            r"(?=[,.!?;\n]|$)",
            re.IGNORECASE,
        ),
        0.6,
    ),
    (
        "interests",
        re.compile(
            r"\bmi\s+piace\s+([A-Za-zÀ-ÖØ-öø-ÿ'’ .-]{2,80}?)"
            r"(?=[,.!?;\n]|$)",
            re.IGNORECASE,
        ),
        0.5,
    ),
    (
        "interests",
        re.compile(
            r"\bmi\s+piacciono\s+([A-Za-zÀ-ÖØ-öø-ÿ'’ .-]{2,80}?)"
            r"(?=[,.!?;\n]|$)",
            re.IGNORECASE,
        ),
        0.5,
    ),
)


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


def _policy(value: Any) -> dict[str, Any]:
    """Normalizza la policy, applicando i valori predefiniti."""

    if value is None:
        return copy.deepcopy(DEFAULT_POLICY)
    if not isinstance(value, dict):
        raise ConfigError(f"{FILENAME}.policy: atteso un oggetto JSON.")
    merged = copy.deepcopy(DEFAULT_POLICY)
    merged.update({key: value[key] for key in DEFAULT_POLICY if key in value})
    visibility = merged.get("agent_fact_visibility")
    if visibility not in AGENT_VISIBILITIES:
        raise ConfigError(
            f"{FILENAME}.policy.agent_fact_visibility: usa confirmed o all."
        )
    sensitive = merged.get("agent_sensitive_access")
    if not isinstance(sensitive, bool):
        raise ConfigError(
            f"{FILENAME}.policy.agent_sensitive_access: atteso true o false."
        )
    days = merged.get("retention_days")
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3650:
        raise ConfigError(
            f"{FILENAME}.policy.retention_days: intero tra 1 e 3650."
        )
    return merged


def _retention_days(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3650:
        raise ValidationError("Giorni di conservazione: intero tra 1 e 3650.")
    return value


def _level(value: Any, label: str, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ValidationError(f"{label}: intero tra 0 e 100.")
    return value


def extract_facts(text: str) -> list[dict[str, Any]]:
    """Estrae candidati da dichiarazioni esplicite in un messaggio.

    L'estrazione è volutamente conservativa: riconosce poche formule
    italiane in prima persona e non deduce nulla su salute, religione,
    politica, orientamento, indirizzo o contatti.
    """

    message = text or ""
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(category: str, value: str, confidence: float) -> None:
        if len(found) >= MAX_EXTRACTED:
            return
        segments = [value]
        if category == "interests":
            segments = re.split(r"\s+e\s+|\s*,\s*|\s+oppure\s+", value)
        for segment in segments:
            cleaned = " ".join(segment.split()).strip(" .,;:!?\"'")
            if category in {"city", "work", "education"}:
                cleaned = _CLAUSE_CUT.split(cleaned, 1)[0].strip()
            if category == "interests":
                cleaned = _INTENSIFIER.sub("", cleaned)
                cleaned = _LEADING_ARTICLE.sub("", cleaned)
            cleaned = cleaned.strip(" .,;:!?\"'")
            if not 2 <= len(cleaned) <= 120:
                continue
            key = (category, cleaned.casefold())
            if key in seen:
                continue
            seen.add(key)
            found.append(
                {
                    "category": category,
                    "value": cleaned,
                    "confidence": confidence,
                    "sensitive": is_sensitive(category),
                }
            )
            if len(found) >= MAX_EXTRACTED:
                return

    for match in _AGE_PATTERN.finditer(message):
        years = int(match.group(1))
        if 14 <= years <= 110:
            add("age", f"{years} anni", 0.85)
    for category, pattern, confidence in _EXTRACT_PATTERNS:
        for match in pattern.finditer(message):
            add(category, match.group(1), confidence)
    return found


class AcquaintanceStore:
    """Lettura e scrittura atomiche del registro delle persone incontrate."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root else project_root()
        self.path = self.root / "config" / FILENAME
        self._lock = threading.RLock()

    # -- documento ---------------------------------------------------------

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "policy": copy.deepcopy(DEFAULT_POLICY),
            "people": [],
        }

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
            promoted_user_id = person.get("promoted_user_id")
            if promoted_user_id is not None and (
                not isinstance(promoted_user_id, str) or not promoted_user_id.strip()
            ):
                raise ConfigError(f"{path}.promoted_user_id: stringa non vuota.")
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
                if fact.get("status") == "confirmed" and not fact.get(
                    "confirmed_at"
                ):
                    raise ConfigError(
                        f"{fact_path}.confirmed_at: obbligatorio per un fatto confermato."
                    )
                origin = fact.get("origin", "manual")
                if origin not in FACT_ORIGINS:
                    raise ConfigError(f"{fact_path}.origin: provenienza non valida.")
        document["policy"] = _policy(document.get("policy"))
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
        origin: str = "manual",
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
                fact.setdefault("origin", origin)
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
            "origin": origin,
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

    def extract(
        self,
        text: str,
        *,
        person_id: str = "",
        display_name: str = "",
        channel: str = "",
        handle: str = "",
        source: str = "",
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Registra i fatti candidati riconosciuti in un messaggio."""

        message = _text(
            text, "Messaggio", maximum=MAX_MESSAGE, required=True
        )
        person_id = _text(person_id, "person_id", maximum=80)
        if person_id and not _PERSON_ID_PATTERN.match(person_id):
            raise ValidationError("person_id: identificativo non valido.")
        display_name = _text(display_name, "Nickname", maximum=80)
        channel = _text(channel, "Canale", maximum=40)
        handle = _text(handle, "Handle", maximum=120)
        source = _text(source, "Fonte", maximum=500)
        candidates = extract_facts(message)
        if not candidates:
            raise ValidationError(
                "Nessun fatto riconoscibile: usa frasi esplicite come "
                "«mi chiamo», «ho 30 anni», «abito a»."
            )
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
            origin_source = source or "Messaggio condiviso"
            for candidate in candidates:
                self._add_fact(
                    person,
                    candidate["category"],
                    candidate["value"],
                    candidate["confidence"],
                    origin_source,
                    now,
                    origin="extracted",
                )
            self._write(document)
            return copy.deepcopy(person), candidates

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

    def mark_promoted(self, person_id: str, user_id: str) -> dict[str, Any]:
        """Registra il collegamento con un contatto del grafo fidato."""

        target = _text(person_id, "person_id", maximum=80, required=True)
        contact_id = _text(user_id, "user_id", maximum=100, required=True)
        with self._lock:
            document = self._read()
            person = next(
                (p for p in document["people"] if p["person_id"] == target),
                None,
            )
            if person is None:
                raise ValidationError(f"Persona non trovata: {person_id}.")
            person["status"] = "known"
            person["promoted_user_id"] = contact_id
            person["promoted_at"] = _now()
            self._write(document)
            return copy.deepcopy(person)

    def save_policy(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("Attesa una policy del registro.")
        unknown = set(payload) - set(DEFAULT_POLICY)
        if unknown:
            raise ValidationError(
                "Policy non valida: chiavi sconosciute " + ", ".join(sorted(unknown))
            )
        with self._lock:
            document = self._read()
            candidate = dict(document["policy"])
            candidate.update(payload)
            document["policy"] = _policy(candidate)
            self._write(document)
            return copy.deepcopy(document["policy"])

    def apply_retention(
        self, days: Any, *, confirmation: str = ""
    ) -> dict[str, Any]:
        """Rimuove dati scartati e profili inattivi oltre la soglia.

        L'operazione non tocca mai una persona promossa o con stato
        ``known``: una relazione stabilita non scade con la retention.
        """

        if confirmation.strip() != RETENTION_CONFIRMATION:
            raise ValidationError(
                f"Scrivi {RETENTION_CONFIRMATION} per confermare la retention."
            )
        limit = _retention_days(days)
        cutoff = datetime.now(timezone.utc) - timedelta(days=limit)
        cutoff_iso = cutoff.replace(microsecond=0).isoformat()
        removed_facts = 0
        removed_people = 0
        with self._lock:
            document = self._read()
            kept_people: list[dict[str, Any]] = []
            for person in document["people"]:
                facts: list[dict[str, Any]] = []
                for fact in person["facts"]:
                    if (
                        fact.get("status") == "rejected"
                        and str(fact.get("observed_at") or "") < cutoff_iso
                    ):
                        removed_facts += 1
                        continue
                    facts.append(fact)
                person["facts"] = facts
                if (
                    person.get("status") != "known"
                    and not person.get("promoted_user_id")
                    and not facts
                    and str(person.get("last_seen_at") or "") < cutoff_iso
                ):
                    removed_people += 1
                    continue
                kept_people.append(person)
            document["people"] = kept_people
            if removed_facts or removed_people:
                self._write(document)
            return {
                "days": limit,
                "removed_facts": removed_facts,
                "removed_people": removed_people,
            }

    def export(self) -> dict[str, Any]:
        document = self.read()
        return {
            "exported_at": _now(),
            "schema_version": document["schema_version"],
            "policy": document["policy"],
            "people": document["people"],
        }

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
        fact.setdefault("origin", "manual")
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
    result["promoted"] = bool(result.get("promoted_user_id"))
    return result


def visible_person(
    person: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any]:
    """Applica la policy dell'agente a una scheda decorata.

    La dashboard non usa questa funzione: l'amministratore vede sempre tutti
    i fatti. I fatti scartati sono esclusi in ogni caso.
    """

    result = copy.deepcopy(person)
    facts: list[dict[str, Any]] = []
    for fact in result.get("facts", []):
        status = fact.get("status")
        if status == "rejected":
            continue
        if (
            policy.get("agent_fact_visibility", "confirmed") == "confirmed"
            and status != "confirmed"
        ):
            continue
        if fact.get("sensitive") and not policy.get("agent_sensitive_access"):
            continue
        facts.append(fact)
    hidden = len(result.get("facts", [])) - len(facts)
    counts = {"candidate": 0, "confirmed": 0, "rejected": 0}
    for fact in facts:
        status = fact.get("status")
        if status in counts:
            counts[status] += 1
    covered = {"nickname"} if result.get("display_name") else set()
    covered |= {
        fact["category"] for fact in facts if fact.get("status") == "confirmed"
    }
    result["facts"] = facts
    result["fact_counts"] = counts
    result["hidden_facts"] = hidden
    result["completeness"] = round(
        len(covered & set(CORE_CATEGORIES)) / len(CORE_CATEGORIES) * 100
    )
    return result


def review_queue(store: AcquaintanceStore) -> list[dict[str, Any]]:
    """Fatti candidati di tutte le persone, dal più vecchio al più recente."""

    items: list[dict[str, Any]] = []
    for person in _decorated_people(store):
        for fact in person.get("facts", []):
            if fact.get("status") != "candidate":
                continue
            items.append(
                {
                    "person_id": person["person_id"],
                    "display_name": person["display_name"],
                    "person_status": person["status"],
                    "fact_id": fact["id"],
                    "category": fact["category"],
                    "category_label": fact["category_label"],
                    "value": fact["value"],
                    "sensitive": bool(fact.get("sensitive")),
                    "confidence": fact.get("confidence"),
                    "source": fact.get("source", ""),
                    "observed_at": fact.get("observed_at", ""),
                    "origin": fact.get("origin", "manual"),
                }
            )
    items.sort(key=lambda item: str(item.get("observed_at") or ""))
    return items[:MAX_REVIEW_ITEMS]


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
        document = self.store.read()
        review = review_queue(self.store)
        confirmed = sum(
            1
            for person in people
            for fact in person.get("facts", [])
            if fact.get("status") == "confirmed"
        )
        sensitive_confirmed = sum(
            1
            for person in people
            for fact in person.get("facts", [])
            if fact.get("status") == "confirmed" and fact.get("sensitive")
        )
        return {
            "people": people,
            "count": len(people),
            "categories": categories_document(),
            "policy": document["policy"],
            "review": review,
            "stats": {
                "people": len(people),
                "candidates": len(review),
                "confirmed": confirmed,
                "sensitive_confirmed": sensitive_confirmed,
                "promoted": sum(1 for person in people if person.get("promoted")),
            },
            "statuses": [
                {"id": status, "label": label}
                for status, label in (
                    ("unknown", "Sconosciuto"),
                    ("acquaintance", "Conoscente"),
                    ("known", "Conosciuto"),
                )
            ],
        }

    def promote(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Crea una relazione fidata da una persona incontrata.

        La promozione richiede la conferma ``PROMUOVI`` e non copia nel
        grafo i fatti sensibili: restano nel registro amministrativo.
        """

        if payload.get("confirmation") != PROMOTION_CONFIRMATION:
            raise ValidationError(
                f"Scrivi {PROMOTION_CONFIRMATION} per promuovere la persona "
                "a relazione."
            )
        person = self.store.get(payload.get("person_id"))
        if person is None:
            raise ValidationError(
                f"Persona non trovata: {payload.get('person_id')}."
            )
        if person.get("promoted_user_id"):
            raise ValidationError(
                "Questa persona è già collegata a una relazione del grafo."
            )
        redirect = str(payload.get("redirect") or person["person_id"])
        if not _PERSON_ID_PATTERN.match(redirect):
            raise ValidationError(
                "user_id della relazione: usa lettere, numeri, trattino o underscore."
            )
        relation = _text(payload.get("relation", ""), "Relazione", maximum=80)
        relation = relation or "Conoscente"
        trust = _level(payload.get("trust"), "Affidabilità", default=30)
        intimacy = _level(payload.get("intimacy"), "Intimità", default=10)
        confirmed_facts = [
            fact
            for fact in person.get("facts", [])
            if fact.get("status") == "confirmed" and not fact.get("sensitive")
        ]
        copied = [
            f"{category_label(fact['category'])}: {fact['value']}"
            for fact in confirmed_facts
        ]
        preferences = [
            fact["value"]
            for fact in confirmed_facts
            if fact["category"] == "interests"
        ]
        full_name = next(
            (
                fact["value"]
                for fact in confirmed_facts
                if fact["category"] == "name"
            ),
            "",
        )
        notes = person.get("notes", "")
        promotion_note = (
            f"Promosso dal registro Incontrati il {_now()[:10]}."
        )
        notes = f"{notes}\n{promotion_note}".strip() if notes else promotion_note
        contact = {
            "user_id": redirect,
            "display_name": person["display_name"],
            "full_name": full_name,
            "relation": relation,
            "notes": notes,
            "trust": trust,
            "intimacy": intimacy,
            "first_interaction_at": None,
            "last_interaction_at": None,
            "interaction_count": 0,
            "interaction_history": [],
            "preferences": preferences,
            "confirmed_facts": copied,
            "boundaries": [],
        }
        config = ConfigStore(self.root)
        with config.transaction():
            graph = config.get("social_graph")
            if any(
                existing.get("user_id") == redirect
                for existing in graph["people"]
            ):
                raise ValidationError(
                    f"Esiste già una relazione con user_id {redirect!r}."
                )
            graph["people"].append(contact)
            config.write("social_graph", graph)
        try:
            updated = self.store.mark_promoted(person["person_id"], redirect)
        except Exception:
            # Il grafo non deve restare con un contatto che il registro non
            # considera promosso: ripristina il documento precedente.
            with config.transaction():
                graph = config.get("social_graph")
                graph["people"] = [
                    item
                    for item in graph["people"]
                    if item.get("user_id") != redirect
                ]
                config.write("social_graph", graph)
            raise
        return {
            "saved": True,
            "action": "promote",
            "person": _decorate(updated),
            "user_id": redirect,
            "redirect": "/#relations",
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
        if action == "extract":
            person, candidates = self.store.extract(
                payload.get("text"),
                person_id=payload.get("person_id", ""),
                display_name=payload.get("display_name", ""),
                channel=payload.get("channel", ""),
                handle=payload.get("handle", ""),
                source=payload.get("source", ""),
            )
            return {
                "saved": True,
                "action": action,
                "person": _decorate(person),
                "extracted": candidates,
                "count": len(candidates),
            }
        if action == "promote":
            return self.promote(payload)
        if action == "policy":
            policy = self.store.save_policy(payload.get("policy", {}))
            return {"saved": True, "action": action, "policy": policy}
        if action == "retention":
            result = self.store.apply_retention(
                payload.get("days"),
                confirmation=str(payload.get("confirmation", "")),
            )
            return {"saved": True, "action": action, **result}
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

    def export(self) -> dict[str, Any]:
        return self.store.export()
