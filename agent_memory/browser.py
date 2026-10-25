"""Browser controllato con whitelist di origini HTTPS.

La policy e il backend sono separati:

- :class:`BrowserPolicy` legge ``config/integrations.json`` e decide quali URL
  possono essere aperti o richiesti dalla pagina.
- :class:`PlaywrightBackend` usa Playwright solo quando il browser viene
  realmente aperto; il pacchetto resta quindi opzionale.
- :class:`BrowserController` coordina navigazione, lettura e azioni, senza
  esporre direttamente il browser al modello.

Il browser è disabilitato per impostazione predefinita. Per abilitarlo, crea
``config/integrations.json`` con una sezione ``browser`` e almeno un'origine
HTTPS nella whitelist.
"""

from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import os
import socket
import sys
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from .config import project_root
from .errors import (
    AgentMemoryError,
    BrowserBlockedError,
    BrowserError,
    BrowserUnavailableError,
    ConfigError,
    ValidationError,
)

INTEGRATIONS_FILENAME = "integrations.json"
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TEXT_CHARS = 50_000
MAX_SELECTOR_CHARS = 500
MAX_FILL_CHARS = 4_000
_LOCAL_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".home",
    ".lan",
)


def _normalize_host(host: str) -> str:
    if not host:
        raise ValueError("host vuoto")
    value = host.strip().rstrip(".").lower()
    try:
        ipaddress.ip_address(value)
    except ValueError:
        try:
            value = value.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("host non valido") from exc
    return value


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _default_resolver(host: str) -> Iterable[str]:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return ()
    return [info[4][0] for info in infos]


def _is_local_host(host: str) -> bool:
    if host in {"localhost", "localhost.localdomain"}:
        return True
    if any(host.endswith(suffix) for suffix in _LOCAL_SUFFIXES):
        return True
    return "." not in host and not _is_ip(host)


@dataclass(frozen=True)
class OriginRule:
    """Un'origine consentita, esatta oppure con sottodomini espliciti."""

    host: str
    wildcard: bool
    source: str

    @classmethod
    def parse(cls, value: str) -> OriginRule:
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(
                "browser.allowed_origins: ogni voce deve essere una stringa non vuota."
            )
        raw = value.strip()
        candidate = raw if "://" in raw else f"https://{raw}"
        parsed = urlsplit(candidate)
        if parsed.scheme.lower() != "https":
            raise ConfigError(
                f"browser.allowed_origins: '{raw}' deve usare HTTPS."
            )
        if parsed.username or parsed.password:
            raise ConfigError(
                f"browser.allowed_origins: '{raw}' non può contenere credenziali."
            )
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ConfigError(
                f"browser.allowed_origins: '{raw}' deve essere un'origine, "
                "senza percorso, query o frammento."
            )
        try:
            port = parsed.port
        except ValueError as exc:
            raise ConfigError(
                f"browser.allowed_origins: porta non valida in '{raw}'."
            ) from exc
        if port not in (None, 443):
            raise ConfigError(
                f"browser.allowed_origins: '{raw}' deve usare la porta 443."
            )
        raw_host = parsed.hostname or ""
        wildcard = raw_host.startswith("*.")
        host = raw_host[2:] if wildcard else raw_host
        if not host or "*" in host or "?" in host:
            raise ConfigError(
                f"browser.allowed_origins: '{raw}' non è un dominio valido."
            )
        try:
            normalized = _normalize_host(host)
        except ValueError as exc:
            raise ConfigError(
                f"browser.allowed_origins: '{raw}' non è un dominio valido."
            ) from exc
        if _is_local_host(normalized):
            raise ConfigError(
                f"browser.allowed_origins: '{raw}' non può puntare a una "
                "destinazione locale."
            )
        return cls(host=normalized, wildcard=wildcard, source=raw)

    def matches(self, host: str) -> bool:
        if self.wildcard:
            return host.endswith(f".{self.host}")
        return host == self.host


