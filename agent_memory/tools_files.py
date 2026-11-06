"""Tool MCP della sandbox file e della generazione visiva."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .errors import AgentMemoryError
from .files import FileSandbox
from .visual import VisualService


def register_file_tools(
    mcp: FastMCP, *, files: FileSandbox, visual: VisualService
) -> None:
    @mcp.tool()
    def generate_image(prompt: str) -> dict[str, str]:
        """Genera un'immagine usando solo il modello configurato, se l'amministratore ha abilitato la generazione.

        Questo tool resta disponibile per retrocompatibilità ma non può inviare
        immagini a un provider esterno: la generazione Replicate richiede
        conferma esplicita nella sezione Aspetto della dashboard.
        """
        try:
            return visual.generate_image(prompt)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def list_files(path: str = "", limit: int = 200) -> dict[str, Any]:
        """Elenca file e cartelle nella sandbox dei file dell'agente.

        La sandbox è la cartella ``files`` del profilo: non contiene né la
        configurazione né i database dell'agente.
        """

        try:
            return files.list_files(path, limit=limit)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def read_file(path: str, max_chars: int = 20000) -> dict[str, Any]:
        """Legge un file di testo UTF-8 dalla sandbox dei file dell'agente."""

        try:
            return files.read_file(path, max_chars=max_chars)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def write_file(
        path: str, content: str, overwrite: bool = False
    ) -> dict[str, Any]:
        """Scrive un file di testo nella sandbox dei file dell'agente.

        Non sovrascrive un file esistente senza ``overwrite=True`` e non può
        uscire dalla sandbox con percorsi assoluti, ``..`` o symlink.
        """

        try:
            return files.write_file(path, content, overwrite=overwrite)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc
