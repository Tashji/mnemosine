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
