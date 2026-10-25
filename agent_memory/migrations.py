"""Versioni degli schemi e migrazioni automatiche del profilo.

Un profilo creato da una versione precedente del programma deve continuare a
funzionare dopo un aggiornamento. Le versioni sono due, una per tipo di dato:

- ``schema_meta.schema_version`` dentro ogni database SQLite;
- ``schema_version`` in cima a ogni documento JSON della configurazione.

Ogni cambiamento non retrocompatibile aggiunge un passo alla mappa
:data:`SQLITE_MIGRATIONS` o :data:`JSON_MIGRATIONS` e alza la versione corrente.
Al primo accesso i passi vengono eseguiti in ordine, da quella trovata sul disco
fino a quella supportata dal codice: un profilo vecchio di due versioni riceve
entrambi i passi, uno dopo l'altro, e i dati esistenti non vengono riscritti.

Un profilo creato da una versione *più recente* del programma non viene toccato:
viene rifiutato con un messaggio esplicito, perché la migrazione al contrario non
esiste e indovinarla vorrebbe dire corrompere dati.

Le versioni sono numeri interi; sul disco sono scritte nella forma ``"1.0"``
usata dagli schemi del pacchetto, ma il confronto avviene sempre sulla parte
intera.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import ConfigError, DatabaseUnavailable

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

# Versione di un profilo nato prima che esistesse il versioning: la prima
# versione pubblicata degli schemi. Un profilo senza marcatura viene trattato
# come se fosse a questa versione, poi i passi lo portano avanti.
BASELINE_VERSION = 1

# Per ogni database: nome del file di schema dentro il pacchetto e versione
# supportata dal codice corrente.
DATABASE_SCHEMAS: dict[str, tuple[str, int]] = {
    "timeline_episodic": ("timeline_episodic.sql", 1),
    "shadow_diary": ("shadow_diary.sql", 1),
    "semantic_memory": ("semantic_memory.sql", 1),
    "vector_index": ("vector_index.sql", 1),
}

# Passi di migrazione: ``{database: {versione: funzione}}``. La funzione porta lo
# schema *a* quella versione e riceve una connessione già aperta; viene eseguita
# dentro una transazione, quindi uno schema nuovo e la sua marcatura di versione
# arrivano insieme oppure non arrivano affatto.
SQLITE_MIGRATIONS: dict[str, dict[int, Callable[[sqlite3.Connection], None]]] = {
    name: {} for name in DATABASE_SCHEMAS
}

# Documenti JSON della configurazione e versione supportata dal codice.
JSON_DOCUMENTS: dict[str, int] = {
    "identity": 1,
    "shadow_traits": 1,
    "current_state": 1,
    "world_history": 1,
    "social_graph": 1,
}

# Passi di migrazione dei JSON: ``{documento: {versione: funzione}}``. La
# funzione riceve il documento attuale e restituisce quello aggiornato; non deve
# modificare l'oggetto ricevuto, perché la migrazione di un file illeggibile
# deve poter fallire senza lasciare tracce.
JSON_MIGRATIONS: dict[str, dict[int, Callable[[dict[str, Any]], dict[str, Any]]]] = {
    name: {} for name in JSON_DOCUMENTS
}


def format_version(version: int) -> str:
    """Forma scritta della versione, coerente con gli schemi del pacchetto."""

    return f"{version}.0"


def _parse_version(value: Any) -> int:
    """Estrae la parte intera di una versione scritta come ``1``, ``"1"`` o ``"1.0"``."""

    if isinstance(value, int) and not isinstance(value, bool):
        version = value
    elif isinstance(value, float) and value.is_integer():
        version = int(value)
    elif isinstance(value, str):
        head = value.strip().split(".", 1)[0]
        if not head.isdigit():
            raise ValueError(f"versione non riconosciuta: {value!r}")
        version = int(head)
    else:
        raise ValueError(f"versione non riconosciuta: {value!r}")
    if version < 1:
        raise ValueError(f"versione non valida: {value!r}")
    return version


# ---------------------------------------------------------------------------
# Database SQLite
# ---------------------------------------------------------------------------


def supported_database_version(database: str) -> int:
    try:
        return DATABASE_SCHEMAS[database][1]
    except KeyError as exc:
        raise DatabaseUnavailable(f"Database sconosciuto: {database}.") from exc


def stored_database_version(conn: sqlite3.Connection) -> int | None:
    """Versione scritta nel database, oppure ``None`` se il profilo è precedente."""

    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        return _parse_version(row[0])
    except ValueError as exc:
        raise DatabaseUnavailable(f"schema_meta.schema_version non valida: {exc}") from exc


def _objects(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
    return {row[0] for row in rows}


def _newer_schema_error(database: str, stored: int, current: int) -> DatabaseUnavailable:
    return DatabaseUnavailable(
        f"{database}: schema alla versione {format_version(stored)}, più recente di "
        f"quella supportata dal programma ({format_version(current)}). "
        "Aggiorna Mnemosine: un profilo creato da una versione successiva non "
        "può essere aperto da questa."
    )


def _stamp(conn: sqlite3.Connection, database: str, version: int) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
        (format_version(version),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('migrated_at', ?)",
        (_now_iso(),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_name', ?)",
        (database,),
    )


def _now_iso() -> str:
    """Istante della migrazione, in UTC per non dipendere dalla timezone del profilo."""

    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _apply_schema_file(conn: sqlite3.Connection, database: str) -> None:
    """Esegue lo schema del pacchetto: solo ``CREATE ... IF NOT EXISTS`` e la riga
    di ``schema_meta``, quindi è sicuro anche su un database già popolato."""

    filename = DATABASE_SCHEMAS[database][0]
    path = SCHEMA_DIR / filename
    try:
        script = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DatabaseUnavailable(
            f"{database}: schema {filename} non leggibile dentro il pacchetto: {exc}"
        ) from exc
    try:
        conn.executescript(script)
    except sqlite3.Error as exc:
        raise DatabaseUnavailable(
            f"{database}: impossibile applicare lo schema {filename}: {exc}"
        ) from exc


def ensure_database_schema(
    conn: sqlite3.Connection,
    database: str,
    path: Path,
    required_tables: set[str],
) -> int:
    """Porta il database all'ultima versione supportata e restituisce la versione.

    Non fa nulla al primo accesso di un profilo aggiornato (una sola lettura di
    `schema_meta`): le scritture avvengono solo quando c'è davvero da migrare.
    """

    if database not in DATABASE_SCHEMAS:
        raise DatabaseUnavailable(f"Database sconosciuto: {database}.")
    current = DATABASE_SCHEMAS[database][1]
    stored = stored_database_version(conn)

    if stored is not None and stored > current:
        raise _newer_schema_error(database, stored, current)

    if stored is None:
        present = _objects(conn)
        if not required_tables <= present:
            # Profilo precedente al versioning o schema parziale: le tabelle
            # mancanti vengono ricostruite dallo schema incluso nel pacchetto.
            _apply_schema_file(conn, database)
        stored = BASELINE_VERSION
        _stamp(conn, database, stored)
        conn.commit()

    if stored < current:
        # Due processi possono aprire lo stesso profilo insieme: il lock
        # esclusivo e la rilettura della versione evitano che applichino due
        # volte lo stesso passo.
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise DatabaseUnavailable(
                f"{database}: impossibile bloccare {path} per la migrazione: {exc}"
            ) from exc
        try:
            locked = stored_database_version(conn)
        except BaseException:
            conn.rollback()
            raise
        if locked is None:
            locked = BASELINE_VERSION
        if locked > current:
            conn.rollback()
            raise _newer_schema_error(database, locked, current)
        if locked == current:
            # Un altro processo ha già fatto il lavoro mentre aspettavamo il lock.
            conn.rollback()
        stored = locked

    for version in range(stored + 1, current + 1):
        step = SQLITE_MIGRATIONS[database].get(version)
        if step is None:
            if conn.in_transaction:
                conn.rollback()
            raise DatabaseUnavailable(
                f"{database}: manca il passo di migrazione alla versione "
                f"{format_version(version)}. Il pacchetto installato è incompleto."
            )
        try:
            step(conn)
            _stamp(conn, database, version)
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise DatabaseUnavailable(
                f"{database}: migrazione alla versione {format_version(version)} "
                f"fallita in {path}; il database è rimasto a {format_version(version - 1)}: {exc}"
            ) from exc

    missing = sorted(required_tables - _objects(conn))
    if missing:
        raise DatabaseUnavailable(
            f"{database}: schema incompleto in {path}; tabelle mancanti: "
            f"{', '.join(missing)}. Crea un profilo nuovo con `mnemosine-profile init` "
            "oppure rigenera il dataset demo con `python scripts/init_db.py --reset`."
        )
    return current


# ---------------------------------------------------------------------------
# Documenti JSON
# ---------------------------------------------------------------------------


def supported_json_version(name: str) -> int:
    try:
        return JSON_DOCUMENTS[name]
    except KeyError as exc:
        raise ConfigError(f"Documento di configurazione sconosciuto: {name}.") from exc


def migrate_json_document(
    name: str, data: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Applica i passi mancanti a un documento e dice se è cambiato.

    Il documento restituito non è mai lo stesso oggetto di partenza quando la
    migrazione produce modifiche, così il chiamante può decidere se riscrivere il
    file solo dopo aver validato il risultato.
    """

    if not isinstance(data, dict):
        raise ConfigError(f"{name}: atteso un oggetto JSON.")
    supported = supported_json_version(name)
    raw = data.get("schema_version")
    if raw is None:
        version = BASELINE_VERSION
        changed = True
    else:
        try:
            version = _parse_version(raw)
        except ValueError as exc:
            raise ConfigError(f"{name}.schema_version non valida: {exc}") from exc
        changed = False

    if version > supported:
        raise ConfigError(
            f"{name}: schema_version {format_version(version)} è più recente di quella "
            f"supportata dal programma ({format_version(supported)}). Aggiorna Mnemosine."
        )

    document = data
    for target in range(version + 1, supported + 1):
        step = JSON_MIGRATIONS[name].get(target)
        if step is None:
            raise ConfigError(
                f"{name}: manca il passo di migrazione alla versione "
                f"{format_version(target)}. Il pacchetto installato è incompleto."
            )
        migrated = step(document)
        if not isinstance(migrated, dict):
            raise ConfigError(
                f"{name}: il passo di migrazione a {format_version(target)} ha "
                "restituito un documento non valido."
            )
        document = migrated
        changed = True

    if not changed:
        return data, False

    document = dict(document)
    document["schema_version"] = format_version(supported)
    return document, True


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Scrittura atomica di un documento: temporaneo, fsync, poi rinomina."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        raise ConfigError(f"Impossibile scrivere {path}: {exc}") from exc
