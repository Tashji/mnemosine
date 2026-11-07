"""Controlli di coerenza temporale e semantica del profilo.

Il checker è in sola lettura: apre i database con ``mode=ro`` e non applica
migrazioni, non corregge righe e non riscrive configurazioni. Segnala soltanto
anomalie strutturali (integrità SQLite, foreign key, versioni, riferimenti
orfani, timestamp non validi, indice vettoriale disallineato) senza stampare
testi, telefoni o contenuti delle memorie.

Uscita: ``mnemosine-consistency --root <profilo>``. Il codice di uscita è 1
solo in presenza di errori; i warning non bloccano.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .config import CONFIG_FILENAMES, project_root
from .migrations import (
    DATABASE_SCHEMAS,
    JSON_DOCUMENTS,
    JSON_MIGRATIONS,
)
from .db import DATABASE_TABLES

EPISODIC_STATES = {
    "open",
    "decided",
    "executing",
    "closed",
    "expired",
    "cancelled",
}
RUN_STATES = {"ready", "running", "awaiting_verification", "completed", "uncertain"}
VECTOR_KINDS = ("episodic", "semantic")


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _version_part(value: Any) -> int | None:
    try:
        return int(str(value).split(".", 1)[0])
    except (TypeError, ValueError):
        return None


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10
    )
    conn.row_factory = sqlite3.Row
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        )
    }


def _count(conn: sqlite3.Connection, table: str) -> int | None:
    try:
        row = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    except sqlite3.Error:
        return None
    return None if row is None else int(row[0])


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql).fetchall()]


class ConsistencyChecker:
    """Esegue tutti i controlli e restituisce un report serializzabile."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root).expanduser().resolve() if root else project_root()
        self.data_dir = self.root / "data"

    @staticmethod
    def _check(name: str) -> dict[str, Any]:
        return {"name": name, "ok": True, "errors": [], "warnings": [], "details": {}}

    @staticmethod
    def _finish(check: dict[str, Any]) -> dict[str, Any]:
        check["ok"] = not check["errors"]
        return check

    # -- profilo e JSON ----------------------------------------------------

    def _check_profile(self) -> dict[str, Any]:
        check = self._check("profilo")
        if not self.root.is_dir():
            check["errors"].append(f"Profilo non trovato: {self.root}.")
            return self._finish(check)
        for name in ("config", "data"):
            if not (self.root / name).is_dir():
                check["errors"].append(f"Cartella mancante: {name}/.")
        check["details"]["root"] = str(self.root)
        check["details"]["files_dir"] = (self.root / "files").is_dir()
        return self._finish(check)

    def _check_config(self) -> dict[str, Any]:
        check = self._check("configurazione")
        config_dir = self.root / "config"
        if not config_dir.is_dir():
            check["errors"].append("Cartella config/ mancante.")
            return self._finish(check)
        identity_id: str | None = None
        for name, filename in CONFIG_FILENAMES.items():
            path = config_dir / filename
            if not path.is_file():
                check["errors"].append(f"Documento mancante: {filename}.")
                continue
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                check["errors"].append(
                    f"Documento JSON non leggibile: {filename}."
                )
                continue
            if not isinstance(document, dict):
                check["errors"].append(
                    f"Documento JSON non oggetto: {filename}."
                )
                continue
            supported = JSON_DOCUMENTS.get(name)
            version = _version_part(document.get("schema_version"))
            if supported is not None:
                if version is None:
                    check["warnings"].append(
                        f"schema_version assente in {filename}."
                    )
                elif version > supported:
                    check["errors"].append(
                        f"{filename}: schema {version} più recente del supportato "
                        f"{supported}."
                    )
                elif version < supported:
                    check["warnings"].append(
                        f"{filename}: schema {version}, migrazione in sospeso."
                    )
            current_id = document.get("identity_id")
            if isinstance(current_id, str) and current_id:
                if name == "identity":
                    identity_id = current_id
                elif identity_id is not None and current_id != identity_id:
                    check["errors"].append(
                        f"{filename}: identity_id diverso da identity.json."
                    )
        if identity_id is None:
            check["errors"].append("identity.json: identity_id mancante.")
        persona = config_dir / "persona.md"
        if not persona.is_file():
            check["warnings"].append("persona.md mancante.")
        elif persona.stat().st_size == 0:
            check["warnings"].append("persona.md è vuoto.")

        # I file accessori della dashboard non hanno una migrazione JSON, ma
        # devono almeno essere oggetti leggibili.
        known = set(CONFIG_FILENAMES.values())
        for path in sorted(config_dir.glob("*.json")):
            if path.name in known:
                continue
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                check["errors"].append(f"Documento JSON non leggibile: {path.name}.")
                continue
            if not isinstance(document, dict):
                check["warnings"].append(
                    f"Documento accessorio non oggetto: {path.name}."
                )
        check["details"]["documents"] = len(CONFIG_FILENAMES)
        check["details"]["migrations"] = {
            name: len(steps) for name, steps in JSON_MIGRATIONS.items() if steps
        }
        return self._finish(check)

    # -- database ----------------------------------------------------------

    def _open_databases(self) -> tuple[dict[str, sqlite3.Connection], list[dict[str, Any]]]:
        connections: dict[str, sqlite3.Connection] = {}
        checks: list[dict[str, Any]] = []
        for database, (_filename, supported) in DATABASE_SCHEMAS.items():
            check = self._check(f"database:{database}")
            path = self.data_dir / f"{database}.db"
            check["details"]["path"] = str(path)
            if not path.is_file():
                check["errors"].append(f"Database mancante: {database}.db.")
                checks.append(self._finish(check))
                continue
            try:
                conn = _connect(path)
            except sqlite3.Error as exc:
                check["errors"].append(
                    f"Apertura fallita per {database}.db: {type(exc).__name__}."
                )
                checks.append(self._finish(check))
                continue
            connections[database] = conn
            try:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()
                integrity_value = (
                    None if integrity is None else str(integrity[0])
                )
                check["details"]["integrity"] = integrity_value
                if integrity_value is None or integrity_value.casefold() != "ok":
                    check["errors"].append(
                        f"{database}.db: integrity_check non ha restituito ok."
                    )
                violations = conn.execute("PRAGMA foreign_key_check").fetchmany(20)
                check["details"]["foreign_key_violations"] = len(violations)
                if violations:
                    check["errors"].append(
                        f"{database}.db: {len(violations)} violazioni di foreign key."
                    )
                present = _tables(conn)
                required = DATABASE_TABLES[database]
                try:
                    row = conn.execute(
                        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                    ).fetchone()
                except sqlite3.Error:
                    row = None
                stored = None if row is None else str(row[0])
                version = _version_part(stored)
                missing = sorted(required - present - {"schema_meta"})
                if missing:
                    detail = f"oggetti mancanti: {', '.join(missing)}"
                    if version is None or version < supported:
                        check["warnings"].append(
                            f"{database}.db: migrazione in sospeso ({detail})."
                        )
                    else:
                        check["errors"].append(f"{database}.db: {detail}.")
                check["details"]["schema_version"] = stored
                check["details"]["schema_supported"] = supported
                if stored is None:
                    check["warnings"].append(
                        f"{database}.db: schema_meta.schema_version assente."
                    )
                elif version is None:
                    check["errors"].append(
                        f"{database}.db: schema_version non valida."
                    )
                elif version > supported:
                    check["errors"].append(
                        f"{database}.db: schema {version} più recente del supportato "
                        f"{supported}."
                    )
                elif version < supported:
                    check["warnings"].append(
                        f"{database}.db: schema {version}, migrazione in sospeso."
                    )
                try:
                    user_version = int(
                        conn.execute("PRAGMA user_version").fetchone()[0]
                    )
                except (sqlite3.Error, TypeError, ValueError):
                    user_version = None
                check["details"]["user_version"] = user_version
                counts: dict[str, int | None] = {}
                for table in sorted(required):
                    if table in present and not table.startswith("schema_"):
                        counts[table] = _count(conn, table)
                check["details"]["counts"] = counts
            except sqlite3.Error as exc:
                check["errors"].append(
                    f"{database}.db: lettura fallita ({type(exc).__name__})."
                )
            checks.append(self._finish(check))
        return connections, checks

    # -- timeline ----------------------------------------------------------

    def _check_timeline(self, conn: sqlite3.Connection | None) -> dict[str, Any]:
        check = self._check("timeline:temporal")
        if conn is None:
            check["details"]["skipped"] = "database non disponibile"
            return self._finish(check)
        try:
            present = _tables(conn)
            rows = _rows(
                conn,
                "SELECT id, event_time, event_date, created_at, updated_at "
                "FROM episodic_memories",
            )
            for row in rows:
                memory_id = str(row.get("id"))
                event_time = _parse_time(row.get("event_time"))
                if event_time is None:
                    check["errors"].append(
                        f"episodic_memories:{memory_id}: event_time non valido."
                    )
                elif str(row.get("event_date")) != str(row.get("event_time"))[:10]:
                    check["errors"].append(
                        f"episodic_memories:{memory_id}: event_date incoerente."
                    )
                created = _parse_time(row.get("created_at"))
                updated = _parse_time(row.get("updated_at"))
                if created is None:
                    check["errors"].append(
                        f"episodic_memories:{memory_id}: created_at non valido."
                    )
                if updated is None:
                    check["errors"].append(
                        f"episodic_memories:{memory_id}: updated_at non valido."
                    )
                if (
                    created is not None
                    and updated is not None
                    and _as_utc(updated) < _as_utc(created)
                ):
                    check["warnings"].append(
                        f"episodic_memories:{memory_id}: updated_at precede created_at."
                    )
            check["details"]["episodic"] = len(rows)

            revisions = (
                _rows(
                    conn,
                    "SELECT memory_id, revision_number, changed_at "
                    "FROM memory_revisions",
                )
                if "memory_revisions" in present
                else []
            )
            for row in revisions:
                if _parse_time(row.get("changed_at")) is None:
                    check["errors"].append(
                        "memory_revisions: changed_at non valido per "
                        f"{row.get('memory_id')}."
                    )
                if int(row.get("revision_number") or 0) < 1:
                    check["errors"].append(
                        "memory_revisions: revision_number non positivo per "
                        f"{row.get('memory_id')}."
                    )
            check["details"]["revisions"] = len(revisions)

            links = (
                _rows(conn, "SELECT created_at FROM memory_links")
                if "memory_links" in present
                else []
            )
            for row in links:
                if _parse_time(row.get("created_at")) is None:
                    check["errors"].append("memory_links: created_at non valido.")
                    break

            episodes = (
                _rows(
                    conn,
                    "SELECT id, state, opened_at, expires_at, decided_at, closed_at "
                    "FROM deliberation_episodes",
                )
                if "deliberation_episodes" in present
                else []
            )
            for row in episodes:
                episode_id = str(row.get("id"))
                if str(row.get("state")) not in EPISODIC_STATES:
                    check["errors"].append(
                        f"deliberation_episodes:{episode_id}: stato non valido."
                    )
                opened = _parse_time(row.get("opened_at"))
                expires = _parse_time(row.get("expires_at"))
                decided = _parse_time(row.get("decided_at"))
                closed = _parse_time(row.get("closed_at"))
                if opened is None:
                    check["errors"].append(
                        f"deliberation_episodes:{episode_id}: opened_at non valido."
                    )
                if expires is None:
                    check["errors"].append(
                        f"deliberation_episodes:{episode_id}: expires_at non valido."
                    )
                if opened is not None and expires is not None:
                    if _as_utc(expires) < _as_utc(opened):
                        check["errors"].append(
                            f"deliberation_episodes:{episode_id}: expires_at precede opened_at."
                        )
                for label, value in (("decided_at", decided), ("closed_at", closed)):
                    if value is not None and opened is not None:
                        if _as_utc(value) < _as_utc(opened):
                            check["errors"].append(
                                f"deliberation_episodes:{episode_id}: {label} precede opened_at."
                            )
                if decided is not None and closed is not None:
                    if _as_utc(closed) < _as_utc(decided):
                        check["warnings"].append(
                            f"deliberation_episodes:{episode_id}: closed_at precede decided_at."
                        )
            check["details"]["deliberation_episodes"] = len(episodes)

            events = (
                _rows(
                    conn, "SELECT episode_id, at, type FROM deliberation_events"
                )
                if "deliberation_events" in present
                else []
            )
            for row in events:
                if _parse_time(row.get("at")) is None:
                    check["errors"].append(
                        "deliberation_events: at non valido per "
                        f"{row.get('episode_id')}."
                    )
                if not str(row.get("type") or "").strip():
                    check["errors"].append(
                        "deliberation_events: type vuoto per "
                        f"{row.get('episode_id')}."
                    )
            check["details"]["deliberation_events"] = len(events)

            if "interaction_events" in present:
                interactions = _rows(
                    conn,
                    "SELECT event_id, user_id, timestamp, entry FROM interaction_events",
                )
                for row in interactions:
                    if _parse_time(row.get("timestamp")) is None:
                        check["errors"].append(
                            "interaction_events: timestamp non valido per "
                            f"{row.get('event_id')}."
                        )
                    if not str(row.get("user_id") or "").strip():
                        check["errors"].append(
                            "interaction_events: user_id vuoto per "
                            f"{row.get('event_id')}."
                        )
                    try:
                        json.loads(str(row.get("entry") or ""))
                    except ValueError:
                        check["errors"].append(
                            "interaction_events: entry JSON non valido per "
                            f"{row.get('event_id')}."
                        )
                check["details"]["interaction_events"] = len(interactions)
            if "autonomous_runs" in present:
                runs = _rows(
                    conn,
                    "SELECT id, state, created_at, steps FROM autonomous_runs",
                )
                run_ids = set()
                for row in runs:
                    run_id = str(row.get("id"))
                    run_ids.add(run_id)
                    if str(row.get("state")) not in RUN_STATES:
                        check["errors"].append(
                            f"autonomous_runs:{run_id}: stato non valido."
                        )
                    if _parse_time(row.get("created_at")) is None:
                        check["errors"].append(
                            f"autonomous_runs:{run_id}: created_at non valido."
                        )
                    if int(row.get("steps") or 0) < 0:
                        check["errors"].append(
                            f"autonomous_runs:{run_id}: steps negativo."
                        )
                if (
                    sum(
                        1
                        for row in runs
                        if str(row.get("state"))
                        in {"ready", "running", "awaiting_verification", "uncertain"}
                    )
                    > 1
                ):
                    check["errors"].append(
                        "autonomous_runs: più di un incarico attivo."
                    )
                if "autonomous_actions" in present:
                    for row in _rows(conn, "SELECT run_id FROM autonomous_actions"):
                        if str(row.get("run_id")) not in run_ids:
                            check["errors"].append(
                                "autonomous_actions: run_id orfano."
                            )
                            break
                if "autonomous_reviews" in present:
                    reviews = _rows(
                        conn,
                        "SELECT run_id, reviewed_at, impulse_cleared "
                        "FROM autonomous_reviews",
                    )
                    for row in reviews:
                        if str(row.get("run_id")) not in run_ids:
                            check["errors"].append(
                                "autonomous_reviews: run_id orfano."
                            )
                        if _parse_time(row.get("reviewed_at")) is None:
                            check["errors"].append(
                                "autonomous_reviews: reviewed_at non valido per "
                                f"{row.get('run_id')}."
                            )
                        if int(row.get("impulse_cleared") or 0) not in (0, 1):
                            check["errors"].append(
                                "autonomous_reviews: impulse_cleared non booleano."
                            )
                check["details"]["autonomous_runs"] = len(runs)
        except sqlite3.Error as exc:
            check["errors"].append(
                f"timeline: lettura fallita ({type(exc).__name__})."
            )
        return self._finish(check)

    # -- semantica e collegamenti tra database ----------------------------

    def _check_semantic(
        self,
        conn: sqlite3.Connection | None,
        timeline: sqlite3.Connection | None,
    ) -> dict[str, Any]:
        check = self._check("semantica:temporal")
        if conn is None:
            check["details"]["skipped"] = "database non disponibile"
            return self._finish(check)
        try:
            items = _rows(
                conn,
                "SELECT id, created_at, updated_at, valid_from, valid_to, "
                "previous_version_id FROM semantic_items",
            )
            for row in items:
                item_id = str(row.get("id"))
                created = _parse_time(row.get("created_at"))
                updated = _parse_time(row.get("updated_at"))
                valid_from = _parse_time(row.get("valid_from"))
                valid_to = _parse_time(row.get("valid_to"))
                if created is None:
                    check["errors"].append(
                        f"semantic_items:{item_id}: created_at non valido."
                    )
                if updated is None:
                    check["errors"].append(
                        f"semantic_items:{item_id}: updated_at non valido."
                    )
                if (
                    created is not None
                    and updated is not None
                    and _as_utc(updated) < _as_utc(created)
                ):
                    check["warnings"].append(
                        f"semantic_items:{item_id}: updated_at precede created_at."
                    )
                if valid_from is not None and valid_to is not None:
                    if _as_utc(valid_from) > _as_utc(valid_to):
                        check["errors"].append(
                            f"semantic_items:{item_id}: valid_from supera valid_to."
                        )
                if str(row.get("previous_version_id") or "") == item_id:
                    check["warnings"].append(
                        f"semantic_items:{item_id}: previous_version_id è se stesso."
                    )
            check["details"]["semantic_items"] = len(items)

            revisions = _rows(
                conn,
                "SELECT item_id, changed_at, revision_number FROM semantic_revisions",
            )
            for row in revisions:
                if _parse_time(row.get("changed_at")) is None:
                    check["errors"].append(
                        "semantic_revisions: changed_at non valido per "
                        f"{row.get('item_id')}."
                    )
                if int(row.get("revision_number") or 0) < 1:
                    check["errors"].append(
                        "semantic_revisions: revision_number non positivo per "
                        f"{row.get('item_id')}."
                    )
            check["details"]["semantic_revisions"] = len(revisions)

            links = _rows(conn, "SELECT created_at FROM semantic_item_links")
            for row in links:
                if _parse_time(row.get("created_at")) is None:
                    check["errors"].append(
                        "semantic_item_links: created_at non valido."
                    )
                    break

            if timeline is not None:
                memory_rows = _rows(
                    timeline,
                    "SELECT id, access_level FROM episodic_memories",
                )
                memories = {str(row["id"]): str(row["access_level"]) for row in memory_rows}
                cross = _rows(
                    conn,
                    "SELECT semantic_id, memory_id FROM semantic_episodic_links",
                )
                missing = 0
                non_conscious = 0
                for row in cross:
                    memory_id = str(row.get("memory_id"))
                    if memory_id not in memories:
                        missing += 1
                        if missing <= 5:
                            check["errors"].append(
                                "semantic_episodic_links: memory_id orfano "
                                f"{memory_id}."
                            )
                    elif memories[memory_id] != "conscious":
                        non_conscious += 1
                        if non_conscious <= 5:
                            check["errors"].append(
                                "semantic_episodic_links: memory_id non conscio "
                                f"{memory_id}."
                            )
                check["details"]["semantic_episodic_links"] = len(cross)
                check["details"]["orphan_memory_links"] = missing
                check["details"]["non_conscious_links"] = non_conscious
        except sqlite3.Error as exc:
            check["errors"].append(
                f"semantica: lettura fallita ({type(exc).__name__})."
            )
        return self._finish(check)

    def _check_vector(
        self,
        conn: sqlite3.Connection | None,
        timeline: sqlite3.Connection | None,
        semantic: sqlite3.Connection | None,
    ) -> dict[str, Any]:
        check = self._check("vettoriale")
        if conn is None:
            check["details"]["skipped"] = "database non disponibile"
            return self._finish(check)
        try:
            entries = _rows(
                conn,
                "SELECT faiss_id, kind, record_id, access_level, text_hash, "
                "model, dimension FROM vector_entries",
            )
            metas = {
                str(row.get("kind")): row
                for row in _rows(conn, "SELECT * FROM index_meta")
            }
            episodic_ids: set[str] | None = None
            semantic_ids: set[str] | None = None
            if timeline is not None:
                episodic_ids = {
                    str(row["id"])
                    for row in timeline.execute(
                        "SELECT id FROM episodic_memories "
                        "WHERE access_level = 'conscious'"
                    ).fetchall()
                }
            if semantic is not None:
                semantic_ids = {
                    str(row["id"])
                    for row in semantic.execute(
                        "SELECT id FROM semantic_items WHERE status = 'active'"
                    ).fetchall()
                }
            index_dir = self.data_dir / "indexes"
            check["details"]["index_dir_exists"] = index_dir.is_dir()
            for kind in VECTOR_KINDS:
                kind_entries = [
                    row for row in entries if str(row.get("kind")) == kind
                ]
                meta = metas.get(kind)
                check["details"][kind] = {
                    "entries": len(kind_entries),
                    "meta_count": None if meta is None else meta.get("count"),
                    "status": None if meta is None else meta.get("status"),
                }
                if meta is None:
                    if kind_entries:
                        check["errors"].append(
                            f"index_meta: voce mancante per l'indice {kind}."
                        )
                    continue
                if int(meta.get("count") or 0) != len(kind_entries):
                    check["errors"].append(
                        f"index_meta:{kind}: count {meta.get('count')} diverso "
                        f"da {len(kind_entries)} mapping."
                    )
                status = str(meta.get("status") or "")
                if status == "inconsistent":
                    check["errors"].append(
                        f"index_meta:{kind}: stato inconsistent."
                    )
                elif status in {"stale", "empty"} and kind_entries:
                    check["warnings"].append(
                        f"index_meta:{kind}: stato {status} con mapping presenti."
                    )
                elif status not in {"empty", "ready", "stale", "inconsistent"}:
                    check["errors"].append(
                        f"index_meta:{kind}: stato non valido."
                    )
                if not str(meta.get("model") or "").strip():
                    check["errors"].append(
                        f"index_meta:{kind}: modello vuoto."
                    )
                try:
                    dimension = int(meta.get("dimension") or 0)
                except (TypeError, ValueError):
                    dimension = 0
                if dimension <= 0:
                    check["errors"].append(
                        f"index_meta:{kind}: dimensione non positiva."
                    )
                path = index_dir / f"{kind}.faiss"
                if kind_entries and not path.is_file():
                    if index_dir.is_dir():
                        check["errors"].append(
                            f"Indice {kind}.faiss mancante in data/indexes."
                        )
                    else:
                        check["warnings"].append(
                            f"Indice {kind}.faiss assente: ricostruibile con "
                            "mnemosine-index --rebuild."
                        )
                elif path.is_file():
                    try:
                        if path.stat().st_size <= 0:
                            check["errors"].append(
                                f"Indice {kind}.faiss vuoto."
                            )
                    except OSError:
                        check["errors"].append(
                            f"Indice {kind}.faiss non leggibile."
                        )
                for row in kind_entries:
                    record_id = str(row.get("record_id"))
                    if str(row.get("access_level")) != "conscious":
                        check["errors"].append(
                            f"vector_entries:{kind}: access_level non conscio."
                        )
                        break
                    if not str(row.get("text_hash") or "").strip():
                        check["errors"].append(
                            f"vector_entries:{kind}: text_hash vuoto."
                        )
                        break
                    if dimension and int(row.get("dimension") or 0) != dimension:
                        check["warnings"].append(
                            f"vector_entries:{kind}: dimensione diversa da index_meta."
                        )
                        break
                    allowed = episodic_ids if kind == "episodic" else semantic_ids
                    if allowed is not None and record_id not in allowed:
                        check["errors"].append(
                            f"vector_entries:{kind}: record_id orfano {record_id}."
                        )
                        break
        except sqlite3.Error as exc:
            check["errors"].append(
                f"vettoriale: lettura fallita ({type(exc).__name__})."
            )
        return self._finish(check)

    # -- file operativi ----------------------------------------------------

    def _check_outbox(self) -> dict[str, Any]:
        check = self._check("outbox")
        path = self.data_dir / "outbox" / "messages.jsonl"
        if path.is_symlink():
            check["errors"].append("messages.jsonl è un symlink.")
            return self._finish(check)
        if not path.is_file():
            check["details"]["exists"] = False
            return self._finish(check)
        check["details"]["exists"] = True
        seen: set[str] = set()
        count = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    count += 1
                    try:
                        item = json.loads(line)
                    except ValueError:
                        check["errors"].append(
                            f"messages.jsonl:{line_number}: JSON non valido."
                        )
                        continue
                    if not isinstance(item, dict):
                        check["errors"].append(
                            f"messages.jsonl:{line_number}: voce non oggetto."
                        )
                        continue
                    message_id = item.get("message_id")
                    if not isinstance(message_id, str) or not message_id.strip():
                        check["errors"].append(
                            f"messages.jsonl:{line_number}: message_id assente."
                        )
                    elif message_id in seen:
                        check["errors"].append(
                            f"messages.jsonl:{line_number}: message_id duplicato."
                        )
                    else:
                        seen.add(message_id)
                    if "created_at" in item and _parse_time(item.get("created_at")) is None:
                        check["errors"].append(
                            f"messages.jsonl:{line_number}: created_at non valido."
                        )
        except OSError as exc:
            check["errors"].append(
                f"messages.jsonl non leggibile ({type(exc).__name__})."
            )
        check["details"]["messages"] = count
        return self._finish(check)

    def _check_files(self) -> dict[str, Any]:
        check = self._check("files")
        root = self.root / "files"
        if not root.exists():
            check["details"]["exists"] = False
            return self._finish(check)
        if root.is_symlink() or not root.is_dir():
            check["errors"].append("files/ non è una directory regolare.")
            return self._finish(check)
        count = 0
        symlinks: list[str] = []
        for current, directories, filenames in os.walk(
            root, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            for directory in directories:
                candidate = current_path / directory
                if candidate.is_symlink():
                    symlinks.append(
                        candidate.relative_to(root).as_posix()
                    )
            for filename in filenames:
                candidate = current_path / filename
                if candidate.is_symlink():
                    symlinks.append(candidate.relative_to(root).as_posix())
                else:
                    count += 1
        if symlinks:
            check["errors"].append(
                "files/: symlink non ammessi (" + ", ".join(symlinks[:5]) + ")."
            )
        check["details"]["files"] = count
        return self._finish(check)

    # -- esecuzione --------------------------------------------------------

    def run(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        connections: dict[str, sqlite3.Connection] = {}
        try:
            checks.append(self._check_profile())
            checks.append(self._check_config())
            connections, database_checks = self._open_databases()
            checks.extend(database_checks)
            checks.append(
                self._check_timeline(connections.get("timeline_episodic"))
            )
            checks.append(
                self._check_semantic(
                    connections.get("semantic_memory"),
                    connections.get("timeline_episodic"),
                )
            )
            checks.append(
                self._check_vector(
                    connections.get("vector_index"),
                    connections.get("timeline_episodic"),
                    connections.get("semantic_memory"),
                )
            )
            checks.append(self._check_outbox())
            checks.append(self._check_files())
        finally:
            for conn in connections.values():
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
        errors = sum(len(check["errors"]) for check in checks)
        warnings = sum(len(check["warnings"]) for check in checks)
        return {
            "ok": errors == 0,
            "generated_at": _now(),
            "root": str(self.root),
            "error_count": errors,
            "warning_count": warnings,
            "checks": checks,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Controlla coerenza temporale e semantica del profilo Mnemosine."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory del profilo (default: progetto corrente).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Stampa il report completo in JSON.",
    )
    args = parser.parse_args(argv)
    report = ConsistencyChecker(args.root).run()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for check in report["checks"]:
            label = "OK" if check["ok"] else "ERRORE"
            print(f"[{label}] {check['name']}")
            for warning in check["warnings"]:
                print(f"  avviso: {warning}")
            for error in check["errors"]:
                print(f"  errore: {error}")
        print(
            f"\nEsito: {report['error_count']} errori, "
            f"{report['warning_count']} avvisi."
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ConsistencyChecker", "main"]