class BrowserPolicy:
    """Whitelist e regole di sicurezza per il browser."""

    def __init__(
        self,
        allowed_origins: Iterable[str] = (),
        *,
        enabled: bool = False,
        interaction_enabled: bool = False,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        executable_path: str | None = None,
        resolver: Callable[[str], Iterable[str]] | None = None,
    ) -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ):
            raise ConfigError(
                "browser.navigation_timeout_seconds: atteso un numero."
            )
        if not 1 <= float(timeout_seconds) <= 120:
            raise ConfigError(
                "browser.navigation_timeout_seconds: valore fuori "
                "intervallo [1, 120]."
            )
        self.enabled = bool(enabled)
        self.interaction_enabled = bool(interaction_enabled)
        self.timeout_seconds = float(timeout_seconds)
        self.executable_path = (
            executable_path
            or os.environ.get("AGENT_MEMORY_BROWSER_EXECUTABLE")
            or None
        )
        self._rules = tuple(OriginRule.parse(origin) for origin in allowed_origins)
        self._resolver = resolver or _default_resolver
        self._resolved: dict[str, tuple[str, ...]] = {}

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        return tuple(rule.source for rule in self._rules)

    @classmethod
    def from_root(cls, root: str | Path | None = None) -> BrowserPolicy:
        """Carica la policy; un file assente significa browser disabilitato."""

        base = Path(root).expanduser().resolve() if root else project_root()
        path = base / "config" / INTEGRATIONS_FILENAME
        if not path.exists():
            return cls()
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"JSON non valido in {path}: riga {exc.lineno}, "
                f"colonna {exc.colno} ({exc.msg})"
            ) from exc
        except OSError as exc:
            raise ConfigError(f"Impossibile leggere {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: atteso un oggetto JSON.")
        browser = data.get("browser", {})
        if not isinstance(browser, dict):
            raise ConfigError(f"{path}: 'browser' deve essere un oggetto.")
        enabled = browser.get("enabled", False)
        interaction_enabled = browser.get("interaction_enabled", False)
        if not isinstance(enabled, bool):
            raise ConfigError(f"{path}: browser.enabled deve essere booleano.")
        if not isinstance(interaction_enabled, bool):
            raise ConfigError(
                f"{path}: browser.interaction_enabled deve essere booleano."
            )
        origins = browser.get("allowed_origins", [])
        if not isinstance(origins, list):
            raise ConfigError(
                f"{path}: browser.allowed_origins deve essere una lista."
            )
        timeout = browser.get(
            "navigation_timeout_seconds", DEFAULT_TIMEOUT_SECONDS
        )
        executable_path = browser.get("executable_path")
        if executable_path is not None and not isinstance(executable_path, str):
            raise ConfigError(
                f"{path}: browser.executable_path deve essere una stringa."
            )
        return cls(
            origins,
            enabled=enabled,
            interaction_enabled=interaction_enabled,
            timeout_seconds=timeout,
            executable_path=executable_path or None,
        )

    def _resolve(self, host: str) -> tuple[str, ...]:
        if host in self._resolved:
            return self._resolved[host]
        try:
            values = self._resolver(host)
        except OSError:
            values = ()
        addresses = tuple(sorted({str(value) for value in values}))
        self._resolved[host] = addresses
        return addresses

    def is_allowed(self, url: str) -> bool:
        if not self.enabled:
            return False
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return False
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or port not in (None, 443)
        ):
            return False
        try:
            host = _normalize_host(parsed.hostname)
        except ValueError:
            return False
        if _is_local_host(host):
            return False
        if not any(rule.matches(host) for rule in self._rules):
            return False
        if _is_ip(host):
            address = ipaddress.ip_address(host)
            return bool(address.is_global)
        addresses = self._resolve(host)
        if not addresses:
            return False
        for address_text in addresses:
            try:
                address = ipaddress.ip_address(address_text)
            except ValueError:
                return False
            if not address.is_global:
                return False
        return True

    def assert_allowed(self, url: str) -> str:
        if not self.enabled:
            raise BrowserUnavailableError(
                "Browser disabilitato: abilita browser.enabled e configura "
                "la whitelist in config/integrations.json."
            )
        if not self.is_allowed(url):
            raise BrowserBlockedError(
                "URL non consentito dalla whitelist del browser."
            )
        return url

    def is_allowed_websocket(self, url: str) -> bool:
        """Consente solo ``wss://`` verso un host già presente in whitelist."""

        if not isinstance(url, str):
            return False
        value = url.strip()
        if not value.lower().startswith("wss://"):
            return False
        return self.is_allowed(f"https://{value[len('wss://') :]}")

    def assert_interaction_allowed(self, url: str) -> str:
        if not self.interaction_enabled:
            raise BrowserBlockedError(
                "Interazione browser disabilitata: imposta "
                "browser.interaction_enabled solo dopo aver configurato "
                "la whitelist."
            )
        self.assert_allowed(url)
        return url


class BrowserBackend(Protocol):
    """Interfaccia minima usata dal controller."""

    url: str

    def open(self, url: str) -> None: ...

    def title(self) -> str: ...

    def text(self) -> str: ...

    def click(self, selector: str) -> None: ...

    def fill(self, selector: str, value: str) -> None: ...

    def screenshot(self, path: Path) -> None: ...

    def close(self) -> None: ...


def _playwright_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


