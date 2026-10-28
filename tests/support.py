"""Fixture condivise per i test che lavorano su una copia del profilo demo.

Il profilo demo del repository viene aperto anche dall'app: quando succede, il
database contiene i documenti operativi (``current_state``, ``social_graph``) e
gli eventi di interazione, che per progetto hanno la precedenza sui JSON. I test
scrivono invece i JSON direttamente, quindi la copia azzera quello stato: senza
questo passaggio la suite dipenderebbe da come qualcuno ha lasciato il profilo
demo dopo aver usato la dashboard o un comando.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IGNORED = ("models", "indexes", "dashboard_tls", "browser", "secrets", "visual", "outbox")


def copy_profile(root: str | Path, *, ignore: tuple[str, ...] = IGNORED) -> Path:
    """Copia ``config/`` e ``data/`` del profilo demo in una directory temporanea."""

    root = Path(root)
    shutil.copytree(PROJECT_ROOT / "config", root / "config")
    shutil.copytree(
        PROJECT_ROOT / "data",
        root / "data",
        ignore=shutil.ignore_patterns(*ignore),
    )
    clear_operational_state(root)
    return root


def clear_operational_state(root: str | Path) -> None:
    """Riporta la copia a uno stato guidato dai JSON, come un profilo legacy."""

    database = Path(root) / "data" / "timeline_episodic.db"
    if not database.is_file():
        return
    conn = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "operational_documents" in tables:
            conn.execute("DELETE FROM operational_documents")
        # Gli eventi vengono reimportati da ``social_graph.json`` al primo accesso
        # tramite lo store operativo: lasciarli qui li duplicherebbe.
        if "interaction_events" in tables:
            conn.execute("DELETE FROM interaction_events")
        if "interaction_receipts" in tables:
            conn.execute("DELETE FROM interaction_receipts")
        conn.commit()
    finally:
        conn.close()


def set_database_version(root: str | Path, version: str = "1.0") -> None:
    """Dichiara la versione di schema della copia, per i test della scala."""

    database = Path(root) / "data" / "timeline_episodic.db"
    conn = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "schema_meta" not in tables:
            raise AssertionError("schema_meta assente nella copia del profilo")
        conn.execute(
            "UPDATE schema_meta SET value=? WHERE key='schema_version'", (version,)
        )
        conn.commit()
    finally:
        conn.close()
