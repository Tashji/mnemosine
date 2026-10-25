"""Recupero ibrido della memoria conscia.

Il servizio combina due segnali:

* una ricerca lessicale, utile per nomi, date e parole esatte;
* una ricerca vettoriale, utile per parafrasi e concetti.

I risultati vengono fusi con Reciprocal Rank Fusion (RRF). Ogni risultato
viene riletto dalla fonte SQLite e verificato contro la vista conscia o
l'archivio semantico attivo: un residuo dell'indice non può far riemergere un
ricordo latente o represso.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .config import ConfigStore
from .db import decode_memory
from .embeddings import Embedder, FastEmbedder
from .errors import (
    AgentMemoryError,
    DatabaseUnavailable,
    NotFoundError,
    ValidationError,
    VectorIndexError,
)
from .semantic import SemanticService
from .service import MemoryService, _clamp_limit, _require_str
from .vector_index import VectorIndexManager

RRF_K = 60
MAX_SEARCH_LIMIT = 50


def _search_limit(limit: Any) -> int:
    value = _clamp_limit(limit, default=10)
    return max(1, min(value, MAX_SEARCH_LIMIT))


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def episodic_embedding_text(memory: dict[str, Any]) -> str:
    """Testo indicizzato per un ricordo episodico conscio."""

    parts = [
        f"Ricordo personale: {_clean_text(memory.get('title'))}",
        _clean_text(memory.get("description")),
    ]
    if memory.get("place"):
        parts.append(f"Luogo: {_clean_text(memory['place'])}")
    people = memory.get("people") or []
    if people:
        parts.append("Persone: " + ", ".join(str(item) for item in people))
    emotion = memory.get("emotional_state") or {}
    if emotion.get("label"):
        parts.append(f"Stato emotivo: {_clean_text(emotion['label'])}")
    if memory.get("consequences"):
        parts.append(f"Conseguenze: {_clean_text(memory['consequences'])}")
    tags = memory.get("tags") or []
    if tags:
        parts.append("Tag: " + ", ".join(str(item) for item in tags))
    return "\n".join(part for part in parts if part)


def semantic_embedding_text(item: dict[str, Any]) -> str:
    """Testo indicizzato per una conoscenza generale attiva."""

    parts = [
        f"Conoscenza: {_clean_text(item.get('title'))}",
        _clean_text(item.get("content")),
    ]
    if item.get("category"):
        parts.append(f"Categoria: {_clean_text(item['category'])}")
    tags = item.get("tags") or []
    if tags:
        parts.append("Tag: " + ", ".join(str(tag) for tag in tags))
    return "\n".join(part for part in parts if part)


class RetrievalService:
    """Ricerca ibrida e manutenzione dell'indice vettoriale."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        embedder: Embedder | None = None,
        index: VectorIndexManager | None = None,
        memory: MemoryService | None = None,
        semantic: SemanticService | None = None,
    ):
        self.config = ConfigStore(root)
        self.root = self.config.root
        self.memory = memory or MemoryService(self.root)
        self.semantic = semantic or SemanticService(self.root)
        self.embedder = embedder or FastEmbedder(
            cache_dir=self.root / "data" / "models"
        )
        self.index = index or VectorIndexManager(
            self.root, embedder=self.embedder
        )

    # -- fonti --------------------------------------------------------------

    def _conscious_memories(self) -> list[dict[str, Any]]:
        with self.memory.db.timeline() as conn:
            rows = conn.execute(
                "SELECT * FROM conscious_episodic_memories ORDER BY id ASC"
            ).fetchall()
        return [decode_memory(row) for row in rows]

    def _semantic_items(self) -> list[dict[str, Any]]:
        return self.semantic.all_active_items()

    def _records(self, kind: str) -> list[dict[str, str]]:
        if kind == "episodic":
            return [
                {
                    "record_id": memory["id"],
                    "text": episodic_embedding_text(memory),
                    "access_level": "conscious",
                }
                for memory in self._conscious_memories()
                if memory.get("access_level") == "conscious"
            ]
        if kind == "semantic":
            return [
                {
                    "record_id": item["id"],
                    "text": semantic_embedding_text(item),
                    "access_level": "conscious",
                }
                for item in self._semantic_items()
                if item.get("status") == "active"
            ]
        raise ValidationError(f"kind: valore non valido '{kind}'.")

    def _record(self, kind: str, record_id: str) -> dict[str, Any] | None:
        if kind == "episodic":
            return self.memory.get_memory(record_id)
        if kind == "semantic":
            return self.semantic.get_item(record_id)
        raise ValidationError(f"kind: valore non valido '{kind}'.")

    # -- indice -------------------------------------------------------------

    def sync_index(self, *, rebuild: bool = False) -> dict[str, Any]:
        """Sincronizza o ricostruisce entrambi gli indici."""

        results: dict[str, Any] = {}
        ok = True
        for kind in ("episodic", "semantic"):
            try:
                results[kind] = self.index.sync(
                    kind, self._records(kind), rebuild=rebuild
                )
            except AgentMemoryError as exc:
                results[kind] = {"kind": kind, "error": str(exc)}
                ok = False
        return {
            "ok": ok,
            "model": self.embedder.model_name,
            "indexes": results,
            "status": self.index.status(),
        }

    def index_memory(self, memory_id: str) -> dict[str, Any]:
        memory = self.memory.get_memory(memory_id)
        if memory is None:
            raise NotFoundError(
                f"Ricordo non trovato nella memoria conscia: {memory_id}."
            )
        return self.index.index_one(
            "episodic", memory["id"], episodic_embedding_text(memory)
        )

    def index_semantic_item(self, item_id: str) -> dict[str, Any]:
        item = self.semantic.get_item(item_id)
        if item is None:
            raise NotFoundError(f"Conoscenza non trovata: {item_id}.")
        return self.index.index_one(
            "semantic", item["id"], semantic_embedding_text(item)
        )

    def index_if_ready(self, kind: str, record_id: str) -> dict[str, Any]:
        """Indicizza un nuovo record solo se l'indice è già inizializzato.

        Serve dopo una scrittura MCP: non si scarica un modello e non si
        costruisce un indice implicito durante la creazione di un ricordo.
        """

        try:
            status = self.index.status()
        except AgentMemoryError as exc:
            return {"indexed": False, "reason": str(exc)}
        item = status.get("indexes", {}).get(kind, {})
        if not item.get("ok"):
            return {
                "indexed": False,
                "reason": "indice non pronto; esegui `python -m agent_memory.index --sync`.",
            }
        try:
            if kind == "episodic":
                return self.index_memory(record_id)
            if kind == "semantic":
                return self.index_semantic_item(record_id)
        except AgentMemoryError as exc:
            return {"indexed": False, "reason": str(exc)}
        return {"indexed": False, "reason": f"kind non valido: {kind}."}

    # -- ricerca lessicale --------------------------------------------------

    @staticmethod
    def _episodic_lexical_score(memory: dict[str, Any], query: str) -> int:
        needle = query.lower().strip()
        if not needle:
            return 0
        title = str(memory.get("title", "")).lower()
        description = str(memory.get("description", "")).lower()
        place = str(memory.get("place", "")).lower()
        people = " ".join(str(item) for item in memory.get("people", [])).lower()
        tags = " ".join(str(item) for item in memory.get("tags", [])).lower()
        score = 0
        if needle in title:
            score += 8
        if needle in description:
            score += 4
        if needle in tags:
            score += 3
        if needle in people:
            score += 3
        if needle in place:
            score += 2
        for token in re.findall(r"[0-9a-zA-ZÀ-ÿ_]+", needle):
            if len(token) < 2:
                continue
            if token in title:
                score += 2
            if token in description:
                score += 1
            if token in tags or token in people:
                score += 1
        return score

    def lexical_search(
        self,
        query: str,
        *,
        include_episodic: bool = True,
        include_semantic: bool = True,
        limit: int = 10,
        start_date: str | None = None,
        end_date: str | None = None,
        person: str | None = None,
        source: str | None = None,
        min_importance: int | None = None,
        tag: str | None = None,
        category: str | None = None,
        knowledge_type: str | None = None,
        semantic_source: str | None = None,
        acquired_after: str | None = None,
        acquired_before: str | None = None,
    ) -> list[dict[str, Any]]:
        limit = _search_limit(limit)
        query = _require_str(query, "query")
        candidate_limit = min(100, max(limit * 5, 20))
        results: list[dict[str, Any]] = []

        if include_episodic:
            candidates = self.memory.search_memories(
                query=query,
                limit=candidate_limit,
                start_date=start_date,
                end_date=end_date,
                source=source,
                min_importance=min_importance,
                tag=tag,
                person=person,
            )
            candidates.sort(
                key=lambda memory: -self._episodic_lexical_score(memory, query)
            )
            for rank, memory in enumerate(candidates[:candidate_limit], start=1):
                results.append(
                    {
                        "kind": "episodic",
                        "record_id": memory["id"],
                        "record": memory,
                        "lexical_rank": rank,
                        "lexical_score": self._episodic_lexical_score(memory, query),
                    }
                )

        if include_semantic:
            items = self.semantic.search_items(
                query=query,
                limit=candidate_limit,
                category=category,
                knowledge_type=knowledge_type,
                source=semantic_source,
                min_importance=min_importance,
                tag=tag,
                acquired_after=acquired_after,
                acquired_before=acquired_before,
            )
            for rank, item in enumerate(items[:candidate_limit], start=1):
                results.append(
                    {
                        "kind": "semantic",
                        "record_id": item["id"],
                        "record": item,
                        "lexical_rank": rank,
                        "lexical_score": 0,
                    }
                )
        return results

    # -- ricerca vettoriale -------------------------------------------------

    def semantic_search(
        self,
        query: str,
        *,
        include_episodic: bool = True,
        include_semantic: bool = True,
        limit: int = 10,
        start_date: str | None = None,
        end_date: str | None = None,
        person: str | None = None,
        source: str | None = None,
        min_importance: int | None = None,
        tag: str | None = None,
        category: str | None = None,
        knowledge_type: str | None = None,
        semantic_source: str | None = None,
        acquired_after: str | None = None,
        acquired_before: str | None = None,
        min_similarity: float = 0.3,
    ) -> list[dict[str, Any]]:
        limit = _search_limit(limit)
        query = _require_str(query, "query")
        query_vector = self.embedder.embed_query(query)
        candidate_limit = min(100, max(limit * 5, 20))
        results: list[dict[str, Any]] = []

        if include_episodic:
            episodic_filters = any(
                value is not None
                for value in (
                    start_date,
                    end_date,
                    person,
                    source,
                    min_importance,
                    tag,
                )
            )
            allowed = (
                self.memory.filter_memory_ids(
                    start_date=start_date,
                    end_date=end_date,
                    source=source,
                    min_importance=min_importance,
                    tag=tag,
                    person=person,
                )
                if episodic_filters
                else None
            )
            hits = self.index.search(
                "episodic",
                query_vector,
                limit=candidate_limit,
                allowed_ids=allowed,
                min_similarity=min_similarity,
            )
            for rank, hit in enumerate(hits, start=1):
                memory = self.memory.get_memory(hit["record_id"])
                if memory is None:
                    continue
                results.append(
                    {
                        "kind": "episodic",
                        "record_id": memory["id"],
                        "record": memory,
                        "semantic_rank": rank,
                        "similarity": hit["similarity"],
                    }
                )

        if include_semantic:
            semantic_filters = any(
                value is not None
                for value in (
                    category,
                    knowledge_type,
                    semantic_source,
                    min_importance,
                    tag,
                    acquired_after,
                    acquired_before,
                )
            )
            allowed = (
                self.semantic.filter_item_ids(
                    category=category,
                    knowledge_type=knowledge_type,
                    source=semantic_source,
                    min_importance=min_importance,
                    tag=tag,
                    acquired_after=acquired_after,
                    acquired_before=acquired_before,
                )
                if semantic_filters
                else None
            )
            hits = self.index.search(
                "semantic",
                query_vector,
                limit=candidate_limit,
                allowed_ids=allowed,
                min_similarity=min_similarity,
            )
            for rank, hit in enumerate(hits, start=1):
                item = self.semantic.get_item(hit["record_id"])
                if item is None:
                    continue
                results.append(
                    {
                        "kind": "semantic",
                        "record_id": item["id"],
                        "record": item,
                        "semantic_rank": rank,
                        "similarity": hit["similarity"],
                    }
                )
        return results

    # -- fusione ------------------------------------------------------------

    def hybrid_search(
        self,
        query: str,
        *,
        mode: str = "hybrid",
        include_episodic: bool = True,
        include_semantic: bool = True,
        limit: int = 10,
        start_date: str | None = None,
        end_date: str | None = None,
        person: str | None = None,
        source: str | None = None,
        min_importance: int | None = None,
        tag: str | None = None,
        category: str | None = None,
        knowledge_type: str | None = None,
        semantic_source: str | None = None,
        acquired_after: str | None = None,
        acquired_before: str | None = None,
        min_similarity: float = 0.3,
    ) -> dict[str, Any]:
        mode = (mode or "hybrid").lower()
        if mode not in {"lexical", "semantic", "hybrid"}:
            raise ValidationError("mode: ammessi 'lexical', 'semantic' o 'hybrid'.")
        if not include_episodic and not include_semantic:
            raise ValidationError(
                "Almeno uno tra include_episodic e include_semantic deve essere true."
            )
        query = _require_str(query, "query")
        limit = _search_limit(limit)

        warnings: list[str] = []
        lexical_results: list[dict[str, Any]] = []
        semantic_results: list[dict[str, Any]] = []
        semantic_available = False

        if mode in {"lexical", "hybrid"}:
            lexical_results = self.lexical_search(
                query,
                include_episodic=include_episodic,
                include_semantic=include_semantic,
                limit=limit,
                start_date=start_date,
                end_date=end_date,
                person=person,
                source=source,
                min_importance=min_importance,
                tag=tag,
                category=category,
                knowledge_type=knowledge_type,
                semantic_source=semantic_source,
                acquired_after=acquired_after,
                acquired_before=acquired_before,
            )

        if mode in {"semantic", "hybrid"}:
            try:
                semantic_results = self.semantic_search(
                    query,
                    include_episodic=include_episodic,
                    include_semantic=include_semantic,
                    limit=limit,
                    start_date=start_date,
                    end_date=end_date,
                    person=person,
                    source=source,
                    min_importance=min_importance,
                    tag=tag,
                    category=category,
                    knowledge_type=knowledge_type,
                    semantic_source=semantic_source,
                    acquired_after=acquired_after,
                    acquired_before=acquired_before,
                    min_similarity=min_similarity,
                )
                semantic_available = True
            except (VectorIndexError, DatabaseUnavailable) as exc:
                if mode == "semantic":
                    raise
                warnings.append(f"Ricerca vettoriale non disponibile: {exc}")
            except AgentMemoryError as exc:
                if mode == "semantic":
                    raise
                warnings.append(f"Ricerca vettoriale non disponibile: {exc}")

        fused: dict[tuple[str, str], dict[str, Any]] = {}

        def add(entry: dict[str, Any], signal: str, rank: int) -> None:
            key = (entry["kind"], entry["record_id"])
            target = fused.get(key)
            if target is None:
                target = {
                    "kind": entry["kind"],
                    "record_id": entry["record_id"],
                    "record": entry["record"],
                    "score": 0.0,
                    "matched_by": [],
                }
                fused[key] = target
            target["score"] += 1.0 / (RRF_K + rank)
            if signal not in target["matched_by"]:
                target["matched_by"].append(signal)
            if signal == "lexical":
                target["lexical_rank"] = rank
                target["lexical_score"] = entry.get("lexical_score", 0)
            else:
                target["semantic_rank"] = rank
                target["similarity"] = entry.get("similarity")

        for entry in lexical_results:
            add(entry, "lexical", int(entry.get("lexical_rank", 1)))
        for entry in semantic_results:
            add(entry, "semantic", int(entry.get("semantic_rank", 1)))

        ordered = sorted(
            fused.values(),
            key=lambda item: (-item["score"], item["kind"], item["record_id"]),
        )[:limit]
        for item in ordered:
            item["score"] = round(float(item["score"]), 8)
            if "similarity" in item:
                item["similarity"] = round(float(item["similarity"]), 6)

        return {
            "query": query,
            "mode": mode,
            "results": ordered,
            "count": len(ordered),
            "semantic_available": semantic_available,
            "warnings": warnings,
            "embedding_model": self.embedder.model_name if semantic_available else None,
        }

    # -- diagnostica --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        memory_health = self.memory.health()
        semantic_health = self.semantic.health()
        try:
            index_health = self.index.status()
        except AgentMemoryError as exc:
            index_health = {"ok": False, "error": str(exc), "indexes": {}}
        result = {
            **memory_health,
            "semantic_memory": semantic_health,
            "vector_index": index_health,
            "index_ready": bool(index_health.get("ok")),
        }
        result["ok"] = bool(memory_health.get("ok") and semantic_health.get("ok"))
        return result
