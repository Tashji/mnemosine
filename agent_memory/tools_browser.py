"""Tool MCP del browser controllato."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .browser import BrowserController
from .errors import AgentMemoryError


def register_browser_tools(mcp: FastMCP, *, browser: BrowserController) -> None:
    @mcp.tool()
    def browser_status() -> dict[str, Any]:
        """Restituisce stato, whitelist e pagina corrente del browser controllato."""

        return browser.status()

    @mcp.tool()
    def browser_open(url: str, max_chars: int = 6000) -> dict[str, Any]:
        """Apre un URL HTTPS consentito e restituisce titolo e testo della pagina.

        Il browser applica la whitelist di ``config/integrations.json``; se il
        browser è disabilitato o l'URL non è autorizzato, il tool restituisce
        un errore senza aprire la pagina.
        """

        try:
            return browser.open(url, max_chars=max_chars)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def browser_read(max_chars: int = 6000) -> dict[str, Any]:
        """Legge la pagina corrente del browser controllato."""

        try:
            return browser.snapshot(max_chars=max_chars)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def browser_links(limit: int = 50) -> dict[str, Any]:
        """Elenca i link della pagina corrente come indice numerato.

        Contiene solo i link che la whitelist autorizza. Usalo al posto dei
        selettori CSS: poi apri un link con ``browser_open_link``.
        """

        try:
            return browser.links(limit=limit)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def browser_open_link(index: int, max_chars: int = 6000) -> dict[str, Any]:
        """Apre il link numero ``index`` restituito dall'ultimo ``browser_links``."""

        try:
            return browser.open_link(index, max_chars=max_chars)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def browser_back(max_chars: int = 6000) -> dict[str, Any]:
        """Torna alla pagina precedente della sessione e la restituisce."""

        try:
            return browser.back(max_chars=max_chars)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def browser_elements(limit: int = 40) -> dict[str, Any]:
        """Elenca gli elementi interattivi della pagina come indice numerato.

        Restituisce campi, aree di scrittura e bottoni visibili con indice,
        ruolo ed etichetta. Usa quell'indice con ``browser_click``,
        ``browser_fill`` e ``browser_press``: nessun selettore da inventare e
        nessuna configurazione per sito.
        """

        try:
            return browser.elements(limit=limit)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def browser_click(
        selector: str | None = None, index: int | None = None
    ) -> dict[str, Any]:
        """Clicca un elemento per selettore o per indice di ``browser_elements``."""

        try:
            return browser.click(selector, index=index)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def browser_fill(
        selector: str | None = None,
        value: str = "",
        index: int | None = None,
    ) -> dict[str, Any]:
        """Scrive in un campo scelto per selettore o per indice."""

        try:
            return browser.fill(selector, value, index=index)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def browser_press(
        key: str,
        selector: str | None = None,
        index: int | None = None,
    ) -> dict[str, Any]:
        """Preme un tasto di controllo (Enter, Tab, Escape, frecce).

        Senza selettore e senza indice il tasto va alla pagina; l'Invio dopo un
        ``browser_fill`` è il modo più comune per inviare un messaggio.
        """

        try:
            return browser.press(key, selector=selector, index=index)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def browser_wait(
        selector: str | None = None,
        text: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Attende che un elemento compaia o che un testo sia nella pagina."""

        try:
            return browser.wait(
                selector=selector, text=text, timeout_seconds=timeout_seconds
            )
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def browser_close() -> dict[str, Any]:
        """Chiude il browser controllato e restituisce lo stato aggiornato."""

        browser.close()
        return browser.status()
