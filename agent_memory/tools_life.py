"""Tool MCP della vita: situazione, stato ed episodi (Fasi 1-3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .deliberation import DeliberationService
from .errors import AgentMemoryError
from .situation import SituationBuilder, render_situation


def register_life_tools(mcp: FastMCP, *, root: str | Path | None) -> None:
    @mcp.tool()
    def life_situation(at: str | None = None) -> dict[str, Any]:
        """Restituisce il rapporto di situazione della vita, senza decidere.

        Include bisogni, attività, stream, loop aperti, relazioni qualitative,
        affordance, vincoli e incognite. Non legge l'ombra, non espone segreti
        o numeri di telefono e non modifica il profilo. ``at`` è un timestamp
        ISO 8601 opzionale, utile per una ricostruzione deterministica.
        """

        try:
            report = SituationBuilder(root, at=at).build()
            return {"report": report, "text": render_situation(report)}
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def life_status() -> dict[str, Any]:
        """Legge lo stato degli episodi deliberativi senza aprire nulla.

        Restituisce il motore abilitato o spento, l'episodio attivo o l'ultimo
        episodio, il conteggio odierno e il prossimo istante utile. È una
        lettura: non applica scadenze, non decide e non modifica il profilo.
        Non espone contenuti riservati.
        """

        try:
            return DeliberationService(root).status()
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def life_decide(decision: dict[str, Any]) -> dict[str, Any]:
        """Chiude l'episodio attivo applicando una decisione interna.

        Accetta ``continue``, ``unstructured`` e ``intent``. In questa fase
        l'intento può usare solo ``tool: "none"``: aggiorna bisogni, emozione,
        loop e stream, ma resta un'azione simulata e non verifica fatti fisici.
        Browser, file e WhatsApp richiedono la Fase 4. ``do_nothing`` non è
        una decisione valida.
        """

        try:
            return DeliberationService(root).decide(decision)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc
