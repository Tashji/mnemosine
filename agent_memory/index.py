"""Manutenzione degli indici vettoriali.

Uso:
    python -m agent_memory.index --rebuild
    python -m agent_memory.index --sync
    python -m agent_memory.index --status
    python -m agent_memory.index --check

Il primo comando scarica il modello di embedding (la prima volta) e ricostruisce
entrambi gli indici dai testi in SQLite. ``--sync`` aggiorna solo i record nuovi,
modificati o rimossi. ``--status`` e ``--check`` non caricano il modello.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import project_root
from .embeddings import FastEmbedder
from .errors import AgentMemoryError
from .retrieval import RetrievalService
from .vector_index import VectorIndexManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sincronizza o verifica gli indici vettoriali della memoria."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory del progetto (default: quella che contiene il pacchetto).",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Elimina i mapping e ricostruisce gli indici da zero.",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Aggiorna i record nuovi, modificati o rimossi (default).",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Mostra lo stato degli indici senza caricare il modello.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verifica che ogni mapping abbia un vettore ricostruibile.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Nome del modello FastEmbed (default: variabile "
            "AGENT_MEMORY_EMBEDDING_MODEL o MiniLM multilingue)."
        ),
    )
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=None,
        help="Directory dei file FAISS (default: data/indexes).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.rebuild and args.sync:
        parser.error("--rebuild e --sync sono alternativi.")

    try:
        root = (
            Path(args.root).expanduser().resolve()
            if args.root is not None
            else project_root()
        )
        embedder = FastEmbedder(
            model_name=args.model,
            cache_dir=root / "data" / "models",
        )
        retrieval = RetrievalService(
            root,
            embedder=embedder,
            index=VectorIndexManager(
                root,
                embedder=embedder,
                index_dir=args.index_dir,
            ),
        )

        if args.status:
            status = retrieval.index.status()
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0 if status.get("ok") else 1

        if args.check:
            status = retrieval.index.check()
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0 if status.get("ok") else 1

        result = retrieval.sync_index(rebuild=bool(args.rebuild))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    except AgentMemoryError as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
