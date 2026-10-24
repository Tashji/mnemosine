"""Servizi di memoria conscia e latente.

``MemoryService`` è la superficie usata dal server MCP: legge e scrive solo la
memoria conscia e non espone mai i tratti o il diario dell'ombra.
``ShadowService`` è la superficie interna riservata al motore dell'ombra e al
pannello amministrativo: non viene registrata come tool MCP.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import CONFIG_FILENAMES, ConfigStore, now_iso
from .db import Database, decode_memory, decode_shadow, json_text
from .errors import (
    ConfigError,
    ConflictError,
    NotFoundError,
    ValidationError,
)


MEMORY_SOURCES = {
    "real_interaction",
    "generated",
    "admin_seed",
    "world_event",
    "routine",
}
SIMULATED_SOURCES = {"generated", "world_event", "routine"}
RELATION_TYPES = {
    "associated",
    "caused_by",
    "follows",
    "contradicts",
    "same_person",
    "same_place",
    "thematic",
}
SHADOW_ENTRY_TYPES = {
    "repressed_memory",
    "projection",
    "latent_reaction",
    "internal_conflict",
    "trigger_response",
    "dream",
    "admin_seed",
}
SHADOW_SOURCES = {"real_interaction", "generated", "admin_seed", "reflection"}
DEFENSE_MECHANISMS = {
    "projection",
    "repression",
    "denial",
    "displacement",
    "rationalization",
    "intellectualization",
    "sublimation",
    "avoidance",
    "none",
}
SHADOW_ACCESS_LEVELS = {"latent", "restricted"}


# ---------------------------------------------------------------------------
# Validazione e utilità
# ---------------------------------------------------------------------------


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _require_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field}: attesa una stringa non vuota.")
    return value.strip()


def _optional_str(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field}: attesa una stringa o null.")
    return value


def _require_int(value: Any, field: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field}: atteso un intero.")
    if not low <= value <= high:
        raise ValidationError(f"{field}: valore {value} fuori intervallo [{low}, {high}].")
    return value


def _require_number(value: Any, field: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field}: atteso un numero.")
    number = float(value)
    if not low <= number <= high:
        raise ValidationError(f"{field}: valore {value} fuori intervallo [{low}, {high}].")
    return number


def _require_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError(f"{field}: atteso un booleano.")
    return value


def _require_datetime(value: Any, field: str) -> str:
    if not isinstance(value, str) or "T" not in value:
        raise ValidationError(
            f"{field}: atteso un timestamp ISO 8601 completo di data e ora."
        )
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{field}: timestamp non valido '{value}'.") from exc
    return value


def _require_date_bound(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field}: attesa una data o un timestamp ISO 8601.")
    try:
        if "T" in value:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{field}: data non valida '{value}'.") from exc
    return value


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError(f"{field}: attesa una lista di stringhe.")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValidationError(f"{field}[{index}]: attesa una stringa non vuota.")
        result.append(item.strip())
    return result


def _object(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError(f"{field}: atteso un oggetto JSON.")
    return copy.deepcopy(value)


def _like_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _clamp_limit(limit: Any, default: int = 20) -> int:
    if limit is None:
        return default
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValidationError("limit: atteso un intero.")
    return max(1, min(limit, 100))


def _clamp_offset(offset: Any) -> int:
    if offset is None:
        return 0
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise ValidationError("offset: atteso un intero.")
    return max(0, offset)


# ---------------------------------------------------------------------------
# Inserimento episodico condiviso
# ---------------------------------------------------------------------------


def _insert_episodic(
    conn: sqlite3.Connection,
    *,
    event_time: str,
    title: str,
    description: str,
    importance: int,
    source: str,
    place: str | None = None,
    people: list[str] | None = None,
    emotional_state: dict[str, Any] | None = None,
    consequences: str | None = None,
    source_ref: str | None = None,
    reliability: float = 1.0,
    is_simulated: bool | None = None,
    access_level: str = "conscious",
    access_reason: str | None = None,
    is_consolidated: bool = False,
    tags: list[str] | None = None,
    memory_id: str | None = None,
    timestamp: str | None = None,
    changed_by: str = "memory_service",
    change_reason: str = "Creazione del ricordo.",
    cascade_id: str | None = None,
) -> dict[str, Any]:
    """Inserisce un ricordo e la sua revisione iniziale.

    La funzione è condivisa da ``MemoryService`` (solo ricordi consci) e
    ``ShadowService`` (ricordi latenti o repressi). La separazione dei permessi
    è responsabilità del chiamante; qui si validano soltanto i dati.
    """

    event_time = _require_datetime(event_time, "event_time")
    title = _require_str(title, "title")
    description = _require_str(description, "description")
    importance = _require_int(importance, "importance", 1, 10)
    access_reason = _optional_str(access_reason, "access_reason")
    if access_reason is not None:
        access_reason = access_reason.strip() or None
    if source not in MEMORY_SOURCES:
        raise ValidationError(
            f"source: valore non valido '{source}'. Ammessi: {', '.join(sorted(MEMORY_SOURCES))}."
        )
    place = _optional_str(place, "place")
    people = _string_list(people, "people")
    tags = _string_list(tags, "tags")
    emotional_state = _object(emotional_state, "emotional_state")
    consequences = _optional_str(consequences, "consequences")
    source_ref = _optional_str(source_ref, "source_ref")
    reliability = _require_number(reliability, "reliability", 0.0, 1.0)
    is_consolidated = _require_bool(is_consolidated, "is_consolidated")

    if is_simulated is None:
        simulated = source != "real_interaction"
    else:
        simulated = _require_bool(is_simulated, "is_simulated")
    if source == "real_interaction" and simulated:
        raise ValidationError("source='real_interaction' richiede is_simulated=False.")
    if source in SIMULATED_SOURCES and not simulated:
        raise ValidationError(f"source='{source}' richiede is_simulated=True.")

    if access_level not in {"conscious", "latent", "repressed"}:
        raise ValidationError(
            "access_level: valore non valido; ammessi conscious, latent, repressed."
        )
    if access_level == "repressed" and not access_reason:
        raise ValidationError(
            "access_reason: obbligatorio per un ricordo con access_level='repressed'."
        )

    if memory_id is None:
        memory_id = _new_id("mem")
    else:
        memory_id = _require_str(memory_id, "memory_id")

    if timestamp is None:
        timestamp = now_iso()
    else:
        timestamp = _require_datetime(timestamp, "timestamp")

    row = {
        "id": memory_id,
        "event_time": event_time,
        "event_date": event_time[:10],
        "title": title,
        "description": description,
        "place": place,
        "people_json": json_text(people),
        "emotional_state_json": json_text(emotional_state),
        "importance": importance,
        "consequences": consequences,
        "source": source,
        "source_ref": source_ref,
        "reliability": reliability,
        "is_simulated": 1 if simulated else 0,
        "access_level": access_level,
        "access_reason": access_reason,
        "is_consolidated": 1 if is_consolidated else 0,
        "tags_json": json_text(tags),
        "embedding_ref": None,
        "embedding_model": None,
        "embedding_updated_at": None,
        "version": 1,
        "previous_version_id": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    columns = list(row)
    sql = (
        f"INSERT INTO episodic_memories ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})"
    )

    try:
        with conn:
            conn.execute(sql, tuple(row[column] for column in columns))
            inserted = conn.execute(
                "SELECT * FROM episodic_memories WHERE id = ?", (memory_id,)
            ).fetchone()
            conn.execute(
                """
                INSERT INTO memory_revisions (
                    memory_id, revision_number, changed_at, changed_by,
                    change_reason, previous_snapshot_json, new_snapshot_json, cascade_id
                ) VALUES (?, 1, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    memory_id,
                    timestamp,
                    changed_by,
                    change_reason,
                    json.dumps(dict(inserted), ensure_ascii=False, indent=2),
                    cascade_id,
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ConflictError(
            f"Impossibile creare il ricordo '{memory_id}': {exc}"
        ) from exc

    return decode_memory(inserted)


def _update_episodic(
    conn: sqlite3.Connection,
    memory_id: str,
    patch: dict[str, Any],
    *,
    timestamp: str,
    changed_by: str,
    change_reason: str,
    cascade_id: str | None = None,
) -> dict[str, Any]:
    """Aggiorna un ricordo conscio conservando lo snapshot precedente.

    La funzione è condivisa dal servizio e dal motore di evoluzione. Il numero
    di revisione coincide con la nuova versione del ricordo, così la catena
    ``version``/``memory_revisions`` resta verificabile.
    """

    memory_id = _require_str(memory_id, "memory_id")
    if not isinstance(patch, dict) or not patch:
        raise ValidationError("Il patch del ricordo deve essere un oggetto non vuoto.")
    allowed = {
        "title",
        "description",
        "place",
        "people",
        "emotional_state",
        "importance",
        "consequences",
        "tags",
        "reliability",
        "is_consolidated",
    }
    unknown = sorted(set(patch) - allowed)
    if unknown:
        raise ValidationError(
            "Campi non modificabili o sconosciuti: " + ", ".join(unknown) + "."
        )

    timestamp = _require_datetime(timestamp, "timestamp")
    cascade_id = _optional_str(cascade_id, "cascade_id")

    row = conn.execute(
        "SELECT * FROM conscious_episodic_memories WHERE id = ?", (memory_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Ricordo non trovato nella memoria conscia: {memory_id}.")

    current = decode_memory(row)
    merged = dict(current)
    for key, value in patch.items():
        merged[key] = copy.deepcopy(value)

    title = _require_str(merged["title"], "title")
    description = _require_str(merged["description"], "description")
    place = _optional_str(merged.get("place"), "place")
    people = _string_list(merged.get("people"), "people")
    emotional_state = _object(merged.get("emotional_state"), "emotional_state")
    importance = _require_int(merged["importance"], "importance", 1, 10)
    consequences = _optional_str(merged.get("consequences"), "consequences")
    tags = _string_list(merged.get("tags"), "tags")
    reliability = _require_number(merged["reliability"], "reliability", 0.0, 1.0)
    raw_consolidated = merged.get("is_consolidated")
    if isinstance(raw_consolidated, int) and not isinstance(raw_consolidated, bool):
        raw_consolidated = bool(raw_consolidated)
    is_consolidated = _require_bool(raw_consolidated, "is_consolidated")

    new_version = int(current["version"]) + 1
    new_row = {
        "title": title,
        "description": description,
        "place": place,
        "people_json": json_text(people),
        "emotional_state_json": json_text(emotional_state),
        "importance": importance,
        "consequences": consequences,
        "tags_json": json_text(tags),
        "reliability": reliability,
        "is_consolidated": 1 if is_consolidated else 0,
        "version": new_version,
        "updated_at": timestamp,
    }
    assignments = ", ".join(f"{column} = ?" for column in new_row)

    try:
        with conn:
            conn.execute(
                f"UPDATE episodic_memories SET {assignments} WHERE id = ?",
                tuple(new_row[column] for column in new_row) + (memory_id,),
            )
            updated = conn.execute(
                "SELECT * FROM episodic_memories WHERE id = ?", (memory_id,)
            ).fetchone()
            conn.execute(
                """
                INSERT INTO memory_revisions (
                    memory_id, revision_number, changed_at, changed_by,
                    change_reason, previous_snapshot_json, new_snapshot_json, cascade_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    new_version,
                    timestamp,
                    changed_by,
                    change_reason,
                    json.dumps(dict(row), ensure_ascii=False, indent=2),
                    json.dumps(dict(updated), ensure_ascii=False, indent=2),
                    cascade_id,
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ConflictError(
            f"Impossibile aggiornare il ricordo '{memory_id}': {exc}"
        ) from exc

    return decode_memory(updated)


# ---------------------------------------------------------------------------
# Memoria conscia
# ---------------------------------------------------------------------------


class MemoryService:
    """Superficie conscia: identità, stato, storia, relazioni e timeline."""

    def __init__(self, root: str | Path | None = None):
        self.config = ConfigStore(root)
        self.db = Database(self.config.root)
        self.root = self.config.root
        # Fail closed: il servizio non parte con configurazioni incoerenti.
        self.config.load_all()

    # -- configurazione -----------------------------------------------------

    def get_identity(self) -> dict[str, Any]:
        return self.config.get("identity")

    def update_identity(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Aggiornamento amministrativo, non esposto come tool MCP."""

        return self.config.update_identity(patch)

    def get_current_state(self) -> dict[str, Any]:
        return self.config.get("current_state")

    def update_current_state(self, patch: dict[str, Any]) -> dict[str, Any]:
        return self.config.update_current_state(patch)

    def get_world_history(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        min_italy_relevance: int | None = None,
    ) -> list[dict[str, Any]]:
        world = self.config.get("world_history")
        if start_date is not None:
            start_date = _require_date_bound(start_date, "start_date")
        if end_date is not None:
            end_date = _require_date_bound(end_date, "end_date")
        if start_date and end_date and start_date > end_date:
            raise ValidationError("start_date non può essere successiva a end_date.")
        if min_italy_relevance is not None:
            min_italy_relevance = _require_int(
                min_italy_relevance, "min_italy_relevance", 0, 10
            )

        events = []
        for event in world["events"]:
            event_date = event["date"]
            if start_date and event_date < start_date[:10]:
                continue
            if end_date and event_date > end_date[:10]:
                continue
            if (
                min_italy_relevance is not None
                and int(event.get("italy_relevance", 0)) < min_italy_relevance
            ):
                continue
            events.append(copy.deepcopy(event))
        events.sort(key=lambda item: item["date"])
        return events

    def get_social_graph(self) -> dict[str, Any]:
        return self.config.get("social_graph")

    def get_person(self, user_id: str) -> dict[str, Any] | None:
        user_id = _require_str(user_id, "user_id")
        for person in self.config.get("social_graph")["people"]:
            if person["user_id"] == user_id:
                return copy.deepcopy(person)
        return None

    # -- timeline -----------------------------------------------------------

    def _query_memories(
        self,
        *,
        query: str | None = None,
        limit: int = 20,
        offset: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
        min_importance: int | None = None,
        tag: str | None = None,
        person: str | None = None,
        order: str = "desc",
    ) -> list[dict[str, Any]]:
        limit = _clamp_limit(limit)
        offset = _clamp_offset(offset)
        order = (order or "desc").lower()
        if order not in {"asc", "desc"}:
            raise ValidationError("order: ammessi 'asc' o 'desc'.")
        if source is not None and source not in MEMORY_SOURCES:
            raise ValidationError(f"source: valore non valido '{source}'.")
        if min_importance is not None:
            min_importance = _require_int(min_importance, "min_importance", 1, 10)

        clauses: list[str] = []
        params: list[Any] = []

        if query:
            pattern = _like_pattern(_require_str(query, "query"))
            clauses.append(
                "(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' "
                "OR place LIKE ? ESCAPE '\\' OR tags_json LIKE ? ESCAPE '\\' "
                "OR people_json LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern] * 5)

        for value, column_date, column_time, operator, field in (
            (start_date, "event_date", "event_time", ">=", "start_date"),
            (end_date, "event_date", "event_time", "<=", "end_date"),
        ):
            if value is None:
                continue
            value = _require_date_bound(value, field)
            column = column_date if "T" not in value else column_time
            clauses.append(f"{column} {operator} ?")
            params.append(value)

        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if min_importance is not None:
            clauses.append("importance >= ?")
            params.append(min_importance)
        if tag is not None:
            tag_json = json.dumps(_require_str(tag, "tag"), ensure_ascii=False)
            clauses.append("tags_json LIKE ? ESCAPE '\\'")
            params.append(_like_pattern(tag_json))
        if person is not None:
            person_json = json.dumps(_require_str(person, "person"), ensure_ascii=False)
            clauses.append("people_json LIKE ? ESCAPE '\\'")
            params.append(_like_pattern(person_json))

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT * FROM conscious_episodic_memories {where} "
            f"ORDER BY event_time {order}, id {order} LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

        with self.db.timeline() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [decode_memory(row) for row in rows]

    def filter_memory_ids(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
        min_importance: int | None = None,
        tag: str | None = None,
        person: str | None = None,
    ) -> list[str]:
        """Restituisce tutti gli ID consci compatibili, senza paginazione.

        Serve a pre-filtrare i candidati prima della ricerca vettoriale.
        I ricordi latenti e repressi non compaiono mai nella vista usata qui.
        """

        if source is not None and source not in MEMORY_SOURCES:
            raise ValidationError(f"source: valore non valido '{source}'.")
        clauses: list[str] = []
        params: list[Any] = []

        for value, column_date, column_time, operator, field in (
            (start_date, "event_date", "event_time", ">=", "start_date"),
            (end_date, "event_date", "event_time", "<=", "end_date"),
        ):
            if value is None:
                continue
            value = _require_date_bound(value, field)
            column = column_date if "T" not in value else column_time
            clauses.append(f"{column} {operator} ?")
            params.append(value)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if min_importance is not None:
            clauses.append("importance >= ?")
            params.append(_require_int(min_importance, "min_importance", 1, 10))
        if tag is not None:
            tag_json = json.dumps(_require_str(tag, "tag"), ensure_ascii=False)
            clauses.append("tags_json LIKE ? ESCAPE '\\'")
            params.append(_like_pattern(tag_json))
        if person is not None:
            person_json = json.dumps(_require_str(person, "person"), ensure_ascii=False)
            clauses.append("people_json LIKE ? ESCAPE '\\'")
            params.append(_like_pattern(person_json))

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.timeline() as conn:
            rows = conn.execute(
                f"SELECT id FROM conscious_episodic_memories {where} ORDER BY id ASC",
                params,
            ).fetchall()
        return [row["id"] for row in rows]

    def list_memories(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
        min_importance: int | None = None,
        tag: str | None = None,
        person: str | None = None,
        order: str = "desc",
    ) -> list[dict[str, Any]]:
        return self._query_memories(
            limit=limit,
            offset=offset,
            start_date=start_date,
            end_date=end_date,
            source=source,
            min_importance=min_importance,
            tag=tag,
            person=person,
            order=order,
        )

    def search_memories(
        self,
        query: str | None = None,
        *,
        limit: int = 20,
        offset: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
        min_importance: int | None = None,
        tag: str | None = None,
        person: str | None = None,
        order: str = "desc",
    ) -> list[dict[str, Any]]:
        return self._query_memories(
            query=query,
            limit=limit,
            offset=offset,
            start_date=start_date,
            end_date=end_date,
            source=source,
            min_importance=min_importance,
            tag=tag,
            person=person,
            order=order,
        )

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        """Restituisce un ricordo solo se è nella memoria conscia.

        Un ricordo latente o represso viene trattato come inesistente: il
        server MCP non deve poter dedurre la sua esistenza.
        """

        memory_id = _require_str(memory_id, "memory_id")
        with self.db.timeline() as conn:
            row = conn.execute(
                "SELECT * FROM conscious_episodic_memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
        return decode_memory(row) if row is not None else None

    def create_memory(
        self,
        *,
        title: str,
        description: str,
        importance: int = 5,
        source: str = "generated",
        event_time: str | None = None,
        place: str | None = None,
        people: list[str] | None = None,
        emotional_state: dict[str, Any] | None = None,
        consequences: str | None = None,
        source_ref: str | None = None,
        tags: list[str] | None = None,
        reliability: float = 0.9,
        is_simulated: bool | None = None,
        is_consolidated: bool = False,
        memory_id: str | None = None,
        cascade_id: str | None = None,
    ) -> dict[str, Any]:
        """Crea un ricordo conscio.

        Il servizio generico accetta anche ``real_interaction`` e
        ``admin_seed``: sono le integrazioni interne a doverlo fare. Il tool
        MCP limita invece le sorgenti a ``generated`` e ``routine``.
        """

        timestamp = now_iso(self.config.timezone_name())
        if event_time is None:
            event_time = timestamp
        with self.db.timeline() as conn:
            return _insert_episodic(
                conn,
                event_time=event_time,
                title=title,
                description=description,
                importance=importance,
                source=source,
                place=place,
                people=people,
                emotional_state=emotional_state,
                consequences=consequences,
                source_ref=source_ref,
                reliability=reliability,
                is_simulated=is_simulated,
                access_level="conscious",
                access_reason=None,
                is_consolidated=is_consolidated,
                tags=tags,
                memory_id=memory_id,
                timestamp=timestamp,
                changed_by="memory_service",
                change_reason="Creazione del ricordo dalla memoria conscia.",
                cascade_id=cascade_id,
            )

    def update_memory(
        self,
        memory_id: str,
        patch: dict[str, Any],
        *,
        changed_by: str = "memory_service",
        change_reason: str = "Aggiornamento del ricordo.",
        cascade_id: str | None = None,
    ) -> dict[str, Any]:
        """Aggiornamento interno con revisione, usato dal motore di evoluzione.

        Non viene registrato come tool MCP: il server può creare ricordi, ma
        non riscrivere il passato con un semplice patch. I ricordi latenti o
        repressi non sono modificabili da questa superficie.
        """

        timestamp = now_iso(self.config.timezone_name())
        with self.db.timeline() as conn:
            return _update_episodic(
                conn,
                memory_id,
                patch,
                timestamp=timestamp,
                changed_by=changed_by,
                change_reason=change_reason,
                cascade_id=cascade_id,
            )

    def add_memory_link(
        self,
        from_memory_id: str,
        to_memory_id: str,
        relation_type: str,
        strength: float = 0.5,
        note: str | None = None,
    ) -> dict[str, Any]:
        from_memory_id = _require_str(from_memory_id, "from_memory_id")
        to_memory_id = _require_str(to_memory_id, "to_memory_id")
        if from_memory_id == to_memory_id:
            raise ValidationError("Un ricordo non può essere collegato a se stesso.")
        if relation_type not in RELATION_TYPES:
            raise ValidationError(
                f"relation_type: valore non valido. Ammessi: {', '.join(sorted(RELATION_TYPES))}."
            )
        strength = _require_number(strength, "strength", 0.0, 1.0)
        note = _optional_str(note, "note")

        with self.db.timeline() as conn:
            available = conn.execute(
                "SELECT COUNT(*) FROM conscious_episodic_memories WHERE id IN (?, ?)",
                (from_memory_id, to_memory_id),
            ).fetchone()[0]
            if available != 2:
                raise NotFoundError("Ricordi non trovati nella memoria conscia.")
            try:
                with conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO memory_links (
                            from_memory_id, to_memory_id, relation_type,
                            strength, note, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            from_memory_id,
                            to_memory_id,
                            relation_type,
                            strength,
                            note,
                            now_iso(self.config.timezone_name()),
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raise ConflictError(
                    f"Collegamento già esistente o non valido: {exc}"
                ) from exc
            row = conn.execute(
                "SELECT * FROM memory_links WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return dict(row)

    def get_memory_links(self, memory_id: str) -> list[dict[str, Any]]:
        memory_id = _require_str(memory_id, "memory_id")
        with self.db.timeline() as conn:
            exists = conn.execute(
                "SELECT 1 FROM conscious_episodic_memories WHERE id = ?", (memory_id,)
            ).fetchone()
            if exists is None:
                return []
            rows = conn.execute(
                """
                SELECT l.*
                FROM memory_links AS l
                JOIN conscious_episodic_memories AS m_from
                    ON m_from.id = l.from_memory_id
                JOIN conscious_episodic_memories AS m_to
                    ON m_to.id = l.to_memory_id
                WHERE l.from_memory_id = ? OR l.to_memory_id = ?
                ORDER BY l.id ASC
                """,
                (memory_id, memory_id),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- diagnostica --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": False,
            "identity_id": None,
            "config": {},
            "databases": self.db.health(),
        }
        try:
            configs = self.config.load_all()
            result["identity_id"] = configs["identity"]["identity_id"]
            result["config"] = {
                "ok": True,
                "files": list(CONFIG_FILENAMES.values()),
            }
        except ConfigError as exc:
            result["config"] = {"ok": False, "error": str(exc)}

        result["ok"] = bool(
            result["config"].get("ok") and result["databases"].get("ok")
        )
        return result


# ---------------------------------------------------------------------------
# Memoria latente
# ---------------------------------------------------------------------------


class ShadowService:
    """Accesso interno all'ombra: mai registrato come tool MCP."""

    def __init__(self, root: str | Path | None = None):
        self.config = ConfigStore(root)
        self.db = Database(self.config.root)
        self.root = self.config.root
        self._shadow = self.config.get("shadow_traits")

        policy = self._shadow.get("access_policy", {})
        if policy.get("conscious_direct_read") is not False:
            raise ConfigError(
                "shadow_traits.access_policy.conscious_direct_read deve essere false."
            )
        if policy.get("rag_exposure") != "never_by_default":
            raise ConfigError(
                "shadow_traits.access_policy.rag_exposure deve essere 'never_by_default'."
            )
        self._trait_ids = {
            trait["id"] for trait in self._shadow.get("traits", [])
        }

    # -- lettura ------------------------------------------------------------

    def get_shadow_traits(self) -> dict[str, Any]:
        return copy.deepcopy(self._shadow)

    def list_traits(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._shadow.get("traits", []))

    def get_trait(self, trait_id: str) -> dict[str, Any] | None:
        trait_id = _require_str(trait_id, "trait_id")
        for trait in self._shadow.get("traits", []):
            if trait["id"] == trait_id:
                return copy.deepcopy(trait)
        return None

    def list_shadow_entries(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        entry_type: str | None = None,
        trait_id: str | None = None,
        memory_id: str | None = None,
        access_level: str | None = None,
    ) -> list[dict[str, Any]]:
        limit = _clamp_limit(limit, default=50)
        offset = _clamp_offset(offset)
        clauses: list[str] = []
        params: list[Any] = []

        if entry_type is not None:
            if entry_type not in SHADOW_ENTRY_TYPES:
                raise ValidationError(f"entry_type: valore non valido '{entry_type}'.")
            clauses.append("entry_type = ?")
            params.append(entry_type)
        if trait_id is not None:
            clauses.append("linked_trait_id = ?")
            params.append(_require_str(trait_id, "trait_id"))
        if memory_id is not None:
            clauses.append("linked_memory_id = ?")
            params.append(_require_str(memory_id, "memory_id"))
        if access_level is not None:
            if access_level not in SHADOW_ACCESS_LEVELS:
                raise ValidationError(f"access_level: valore non valido '{access_level}'.")
            clauses.append("access_level = ?")
            params.append(access_level)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT * FROM shadow_entries {where} "
            "ORDER BY entry_time DESC, id DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        with self.db.shadow() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [decode_shadow(row) for row in rows]

    def get_shadow_entry(self, entry_id: str) -> dict[str, Any] | None:
        entry_id = _require_str(entry_id, "entry_id")
        with self.db.shadow() as conn:
            row = conn.execute(
                "SELECT * FROM shadow_entries WHERE id = ?", (entry_id,)
            ).fetchone()
        return decode_shadow(row) if row is not None else None

    def get_episodic_memory(self, memory_id: str) -> dict[str, Any] | None:
        """Lettura interna, inclusi ricordi latenti e repressi."""

        memory_id = _require_str(memory_id, "memory_id")
        with self.db.timeline() as conn:
            row = conn.execute(
                "SELECT * FROM episodic_memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return decode_memory(row) if row is not None else None

    # -- scrittura ----------------------------------------------------------

    def create_episodic_memory(
        self,
        *,
        title: str,
        description: str,
        importance: int = 5,
        source: str = "generated",
        event_time: str | None = None,
        place: str | None = None,
        people: list[str] | None = None,
        emotional_state: dict[str, Any] | None = None,
        consequences: str | None = None,
        source_ref: str | None = None,
        tags: list[str] | None = None,
        reliability: float = 0.9,
        is_simulated: bool | None = None,
        access_level: str = "latent",
        access_reason: str | None = None,
        memory_id: str | None = None,
    ) -> dict[str, Any]:
        if access_level not in {"latent", "repressed"}:
            raise ValidationError(
                "access_level: ShadowService può creare solo ricordi latent o repressed."
            )
        timestamp = now_iso(self.config.timezone_name())
        if event_time is None:
            event_time = timestamp
        with self.db.timeline() as conn:
            return _insert_episodic(
                conn,
                event_time=event_time,
                title=title,
                description=description,
                importance=importance,
                source=source,
                place=place,
                people=people,
                emotional_state=emotional_state,
                consequences=consequences,
                source_ref=source_ref,
                reliability=reliability,
                is_simulated=is_simulated,
                access_level=access_level,
                access_reason=access_reason,
                is_consolidated=True,
                tags=tags,
                memory_id=memory_id,
                timestamp=timestamp,
                changed_by="shadow_engine",
                change_reason="Creazione di un ricordo latente o represso.",
            )

    def create_shadow_entry(
        self,
        *,
        entry_type: str,
        title: str,
        content: str,
        intensity: int,
        source: str,
        entry_time: str | None = None,
        linked_memory_id: str | None = None,
        linked_trait_id: str | None = None,
        trigger: str | None = None,
        emotions: dict[str, Any] | None = None,
        defense_mechanism: str = "none",
        conscious_manifestation: str | None = None,
        access_level: str = "latent",
        source_ref: str | None = None,
        entry_id: str | None = None,
    ) -> dict[str, Any]:
        if entry_type not in SHADOW_ENTRY_TYPES:
            raise ValidationError(
                f"entry_type: valore non valido. Ammessi: {', '.join(sorted(SHADOW_ENTRY_TYPES))}."
            )
        title = _require_str(title, "title")
        content = _require_str(content, "content")
        intensity = _require_int(intensity, "intensity", 1, 10)
        if source not in SHADOW_SOURCES:
            raise ValidationError(
                f"source: valore non valido. Ammessi: {', '.join(sorted(SHADOW_SOURCES))}."
            )
        if defense_mechanism not in DEFENSE_MECHANISMS:
            raise ValidationError(
                f"defense_mechanism: valore non valido '{defense_mechanism}'."
            )
        if access_level not in SHADOW_ACCESS_LEVELS:
            raise ValidationError(
                f"access_level: valore non valido '{access_level}'."
            )
        if entry_time is None:
            entry_time = now_iso(self.config.timezone_name())
        entry_time = _require_datetime(entry_time, "entry_time")
        trigger = _optional_str(trigger, "trigger")
        emotions = _object(emotions, "emotions")
        conscious_manifestation = _optional_str(
            conscious_manifestation, "conscious_manifestation"
        )
        source_ref = _optional_str(source_ref, "source_ref")

        if linked_trait_id is not None:
            linked_trait_id = _require_str(linked_trait_id, "linked_trait_id")
            if linked_trait_id not in self._trait_ids:
                raise ValidationError(
                    f"linked_trait_id: tratto sconosciuto '{linked_trait_id}'."
                )

        if linked_memory_id is not None:
            linked_memory_id = _require_str(linked_memory_id, "linked_memory_id")
            with self.db.timeline() as timeline:
                exists = timeline.execute(
                    "SELECT 1 FROM episodic_memories WHERE id = ?", (linked_memory_id,)
                ).fetchone()
            if exists is None:
                raise ValidationError(
                    f"linked_memory_id: ricordo sconosciuto '{linked_memory_id}'."
                )

        if entry_id is None:
            entry_id = _new_id("shd")
        else:
            entry_id = _require_str(entry_id, "entry_id")

        timestamp = now_iso(self.config.timezone_name())
        row = {
            "id": entry_id,
            "entry_time": entry_time,
            "created_at": timestamp,
            "updated_at": timestamp,
            "entry_type": entry_type,
            "title": title,
            "content": content,
            "linked_memory_id": linked_memory_id,
            "linked_trait_id": linked_trait_id,
            "trigger": trigger,
            "emotions_json": json_text(emotions),
            "intensity": intensity,
            "defense_mechanism": defense_mechanism,
            "conscious_manifestation": conscious_manifestation,
            "access_level": access_level,
            "source": source,
            "source_ref": source_ref,
            "version": 1,
            "previous_version_id": None,
        }
        columns = list(row)
        sql = (
            f"INSERT INTO shadow_entries ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})"
        )

        with self.db.shadow() as conn:
            try:
                with conn:
                    conn.execute(sql, tuple(row[column] for column in columns))
                    inserted = conn.execute(
                        "SELECT * FROM shadow_entries WHERE id = ?", (entry_id,)
                    ).fetchone()
                    conn.execute(
                        """
                        INSERT INTO shadow_revisions (
                            entry_id, revision_number, changed_at, changed_by,
                            change_reason, previous_snapshot_json, new_snapshot_json, cascade_id
                        ) VALUES (?, 1, ?, ?, ?, NULL, ?, NULL)
                        """,
                        (
                            entry_id,
                            timestamp,
                            "shadow_engine",
                            "Creazione della voce nel diario dell'ombra.",
                            json.dumps(dict(inserted), ensure_ascii=False, indent=2),
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raise ConflictError(
                    f"Impossibile creare la voce '{entry_id}': {exc}"
                ) from exc
        return decode_shadow(inserted)

    def record_trigger(
        self,
        *,
        trait_id: str,
        trigger_description: str,
        intensity: int,
        triggered_at: str | None = None,
        defense_mechanism: str = "none",
        response_summary: str | None = None,
        related_entry_id: str | None = None,
        resolved: bool = False,
    ) -> dict[str, Any]:
        trait_id = _require_str(trait_id, "trait_id")
        if trait_id not in self._trait_ids:
            raise ValidationError(f"trait_id: tratto sconosciuto '{trait_id}'.")
        trigger_description = _require_str(trigger_description, "trigger_description")
        intensity = _require_int(intensity, "intensity", 1, 10)
        if defense_mechanism not in DEFENSE_MECHANISMS:
            raise ValidationError(
                f"defense_mechanism: valore non valido '{defense_mechanism}'."
            )
        resolved = _require_bool(resolved, "resolved")
        if triggered_at is None:
            triggered_at = now_iso(self.config.timezone_name())
        triggered_at = _require_datetime(triggered_at, "triggered_at")
        response_summary = _optional_str(response_summary, "response_summary")

        if related_entry_id is not None:
            related_entry_id = _require_str(related_entry_id, "related_entry_id")
            with self.db.shadow() as conn:
                exists = conn.execute(
                    "SELECT 1 FROM shadow_entries WHERE id = ?", (related_entry_id,)
                ).fetchone()
            if exists is None:
                raise ValidationError(
                    f"related_entry_id: voce sconosciuta '{related_entry_id}'."
                )

        with self.db.shadow() as conn:
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO shadow_trigger_log (
                            trait_id, triggered_at, trigger_description, intensity,
                            defense_mechanism, response_summary, related_entry_id, resolved
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            trait_id,
                            triggered_at,
                            trigger_description,
                            intensity,
                            defense_mechanism,
                            response_summary,
                            related_entry_id,
                            1 if resolved else 0,
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raise ConflictError(f"Trigger non valido: {exc}") from exc
            row = conn.execute(
                """
                SELECT * FROM shadow_trigger_log
                WHERE trait_id = ? AND triggered_at = ? AND trigger_description = ?
                """,
                (trait_id, triggered_at, trigger_description),
            ).fetchone()
        return dict(row)

    # -- diagnostica --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        database = self.db.health()["shadow_diary"]
        policy = self._shadow.get("access_policy", {})
        result = {
            "ok": bool(
                database.get("ok")
                and policy.get("conscious_direct_read") is False
                and policy.get("rag_exposure") == "never_by_default"
            ),
            "access_policy": {
                "conscious_direct_read": policy.get("conscious_direct_read"),
                "rag_exposure": policy.get("rag_exposure"),
            },
            "trait_count": len(self._trait_ids),
            "database": database,
        }
        return result
