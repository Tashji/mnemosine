"""Tool MCP del registro delle persone incontrate.

Il registro è separato dal grafo fidato: questi tool leggono e aggiungono
osservazioni, ma non confermano fatti e non creano relazioni nel
``social_graph``. La conferma resta un'azione amministrativa.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .acquaintances import AcquaintanceService
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
            people = data["people"]
            if status != "all":
                people = [p for p in people if p["status"] == status]
            return {
                "people": people,
                "count": len(people),
                "categories": data["categories"],
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
            return person
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
