"""Tool MCP del registro delle persone incontrate.

Il registro è separato dal grafo fidato: questi tool leggono e aggiungono
osservazioni, ma non confermano fatti e non creano relazioni nel
``social_graph``. La conferma e la promozione restano azioni amministrative.

Le letture rispettano la policy del registro: per impostazione predefinita
l'agente vede solo i fatti confermati e non sensibili, mentre i candidati
restano nella coda di revisione della dashboard fino a una conferma.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .acquaintances import AcquaintanceService, visible_person
from .errors import AgentMemoryError


def register_acquaintance_tools(
    mcp: FastMCP, *, service: AcquaintanceService
) -> None:
    @mcp.tool()
    def list_acquaintances(status: str = "all") -> dict[str, Any]:
        """Elenca le persone incontrate e i loro profili progressivi.

        ``status`` può essere ``all``, ``unknown``, ``acquaintance`` o
        ``known``. Ogni fatto riporta se è candidato, confermato o scartato.
        Le persone importanti e i contatti fidati restano in
        ``get_social_graph``.
        """

        try:
            if status not in ("all", "unknown", "acquaintance", "known"):
                raise ToolError("status: usa all, unknown, acquaintance o known.")
            data = service.snapshot()
            policy = data["policy"]
            people = [
                visible_person(person, policy) for person in data["people"]
            ]
            if status != "all":
                people = [p for p in people if p["status"] == status]
            return {
                "people": people,
                "count": len(people),
                "categories": data["categories"],
                "agent_access": {
                    "fact_visibility": policy["agent_fact_visibility"],
                    "sensitive_access": policy["agent_sensitive_access"],
                },
            }
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def get_acquaintance(person_id: str) -> dict[str, Any]:
        """Restituisce il profilo progressivo di una persona incontrata."""

        try:
            person = service.person(person_id)
            if person is None:
                raise ToolError(f"Persona incontrata non trovata: {person_id}.")
            policy = service.store.read()["policy"]
            return visible_person(person, policy)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def record_acquaintance_observation(
        category: str,
        value: str,
        person_id: str = "",
        display_name: str = "",
        channel: str = "",
        handle: str = "",
        confidence: float = 0.4,
        source: str = "",
    ) -> dict[str, Any]:
        """Registra un fatto candidato su una persona incontrata.

        Usa solo ciò che la persona ha dichiarato o che è visibile nel
        contesto (per esempio il nickname pubblico). Il fatto resta
        candidato finché l'amministratore non lo conferma dalla dashboard;
        i dati sensibili non vengono mai confermati da questo tool. Se la
        persona non esiste, viene creata con il nickname o l'handle.
        """

        try:
            person = service.store.observe(
                person_id=person_id,
                display_name=display_name,
                channel=channel,
                handle=handle,
                category=category,
                value=value,
                confidence=confidence,
                source=source,
            )
            decorated = service.person(person["person_id"])
            return {"person": decorated}
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def extract_acquaintance_facts(
        text: str,
        person_id: str = "",
        display_name: str = "",
        channel: str = "",
        handle: str = "",
        source: str = "",
    ) -> dict[str, Any]:
        """Riconosce fatti candidati in un messaggio condiviso dall'utente.

        Usa il testo originale del messaggio: l'estrazione è deterministica e
        conservativa, riconosce solo dichiarazioni esplicite in prima persona
        (nome, età, città, lavoro, formazione, interessi) e crea candidati a
        bassa confidenza. Non deduce dati sensibili e non conferma nulla:
        la revisione resta nella dashboard. Se la persona non esiste, viene
        creata con il nickname o l'handle.
        """

        try:
            person, candidates = service.store.extract(
                text,
                person_id=person_id,
                display_name=display_name,
                channel=channel,
                handle=handle,
                source=source,
            )
            decorated = service.person(person["person_id"])
            return {
                "person": decorated,
                "extracted": candidates,
                "count": len(candidates),
            }
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc
