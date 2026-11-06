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

from mcp.server.fastmcp import FastMCP

from .autonomy import AutonomyService
from .browser import BrowserController
from .config import project_root
from .embeddings import Embedder
from .errors import AgentMemoryError
from .files import FileSandbox
from .instructions import build_instructions
from .persona import PERSONA_URI, load_persona
from .retrieval import RetrievalService
from .semantic import SemanticService
from .service import MemoryService, ShadowService
from .tools_autonomy import register_autonomy_tools
from .tools_browser import register_browser_tools
from .tools_files import register_file_tools
from .tools_identity import register_identity_tools
from .tools_life import register_life_tools
from .tools_memory import register_memory_tools
from .tools_state import register_state_tools
from .tools_status import register_status_tools
from .visual import VisualService

SERVER_NAME = "agent-memory"


def create_server(
    root: str | Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    embedder: Embedder | None = None,
    browser: BrowserController | None = None,
) -> FastMCP:
    """Costruisce il server MCP con i tool della memoria conscia."""

    browser_controller = browser or BrowserController(root)
    autonomy = AutonomyService(root, browser_controller)
    files = FileSandbox(root)
    visual = VisualService(root)
    service = MemoryService(root)
    semantic = SemanticService(root)
    retrieval = RetrievalService(
        root, embedder=embedder, memory=service, semantic=semantic
    )
    persona = load_persona(root)
    mcp = FastMCP(
        SERVER_NAME,
        instructions=build_instructions(persona),
        host=host,
        port=port,
    )

    @mcp.resource(
        PERSONA_URI,
        name="persona",
        description="Identità conversazionale e regole del profilo attivo.",
        mime_type="text/markdown",
    )
    def persona_resource() -> str:
        """Espone la persona del profilo anche ai client che leggono le risorse."""

        return persona

    register_identity_tools(mcp, root=root, service=service)
    register_state_tools(mcp, root=root, service=service)
    register_life_tools(mcp, root=root)
    register_memory_tools(
        mcp, service=service, semantic=semantic, retrieval=retrieval
    )
    register_autonomy_tools(mcp, autonomy=autonomy)
    register_browser_tools(mcp, browser=browser_controller)
    register_file_tools(mcp, files=files, visual=visual)
    register_status_tools(mcp, root=root, retrieval=retrieval, files=files)
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
        target = args.root if args.root is not None else project_root()
        if not (target / "config").is_dir():
            print(
                "Nessun profilo in questa directory: al primo avvio esegui "
                "`mnemosine-start` (oppure `python -m agent_memory.start`) per "
                "crearlo con il wizard.",
                file=sys.stderr,
            )
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
