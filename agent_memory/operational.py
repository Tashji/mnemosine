"""Stato operativo autorevole e storico nel database della timeline.

I JSON restano esportazioni di compatibilità; mai reimportati dopo il primo
commit. Tutti i consumatori passano da ConfigStore per vedere lo stesso stato.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import warnings
from pathlib import Path
from typing import Any

from .errors import ConfigError, ValidationError

OPERATIONAL_NAMES = frozenset({"current_state", "social_graph"})


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class OperationalStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def connect(self) -> sqlite3.Connection:
        # Local import avoids config -> operational -> db -> config cycle.
        from .db import Database
        db = Database(self.root)
        return db._open(db.timeline_path, "timeline_episodic", {"operational_documents", "interaction_events", "interaction_receipts"})

    @staticmethod
    def documents(conn: sqlite3.Connection) -> dict[str, Any]:
        return {row["name"]: json.loads(row["document"]) for row in conn.execute("SELECT name, document FROM operational_documents")}

    @staticmethod
    def put(conn: sqlite3.Connection, name: str, document: dict[str, Any]) -> None:
        conn.execute("INSERT INTO operational_documents(name, document) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET document=excluded.document", (name, canonical(document)))

    def initialize(self, configs: dict[str, Any]) -> bool:
        """Importazione unica, tutta nella stessa transazione della marcatura."""
        conn = self.connect()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                if self.documents(conn):
                    return False
                graph = copy.deepcopy(configs["social_graph"])
                for person in graph["people"]:
                    history = person.pop("interaction_history", [])
                    if not isinstance(history, list):
                        history = []
                    for index, entry in enumerate(history):
                        if not isinstance(entry, dict):
                            raise ConfigError("Storico legacy non valido: atteso un oggetto.")
                        conn.execute("INSERT INTO interaction_events(event_id, user_id, timestamp, entry) VALUES (?, ?, ?, ?)",
                                     (f"legacy:{person['user_id']}:{index}", person["user_id"], str(entry.get("timestamp") or ""), canonical(entry)))
                self.put(conn, "social_graph", graph)
                self.put(conn, "current_state", configs["current_state"])
                conn.commit()
                return True
        finally:
            conn.close()

    def read(self) -> dict[str, Any]:
        conn = self.connect()
        try:
            return self.documents(conn)
        finally:
            conn.close()

    def export(self, documents: dict[str, Any]) -> list[str]:
        from .migrations import write_json_atomic
        failures = []
        for name in sorted(OPERATIONAL_NAMES & documents.keys()):
            try:
                write_json_atomic(self.root / "config" / f"{name}.json", documents[name])
            except (OSError, ConfigError) as exc:
                failures.append(name)
                warnings.warn(f"Commit SQLite completato; esportazione {name} da riallineare: {exc}", RuntimeWarning, stacklevel=2)
        return failures

    def history(self, user_id: str, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValidationError("user_id obbligatorio.")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValidationError("offset deve essere un intero non negativo.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValidationError("limit deve essere tra 1 e 100.")
        conn = self.connect()
        try:
            # A read transaction keeps count and page on the same snapshot.
            conn.execute("BEGIN")
            total = conn.execute("SELECT COUNT(*) FROM interaction_events WHERE user_id=?", (user_id,)).fetchone()[0]
            rows = conn.execute("SELECT entry FROM interaction_events WHERE user_id=? ORDER BY timestamp DESC, sequence DESC LIMIT ? OFFSET ?", (user_id, limit, offset)).fetchall()
            return {"user_id": user_id, "total": total, "offset": offset, "limit": limit,
                    "entries": [json.loads(row[0]) for row in rows]}
        finally:
            conn.close()
