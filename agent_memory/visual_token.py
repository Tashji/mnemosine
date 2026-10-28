"""Salvataggio locale e verifica del token Replicate.

Il token non passa mai dagli argomenti della riga di comando, da un file
versionato o dai dati del profilo: viene letto da input nascosto e scritto con
permessi ``0600`` dentro una cartella ``0700``. Senza token, la generazione
resta disattivata.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import project_root
from .errors import ConfigError
from .visual import (
    _ensure_private_dir,
    _valid_token,
    _write_private,
    resolve_token,
    token_path,
)

ACCOUNT_URL = "https://api.replicate.com/v1/account"
ACCOUNT_MAX_BYTES = 64 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise ConfigError("Replicate ha richiesto un redirect inatteso durante la verifica.")


def store_token(root: str | Path, token: str) -> Path:
    """Scrive il token in un file privato e restituisce il percorso."""

    if not _valid_token(token):
        raise ConfigError(
            "Token Replicate non valido: attesi da 8 a 500 caratteri senza spazi."
        )
    path = token_path(root)
    _ensure_private_dir(path.parent)
    _write_private(path, token.encode("utf-8") + b"\n")
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise ConfigError("Impossibile proteggere il file del token: permessi troppo aperti.")
    return path


def remove_token(root: str | Path) -> bool:
    path = token_path(root)
    if path.is_symlink():
        raise ConfigError("Il file del token non può essere un symlink.")
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError as exc:
        raise ConfigError("Impossibile rimuovere il file del token.") from exc
    return True


def check_token(token: str) -> dict[str, Any]:
    """Interroga l'account Replicate senza avviare generazioni a pagamento."""

    request = Request(
        ACCOUNT_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "mnemosine-visual-token",
        },
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=20) as response:
            if response.status != 200:
                return {"valid": False, "status": response.status}
            body = response.read(ACCOUNT_MAX_BYTES)
    except HTTPError as exc:
        return {"valid": False, "status": exc.code}
    except ConfigError:
        raise
    except (URLError, OSError, ValueError) as exc:
        raise ConfigError(
            "Verifica non riuscita: connessione a Replicate non disponibile."
        ) from exc
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        payload = {}
    username = payload.get("username") if isinstance(payload, dict) else None
    return {
        "valid": True,
        "status": 200,
        "username": username if isinstance(username, str) else None,
    }


def _describe(payload: dict[str, Any]) -> str:
    if payload.get("valid"):
        account = payload.get("username")
        suffix = f", account {account}" if account else ""
        return f"Token valido (origine: {payload.get('source')}{suffix})."
    return (
        f"Token rifiutato (HTTP {payload.get('status')}): controlla di aver copiato "
        "il token completo dalla pagina Replicate."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mnemosine-visual-token",
        description=(
            "Salva o verifica il token Replicate in un file locale protetto. "
            "Il token viene letto da input nascosto, mai da argomenti."
        ),
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Directory del profilo (quella con config/ e data/); default: directory del progetto.",
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true", help="Verifica il token senza generare immagini.")
    actions.add_argument("--remove", action="store_true", help="Rimuove il token salvato localmente.")
    parser.add_argument("--json", action="store_true", help="Stampa l'esito in JSON.")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve() if args.root else project_root()

    try:
        if args.remove:
            removed = remove_token(root)
            message = (
                f"Token rimosso da {token_path(root)}."
                if removed
                else "Nessun token salvato: nulla da rimuovere."
            )
            print(json.dumps({"removed": removed}) if args.json else message)
            return 0

        if args.check:
            token, source = resolve_token(root)
            payload = {
                "configured": True,
                "source": source,
                "path": str(token_path(root)),
                **check_token(token),
            }
            print(json.dumps(payload, ensure_ascii=False) if args.json else _describe(payload))
            return 0 if payload.get("valid") else 1

        token = getpass.getpass("Token Replicate (input nascosto): ").strip()
        confirm = getpass.getpass("Ripeti il token: ").strip()
        if token != confirm:
            print("I due inserimenti non coincidono: nessun file scritto.", file=sys.stderr)
            return 1
        path = store_token(root, token)
        print(f"Token salvato in {path} con permessi 0600.")
        print("La dashboard lo legge a ogni richiesta: se è aperta, ricarica la vista Aspetto.")
        return 0
    except ConfigError as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
