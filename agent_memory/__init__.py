"""Memoria conscia e latente dell'agente.

Il pacchetto è indipendente dal trasporto MCP: ``MemoryService`` e
``ShadowService`` possono essere usati anche da test, dal motore dell'ombra o
dal pannello amministrativo.
"""

from .config import ConfigStore
from .db import Database
from .embeddings import DEFAULT_MODEL, FastEmbedder
from .errors import (
    AccessDeniedError,
    AgentMemoryError,
    BrowserBlockedError,
    BrowserError,
    BrowserUnavailableError,
    ConfigError,
    ConflictError,
    DatabaseUnavailable,
    EmbeddingUnavailable,
    NotFoundError,
    ValidationError,
    VectorIndexError,
)
from .retrieval import RetrievalService
from .semantic import SemanticService
from .service import MemoryService, ShadowService
from .vector_index import VectorIndexManager

__version__ = "0.5.0"

__all__ = [
    "DEFAULT_MODEL",
    "AccessDeniedError",
    "AgentMemoryError",
    "BrowserBlockedError",
    "BrowserError",
    "BrowserUnavailableError",
    "ConfigError",
    "ConfigStore",
    "ConflictError",
    "Database",
    "DatabaseUnavailable",
    "EmbeddingUnavailable",
    "FastEmbedder",
    "MemoryService",
    "NotFoundError",
    "RetrievalService",
    "SemanticService",
    "ShadowService",
    "ValidationError",
    "VectorIndexError",
    "VectorIndexManager",
    "__version__",
]
