"""Accesso ai database SQLite della memoria.

Il modulo fornisce connessioni configurate, conversione delle righe e
diagnostica per timeline episodica, diario dell'ombra, archivio semantico e
metadati dell'indice vettoriale. Le regole di visibilità vivono nei servizi.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import project_root
from .errors import DatabaseUnavailable

TIMELINE_TABLES = {
    "episodic_memories",
    "memory_links",
    "memory_revisions",
    "schema_meta",
    "conscious_episodic_memories",
}
SHADOW_TABLES = {
    "shadow_entries",
    "shadow_revisions",
    "shadow_trigger_log",
    "schema_meta",
}
SEMANTIC_TABLES = {
    "semantic_items",
    "semantic_revisions",
    "semantic_item_links",
    "semantic_episodic_links",
    "active_semantic_items",
    "schema_meta",
}
VECTOR_TABLES = {
    "vector_entries",
    "index_meta",
    "schema_meta",
}

TIMELINE_COUNTS = {
    "episodic_memories": "episodic_memories",
    "conscious_memories": "conscious_episodic_memories",
    "memory_links": "memory_links",
    "memory_revisions": "memory_revisions",
}
SHADOW_COUNTS = {
    "shadow_entries": "shadow_entries",
    "shadow_revisions": "shadow_revisions",
    "shadow_trigger_log": "shadow_trigger_log",
}


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def decode_memory(row: sqlite3.Row) -> dict[str, Any]:
    """Converte una riga episodica in un dizionario con campi JSON espansi."""

    data = dict(row)
    data["people"] = json.loads(data.pop("people_json", "[]") or "[]")
    data["emotional_state"] = json.loads(
        data.pop("emotional_state_json", "{}") or "{}"
    )
    data["tags"] = json.loads(data.pop("tags_json", "[]") or "[]")
    return data


def decode_shadow(row: sqlite3.Row) -> dict[str, Any]:
    """Converte una voce del diario dell'ombra in un dizionario."""

    data = dict(row)
    data["emotions"] = json.loads(data.pop("emotions_json", "{}") or "{}")
    return data


class Database:
    """Percorsi e connessioni ai database del progetto."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root).expanduser().resolve() if root else project_root()
        self.data_dir = self.root / "data"
        self.timeline_path = self.data_dir / "timeline_episodic.db"
        self.shadow_path = self.data_dir / "shadow_diary.db"
        self.semantic_path = self.data_dir / "semantic_memory.db"
        self.vector_path = self.data_dir / "vector_index.db"
        self.index_dir = self.data_dir / "indexes"

    # -- connessioni --------------------------------------------------------

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _ensure_one(
        self, path: Path, required_tables: set[str], label: str
    ) -> None:
        if not path.exists():
            raise DatabaseUnavailable(
                f"{label}: database non trovato in {path}. "
                "Esegui `python scripts/init_db.py --reset` per crearlo."
            )
        try:
            conn = self._connect(path)
        except sqlite3.Error as exc:
            raise DatabaseUnavailable(f"{label}: impossibile aprire {path}: {exc}") from exc
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
            present = {row["name"] for row in rows}
            missing = sorted(required_tables - present)
            if missing:
                raise DatabaseUnavailable(
                    f"{label}: schema incompleto in {path}; tabelle mancanti: "
                    f"{', '.join(missing)}. Esegui `python scripts/init_db.py --reset`."
                )
        except sqlite3.Error as exc:
            raise DatabaseUnavailable(f"{label}: schema illeggibile in {path}: {exc}") from exc
        finally:
            conn.close()

    @contextmanager
    def timeline(self) -> Iterator[sqlite3.Connection]:
        self._ensure_one(self.timeline_path, TIMELINE_TABLES, "timeline_episodic")
        conn = self._connect(self.timeline_path)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def shadow(self) -> Iterator[sqlite3.Connection]:
        self._ensure_one(self.shadow_path, SHADOW_TABLES, "shadow_diary")
        conn = self._connect(self.shadow_path)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def semantic(self) -> Iterator[sqlite3.Connection]:
        self._ensure_one(self.semantic_path, SEMANTIC_TABLES, "semantic_memory")
        conn = self._connect(self.semantic_path)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def vector(self) -> Iterator[sqlite3.Connection]:
        self._ensure_one(self.vector_path, VECTOR_TABLES, "vector_index")
        conn = self._connect(self.vector_path)
        try:
            yield conn
        finally:
            conn.close()

    # -- diagnostica --------------------------------------------------------

    def _health_one(
        self,
        path: Path,
        required_tables: set[str],
        counts: dict[str, str],
        label: str,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {"path": str(path), "ok": False}
        if not path.exists():
            result["error"] = (
                "Database non trovato. Esegui `python scripts/init_db.py --reset`."
            )
            return result
        try:
            conn = self._connect(path)
        except sqlite3.Error as exc:
            result["error"] = f"Impossibile aprire il database: {exc}"
            return result

        try:
            present = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
                )
            }
            missing = sorted(required_tables - present)
            if missing:
                result["error"] = f"Tabelle mancanti: {', '.join(missing)}"
                return result

            integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()
            integrity = [row[0] for row in integrity_rows]
            fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
            result["integrity"] = integrity
            result["foreign_key_violations"] = len(fk_rows)
            result["counts"] = {
                name: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for name, table in counts.items()
            }
            result["ok"] = integrity == ["ok"] and not fk_rows
            if not result["ok"]:
                result["error"] = "Integrity check o foreign key check fallita."
            return result
        except sqlite3.Error as exc:
            result["error"] = f"Errore SQLite: {exc}"
            return result
        finally:
            conn.close()

    def health(self) -> dict[str, Any]:
        timeline = self._health_one(
            self.timeline_path, TIMELINE_TABLES, TIMELINE_COUNTS, "timeline_episodic"
        )
        shadow = self._health_one(
            self.shadow_path, SHADOW_TABLES, SHADOW_COUNTS, "shadow_diary"
        )
        return {
            "ok": bool(timeline.get("ok") and shadow.get("ok")),
            "data_dir": str(self.data_dir),
            "timeline_episodic": timeline,
            "shadow_diary": shadow,
        }
