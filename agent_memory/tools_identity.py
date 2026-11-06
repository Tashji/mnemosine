"""Tool MCP dell'identità e della trasparenza."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .config import ConfigStore, project_root
from .errors import AgentMemoryError
from .identity import disclosure_payload
from .service import MemoryService
from .shadow_policy import compromises_disclosure, disclosure_guidance


def register_identity_tools(
    mcp: FastMCP, *, root: str | Path | None, service: MemoryService
) -> None:
    @mcp.tool()
    def get_identity() -> dict[str, Any]:
        """Restituisce l'identità conscia dell'agente (anagrafica, famiglia, personalità)."""

        try:
            return service.get_identity()
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def identity_disclosure(question: str) -> dict[str, Any]:
        """Risponde in modo deterministico alle domande dirette sulla propria identità.

        Usalo quando l'utente chiede se sei una persona reale, un essere umano,
        un bot, un programma o un'intelligenza artificiale. Se
        ``direct_identity_question`` è vero e ``applies`` è vero, riporta
        ``reply`` testualmente: l'identità non dipende dal modello. Se
        ``applies`` è falso, segui ``guidance``, se presente, e rispondi come
        il personaggio. Se la domanda non riguarda la tua identità, va gestita
        normalmente.
        """

        shadow = ConfigStore(root or project_root()).get("shadow_traits")
        return disclosure_payload(
            question,
            compromises=compromises_disclosure(shadow),
            guidance=disclosure_guidance(shadow),
        )
