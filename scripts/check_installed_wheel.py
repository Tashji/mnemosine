"""Smoke test del wheel: eseguire con il Python isolato del venv installato.

Uso: cd <directory esterna ai sorgenti> && <venv>/bin/python -I \
    /percorso/repository/scripts/check_installed_wheel.py
Nessun import dai test, profilo demo, modello o account esterno.
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

import agent_memory

ASSETS = (
    "index.html", "login.html", "setup.html", "setup_complete.html",
    "app.js", "setup.js", "setup_complete.js", "styles.css", "foreign-geo.json",
)
SCHEMAS = (
    "timeline_episodic.sql", "shadow_diary.sql", "semantic_memory.sql", "vector_index.sql",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def command(name: str) -> str:
    path = Path(sys.executable).parent / name
    require(path.is_file(), f"Entry point assente nel venv: {path}")
    return str(path)


async def handshake(root: Path) -> None:
    params = StdioServerParameters(
        command=command("mnemosine"), args=["--root", str(root)],
    )
    async with asyncio.timeout(60):
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            info = await session.initialize()
            require(info.serverInfo.name == "agent-memory", "Nome server MCP inatteso")
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            required = {
                "get_identity", "get_status", "interaction_history", "browser_elements",
                "browser_press", "browser_wait", "autonomous_claim", "generate_image",
            }
            require(required <= names, f"Tool mancanti: {sorted(required - names)}")
            require(len(names) == len(tools.tools), "Tool MCP duplicati")
            identity = await session.call_tool("get_identity", {})
            require(not identity.isError, f"get_identity fallito: {identity.content}")
            payload = json.loads(identity.content[0].text)
            require(payload["personal_data"]["full_name"] == "Prova Utente", "Profilo MCP errato")
            status = await session.call_tool("get_status", {})
            require(not status.isError, f"get_status fallito: {status.content}")
            require(json.loads(status.content[0].text)["ok"], "Stato MCP non valido")
            print(f"Handshake MCP OK: {len(names)} tool, identità e stato validi", flush=True)


def main() -> None:
    source = Path(__file__).resolve().parents[1]
    package = Path(agent_memory.__file__).resolve().parent
    require(sys.prefix != sys.base_prefix, "Eseguire in un venv separato")
    require(sys.flags.isolated == 1, "Eseguire Python con -I")
    require(Path.cwd().resolve() != source and not (Path.cwd() / "agent_memory").exists(),
            "CWD non deve contenere i sorgenti del pacchetto")
    require(package.is_relative_to(Path(sys.prefix).resolve()), f"Import esterno al venv: {package}")
    distribution = importlib.metadata.distribution("mnemosine")
    require(distribution.version == agent_memory.__version__, "Versione wheel e modulo divergenti")
    direct_url = distribution.read_text("direct_url.json")
    require(not direct_url or not json.loads(direct_url).get("dir_info", {}).get("editable"),
            "Installazione editable non ammessa")
    for directory, names in (("dashboard_assets", ASSETS), ("schemas", SCHEMAS)):
        for name in names:
            path = package / directory / name
            require(path.is_file() and path.stat().st_size > 0, f"Asset assente o vuoto: {path}")
    print(f"Wheel {distribution.version}: asset e schemi OK ({package})", flush=True)
    with tempfile.TemporaryDirectory(prefix="wheel-profile-", dir=Path.cwd()) as tmp:
        root = Path(tmp) / "profile"
        subprocess.run([
            command("mnemosine-profile"), "init", "--root", str(root),
            "--first-name", "Prova", "--last-name", "Utente", "--birth-date", "1990-01-01",
            "--birth-city", "Roma", "--birth-province", "RM", "--gender", "femmina",
        ], check=True, timeout=60)
        result = subprocess.run([
            command("mnemosine"), "--root", str(root), "--check",
        ], check=True, capture_output=True, text=True, timeout=60)
        require(json.loads(result.stdout)["ok"], "Controllo del profilo installato fallito")
        for schema in SCHEMAS:
            require((root / "data" / Path(schema).with_suffix(".db")).is_file(),
                    f"Database non creato: {schema}")
        asyncio.run(handshake(root))


if __name__ == "__main__":
    main()
