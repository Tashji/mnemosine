"""Server MCP della memoria conscia.

Il server espone solo i dati che l'agente può conoscere consapevolmente:
identità, stato corrente, storia mondiale, grafo sociale, memoria episodica
conscia e conoscenze semantiche attive. I tratti latenti e il diario
dell'ombra restano accessibili soltanto tramite ``ShadowService``, che non
viene registrato come tool MCP.

Avvio:
    python -m agent_memory.server
    python -m agent_memory.server --transport streamable-http --port 8000
    python -m agent_memory.server --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .embeddings import Embedder
from .errors import AgentMemoryError
from .retrieval import RetrievalService
from .semantic import SemanticService
from .service import MemoryService, ShadowService

SERVER_NAME = "agent-memory"


def create_server(
    root: str | Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    embedder: Embedder | None = None,
) -> FastMCP:
    """Costruisce il server MCP con i tool della memoria conscia."""

    service = MemoryService(root)
    semantic = SemanticService(root)
    retrieval = RetrievalService(
        root, embedder=embedder, memory=service, semantic=semantic
    )
    mcp = FastMCP(
        SERVER_NAME,
        instructions=(
            "Memoria conscia dell'agente. Usa i tool per leggere identità, stato "
            "corrente, storia mondiale, relazioni, ricordi episodici consci e "
            "conoscenze semantiche attive. La ricerca ibrida combina corrispondenze "
            "lessicali e vettoriali. Non esistono tool per leggere o scrivere i "
            "tratti latenti o il diario dell'ombra: quei contenuti sono mediati "
            "dal motore dell'ombra."
        ),
        host=host,
        port=port,
    )

    @mcp.tool()
    def get_identity() -> dict[str, Any]:
        """Restituisce l'identità conscia dell'agente (anagrafica, famiglia, personalità)."""

        try:
            return service.get_identity()
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def get_current_state() -> dict[str, Any]:
        """Restituisce l'attività, l'emozione, il carico cognitivo e la routine correnti."""

        try:
            return service.get_current_state()
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def update_current_state(patch: dict[str, Any]) -> dict[str, Any]:
        """Aggiorna parzialmente lo stato corrente e restituisce il documento risultante.

        Il patch è un oggetto JSON: le chiavi annidate vengono unite, mentre i
        valori scalari e le liste sostituiscono il valore precedente. I campi di
        sicurezza dell'ombra non sono modificabili.
        """

        try:
            return service.update_current_state(patch)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def get_world_history(
        start_date: str | None = None,
        end_date: str | None = None,
        min_italy_relevance: int | None = None,
    ) -> dict[str, Any]:
        """Restituisce gli eventi storici reali, filtrabili per data e rilevanza italiana."""

        try:
            events = service.get_world_history(
                start_date=start_date,
                end_date=end_date,
                min_italy_relevance=min_italy_relevance,
            )
            return {"events": events, "count": len(events)}
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def get_social_graph() -> dict[str, Any]:
        """Restituisce il grafo delle relazioni con fiducia, intimità e storico sintetico."""

        try:
            return service.get_social_graph()
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def get_person(user_id: str) -> dict[str, Any]:
        """Restituisce la relazione con una persona nota, identificata da user_id."""

        try:
            person = service.get_person(user_id)
            if person is None:
                raise ToolError(f"Persona non trovata: {user_id}.")
            return person
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def search_memories(
        query: str | None = None,
        limit: int = 20,
        offset: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
        min_importance: int | None = None,
        tag: str | None = None,
        person: str | None = None,
        order: str = "desc",
    ) -> dict[str, Any]:
        """Cerca nella memoria episodica conscia con filtri testuali, temporali e relazionali.

        La ricerca è letterale (LIKE) e resta disponibile per compatibilità.
        Per il recupero semantico o ibrido usa ``search_memory``. I ricordi
        latenti o repressi sono esclusi.
        """

        try:
            memories = service.search_memories(
                query=query,
                limit=limit,
                offset=offset,
                start_date=start_date,
                end_date=end_date,
                source=source,
                min_importance=min_importance,
                tag=tag,
                person=person,
                order=order,
            )
            return {"memories": memories, "count": len(memories)}
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def get_memory(memory_id: str) -> dict[str, Any]:
        """Restituisce un ricordo conscio; i ricordi latenti o repressi risultano assenti."""

        try:
            memory = service.get_memory(memory_id)
            if memory is None:
                raise ToolError("Ricordo non trovato.")
            return memory
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def search_memory(
        query: str,
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
        """Cerca nella memoria conscia con ricerca lessicale, semantica o ibrida.

        ``mode='hybrid'`` fonde i due segnali con Reciprocal Rank Fusion.
        I filtri temporali e relazionali si applicano ai ricordi episodici;
        categoria, tipo e fonte semantica si applicano alle conoscenze.
        ``min_similarity`` è la soglia coseno per i risultati vettoriali.
        I contenuti latenti o repressi non sono mai restituiti.
        """

        try:
            return retrieval.hybrid_search(
                query,
                mode=mode,
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
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def search_semantic_memory(
        query: str,
        mode: str = "hybrid",
        limit: int = 10,
        category: str | None = None,
        knowledge_type: str | None = None,
        source: str | None = None,
        min_importance: int | None = None,
        tag: str | None = None,
        acquired_after: str | None = None,
        acquired_before: str | None = None,
        min_similarity: float = 0.3,
    ) -> dict[str, Any]:
        """Cerca soltanto nell'archivio delle conoscenze generali attive."""

        try:
            return retrieval.hybrid_search(
                query,
                mode=mode,
                include_episodic=False,
                include_semantic=True,
                limit=limit,
                category=category,
                knowledge_type=knowledge_type,
                semantic_source=source,
                min_importance=min_importance,
                tag=tag,
                acquired_after=acquired_after,
                acquired_before=acquired_before,
                min_similarity=min_similarity,
            )
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def get_semantic_item(item_id: str) -> dict[str, Any]:
        """Restituisce una conoscenza generale attiva, identificata da item_id."""

        try:
            item = semantic.get_item(item_id)
            if item is None:
                raise ToolError(f"Conoscenza non trovata: {item_id}.")
            return item
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def list_semantic_memory(
        limit: int = 20,
        offset: int = 0,
        category: str | None = None,
        knowledge_type: str | None = None,
        status: str = "active",
        source: str | None = None,
        min_importance: int | None = None,
        tag: str | None = None,
    ) -> dict[str, Any]:
        """Elenca le conoscenze generali, con filtri per categoria, tipo e stato.

        Usa ``status='all'`` per includere anche conoscenze superate o ritirate.
        """

        try:
            status_filter = None if status == "all" else status
            items = semantic.list_items(
                limit=limit,
                offset=offset,
                category=category,
                knowledge_type=knowledge_type,
                status=status_filter,
                source=source,
                min_importance=min_importance,
                tag=tag,
            )
            return {"items": items, "count": len(items)}
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def create_semantic_item(
        title: str,
        content: str,
        category: str,
        knowledge_type: str = "user_provided",
        source: str = "conversation",
        source_ref: str | None = None,
        reliability: float = 0.6,
        importance: int = 5,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Registra una conoscenza generale appresa durante una conversazione.

        Il server MCP non può dichiarare un fatto verificato: i fatti
        verificati richiedono una fonte documentale o amministrativa e sono
        registrati dal sistema. Sono ammessi contenuti forniti dall'utente,
        opinioni, ipotesi e informazioni incerte.
        """

        if knowledge_type == "verified_fact":
            raise ToolError(
                "Il server MCP non può dichiarare un fatto verificato; usa una "
                "fonte documentale o amministrativa."
            )
        if source == "admin_seed":
            raise ToolError(
                "Il server MCP non può usare la fonte admin_seed."
            )
        try:
            item = semantic.create_item(
                title=title,
                content=content,
                category=category,
                knowledge_type=knowledge_type,
                source=source,
                source_ref=source_ref,
                reliability=reliability,
                importance=importance,
                tags=tags,
                metadata=metadata,
                changed_by="mcp_server",
                change_reason="Conoscenza registrata tramite il server MCP.",
            )
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc
        item["index"] = retrieval.index_if_ready("semantic", item["id"])
        return item

    @mcp.tool()
    def get_semantic_links(item_id: str) -> dict[str, Any]:
        """Restituisce i collegamenti di una conoscenza verso altre conoscenze e ricordi."""

        try:
            return semantic.get_links(item_id)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def create_memory(
        title: str,
        description: str,
        importance: int = 5,
        source: str = "generated",
        event_time: str | None = None,
        place: str | None = None,
        people: list[str] | None = None,
        emotional_state: dict[str, Any] | None = None,
        consequences: str | None = None,
        source_ref: str | None = None,
        tags: list[str] | None = None,
        reliability: float = 0.9,
    ) -> dict[str, Any]:
        """Crea un ricordo episodico conscio generato dall'agente o dalla routine.

        Le interazioni reali e i seed amministrativi non possono essere creati
        da questo tool: sono registrati dalle integrazioni interne.
        """

        if source not in {"generated", "routine"}:
            raise ToolError(
                "Il server MCP può creare solo ricordi con source 'generated' o "
                "'routine'; le interazioni reali e i seed sono registrati dal sistema."
            )
        try:
            memory = service.create_memory(
                title=title,
                description=description,
                importance=importance,
                source=source,
                event_time=event_time,
                place=place,
                people=people,
                emotional_state=emotional_state,
                consequences=consequences,
                source_ref=source_ref,
                tags=tags,
                reliability=reliability,
            )
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc
        memory["index"] = retrieval.index_if_ready("episodic", memory["id"])
        return memory

    @mcp.tool()
    def add_memory_link(
        from_memory_id: str,
        to_memory_id: str,
        relation_type: str,
        strength: float = 0.5,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Collega due ricordi consci con una relazione tipizzata e una forza 0–1."""

        try:
            return service.add_memory_link(
                from_memory_id=from_memory_id,
                to_memory_id=to_memory_id,
                relation_type=relation_type,
                strength=strength,
                note=note,
            )
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def get_memory_links(memory_id: str) -> dict[str, Any]:
        """Restituisce i collegamenti che coinvolgono un ricordo conscio."""

        try:
            links = service.get_memory_links(memory_id)
            return {"links": links, "count": len(links)}
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def get_status() -> dict[str, Any]:
        """Restituisce lo stato di configurazione e database, senza contenuti latenti."""

        health = retrieval.health()
        try:
            shadow = ShadowService(service.root).health()
            health["shadow_policy"] = {
                "ok": shadow["ok"],
                "access_policy": shadow["access_policy"],
                "trait_count": shadow["trait_count"],
            }
        except AgentMemoryError as exc:
            health["shadow_policy"] = {"ok": False, "error": str(exc)}
        return health

    return mcp


def _check(root: str | Path | None) -> int:
    retrieval = RetrievalService(root)
    health = retrieval.health()
    try:
        health["shadow_diary"] = ShadowService(retrieval.root).health()
    except AgentMemoryError as exc:
        health["shadow_diary"] = {"ok": False, "error": str(exc)}
    print(json.dumps(health, ensure_ascii=False, indent=2))
    return 0 if health.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Server MCP della memoria conscia dell'agente."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory del progetto (default: quella che contiene il pacchetto).",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="Trasporto MCP (default: stdio).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host per i trasporti HTTP.")
    parser.add_argument("--port", type=int, default=8000, help="Porta per i trasporti HTTP.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verifica configurazione e database, stampa un riepilogo JSON ed esce.",
    )
    args = parser.parse_args(argv)

    try:
        if args.check:
            return _check(args.root)
        server = create_server(args.root, host=args.host, port=args.port)
        server.run(transport=args.transport)
        return 0
    except AgentMemoryError as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
