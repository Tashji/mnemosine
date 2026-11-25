"""Manutenzione amministrativa: backup, coerenza e stato dell'indice.

La vista riusa i servizi già esistenti (``BackupService``,
``ConsistencyChecker`` e ``VectorIndexManager``) senza duplicarne la logica.
Crea e verifica backup e mostra il comando di ripristino, ma non lo esegue:
il ripristino richiede la dashboard ferma ed è volutamente solo da CLI.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .backup import DEFAULT_KEEP, RESTORE_CONFIRMATION, BackupService
from .consistency import ConsistencyChecker
from .errors import ValidationError
from .vector_index import VectorIndexManager

DATABASES = (
    "timeline_episodic",
    "semantic_memory",
    "shadow_diary",
    "vector_index",
)

BACKUP_ACTIONS = {"create", "verify"}


def _database_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


class MaintenanceService:
    """Espone in sola lettura lo stato del profilo e poche azioni mirate."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = (
            Path(root).expanduser().resolve() if root is not None else None
        )
        if self.root is None:
            raise ValidationError("Directory del profilo non indicata.")
        self.backup = BackupService(self.root)

    # -- lettura -----------------------------------------------------------

    def _backups(self) -> dict[str, Any]:
        try:
            items = self.backup.list()
        except Exception as exc:  # noqa: BLE001 - la vista deve restare utile
            return {
                "ok": False,
                "error": str(exc),
                "items": [],
                "keep": DEFAULT_KEEP,
            }
        return {"ok": True, "error": None, "items": items, "keep": DEFAULT_KEEP}

    def databases(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for name in DATABASES:
            path = self.root / "data" / f"{name}.db"
            entry: dict[str, Any] = {
                "name": name,
                "path": str(path),
                "exists": path.is_file(),
                "bytes": _database_size(path),
                "schema_version": None,
            }
            if path.is_file():
                conn = None
                try:
                    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                    row = conn.execute(
                        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                    ).fetchone()
                    if row is not None:
                        entry["schema_version"] = str(row[0])
                except sqlite3.Error:
                    entry["schema_version"] = None
                finally:
                    if conn is not None:
                        conn.close()
            result.append(entry)
        return result

    def index_status(self) -> dict[str, Any]:
        try:
            manager = VectorIndexManager(self.root)
            status = manager.status()
        except Exception as exc:  # noqa: BLE001 - indice assente o illeggibile
            return {
                "ok": False,
                "error": str(exc),
                "indexes": {},
                "index_dir": None,
            }
        return {
            "ok": bool(status.get("ok")),
            "error": None,
            "index_dir": status.get("index_dir"),
            "model": status.get("model"),
            "indexes": status.get("indexes", {}),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "backups": self._backups(),
            "databases": self.databases(),
            "index": self.index_status(),
            "restore_confirmation": RESTORE_CONFIRMATION,
        }

    # -- azioni ------------------------------------------------------------

    def check(self) -> dict[str, Any]:
        """Esegue i controlli di coerenza senza modificare il profilo."""

        report = ConsistencyChecker(self.root).run()
        if not isinstance(report, dict):
            raise ValidationError("Risposta del controllo non valida.")
        return {"report": report, "databases": self.databases()}

    def _name(self, payload: dict[str, Any]) -> str:
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("Indica il backup su cui operare.")
        return name.strip()

    def create(self) -> dict[str, Any]:
        result = self.backup.create(reason="dashboard", keep=DEFAULT_KEEP)
        return {"result": result, "backups": self._backups()}

    def verify(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = self._name(payload)
        result = self.backup.verify(name)
        return {"result": result, "backups": self._backups()}

    def run_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = payload.get("action")
        if action == "check":
            return self.check()
        if action not in BACKUP_ACTIONS:
            if action == "restore":
                raise ValidationError(
                    "Il ripristino va eseguito da terminale con la dashboard "
                    f"ferma: mnemosine-backup restore <nome> --confirm {RESTORE_CONFIRMATION}."
                )
            raise ValidationError("Azione di manutenzione non riconosciuta.")
        if action == "create":
            return self.create()
        return self.verify(payload)