class PlaywrightBackend:
    """Backend Playwright caricato solo alla prima apertura."""

    def __init__(self, policy: BrowserPolicy):
        if not _playwright_available():
            raise BrowserUnavailableError(
                "Playwright non è installato. Esegui "
                "`pip install -r requirements-browser.txt` e "
                "`python -m playwright install chromium`."
            )
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - difesa dal runtime
            raise BrowserUnavailableError(
                "Playwright non è disponibile."
            ) from exc

        self.policy = policy
        self.url = ""
        self._timeout_ms = int(policy.timeout_seconds * 1000)
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=True,
                executable_path=policy.executable_path,
            )
            self._context = self._browser.new_context(
                accept_downloads=False,
                ignore_https_errors=False,
                service_workers="block",
            )
            self._context.set_default_timeout(self._timeout_ms)
            self._context.route("**/*", self._handle_route)
            route_websocket = getattr(self._context, "route_web_socket", None)
            if callable(route_websocket):
                route_websocket("**/*", self._handle_websocket)
            self._context.on("page", self._handle_page)
            self._page = self._context.new_page()
        except AgentMemoryError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise BrowserError(
                f"Impossibile avviare il browser: {exc}"
            ) from exc

    def _handle_route(self, route: Any) -> None:
        request_url = str(route.request.url)
        # Le risorse inline non lasciano il browser e non cambiano origine.
        if request_url.startswith(("data:", "blob:")):
            route.continue_()
            return
        if self.policy.is_allowed(request_url):
            route.continue_()
        else:
            route.abort()

    def _handle_websocket(self, websocket: Any) -> None:
        # ``context.route`` non copre i WebSocket: senza questo controllo una
        # pagina potrebbe aprire un canale verso un server fuori whitelist.
        try:
            allowed = self.policy.is_allowed_websocket(str(websocket.url))
        except Exception:  # noqa: BLE001 - in dubbio si chiude il canale
            allowed = False
        with suppress(Exception):
            if allowed:
                websocket.connect_to_server()
            else:
                websocket.close()

    def _handle_page(self, page: Any) -> None:
        # Le finestre aperte da script o link esterni non devono creare
        # sessioni fuori dal controllo del controller.
        if self._page is not None and page is not self._page:
            with suppress(Exception):
                page.close()

    def _require_page(self) -> Any:
        if self._page is None:
            raise BrowserUnavailableError("Nessuna pagina browser aperta.")
        return self._page

    def open(self, url: str) -> None:
        page = self._require_page()
        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self._timeout_ms,
            )
        except Exception as exc:
            raise BrowserError(f"Navigazione non riuscita: {exc}") from exc
        self.url = page.url

    def title(self) -> str:
        page = self._require_page()
        try:
            return str(page.title())
        except Exception as exc:
            raise BrowserError(f"Titolo non leggibile: {exc}") from exc

    def text(self) -> str:
        page = self._require_page()
        try:
            return str(page.inner_text("body"))
        except Exception:  # noqa: BLE001 - pagina senza body leggibile
            return ""

    def click(self, selector: str) -> None:
        page = self._require_page()
        try:
            page.click(selector, timeout=self._timeout_ms)
        except Exception as exc:
            raise BrowserError(f"Click non riuscito: {exc}") from exc
        self.url = page.url

    def fill(self, selector: str, value: str) -> None:
        page = self._require_page()
        try:
            page.fill(selector, value, timeout=self._timeout_ms)
        except Exception as exc:
            raise BrowserError(f"Compilazione non riuscita: {exc}") from exc
        self.url = page.url

    def screenshot(self, path: Path) -> None:
        page = self._require_page()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(path), full_page=True)
        except Exception as exc:
            raise BrowserError(f"Screenshot non riuscito: {exc}") from exc

    def close(self) -> None:
        for closer in (
            getattr(self, "_context", None),
            getattr(self, "_browser", None),
        ):
            if closer is None:
                continue
            with suppress(Exception):
                closer.close()
        playwright = getattr(self, "_playwright", None)
        if playwright is not None:
            with suppress(Exception):
                playwright.stop()
        self._context = None
        self._browser = None
        self._playwright = None
        self._page = None
        self.url = ""


