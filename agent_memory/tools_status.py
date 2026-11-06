"""Tool MCP di stato e salute del profilo."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .errors import AgentMemoryError
from .files import FileSandbox
from .retrieval import RetrievalService
from .service import ShadowService


def register_status_tools(
    mcp: FastMCP,
    *,
    root: str | Path | None,
    retrieval: RetrievalService,
    files: FileSandbox,
) -> None:
    @mcp.tool()
    def get_status() -> dict[str, Any]:
        """Restituisce lo stato di configurazione e database, senza contenuti latenti."""

        health = retrieval.health()
        try:
            shadow = ShadowService(retrieval.root).health()
            health["shadow_policy"] = {
                "ok": shadow["ok"],
                "access_policy": shadow["access_policy"],
                "trait_count": shadow["trait_count"],
            }
        except AgentMemoryError as exc:
            health["shadow_policy"] = {"ok": False, "error": str(exc)}
        health["files"] = files.status()
        return health
