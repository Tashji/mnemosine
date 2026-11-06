"""Primo avvio guidato: apre il wizard di creazione del profilo.

Chi scarica il progetto non deve conoscere le opzioni della dashboard né la
struttura delle directory: ``mnemosine-start`` sceglie un profilo per utente,
avvia la dashboard in locale e apre il browser sulla pagina di configurazione.
Se il profilo esiste già, lo stesso comando riapre la dashboard.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .dashboard import main as dashboard_main
from .profile import PROFILE_STATE_READY, profile_state

PROFILE_ENV = "AGENT_MEMORY_PROFILE"
DEFAULT_PORT = 8765


def default_profile_root() -> Path:
    """Directory del profilo usata quando non ne viene indicata una.

    Sta fuori dal repository, così un clone nuovo non contiene mai un profilo
    già pronto e il primo avvio porta sempre alla configurazione.
    """

    override = os.environ.get(PROFILE_ENV)
    if override and override.strip():
        return Path(override).expanduser()
    return Path.home() / ".mnemosine" / "profile"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mnemosine-start",
        description=(
            "Primo avvio di Mnemosine: crea il profilo con il wizard nel "
            "browser e poi riapre la dashboard."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            "Directory del profilo da creare o aprire "
            f"(default: ~/.mnemosine/profile, oppure ${PROFILE_ENV})."
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Indirizzo di ascolto (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Porta della dashboard (default: {DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Non aprire il browser: stampa solo l'indirizzo da visitare.",
    )
    parser.add_argument(
        "--auth",
        action="store_true",
        help="Richiede la password anche in locale.",
    )
    parser.add_argument(
        "--https",
        action="store_true",
        help="Abilita HTTPS con un certificato self-signed dedicato.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = (args.root or default_profile_root()).expanduser().resolve()
    state = profile_state(root)

    if state == PROFILE_STATE_READY:
        print(f"Profilo già configurato: {root}", flush=True)
        print("Apro la dashboard.", flush=True)
    else:
        print("Prima configurazione di Mnemosine", flush=True)
        print(f"Il profilo verrà creato in: {root}", flush=True)
        print(
            "Il wizard si apre nel browser sulla pagina di configurazione.",
            flush=True,
        )
        if args.no_open:
            print(
                f"Apri manualmente: http://{args.host}:{args.port}/setup",
                flush=True,
            )

    forwarded = [
        "--root",
        str(root),
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    if not args.no_open:
        forwarded.append("--open")
    if args.auth:
        forwarded.append("--auth")
    if args.https:
        forwarded.append("--https")
    return dashboard_main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
