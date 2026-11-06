"""Tool MCP degli incarichi autonomi."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .autonomy import AutonomyService
from .errors import AgentMemoryError


def register_autonomy_tools(mcp: FastMCP, *, autonomy: AutonomyService) -> None:
    @mcp.tool()
    def autonomous_claim(url: str) -> dict[str, Any]:
        """Acquisisce un impulso senza aprire siti. Il client pianifica questa chiamata."""
        try:
            return autonomy.claim(url)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def autonomous_status(run_id: str) -> dict[str, Any]:
        """Legge stato durevole; un'azione interrotta resta incerta e non va ripetuta."""
        try:
            return autonomy.status(run_id)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def autonomous_observe(run_id: str) -> dict[str, Any]:
        """Legge pagina ed elementi senza compiere azioni sul sito."""
        try:
            return autonomy.observe(run_id)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def autonomous_act(run_id: str, action_id: str, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Esegue open/click/fill/press/wait una sola volta per action_id; incerto significa non ripetere."""
        try:
            return autonomy.act(run_id, action_id, action, arguments)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def autonomous_verify(run_id: str, text: str) -> dict[str, Any]:
        """Verifica testo nella UI e chiude l'impulso originale; non certifica la consegna remota."""
        try:
            return autonomy.verify(run_id, text)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def autonomous_abort(run_id: str) -> dict[str, Any]:
        """Interrompe un incarico senza dichiarare eseguito il suo obiettivo."""
        try:
            return autonomy.abort(run_id)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc
