"""Fixture condivise per i test che lavorano su una copia del profilo demo.

Il profilo demo del repository viene aperto anche dall'app: quando succede, il
database contiene i documenti operativi (``current_state``, ``social_graph``) e
gli eventi di interazione, che per progetto hanno la precedenza sui JSON. I test
scrivono invece i JSON direttamente, quindi la copia azzera quello stato: senza
questo passaggio la suite dipenderebbe da come qualcuno ha lasciato il profilo
demo dopo aver usato la dashboard o un comando.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IGNORED = ("models", "indexes", "dashboard_tls", "browser", "secrets", "visual", "outbox")


def copy_profile(
    root: str | Path,
    *,
    include_data: bool = True,
    ignore: tuple[str, ...] = IGNORED,
) -> Path:
    """Copia ``config/`` e, se richiesto, ``data/`` del profilo demo.

    ``include_data=False`` serve ai test che lavorano solo sui documenti JSON:
    senza database non esiste stato operativo e le scritture restano nei JSON,
    come prima che lo store operativo fosse introdotto.
    """

    root = Path(root)
    shutil.copytree(PROJECT_ROOT / "config", root / "config")
    if not include_data:
        return root
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
        # Al primo accesso lo store reimporta dallo storico di
        # ``social_graph.json`` le righe ``legacy:*``: se restassero, l'insert
        # violerebbe la chiave primaria. Si tolgono quindi solo gli id che il
        # JSON sta per ricreare; la storia già migrata nel database, che il
        # file non riporta più, resta intatta.
        if "interaction_events" in tables:
            for event_id in _legacy_event_ids(Path(root)):
                conn.execute("DELETE FROM interaction_events WHERE event_id=?", (event_id,))
        if "interaction_receipts" in tables:
            conn.execute("DELETE FROM interaction_receipts")
        conn.commit()
    finally:
        conn.close()


def _legacy_event_ids(root: Path) -> list[str]:
    """Id che l'import iniziale ricrea dallo storico presente nel JSON."""

    path = root / "config" / "social_graph.json"
    try:
        graph = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(graph, dict):
        return []
    ids: list[str] = []
    for person in graph.get("people", []):
        if not isinstance(person, dict):
            continue
        user_id = person.get("user_id")
        history = person.get("interaction_history")
        if not isinstance(user_id, str) or not isinstance(history, list):
            continue
        ids.extend(f"legacy:{user_id}:{index}" for index in range(len(history)))
    return ids


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
