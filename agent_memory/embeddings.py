"""Generazione degli embedding per la memoria semantica.

Il modello predefinito è multilingue (italiano incluso) e viene eseguito in
locale tramite ONNX, senza dipendere da un servizio API. Il modello viene
caricato solo al primo utilizzo: il server può partire e rispondere ai tool
non semantici anche prima che il modello sia stato scaricato.

``DeterministicEmbedder`` è un piccolo embedder lessicale usato soltanto nei
test e nelle verifiche offline. Non è un modello semantico e non è mai il
default del sistema.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np

from .errors import EmbeddingUnavailable


DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_DIMENSION = 384
DEFAULT_BATCH_SIZE = 32


def _normalize(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.ndim == 1:
        vectors = vectors.reshape(1, -1)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return vectors / norms


def embedding_text_hash(model_name: str, text: str) -> str:
    """Hash stabile del testo e del modello, per evitare re-embedding inutili."""

    payload = f"{model_name}\n{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class Embedder(Protocol):
    """Contratto minimo di un generatore di embedding."""

    model_name: str

    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[np.ndarray]: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class FastEmbedder:
    """Embedding locale con FastEmbed (ONNX Runtime)."""

    def __init__(
        self,
        model_name: str | None = None,
        *,
        cache_dir: str | Path | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ):
        self.model_name = (
            model_name
            or os.environ.get("AGENT_MEMORY_EMBEDDING_MODEL")
            or DEFAULT_MODEL
        )
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.batch_size = batch_size
        self._model = None
        self._dimension: int | None = None

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._ensure_model()
        assert self._dimension is not None
        return self._dimension

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise EmbeddingUnavailable(
                "FastEmbed non è installato. Esegui `pip install -r requirements.txt`."
            ) from exc

        kwargs: dict[str, object] = {"model_name": self.model_name}
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            kwargs["cache_dir"] = str(self.cache_dir)
        try:
            self._model = TextEmbedding(**kwargs)
            for entry in TextEmbedding.list_supported_models():
                if entry.get("model") == self.model_name:
                    self._dimension = int(entry["dim"])
                    break
            if self._dimension is None:
                probe = list(self._model.query_embed(["dimension probe"]))[0]
                self._dimension = int(np.asarray(probe).shape[0])
        except Exception as exc:  # fastembed/onnx/huggingface errors
            self._model = None
            raise EmbeddingUnavailable(
                f"Impossibile caricare il modello di embedding '{self.model_name}': {exc}"
            ) from exc

    def embed_documents(self, texts: Sequence[str]) -> list[np.ndarray]:
        self._ensure_model()
        assert self._model is not None
        if not texts:
            return []
        try:
            vectors = list(
                self._model.embed(list(texts), batch_size=self.batch_size)
            )
        except Exception as exc:
            raise EmbeddingUnavailable(
                f"Generazione degli embedding fallita: {exc}"
            ) from exc
        normalized = _normalize(np.vstack(vectors))
        if normalized.shape[0] != len(texts):
            raise EmbeddingUnavailable(
                "Il modello di embedding ha restituito un numero di vettori inatteso."
            )
        if self._dimension is None:
            self._dimension = int(normalized.shape[1])
        elif normalized.shape[1] != self._dimension:
            raise EmbeddingUnavailable(
                f"Dimensione embedding inattesa: {normalized.shape[1]} "
                f"(attesa {self._dimension})."
            )
        return [normalized[index] for index in range(normalized.shape[0])]

    def embed_query(self, text: str) -> np.ndarray:
        if not isinstance(text, str) or not text.strip():
            raise EmbeddingUnavailable("La query per l'embedding non può essere vuota.")
        self._ensure_model()
        assert self._model is not None
        try:
            vector = list(self._model.query_embed([text]))[0]
        except Exception as exc:
            raise EmbeddingUnavailable(
                f"Generazione dell'embedding della query fallita: {exc}"
            ) from exc
        return _normalize(np.asarray(vector))[0]


class DeterministicEmbedder:
    """Embedder lessicale deterministico per test e verifiche offline.

    Non è un modello semantico: produce vettori normalizzati a partire dai
    token, con una dimensione fissa. Serve a testare indicizzazione, filtri e
    fusione dei risultati senza scaricare un modello.
    """

    model_name = "deterministic-test-v1"
    _TOKEN_RE = re.compile(r"[0-9a-zA-ZÀ-ÿ_]+", re.UNICODE)

    def __init__(self, dimension: int = 128):
        if dimension <= 0:
            raise ValueError("dimension deve essere positiva.")
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(self._dimension, dtype=np.float32)
        tokens = self._TOKEN_RE.findall(text.lower())
        if not tokens:
            tokens = [text.lower()]
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self._dimension
            vector[index] += 1.0
        return _normalize(vector)[0]

    def embed_documents(self, texts: Sequence[str]) -> list[np.ndarray]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> np.ndarray:
        if not isinstance(text, str) or not text.strip():
            raise EmbeddingUnavailable("La query per l'embedding non può essere vuota.")
        return self._vector(text)
