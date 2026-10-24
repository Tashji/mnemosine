"""Archivio della memoria semantica.

La memoria semantica conserva conoscenze generali, non esperienze personali.
Ogni elemento dichiara il tipo di conoscenza, la fonte, l'affidabilità e lo
stato. Le contraddizioni non sovrascrivono automaticamente un contenuto:
vengono registrate come collegamenti tipizzati e restano entrambe tracciabili.

Questo modulo è una superficie conscia. Non legge né scrive il diario
dell'ombra; i collegamenti verso la timeline episodica sono ammessi solo per
ricordi presenti nella vista ``conscious_episodic_memories``.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import ConfigStore, now_iso
from .db import Database, json_text
from .errors import ConflictError, DatabaseUnavailable, NotFoundError, ValidationError
from .service import (
    _clamp_limit,
    _clamp_offset,
    _like_pattern,
    _optional_str,
    _require_int,
    _require_number,
    _require_str,
    _string_list,
)


KNOWLEDGE_TYPES = {
    "verified_fact",
    "user_provided",
    "opinion",
    "hypothesis",
    "uncertain",
}
SEMANTIC_SOURCES = {
    "conversation",
    "document",
    "admin_seed",
    "reflection",
    "inference",
    "external_source",
}
SEMANTIC_STATUSES = {"active", "superseded", "retracted"}
ITEM_RELATIONS = {
    "related_to",
    "refines",
    "derived_from",
    "contradicts",
    "supersedes",
}
EPISODIC_RELATIONS = {
    "related_to",
    "explains",
    "illustrates",
    "derived_from",
    "contradicts",
}


def _new_id(prefix: str = "sem") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["tags"] = json.loads(data.pop("tags_json", "[]") or "[]")
    data["metadata"] = json.loads(data.pop("metadata_json", "{}") or "{}")
    return data


def _snapshot(row: sqlite3.Row | dict[str, Any]) -> str:
    return json.dumps(dict(row), ensure_ascii=False, indent=2)


def _validate_reliability(value: Any) -> float:
    return _require_number(value, "reliability", 0.0, 1.0)


def _validate_importance(value: Any) -> int:
    return _require_int(value, "importance", 1, 10)


def _validate_date_or_datetime(value: Any, field: str) -> str | None:
    if value is None:
        return None
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


def _validate_metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError("metadata: atteso un oggetto JSON.")
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError("metadata: oggetto non serializzabile in JSON.") from exc
    return copy.deepcopy(value)


def _validate_quality_rules(
    knowledge_type: str, source: str, reliability: float
) -> None:
    """Impedisce che un'opinione venga presentata come fatto verificato."""

    if knowledge_type == "verified_fact":
        if source not in {"admin_seed", "document", "external_source"}:
            raise ValidationError(
                "Un fatto verificato richiede una fonte documentale, esterna o "
                "amministrativa; non può provenire da conversazione, riflessione o inferenza."
            )
        if reliability < 0.5:
            raise ValidationError(
                "Un fatto verificato richiede reliability >= 0.5."
            )
    if knowledge_type == "user_provided" and source not in {
        "conversation",
        "document",
        "external_source",
    }:
        raise ValidationError(
            "Una conoscenza user_provided richiede una fonte conversation, "
            "document o external_source."
        )


