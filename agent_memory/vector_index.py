"""Indice vettoriale FAISS per la memoria conscia.

FAISS conserva i vettori; ``data/vector_index.db`` conserva la mappa tra gli
ID FAISS e i record originali. SQLite resta la fonte di verità: l'indice può
essere sempre ricostruito dai testi della timeline episodica e dell'archivio
semantico.

Regola di separazione: possono entrare nell'indice soltanto record con
``access_level='conscious'``. Il vincolo è applicato sia dal codice sia dallo
schema SQLite di ``vector_entries``. I contenuti latenti o repressi non hanno
un indice vettoriale in questa fase.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .config import ConfigStore, now_iso
from .db import Database
from .embeddings import DEFAULT_DIMENSION, Embedder, FastEmbedder, embedding_text_hash
from .errors import (
    AccessDeniedError,
    DatabaseUnavailable,
    ValidationError,
    VectorIndexError,
)

INDEX_KINDS = {"episodic", "semantic"}

logger = logging.getLogger(__name__)


def _import_faiss() -> Any:
    try:
        import faiss  # type: ignore
    except ImportError as exc:
        raise VectorIndexError(
            "FAISS non è installato. Esegui `pip install -r requirements.txt`."
        ) from exc
    return faiss


def _validate_kind(kind: str) -> str:
    if kind not in INDEX_KINDS:
        raise ValidationError(
            f"kind: valore non valido '{kind}'. Ammessi: {', '.join(sorted(INDEX_KINDS))}."
        )
    return kind


def _chunks(values: Sequence[str], size: int = 900) -> Iterable[Sequence[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


class VectorIndexManager:
    """Gestisce gli indici FAISS episodico e semantico."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        embedder: Embedder | None = None,
        index_dir: str | Path | None = None,
    ):
        self.config = ConfigStore(root)
        self.db = Database(self.config.root)
        self.root = self.config.root
        self.embedder = embedder or FastEmbedder(
            cache_dir=self.root / "data" / "models"
        )
        self.index_dir = (
            Path(index_dir).expanduser().resolve()
            if index_dir is not None
            else self.db.index_dir
        )
        self._lock = threading.RLock()
        # Cache: kind -> (firma del file, indice). La firma permette di
        # accorgersi di una ricostruzione fatta da un altro processo (CLI).
        self._indexes: dict[str, tuple[tuple[int, int] | None, Any]] = {}

    # -- file e metadati ----------------------------------------------------

    def index_path(self, kind: str) -> Path:
        return self.index_dir / f"{_validate_kind(kind)}.faiss"

    def _meta(self, kind: str) -> dict[str, Any] | None:
        with self.db.vector() as conn:
            row = conn.execute(
                "SELECT * FROM index_meta WHERE kind = ?", (kind,)
            ).fetchone()
        return dict(row) if row is not None else None

    def _entries(self, kind: str) -> list[dict[str, Any]]:
        with self.db.vector() as conn:
            rows = conn.execute(
                "SELECT * FROM vector_entries WHERE kind = ? ORDER BY faiss_id ASC",
                (kind,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _entries_for_ids(self, kind: str, record_ids: Sequence[str]) -> list[dict[str, Any]]:
        if not record_ids:
            return []
        result: list[dict[str, Any]] = []
        with self.db.vector() as conn:
            for chunk in _chunks(list(record_ids)):
                placeholders = ", ".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT * FROM vector_entries WHERE kind = ? "
                    f"AND record_id IN ({placeholders})",
                    (kind, *chunk),
                ).fetchall()
                result.extend(dict(row) for row in rows)
        return result

    def _set_meta(
        self,
        kind: str,
        *,
        model: str,
        dimension: int,
        count: int,
        status: str,
        error: str | None = None,
    ) -> None:
        timestamp = now_iso(self.config.timezone_name())
        with self.db.vector() as conn, conn:
            conn.execute(
                """
                    INSERT INTO index_meta (
                        kind, model, dimension, metric, count, status,
                        last_error, updated_at
                    ) VALUES (?, ?, ?, 'cosine', ?, ?, ?, ?)
                    ON CONFLICT(kind) DO UPDATE SET
                        model = excluded.model,
                        dimension = excluded.dimension,
                        count = excluded.count,
                        status = excluded.status,
                        last_error = excluded.last_error,
                        updated_at = excluded.updated_at
                    """,
                (
                    kind,
                    model,
                    dimension,
                    count,
                    status,
                    error,
                    timestamp,
                ),
            )

    def _mark_inconsistent(self, kind: str, error: str) -> None:
        try:
            meta = self._meta(kind)
            with self.db.vector() as conn, conn:
                conn.execute(
                    """
                        UPDATE index_meta
                        SET status = 'inconsistent', last_error = ?, updated_at = ?
                        WHERE kind = ?
                        """,
                    (error[:1000], now_iso(self.config.timezone_name()), kind),
                )
            if meta is None:
                # Non c'era uno stato precedente: registra almeno il guasto.
                self._set_meta(
                    kind,
                    model=self.embedder.model_name,
                    dimension=DEFAULT_DIMENSION,
                    count=0,
                    status="inconsistent",
                    error=error,
                )
        except (DatabaseUnavailable, sqlite3.Error) as exc:
            logger.debug(
                "Impossibile registrare lo stato inconsistente per l'indice %s.",
                kind,
                exc_info=exc,
            )

    # -- caricamento e persistenza -----------------------------------------

    def _load_index(self, kind: str, dimension: int | None = None) -> Any | None:
        """Carica l'indice, oppure restituisce None se non esiste ancora."""

        faiss = _import_faiss()
        path = self.index_path(kind)
        if not path.exists():
            self._indexes.pop(kind, None)
            meta = self._meta(kind)
            if meta is not None:
                raise VectorIndexError(
                    f"File dell'indice {kind} mancante in {path}, ma i metadati "
                    "esistono. Esegui `python -m agent_memory.index --rebuild`."
                )
            if dimension is None:
                return None
            index = faiss.IndexIDMap2(faiss.IndexFlatIP(dimension))
            self._indexes[kind] = (None, index)
            return index

        signature = (path.stat().st_mtime_ns, path.stat().st_size)
        cached = self._indexes.get(kind)
        if cached is not None and cached[0] == signature:
            index = cached[1]
            if dimension is not None and index.d != dimension:
                raise VectorIndexError(
                    f"Indice {kind} con dimensione {index.d}, attesa {dimension}. "
                    "Esegui `python -m agent_memory.index --rebuild`."
                )
            return index

        try:
            index = faiss.read_index(str(path))
        except Exception as exc:
            raise VectorIndexError(
                f"Impossibile leggere l'indice {kind} da {path}: {exc}. "
                "Esegui `python -m agent_memory.index --rebuild`."
            ) from exc
        if not hasattr(index, "id_map"):
            raise VectorIndexError(
                f"L'indice {kind} non è un IndexIDMap2; esegui --rebuild."
            )
        if dimension is not None and index.d != dimension:
            raise VectorIndexError(
                f"Indice {kind} con dimensione {index.d}, attesa {dimension}. "
                "Esegui `python -m agent_memory.index --rebuild`."
            )
        self._indexes[kind] = (signature, index)
        return index

    def _new_index(self, dimension: int) -> Any:
        faiss = _import_faiss()
        return faiss.IndexIDMap2(faiss.IndexFlatIP(dimension))

    def _persist(self, kind: str, index: Any) -> None:
        faiss = _import_faiss()
        self.index_dir.mkdir(parents=True, exist_ok=True)
        path = self.index_path(kind)
        tmp = path.with_name(f".{path.name}.tmp")
        try:
            faiss.write_index(index, str(tmp))
            os.replace(tmp, path)
            self._indexes[kind] = (
                (path.stat().st_mtime_ns, path.stat().st_size),
                index,
            )
        except Exception as exc:
            raise VectorIndexError(
                f"Impossibile scrivere l'indice {kind} in {path}: {exc}"
            ) from exc
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def _invalidate(self, kind: str) -> None:
        self._indexes.pop(kind, None)

    # -- scrittura ----------------------------------------------------------

    def _verify_source_visible(self, kind: str, record_id: str) -> None:
        """Verifica che il record esista ancora nella superficie conscia.

        Protegge anche l'uso diretto del gestore dell'indice: un ID latente o
        rimosso non può essere indicizzato semplicemente dichiarandolo conscio.
        """

        if kind == "episodic":
            with self.db.timeline() as conn:
                row = conn.execute(
                    "SELECT 1 FROM conscious_episodic_memories WHERE id = ?",
                    (record_id,),
                ).fetchone()
        else:
            with self.db.semantic() as conn:
                row = conn.execute(
                    "SELECT 1 FROM active_semantic_items WHERE id = ?",
                    (record_id,),
                ).fetchone()
        if row is None:
            raise AccessDeniedError(
                f"Il record {record_id} non è visibile nella memoria conscia."
            )

    def _validate_record(
        self, kind: str, record_id: str, text: str, access_level: str
    ) -> tuple[str, str]:
        _validate_kind(kind)
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValidationError("record_id: attesa una stringa non vuota.")
        if not isinstance(text, str) or not text.strip():
            raise ValidationError(f"Testo vuoto per il record {record_id}.")
        if access_level != "conscious":
            raise AccessDeniedError(
                f"Il record {record_id} non è conscio: non può entrare nell'indice vettoriale."
            )
        return record_id.strip(), text.strip()

    def _allocate_entry(
        self,
        kind: str,
        record_id: str,
        text_hash: str,
        model: str,
        dimension: int,
    ) -> int:
        timestamp = now_iso(self.config.timezone_name())
        with self.db.vector() as conn, conn:
            row = conn.execute(
                "SELECT faiss_id FROM vector_entries WHERE kind = ? AND record_id = ?",
                (kind, record_id),
            ).fetchone()
            if row is not None:
                conn.execute(
                    """
                        UPDATE vector_entries
                        SET text_hash = ?, model = ?, dimension = ?, updated_at = ?
                        WHERE kind = ? AND record_id = ?
                        """,
                    (
                        text_hash,
                        model,
                        dimension,
                        timestamp,
                        kind,
                        record_id,
                    ),
                )
                return int(row["faiss_id"])
            cursor = conn.execute(
                """
                    INSERT INTO vector_entries (
                        kind, record_id, access_level, text_hash, model,
                        dimension, indexed_at, updated_at
                    ) VALUES (?, ?, 'conscious', ?, ?, ?, ?, ?)
                    """,
                (
                    kind,
                    record_id,
                    text_hash,
                    model,
                    dimension,
                    timestamp,
                    timestamp,
                ),
            )
            return int(cursor.lastrowid)

    def _delete_entry(self, kind: str, record_id: str) -> None:
        with self.db.vector() as conn, conn:
            conn.execute(
                "DELETE FROM vector_entries WHERE kind = ? AND record_id = ?",
                (kind, record_id),
            )

    def _remove_vector(self, index: Any, faiss_id: int) -> None:
        try:
            index.remove_ids(np.asarray([faiss_id], dtype=np.int64))
        except (RuntimeError, TypeError, ValueError) as exc:
            # Se l'ID non è presente, non è un errore: l'indice può essere
            # già parzialmente ricostruito.
            logger.debug(
                "Rimozione del vettore FAISS %s non riuscita.",
                faiss_id,
                exc_info=exc,
            )

    def sync(
        self,
        kind: str,
        records: Iterable[dict[str, str]],
        *,
        rebuild: bool = False,
    ) -> dict[str, Any]:
        """Allinea l'indice a un insieme completo di record.

        ``records`` deve contenere tutti i record che dovrebbero essere
        indicizzati per quel tipo di memoria. Tutti gli altri vengono rimossi.
        """

        kind = _validate_kind(kind)
        desired: dict[str, str] = {}
        for record in records:
            record_id = record.get("record_id")
            text = record.get("text")
            access_level = record.get("access_level", "conscious")
            if not isinstance(record_id, str) or not isinstance(text, str):
                raise ValidationError(
                    "Ogni record indicizzato richiede record_id e text."
                )
            record_id, text = self._validate_record(
                kind, record_id, text, access_level
            )
            self._verify_source_visible(kind, record_id)
            if record_id in desired:
                raise ValidationError(
                    f"Record duplicato nell'input dell'indice: {record_id}."
                )
            desired[record_id] = text

        with self._lock:
            meta = self._meta(kind)
            if not desired and meta is None and not self.index_path(kind).exists():
                return {
                    "kind": kind,
                    "indexed": 0,
                    "updated": 0,
                    "removed": 0,
                    "unchanged": 0,
                    "count": 0,
                    "status": "empty",
                }

            if rebuild:
                dimension = self.embedder.dimension
                index = self._new_index(dimension)
                if meta is not None:
                    with self.db.vector() as conn, conn:
                        conn.execute(
                            "DELETE FROM vector_entries WHERE kind = ?", (kind,)
                        )
                self._invalidate(kind)
                existing: dict[str, dict[str, Any]] = {}
            else:
                if meta is None:
                    raise VectorIndexError(
                        f"Indice {kind} non inizializzato. "
                        "Esegui `python -m agent_memory.index --rebuild`."
                    )
                if meta["model"] != self.embedder.model_name:
                    raise VectorIndexError(
                        f"L'indice {kind} usa il modello '{meta['model']}', "
                        f"mentre il modello configurato è '{self.embedder.model_name}'. "
                        "Esegui `python -m agent_memory.index --rebuild`."
                    )
                dimension = int(meta["dimension"])
                index = self._load_index(kind, dimension=dimension)
                if index is None:
                    raise VectorIndexError(
                        f"File dell'indice {kind} mancante. "
                        "Esegui `python -m agent_memory.index --rebuild`."
                    )
                existing = {entry["record_id"]: entry for entry in self._entries(kind)}
                if len(existing) != int(index.ntotal):
                    raise VectorIndexError(
                        f"Indice {kind} incoerente: {len(existing)} mapping, "
                        f"{index.ntotal} vettori. Esegui "
                        "`python -m agent_memory.index --rebuild`."
                    )

            model = self.embedder.model_name
            self._set_meta(
                kind,
                model=model,
                dimension=dimension,
                count=int(index.ntotal),
                status="stale",
                error=None,
            )

            try:
                removed = 0
                stale_ids = [record_id for record_id in existing if record_id not in desired]
                for record_id in stale_ids:
                    self._remove_vector(index, int(existing[record_id]["faiss_id"]))
                    self._delete_entry(kind, record_id)
                    removed += 1

                to_embed: list[tuple[str, str, str]] = []
                unchanged = 0
                for record_id, text in desired.items():
                    text_hash = embedding_text_hash(model, text)
                    entry = existing.get(record_id)
                    if (
                        entry is not None
                        and entry["text_hash"] == text_hash
                        and entry["model"] == model
                        and int(entry["dimension"]) == dimension
                    ):
                        unchanged += 1
                        continue
                    to_embed.append((record_id, text, text_hash))

                vectors = self.embedder.embed_documents(
                    [text for _record_id, text, _hash in to_embed]
                )
                indexed = 0
                updated = 0
                for (record_id, _text, text_hash), vector in zip(to_embed, vectors):
                    vector = np.asarray(vector, dtype=np.float32).reshape(1, -1)
                    if vector.shape[1] != dimension:
                        raise VectorIndexError(
                            f"Dimensione embedding {vector.shape[1]} diversa "
                            f"da quella dell'indice {dimension}."
                        )
                    entry = existing.get(record_id)
                    if entry is not None:
                        self._remove_vector(index, int(entry["faiss_id"]))
                        faiss_id = int(entry["faiss_id"])
                        with self.db.vector() as conn, conn:
                            conn.execute(
                                """
                                    UPDATE vector_entries
                                    SET text_hash = ?, model = ?, dimension = ?,
                                        indexed_at = ?, updated_at = ?
                                    WHERE kind = ? AND record_id = ?
                                    """,
                                (
                                    text_hash,
                                    model,
                                    dimension,
                                    now_iso(self.config.timezone_name()),
                                    now_iso(self.config.timezone_name()),
                                    kind,
                                    record_id,
                                ),
                            )
                        updated += 1
                    else:
                        faiss_id = self._allocate_entry(
                            kind, record_id, text_hash, model, dimension
                        )
                        indexed += 1
                    index.add_with_ids(
                        vector, np.asarray([faiss_id], dtype=np.int64)
                    )

                self._persist(kind, index)
                self._set_meta(
                    kind,
                    model=model,
                    dimension=dimension,
                    count=int(index.ntotal),
                    status="ready" if index.ntotal else "empty",
                    error=None,
                )
                return {
                    "kind": kind,
                    "indexed": indexed,
                    "updated": updated,
                    "removed": removed,
                    "unchanged": unchanged,
                    "count": int(index.ntotal),
                    "status": "ready" if index.ntotal else "empty",
                }
            except Exception as exc:
                self._invalidate(kind)
                self._mark_inconsistent(kind, str(exc))
                if isinstance(exc, (AccessDeniedError, ValidationError, VectorIndexError)):
                    raise
                raise VectorIndexError(
                    f"Sincronizzazione dell'indice {kind} fallita: {exc}"
                ) from exc

    def index_one(
        self, kind: str, record_id: str, text: str, *, access_level: str = "conscious"
    ) -> dict[str, Any]:
        """Aggiorna un singolo record in un indice già inizializzato."""

        kind = _validate_kind(kind)
        record_id, text = self._validate_record(
            kind, record_id, text, access_level
        )
        self._verify_source_visible(kind, record_id)
        with self._lock:
            meta = self._meta(kind)
            if meta is None:
                raise VectorIndexError(
                    f"Indice {kind} non inizializzato. "
                    "Esegui `python -m agent_memory.index --rebuild`."
                )
            if meta["model"] != self.embedder.model_name:
                raise VectorIndexError(
                    f"Modello dell'indice '{meta['model']}' diverso da quello "
                    f"configurato '{self.embedder.model_name}'."
                )
            dimension = int(meta["dimension"])
            index = self._load_index(kind, dimension=dimension)
            if index is None:
                raise VectorIndexError(
                    f"File dell'indice {kind} mancante. "
                    "Esegui `python -m agent_memory.index --rebuild`."
                )
            text_hash = embedding_text_hash(self.embedder.model_name, text)
            entry = None
            with self.db.vector() as conn:
                row = conn.execute(
                    "SELECT * FROM vector_entries WHERE kind = ? AND record_id = ?",
                    (kind, record_id),
                ).fetchone()
                entry = dict(row) if row is not None else None
            if (
                entry is not None
                and entry["text_hash"] == text_hash
                and entry["model"] == self.embedder.model_name
                and int(entry["dimension"]) == dimension
            ):
                return {"kind": kind, "record_id": record_id, "status": "unchanged"}

            try:
                vector = self.embedder.embed_documents([text])[0]
                vector = np.asarray(vector, dtype=np.float32).reshape(1, -1)
                if vector.shape[1] != dimension:
                    raise VectorIndexError(
                        f"Dimensione embedding {vector.shape[1]} diversa "
                        f"da quella dell'indice {dimension}."
                    )
                if entry is not None:
                    self._remove_vector(index, int(entry["faiss_id"]))
                    faiss_id = int(entry["faiss_id"])
                    with self.db.vector() as conn, conn:
                        conn.execute(
                            """
                                UPDATE vector_entries
                                SET text_hash = ?, model = ?, dimension = ?,
                                    indexed_at = ?, updated_at = ?
                                WHERE kind = ? AND record_id = ?
                                """,
                            (
                                text_hash,
                                self.embedder.model_name,
                                dimension,
                                now_iso(self.config.timezone_name()),
                                now_iso(self.config.timezone_name()),
                                kind,
                                record_id,
                            ),
                        )
                else:
                    faiss_id = self._allocate_entry(
                        kind,
                        record_id,
                        text_hash,
                        self.embedder.model_name,
                        dimension,
                    )
                index.add_with_ids(vector, np.asarray([faiss_id], dtype=np.int64))
                self._persist(kind, index)
                self._set_meta(
                    kind,
                    model=self.embedder.model_name,
                    dimension=dimension,
                    count=int(index.ntotal),
                    status="ready",
                    error=None,
                )
                return {
                    "kind": kind,
                    "record_id": record_id,
                    "status": "updated" if entry is not None else "indexed",
                }
            except Exception as exc:
                self._invalidate(kind)
                self._mark_inconsistent(kind, str(exc))
                if isinstance(exc, VectorIndexError):
                    raise
                raise VectorIndexError(
                    f"Aggiornamento dell'indice fallito: {exc}"
                ) from exc

    # -- ricerca ------------------------------------------------------------

    def search(
        self,
        kind: str,
        query_vector: Sequence[float],
        *,
        limit: int = 10,
        allowed_ids: Sequence[str] | None = None,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        kind = _validate_kind(kind)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValidationError("limit: atteso un intero positivo.")
        query = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(query))
        if norm == 0.0:
            raise ValidationError("Il vettore della query è nullo.")
        query = query / norm

        with self._lock:
            meta = self._meta(kind)
            if meta is None:
                raise VectorIndexError(
                    f"Indice {kind} non inizializzato. "
                    "Esegui `python -m agent_memory.index --rebuild`."
                )
            dimension = int(meta["dimension"])
            if query.shape[0] != dimension:
                raise VectorIndexError(
                    f"Query di dimensione {query.shape[0]}, indice {dimension}."
                )
            index = self._load_index(kind, dimension=dimension)
            if index is None:
                raise VectorIndexError(
                    f"File dell'indice {kind} mancante. "
                    "Esegui `python -m agent_memory.index --rebuild`."
                )
            if index.ntotal == 0:
                return []

            if allowed_ids is not None:
                unique_ids = list(dict.fromkeys(str(item) for item in allowed_ids))
                if not unique_ids:
                    return []
                entries = self._entries_for_ids(kind, unique_ids)
                if not entries:
                    return []
                vectors: list[np.ndarray] = []
                kept: list[dict[str, Any]] = []
                for entry in entries:
                    try:
                        vector = np.asarray(
                            index.reconstruct(int(entry["faiss_id"])),
                            dtype=np.float32,
                        ).reshape(-1)
                    except (RuntimeError, TypeError, ValueError) as exc:
                        logger.debug(
                            "Vettore FAISS %s non ricostruibile.",
                            entry["faiss_id"],
                            exc_info=exc,
                        )
                        continue
                    if vector.shape[0] != dimension:
                        continue
                    vectors.append(vector)
                    kept.append(entry)
                if not vectors:
                    return []
                matrix = np.vstack(vectors)
                scores = matrix @ query
                order = np.argsort(-scores)
                results: list[dict[str, Any]] = []
                for position in order[:limit]:
                    score = float(scores[position])
                    if score < min_similarity:
                        continue
                    entry = kept[int(position)]
                    results.append(
                        {
                            "record_id": entry["record_id"],
                            "faiss_id": int(entry["faiss_id"]),
                            "similarity": score,
                        }
                    )
                return results

            k = min(limit, int(index.ntotal))
            distances, ids = index.search(query.reshape(1, -1), k)
            entries_by_faiss = {
                int(entry["faiss_id"]): entry for entry in self._entries(kind)
            }
            results = []
            for distance, faiss_id in zip(distances[0], ids[0]):
                if int(faiss_id) < 0:
                    continue
                entry = entries_by_faiss.get(int(faiss_id))
                if entry is None:
                    continue
                score = float(distance)
                if score < min_similarity:
                    continue
                results.append(
                    {
                        "record_id": entry["record_id"],
                        "faiss_id": int(faiss_id),
                        "similarity": score,
                    }
                )
            return results

    # -- diagnostica --------------------------------------------------------

    def status(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": True,
            "index_dir": str(self.index_dir),
            "model": self.embedder.model_name,
            "indexes": {},
        }
        for kind in ("episodic", "semantic"):
            item: dict[str, Any] = {
                "ok": False,
                "path": str(self.index_path(kind)),
                "model": None,
                "dimension": None,
                "count": 0,
                "ntotal": 0,
                "status": "empty",
                "issues": [],
            }
            try:
                meta = self._meta(kind)
                path_exists = self.index_path(kind).exists()
                if meta is None:
                    item["issues"].append(
                        "Metadati assenti: esegui `python -m agent_memory.index --rebuild`."
                    )
                    if not path_exists:
                        item["issues"] = [
                            (
                                "Indice non costruito: esegui "
                                "`python -m agent_memory.index --rebuild`."
                            )
                        ]
                        result["indexes"][kind] = item
                        result["ok"] = False
                        continue
                dimension = int(meta["dimension"]) if meta else None
                index = self._load_index(kind, dimension=dimension)
                entries = self._entries(kind)
                ntotal = int(index.ntotal) if index is not None else 0
                item.update(
                    {
                        "model": meta["model"] if meta else None,
                        "dimension": index.d if index is not None else dimension,
                        "count": len(entries),
                        "ntotal": ntotal,
                        "status": meta["status"] if meta else "empty",
                    }
                )
                if meta is None:
                    item["issues"].append("Metadati assenti.")
                else:
                    if meta["model"] != self.embedder.model_name:
                        item["issues"].append(
                            f"Modello indice '{meta['model']}' diverso da quello "
                            f"configurato '{self.embedder.model_name}'."
                        )
                    if int(meta["count"]) != ntotal:
                        item["issues"].append(
                            f"Conteggio metadati {meta['count']} diverso da "
                            f"ntotal {ntotal}."
                        )
                    if meta["status"] in {"stale", "inconsistent"}:
                        item["issues"].append(f"Stato indice: {meta['status']}.")
                if len(entries) != ntotal:
                    item["issues"].append(
                        f"Record mappati {len(entries)} diversi da ntotal {ntotal}."
                    )
                if not path_exists:
                    item["issues"].append("File indice assente.")
                item["ok"] = not item["issues"]
            except (DatabaseUnavailable, VectorIndexError, ValidationError) as exc:
                item["issues"].append(str(exc))
                item["ok"] = False
            result["indexes"][kind] = item
            result["ok"] = bool(result["ok"] and item["ok"])
        return result

    def check(self) -> dict[str, Any]:
        """Verifica che ogni mapping abbia un vettore ricostruibile."""

        result = self.status()
        for kind in ("episodic", "semantic"):
            item = result["indexes"][kind]
            if not item.get("path") or not Path(item["path"]).exists():
                continue
            try:
                meta = self._meta(kind)
                if meta is None:
                    continue
                index = self._load_index(kind, dimension=int(meta["dimension"]))
                if index is None:
                    continue
                for entry in self._entries(kind):
                    try:
                        self._verify_source_visible(kind, entry["record_id"])
                    except AccessDeniedError as exc:
                        item["issues"].append(str(exc))
                    try:
                        vector = np.asarray(
                            index.reconstruct(int(entry["faiss_id"])),
                            dtype=np.float32,
                        )
                    except (RuntimeError, TypeError, ValueError):
                        item["issues"].append(
                            f"Vettore mancante per {entry['record_id']} "
                            f"(faiss_id {entry['faiss_id']})."
                        )
                        continue
                    if vector.shape[0] != int(meta["dimension"]):
                        item["issues"].append(
                            f"Dimensione errata per {entry['record_id']}."
                        )
                item["ok"] = not item["issues"]
            except (DatabaseUnavailable, VectorIndexError, ValidationError) as exc:
                item["issues"].append(str(exc))
                item["ok"] = False
        result["ok"] = all(
            item.get("ok") for item in result["indexes"].values()
        )
        return result
