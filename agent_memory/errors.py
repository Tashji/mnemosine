"""Errori applicativi del server di memoria.

Gli errori di dominio vengono trasformati in ``ToolError`` dal server MCP,
così il client riceve un messaggio comprensibile senza esporre stack trace.
"""

from __future__ import annotations


class AgentMemoryError(Exception):
    """Errore base del sistema di memoria."""


class ConfigError(AgentMemoryError):
    """Configurazione mancante, illeggibile o non coerente."""


class ValidationError(AgentMemoryError):
    """Dati in ingresso non validi."""


class NotFoundError(AgentMemoryError):
    """Risorsa richiesta inesistente o non accessibile."""


class ConflictError(AgentMemoryError):
    """La scrittura entra in conflitto con un record esistente."""


class AccessDeniedError(AgentMemoryError):
    """Accesso negato dalle regole di visibilità della memoria."""


class DatabaseUnavailable(AgentMemoryError):
    """Database SQLite mancante, non inizializzato o illeggibile."""


class EmbeddingUnavailable(AgentMemoryError):
    """Modello di embedding assente, non caricabile o non utilizzabile."""


class VectorIndexError(AgentMemoryError):
    """Indice vettoriale mancante, incoerente o non aggiornabile."""


class BrowserError(AgentMemoryError):
    """Errore del browser controllato."""


class BrowserBusyError(BrowserError):
    """La pagina sta cambiando documento: la lettura va ripetuta."""


class BrowserBlockedError(BrowserError):
    """URL o azione non consentiti dalla whitelist."""


class BrowserUnavailableError(BrowserError):
    """Browser disabilitato o backend non installato."""


class OutboxError(AgentMemoryError):
    """Errore del trasporto dell'outbox verso WhatsApp Web."""


class OutboxBusyError(OutboxError):
    """Un'altra esecuzione del trasporto è già attiva sul profilo."""


class OutboxBlockedError(OutboxError):
    """Il trasporto non può agire: nessun effetto esterno è stato prodotto."""


class OutboxUncertainError(OutboxError):
    """L'azione può aver avuto effetto, ma l'esito non è verificabile."""