class SemanticService:
    """Superficie conscia dell'archivio delle conoscenze generali."""

    def __init__(self, root: str | Path | None = None):
        self.config = ConfigStore(root)
        self.db = Database(self.config.root)
        self.root = self.config.root
        self.config.load_all()

    # -- lettura ------------------------------------------------------------

    def list_items(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        category: str | None = None,
        knowledge_type: str | None = None,
        status: str | None = "active",
        source: str | None = None,
        min_importance: int | None = None,
        tag: str | None = None,
        acquired_after: str | None = None,
        acquired_before: str | None = None,
        order: str = "desc",
    ) -> list[dict[str, Any]]:
        limit = _clamp_limit(limit)
        offset = _clamp_offset(offset)
        order = (order or "desc").lower()
        if order not in {"asc", "desc"}:
            raise ValidationError("order: ammessi 'asc' o 'desc'.")

        clauses: list[str] = []
        params: list[Any] = []
        if category is not None:
            clauses.append("category = ?")
            params.append(_require_str(category, "category"))
        if knowledge_type is not None:
            if knowledge_type not in KNOWLEDGE_TYPES:
                raise ValidationError(
                    f"knowledge_type: valore non valido '{knowledge_type}'."
                )
            clauses.append("knowledge_type = ?")
            params.append(knowledge_type)
        if status is not None:
            if status not in SEMANTIC_STATUSES:
                raise ValidationError(f"status: valore non valido '{status}'.")
            clauses.append("status = ?")
            params.append(status)
        if source is not None:
            if source not in SEMANTIC_SOURCES:
                raise ValidationError(f"source: valore non valido '{source}'.")
            clauses.append("source = ?")
            params.append(source)
        if min_importance is not None:
            clauses.append("importance >= ?")
            params.append(_require_int(min_importance, "min_importance", 1, 10))
        if tag is not None:
            tag_json = json.dumps(_require_str(tag, "tag"), ensure_ascii=False)
            clauses.append("tags_json LIKE ? ESCAPE '\\'")
            params.append(_like_pattern(tag_json))
        for value, column, operator, field in (
            (acquired_after, "created_at", ">=", "acquired_after"),
            (acquired_before, "created_at", "<=", "acquired_before"),
        ):
            if value is None:
                continue
            clauses.append(f"{column} {operator} ?")
            params.append(_validate_date_or_datetime(value, field))

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT * FROM semantic_items {where} "
            f"ORDER BY updated_at {order}, id {order} LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        with self.db.semantic() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_decode(row) for row in rows]

    def search_items(
        self,
        query: str | None = None,
        *,
        limit: int = 20,
        offset: int = 0,
        category: str | None = None,
        knowledge_type: str | None = None,
        status: str | None = "active",
        source: str | None = None,
        min_importance: int | None = None,
        tag: str | None = None,
        acquired_after: str | None = None,
        acquired_before: str | None = None,
        order: str = "desc",
    ) -> list[dict[str, Any]]:
        """Ricerca lessicale, usata come segnale del recupero ibrido."""

        if query is not None:
            query = _require_str(query, "query")
        if not query:
            return self.list_items(
                limit=limit,
                offset=offset,
                category=category,
                knowledge_type=knowledge_type,
                status=status,
                source=source,
                min_importance=min_importance,
                tag=tag,
                acquired_after=acquired_after,
                acquired_before=acquired_before,
                order=order,
            )
        items = self.list_items(
            limit=100,
            offset=0,
            category=category,
            knowledge_type=knowledge_type,
            status=status,
            source=source,
            min_importance=min_importance,
            tag=tag,
            acquired_after=acquired_after,
            acquired_before=acquired_before,
            order=order,
        )
        needle = query.lower()
        scored: list[tuple[int, dict[str, Any]]] = []
        for item in items:
            haystack = " ".join(
                [
                    str(item.get("title", "")),
                    str(item.get("content", "")),
                    str(item.get("category", "")),
                    " ".join(str(tag_item) for tag_item in item.get("tags", [])),
                ]
            ).lower()
            if needle not in haystack:
                continue
            score = 0
            if needle in str(item.get("title", "")).lower():
                score += 3
            if needle in str(item.get("content", "")).lower():
                score += 2
            if any(needle in str(tag_item).lower() for tag_item in item.get("tags", [])):
                score += 1
            scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["updated_at"], pair[1]["id"]))
        start = _clamp_offset(offset)
        return [item for _score, item in scored[start : start + _clamp_limit(limit)]]

    def get_item(
        self, item_id: str, *, include_inactive: bool = False
    ) -> dict[str, Any] | None:
        item_id = _require_str(item_id, "item_id")
        view = "semantic_items" if include_inactive else "active_semantic_items"
        with self.db.semantic() as conn:
            row = conn.execute(
                f"SELECT * FROM {view} WHERE id = ?", (item_id,)
            ).fetchone()
        return _decode(row) if row is not None else None

    def get_item_or_raise(self, item_id: str) -> dict[str, Any]:
        item = self.get_item(item_id)
        if item is None:
            raise NotFoundError(f"Conoscenza non trovata: {item_id}.")
        return item

    def all_active_items(self) -> list[dict[str, Any]]:
        """Restituisce l'intero archivio attivo, senza paginazione.

        Serve alla sincronizzazione dell'indice: la fonte di verità resta
        SQLite, non l'indice vettoriale.
        """

        with self.db.semantic() as conn:
            rows = conn.execute(
                "SELECT * FROM active_semantic_items ORDER BY id ASC"
            ).fetchall()
        return [_decode(row) for row in rows]

    def filter_item_ids(
        self,
        *,
        category: str | None = None,
        knowledge_type: str | None = None,
        status: str | None = "active",
        source: str | None = None,
        min_importance: int | None = None,
        tag: str | None = None,
        acquired_after: str | None = None,
        acquired_before: str | None = None,
    ) -> list[str]:
        """Restituisce tutti gli ID compatibili, senza paginazione.

        Serve a pre-filtrare i candidati prima della ricerca vettoriale.
        """

        clauses: list[str] = []
        params: list[Any] = []
        if category is not None:
            clauses.append("category = ?")
            params.append(_require_str(category, "category"))
        if knowledge_type is not None:
            if knowledge_type not in KNOWLEDGE_TYPES:
                raise ValidationError(
                    f"knowledge_type: valore non valido '{knowledge_type}'."
                )
            clauses.append("knowledge_type = ?")
            params.append(knowledge_type)
        if status is not None:
            if status not in SEMANTIC_STATUSES:
                raise ValidationError(f"status: valore non valido '{status}'.")
            clauses.append("status = ?")
            params.append(status)
        if source is not None:
            if source not in SEMANTIC_SOURCES:
                raise ValidationError(f"source: valore non valido '{source}'.")
            clauses.append("source = ?")
            params.append(source)
        if min_importance is not None:
            clauses.append("importance >= ?")
            params.append(_require_int(min_importance, "min_importance", 1, 10))
        if tag is not None:
            tag_json = json.dumps(_require_str(tag, "tag"), ensure_ascii=False)
            clauses.append("tags_json LIKE ? ESCAPE '\\'")
            params.append(_like_pattern(tag_json))
        for value, column, operator, field in (
            (acquired_after, "created_at", ">=", "acquired_after"),
            (acquired_before, "created_at", "<=", "acquired_before"),
        ):
            if value is None:
                continue
            clauses.append(f"{column} {operator} ?")
            params.append(_validate_date_or_datetime(value, field))

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.semantic() as conn:
            rows = conn.execute(
                f"SELECT id FROM semantic_items {where} ORDER BY id ASC", params
            ).fetchall()
        return [row["id"] for row in rows]

    # -- scrittura ----------------------------------------------------------

    def create_item(
        self,
        *,
        title: str,
        content: str,
        category: str,
        knowledge_type: str,
        source: str,
        source_ref: str | None = None,
        reliability: float = 0.5,
        importance: int = 5,
        status: str = "active",
        valid_from: str | None = None,
        valid_to: str | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        item_id: str | None = None,
        changed_by: str = "semantic_service",
        change_reason: str = "Creazione della conoscenza.",
        cascade_id: str | None = None,
    ) -> dict[str, Any]:
        title = _require_str(title, "title")
        content = _require_str(content, "content")
        category = _require_str(category, "category")
        if knowledge_type not in KNOWLEDGE_TYPES:
            raise ValidationError(
                f"knowledge_type: valore non valido '{knowledge_type}'. "
                f"Ammessi: {', '.join(sorted(KNOWLEDGE_TYPES))}."
            )
        if source not in SEMANTIC_SOURCES:
            raise ValidationError(
                f"source: valore non valido '{source}'. "
                f"Ammessi: {', '.join(sorted(SEMANTIC_SOURCES))}."
            )
        if status not in SEMANTIC_STATUSES:
            raise ValidationError(f"status: valore non valido '{status}'.")
        reliability = _validate_reliability(reliability)
        importance = _validate_importance(importance)
        _validate_quality_rules(knowledge_type, source, reliability)
        source_ref = _optional_str(source_ref, "source_ref")
        cascade_id = _optional_str(cascade_id, "cascade_id")
        valid_from = _validate_date_or_datetime(valid_from, "valid_from")
        valid_to = _validate_date_or_datetime(valid_to, "valid_to")
        if valid_from and valid_to and valid_from > valid_to:
            raise ValidationError("valid_from non può essere successiva a valid_to.")
        tags = _string_list(tags, "tags")
        metadata = _validate_metadata(metadata)

        if item_id is None:
            item_id = _new_id()
        else:
            item_id = _require_str(item_id, "item_id")

        timestamp = now_iso(self.config.timezone_name())
        row = {
            "id": item_id,
            "title": title,
            "content": content,
            "category": category,
            "knowledge_type": knowledge_type,
            "source": source,
            "source_ref": source_ref,
            "reliability": reliability,
            "importance": importance,
            "status": status,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "tags_json": json_text(tags),
            "metadata_json": json_text(metadata),
            "embedding_model": None,
            "embedding_updated_at": None,
            "version": 1,
            "previous_version_id": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        columns = list(row)
        sql = (
            f"INSERT INTO semantic_items ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})"
        )
        try:
            with self.db.semantic() as conn:
                with conn:
                    conn.execute(sql, tuple(row[column] for column in columns))
                    conn.execute(
                        """
                        INSERT INTO semantic_revisions (
                            item_id, revision_number, changed_at, changed_by,
                            change_reason, previous_snapshot_json,
                            new_snapshot_json, cascade_id
                        ) VALUES (?, 1, ?, ?, ?, NULL, ?, ?)
                        """,
                        (
                            item_id,
                            timestamp,
                            changed_by,
                            change_reason,
                            _snapshot(row),
                            cascade_id,
                        ),
                    )
                    inserted = conn.execute(
                        "SELECT * FROM semantic_items WHERE id = ?", (item_id,)
                    ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ConflictError(
                f"Impossibile creare la conoscenza '{item_id}': {exc}"
            ) from exc
        return _decode(inserted)

    def update_item(
        self,
        item_id: str,
        patch: dict[str, Any],
        *,
        changed_by: str = "semantic_service",
        change_reason: str = "Aggiornamento della conoscenza.",
        cascade_id: str | None = None,
    ) -> dict[str, Any]:
        item_id = _require_str(item_id, "item_id")
        if not isinstance(patch, dict) or not patch:
            raise ValidationError("Il patch della conoscenza deve essere un oggetto non vuoto.")

        allowed = {
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
            "tags",
            "metadata",
        }
        unknown = sorted(set(patch) - allowed)
        if unknown:
            raise ValidationError(
                "Campi non modificabili o sconosciuti: " + ", ".join(unknown) + "."
            )

        with self.db.semantic() as conn:
            current = conn.execute(
                "SELECT * FROM semantic_items WHERE id = ?", (item_id,)
            ).fetchone()
            if current is None:
                raise NotFoundError(f"Conoscenza non trovata: {item_id}.")

            merged = _decode(current)
            for key, value in patch.items():
                merged[key] = copy.deepcopy(value)

            title = _require_str(merged["title"], "title")
            content = _require_str(merged["content"], "content")
            category = _require_str(merged["category"], "category")
            knowledge_type = merged["knowledge_type"]
            source = merged["source"]
            status = merged["status"]
            if knowledge_type not in KNOWLEDGE_TYPES:
                raise ValidationError(
                    f"knowledge_type: valore non valido '{knowledge_type}'."
                )
            if source not in SEMANTIC_SOURCES:
                raise ValidationError(f"source: valore non valido '{source}'.")
            if status not in SEMANTIC_STATUSES:
                raise ValidationError(f"status: valore non valido '{status}'.")
            reliability = _validate_reliability(merged["reliability"])
            importance = _validate_importance(merged["importance"])
            _validate_quality_rules(knowledge_type, source, reliability)
            source_ref = _optional_str(merged.get("source_ref"), "source_ref")
            cascade_id = _optional_str(cascade_id, "cascade_id")
            valid_from = _validate_date_or_datetime(
                merged.get("valid_from"), "valid_from"
            )
            valid_to = _validate_date_or_datetime(merged.get("valid_to"), "valid_to")
            if valid_from and valid_to and valid_from > valid_to:
                raise ValidationError("valid_from non può essere successiva a valid_to.")
            tags = _string_list(merged.get("tags"), "tags")
            metadata = _validate_metadata(merged.get("metadata"))

            new_version = int(current["version"]) + 1
            timestamp = now_iso(self.config.timezone_name())
            new_row = {
                "title": title,
                "content": content,
                "category": category,
                "knowledge_type": knowledge_type,
                "source": source,
                "source_ref": source_ref,
                "reliability": reliability,
                "importance": importance,
                "status": status,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "tags_json": json_text(tags),
                "metadata_json": json_text(metadata),
                "embedding_model": None,
                "embedding_updated_at": None,
                "version": new_version,
                "updated_at": timestamp,
            }
            assignments = ", ".join(f"{column} = ?" for column in new_row)
            try:
                with conn:
                    conn.execute(
                        f"UPDATE semantic_items SET {assignments} WHERE id = ?",
                        tuple(new_row[column] for column in new_row) + (item_id,),
                    )
                    updated = conn.execute(
                        "SELECT * FROM semantic_items WHERE id = ?", (item_id,)
                    ).fetchone()
                    conn.execute(
                        """
                        INSERT INTO semantic_revisions (
                            item_id, revision_number, changed_at, changed_by,
                            change_reason, previous_snapshot_json,
                            new_snapshot_json, cascade_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item_id,
                            new_version,
                            timestamp,
                            changed_by,
                            change_reason,
                            _snapshot(current),
                            _snapshot(updated),
                            cascade_id,
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raise ConflictError(
                    f"Impossibile aggiornare la conoscenza '{item_id}': {exc}"
                ) from exc
        return _decode(updated)

    def link_items(
        self,
        from_item_id: str,
        to_item_id: str,
        relation_type: str = "related_to",
        strength: float = 0.5,
        note: str | None = None,
    ) -> dict[str, Any]:
        from_item_id = _require_str(from_item_id, "from_item_id")
        to_item_id = _require_str(to_item_id, "to_item_id")
        if from_item_id == to_item_id:
            raise ValidationError("Una conoscenza non può essere collegata a se stessa.")
        if relation_type not in ITEM_RELATIONS:
            raise ValidationError(
                f"relation_type: valore non valido '{relation_type}'. "
                f"Ammessi: {', '.join(sorted(ITEM_RELATIONS))}."
            )
        strength = _require_number(strength, "strength", 0.0, 1.0)
        note = _optional_str(note, "note")

        with self.db.semantic() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM semantic_items WHERE id IN (?, ?)",
                (from_item_id, to_item_id),
            ).fetchone()[0]
            if count != 2:
                raise NotFoundError("Una o entrambe le conoscenze non esistono.")
            try:
                with conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO semantic_item_links (
                            from_item_id, to_item_id, relation_type,
                            strength, note, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            from_item_id,
                            to_item_id,
                            relation_type,
                            strength,
                            note,
                            now_iso(self.config.timezone_name()),
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raise ConflictError(f"Collegamento già esistente: {exc}") from exc
            row = conn.execute(
                "SELECT * FROM semantic_item_links WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return dict(row)

    def link_to_memory(
        self,
        semantic_id: str,
        memory_id: str,
        relation_type: str = "related_to",
        strength: float = 0.5,
        note: str | None = None,
    ) -> dict[str, Any]:
        semantic_id = _require_str(semantic_id, "semantic_id")
        memory_id = _require_str(memory_id, "memory_id")
        if relation_type not in EPISODIC_RELATIONS:
            raise ValidationError(
                f"relation_type: valore non valido '{relation_type}'. "
                f"Ammessi: {', '.join(sorted(EPISODIC_RELATIONS))}."
            )
        strength = _require_number(strength, "strength", 0.0, 1.0)
        note = _optional_str(note, "note")

        with self.db.semantic() as conn:
            exists = conn.execute(
                "SELECT 1 FROM semantic_items WHERE id = ?", (semantic_id,)
            ).fetchone()
            if exists is None:
                raise NotFoundError(f"Conoscenza non trovata: {semantic_id}.")

        # La tabella episodica vive in un altro file: si verifica la vista
        # conscia, non la tabella completa. Un ricordo latente o represso
        # non deve poter essere collegato dalla superficie semantica.
        with self.db.timeline() as timeline:
            visible = timeline.execute(
                "SELECT 1 FROM conscious_episodic_memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
        if visible is None:
            raise NotFoundError(
                f"Ricordo non trovato nella memoria conscia: {memory_id}."
            )

        with self.db.semantic() as conn:
            try:
                with conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO semantic_episodic_links (
                            semantic_id, memory_id, relation_type,
                            strength, note, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            semantic_id,
                            memory_id,
                            relation_type,
                            strength,
                            note,
                            now_iso(self.config.timezone_name()),
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raise ConflictError(f"Collegamento già esistente: {exc}") from exc
            row = conn.execute(
                "SELECT * FROM semantic_episodic_links WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return dict(row)

    def get_links(
        self, item_id: str, *, include_inactive: bool = False
    ) -> dict[str, Any]:
        item_id = _require_str(item_id, "item_id")
        view = "semantic_items" if include_inactive else "active_semantic_items"
        with self.db.semantic() as conn:
            exists = conn.execute(
                f"SELECT 1 FROM {view} WHERE id = ?", (item_id,)
            ).fetchone()
            if exists is None:
                raise NotFoundError(f"Conoscenza non trovata: {item_id}.")
            item_links = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT l.*, f.title AS from_title, t.title AS to_title
                    FROM semantic_item_links AS l
                    JOIN semantic_items AS f ON f.id = l.from_item_id
                    JOIN semantic_items AS t ON t.id = l.to_item_id
                    WHERE l.from_item_id = ? OR l.to_item_id = ?
                    ORDER BY l.id ASC
                    """,
                    (item_id, item_id),
                ).fetchall()
            ]
            episodic_links = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM semantic_episodic_links
                    WHERE semantic_id = ?
                    ORDER BY id ASC
                    """,
                    (item_id,),
                ).fetchall()
            ]

        # La superficie semantica è conscia: se un ricordo collegato è
        # diventato latente o represso, il collegamento non deve rivelarne
        # l'esistenza. La verifica avviene sulla vista, non sulla tabella.
        if episodic_links:
            memory_ids = {link["memory_id"] for link in episodic_links}
            with self.db.timeline() as timeline:
                placeholders = ", ".join("?" for _ in memory_ids)
                visible = {
                    row["id"]
                    for row in timeline.execute(
                        f"SELECT id FROM conscious_episodic_memories "
                        f"WHERE id IN ({placeholders})",
                        tuple(memory_ids),
                    ).fetchall()
                }
            episodic_links = [
                link for link in episodic_links if link["memory_id"] in visible
            ]

        return {
            "item_id": item_id,
            "semantic_links": item_links,
            "episodic_links": episodic_links,
        }

    def count_revisions(self, item_id: str) -> int:
        item_id = _require_str(item_id, "item_id")
        with self.db.semantic() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM semantic_revisions WHERE item_id = ?",
                    (item_id,),
                ).fetchone()[0]
            )

    # -- diagnostica --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": False,
            "path": str(self.db.semantic_path),
            "counts": {},
        }
        try:
            with self.db.semantic() as conn:
                integrity = [
                    row[0] for row in conn.execute("PRAGMA integrity_check").fetchall()
                ]
                fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                result["integrity"] = integrity
                result["foreign_key_violations"] = len(fk_violations)
                result["counts"] = {
                    "items": int(
                        conn.execute("SELECT COUNT(*) FROM semantic_items").fetchone()[0]
                    ),
                    "active": int(
                        conn.execute(
                            "SELECT COUNT(*) FROM active_semantic_items"
                        ).fetchone()[0]
                    ),
                    "revisions": int(
                        conn.execute(
                            "SELECT COUNT(*) FROM semantic_revisions"
                        ).fetchone()[0]
                    ),
                    "item_links": int(
                        conn.execute(
                            "SELECT COUNT(*) FROM semantic_item_links"
                        ).fetchone()[0]
                    ),
                    "episodic_links": int(
                        conn.execute(
                            "SELECT COUNT(*) FROM semantic_episodic_links"
                        ).fetchone()[0]
                    ),
                }
                result["by_type"] = {
                    row["knowledge_type"]: int(row["count"])
                    for row in conn.execute(
                        "SELECT knowledge_type, COUNT(*) AS count "
                        "FROM semantic_items GROUP BY knowledge_type"
                    ).fetchall()
                }
                result["by_status"] = {
                    row["status"]: int(row["count"])
                    for row in conn.execute(
                        "SELECT status, COUNT(*) AS count "
                        "FROM semantic_items GROUP BY status"
                    ).fetchall()
                }
                result["ok"] = integrity == ["ok"] and not fk_violations
                if not result["ok"]:
                    result["error"] = (
                        "Integrity check o foreign key check fallita."
                    )
        except DatabaseUnavailable as exc:
            result["error"] = str(exc)
        except sqlite3.Error as exc:
            result["error"] = f"Errore SQLite: {exc}"
        return result
