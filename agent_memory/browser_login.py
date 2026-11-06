"""Primo accesso a un sito con browser visibile, per salvare la sessione.

Il login non è un'integrazione per sito: cookie e storage locale vivono in
``data/browser/session.json`` e vengono ricaricati dal browser headless a ogni
apertura. Questo comando apre Chromium con lo schermo, lascia completare
l'accesso all'utente (password, QR o codice a due fattori) e salva il risultato
con permessi ristretti.

Va eseguito sulla macchina dove c'è uno schermo: in un server senza display il
browser visibile non può partire.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import suppress
from pathlib import Path

from .browser import (
    SERVICE_WORKERS_WARNING,
    SESSION_RELATIVE_PATH,
    BrowserPolicy,
    _playwright_available,
    _save_storage_state,
    _storage_state,
)
from .errors import (
    AgentMemoryError,
    BrowserError,
    BrowserUnavailableError,
    ConfigError,
    ValidationError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Apre un browser visibile per il primo accesso a un sito e salva "
            "la sessione usata poi dal browser headless."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory del profilo (default: quella che contiene il pacchetto).",
    )
    parser.add_argument(
        "--origin",
        metavar="ORIGINE",
        default=None,
        help=(
            "Origine da aprire, nella forma https://sito o sito. Deve essere "
            "nella whitelist; il default è la prima origine consentita."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Mostra whitelist e stato della sessione, senza aprire il browser.",
    )
    return parser


def _as_url(value: str) -> str:
    raw = value.strip()
    return raw if "://" in raw else f"https://{raw}"


def _run(policy: BrowserPolicy, origin: str) -> int:
    if not _playwright_available():
        raise BrowserUnavailableError(
            "Playwright non è installato. Esegui "
            "`pip install -r requirements-browser.txt` e "
            "`python -m playwright install chromium`."
        )
    from playwright.sync_api import sync_playwright

    timeout_ms = int(policy.timeout_seconds * 1000)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=False,
                executable_path=policy.executable_path,
            )
            context = browser.new_context(
                accept_downloads=False,
                service_workers=policy.service_worker_mode,
                user_agent=policy.user_agent,
                storage_state=_storage_state(policy),
            )
            try:
                page = context.new_page()
                page.goto(origin, wait_until="domcontentloaded", timeout=timeout_ms)
                print(f"Browser aperto su {origin}.")
                print(
                    "Completa l'accesso nella finestra (password, QR o codice a "
                    "due fattori), poi torna a questo terminale."
                )
                try:
                    input("Premi Invio per salvare la sessione... ")
                except EOFError as exc:
                    raise ValidationError(
                        "Serve un terminale interattivo per confermare il "
                        "salvataggio."
                    ) from exc
                if not _save_storage_state(policy, context):
                    raise BrowserError(
                        "Salvataggio della sessione non riuscito: controlla i "
                        "permessi della cartella data/."
                    )
            finally:
                # La chiusura non è un esito: se fallisce non nasconde l'errore.
                with suppress(Exception):
                    context.close()
                with suppress(Exception):
                    browser.close()
    except AgentMemoryError:
        raise
    except Exception as exc:
        raise BrowserError(
            f"Browser visibile non avviato: {exc}. Questo comando richiede uno "
            "schermo; su un server remoto usa il browser della tua macchina."
        ) from exc

    print(f"Sessione salvata in {SESSION_RELATIVE_PATH} (permessi 600).")
    print(
        "Contiene cookie e token di accesso: non condividerla, non copiarla nel "
        "repository e cancellala quando revochi l'accesso."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        policy = BrowserPolicy.from_root(args.root)
        session_file = policy.session_path
        if args.check:
            print(
                json.dumps(
                    {
                        "enabled": policy.enabled,
                        "allowed_origins": list(policy.allowed_origins),
                        "session_file": str(SESSION_RELATIVE_PATH),
                        "session_saved": bool(
                            session_file is not None and session_file.is_file()
                        ),
                        "user_agent": policy.user_agent,
                        "service_workers": policy.service_worker_mode,
                        "service_workers_warning": (
                            SERVICE_WORKERS_WARNING
                            if policy.service_workers
                            else None
                        ),
                        "playwright_available": _playwright_available(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        policy.require_enabled()
        origins = list(policy.allowed_origins)
        origin = args.origin or (origins[0] if origins else None)
        if not origin:
            raise ConfigError(
                "Nessuna origine consentita: aggiungi un indirizzo a "
                "browser.allowed_origins in config/integrations.json."
            )
        target = _as_url(origin)
        policy.assert_allowed(target)
        if not sys.stdin.isatty():
            raise ValidationError(
                "Serve un terminale interattivo: l'accesso va completato a mano "
                "e confermato con Invio."
            )
        return _run(policy, target)
    except AgentMemoryError as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
