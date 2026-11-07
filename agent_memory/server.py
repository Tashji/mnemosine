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
import functools
import inspect
import json
import sys
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .audit import AuditLog
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


def _instrument_tools(mcp: FastMCP, audit: AuditLog) -> None:
    """Registra i tool avvolgendoli con l'audit, senza toccarne la firma.

    FastMCP conserva il riferimento alla funzione al momento della
    registrazione: sostituire ``mcp.call_tool`` dopo la costruzione non
    basterebbe per il trasporto reale. Il decoratore lavora quindi prima che i
    moduli registrino i loro tool.
    """

    original_tool = mcp.tool

    def audited_tool(*args: Any, **kwargs: Any) -> Any:
        register = original_tool(*args, **kwargs)

        def decorate(function: Any) -> Any:
            signature = inspect.signature(function)

            def arguments(call_args: tuple[Any, ...], call_kwargs: dict[str, Any]) -> Any:
                try:
                    bound = signature.bind(*call_args, **call_kwargs)
                    bound.apply_defaults()
                    return dict(bound.arguments)
                except (TypeError, ValueError):
                    return dict(call_kwargs)

            def successful(started: float, call_args: tuple[Any, ...], call_kwargs: dict[str, Any]) -> None:
                audit.record_tool_call(
                    function.__name__,
                    arguments(call_args, call_kwargs),
                    outcome="ok",
                    duration_ms=(time.perf_counter() - started) * 1000,
                )

            def failed(
                started: float,
                call_args: tuple[Any, ...],
                call_kwargs: dict[str, Any],
                exc: Exception,
            ) -> None:
                audit.record_tool_call(
                    function.__name__,
                    arguments(call_args, call_kwargs),
                    outcome="error",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    error_type=type(exc).__name__,
                )

            if inspect.iscoroutinefunction(function):

                @functools.wraps(function)
                async def async_wrapper(*call_args: Any, **call_kwargs: Any) -> Any:
                    started = time.perf_counter()
                    try:
                        result = await function(*call_args, **call_kwargs)
                    except Exception as exc:
                        failed(started, call_args, call_kwargs, exc)
                        raise
                    successful(started, call_args, call_kwargs)
                    return result

                return register(async_wrapper)

            @functools.wraps(function)
            def wrapper(*call_args: Any, **call_kwargs: Any) -> Any:
                started = time.perf_counter()
                try:
                    result = function(*call_args, **call_kwargs)
                except Exception as exc:
                    failed(started, call_args, call_kwargs, exc)
                    raise
                successful(started, call_args, call_kwargs)
                return result

            return register(wrapper)

        return decorate

    mcp.tool = audited_tool


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
    audit = AuditLog(root or project_root())

    @mcp.resource(
        PERSONA_URI,
        name="persona",
        description="Identità conversazionale e regole del profilo attivo.",
        mime_type="text/markdown",
    )
    def persona_resource() -> str:
        """Espone la persona del profilo anche ai client che leggono le risorse."""

        return persona

    _instrument_tools(mcp, audit)
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
