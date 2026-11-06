"""Tool MCP dello stato corrente, delle interazioni e dell'heartbeat."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .errors import AgentMemoryError
from .executor import MessageExecutor
from .heartbeat import HeartbeatEngine
from .service import MemoryService


def register_state_tools(
    mcp: FastMCP, *, root: str | Path | None, service: MemoryService
) -> None:
    @mcp.tool()
    def get_current_state() -> dict[str, Any]:
        """Restituisce l'attività, l'emozione, il carico cognitivo e la routine correnti."""

        try:
            return service.get_current_state()
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def update_current_state(patch: dict[str, Any]) -> dict[str, Any]:
        """Aggiorna parzialmente lo stato corrente e restituisce il documento risultante.

        Il patch è un oggetto JSON: le chiavi annidate vengono unite, mentre i
        valori scalari e le liste sostituiscono il valore precedente. I campi di
        sicurezza dell'ombra non sono modificabili.
        """

        try:
            return service.update_current_state(patch)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def record_interaction(
        messages: int = 1,
        complexity: str = "normale",
        user_id: str | None = None,
        summary: str = "",
        channel: str = "chat",
        sentiment: str | None = None,
        memory_id: str | None = None,
        interaction_id: str | None = None,
    ) -> dict[str, Any]:
        """Registra i messaggi appena scambiati e aggiorna il carico cognitivo.

        ``complexity`` vale ``leggera``, ``normale`` o ``complessa``. Il carico
        risultante decide lo stile di risposta in ``availability``: oltre la
        soglia morbida diventa più asciutto, oltre quella dura ancora di più.
        Se indichi ``user_id``, l'interazione aggiorna anche ``last_interaction_at``,
        ``interaction_count`` e lo storico nel grafo relazionale con ``summary``,
        ``channel``, ``sentiment`` e ``memory_id``; un id sconosciuto viene rifiutato.
        Se presente, ``response_guidance`` è un suggerimento prudente per il turno,
        non un'autorizzazione a compiere azioni o divulgare informazioni private.
        """

        try:
            return HeartbeatEngine(root, at=None).record_interaction(
                messages=messages,
                complexity=complexity,
                user_id=user_id,
                summary=summary,
                channel=channel,
                sentiment=sentiment,
                memory_id=memory_id,
                interaction_id=interaction_id,
            )
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def interaction_history(user_id: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        """Legge una pagina SQL dello storico, senza espandere tutto il grafo."""
        try:
            service.config.load_all()
            return service.config.operational.history(user_id, offset=offset, limit=limit)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def acknowledge_impulse(note: str = "") -> dict[str, Any]:
        """Segna come gestito l'impulso spontaneo in attesa di risposta.

        L'Heartbeat mette in coda un impulso, non un messaggio: dopo averlo
        letto o inviato, questo tool chiude la voce e registra una nota.
        """

        try:
            return HeartbeatEngine(root, at=None).acknowledge_impulse(note=note)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool()
    def deliver_impulse(text: str) -> dict[str, Any]:
        """Scrive al destinatario il messaggio dell'impulso in attesa.

        Il testo è l'unica parte che decidi tu: destinatario, canale e
        momento vengono dall'impulso e dalle regole del profilo. La consegna
        riesce solo se l'integrazione è abilitata, il destinatario è in
        whitelist, il flag di invio è attivo e nessun freno (sonno,
        manutenzione, silenzio, limite giornaliero) è in corso. Il messaggio
        finisce nella coda di consegna del profilo e l'impulso viene chiuso:
        il trasporto verso WhatsApp Web resta esterno a questo processo.
        """

        try:
            return MessageExecutor(root, at=None).deliver(text)
        except AgentMemoryError as exc:
            raise ToolError(str(exc)) from exc
