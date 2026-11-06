"""Tool MCP del rapporto di situazione (Fase 1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

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