class BrowserController:
    """Coordina policy, backend e operazioni esposte al server MCP."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        policy: BrowserPolicy | None = None,
        backend: BrowserBackend | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve() if root else project_root()
        self.policy = policy or BrowserPolicy.from_root(self.root)
        self._backend = backend

    @property
    def current_url(self) -> str | None:
        if self._backend is None or not self._backend.url:
            return None
        return self._backend.url

    def _ensure_backend(self) -> BrowserBackend:
        if self._backend is None:
            self._backend = PlaywrightBackend(self.policy)
        return self._backend

    @staticmethod
    def _max_chars(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError("max_chars deve essere un intero.")
        if not 1 <= value <= MAX_TEXT_CHARS:
            raise ValidationError(
                f"max_chars deve essere tra 1 e {MAX_TEXT_CHARS}."
            )
        return value

    @staticmethod
    def _selector(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError("Il selettore non può essere vuoto.")
        if len(value) > MAX_SELECTOR_CHARS:
            raise ValidationError(
                f"Il selettore supera {MAX_SELECTOR_CHARS} caratteri."
            )
        return value

    def open(self, url: str, *, max_chars: int = 6_000) -> dict[str, Any]:
        max_chars = self._max_chars(max_chars)
        self.policy.assert_allowed(url)
        backend = self._ensure_backend()
        backend.open(url)
        self.policy.assert_allowed(backend.url)
        return self.snapshot(max_chars=max_chars)

    def snapshot(self, *, max_chars: int = 6_000) -> dict[str, Any]:
        max_chars = self._max_chars(max_chars)
        backend = self._backend
        if backend is None or not backend.url:
            raise BrowserUnavailableError(
                "Nessuna pagina aperta: usa prima browser_open."
            )
        self.policy.assert_allowed(backend.url)
        full_text = backend.text()
        truncated = len(full_text) > max_chars
        return {
            "url": backend.url,
            "title": backend.title(),
            "text": full_text[:max_chars],
            "truncated": truncated,
        }

    def click(self, selector: str) -> dict[str, Any]:
        backend = self._backend
        if backend is None or not backend.url:
            raise BrowserUnavailableError(
                "Nessuna pagina aperta: usa prima browser_open."
            )
        selector = self._selector(selector)
        self.policy.assert_interaction_allowed(backend.url)
        backend.click(selector)
        self.policy.assert_allowed(backend.url)
        return self._page_info()

    def fill(self, selector: str, value: str) -> dict[str, Any]:
        backend = self._backend
        if backend is None or not backend.url:
            raise BrowserUnavailableError(
                "Nessuna pagina aperta: usa prima browser_open."
            )
        selector = self._selector(selector)
        if not isinstance(value, str):
            raise ValidationError("Il valore da inserire deve essere testo.")
        if len(value) > MAX_FILL_CHARS:
            raise ValidationError(
                f"Il valore supera {MAX_FILL_CHARS} caratteri."
            )
        self.policy.assert_interaction_allowed(backend.url)
        backend.fill(selector, value)
        self.policy.assert_allowed(backend.url)
        return self._page_info()

    def screenshot(self, path: str | Path) -> str:
        backend = self._backend
        if backend is None or not backend.url:
            raise BrowserUnavailableError(
                "Nessuna pagina aperta: usa prima browser_open."
            )
        self.policy.assert_allowed(backend.url)
        target = Path(path).expanduser()
        backend.screenshot(target)
        return str(target)

    def _page_info(self) -> dict[str, Any]:
        backend = self._backend
        if backend is None:
            raise BrowserUnavailableError("Nessun browser aperto.")
        return {
            "url": backend.url,
            "title": backend.title(),
        }

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.policy.enabled,
            "interaction_enabled": self.policy.interaction_enabled,
            "allowed_origins": list(self.policy.allowed_origins),
            "navigation_timeout_seconds": self.policy.timeout_seconds,
            "playwright_available": _playwright_available(),
            "current_url": self.current_url,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Browser controllato da whitelist per Mnemosine."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory del progetto (default: quella che contiene il pacchetto).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Mostra la policy e lo stato del backend, senza aprire il browser.",
    )
    parser.add_argument(
        "--open",
        metavar="URL",
        help="Apre un URL consentito e stampa il contenuto della pagina.",
    )
    parser.add_argument(
        "--text",
        action="store_true",
        help="Con --open, stampa solo il testo della pagina.",
    )
    parser.add_argument(
        "--screenshot",
        type=Path,
        metavar="FILE",
        help="Con --open, salva uno screenshot completo della pagina.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.check and not args.open:
        parser.error("indica --check oppure --open URL")
    controller: BrowserController | None = None
    try:
        controller = BrowserController(args.root)
        if args.check:
            print(json.dumps(controller.status(), ensure_ascii=False, indent=2))
            return 0
        result = controller.open(args.open)
        if args.text:
            print(result["text"])
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.screenshot:
            print(controller.screenshot(args.screenshot))
        return 0
    except AgentMemoryError as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1
    finally:
        if controller is not None:
            controller.close()


if __name__ == "__main__":
    raise SystemExit(main())
