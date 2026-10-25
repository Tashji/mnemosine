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

Il login non è un'integrazione per sito: cookie e storage locale vivono in
``data/browser/session.json`` e vengono ricaricati a ogni apertura, così una
sessione autenticata vale per qualunque origine in whitelist. Il comando
``mnemosine-browser-login`` apre il browser con lo schermo per il primo accesso.
"""

from __future__ import annotations

import argparse
import importlib.util
import ipaddress
import json
import os
import re
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
MAX_HISTORY = 50
MAX_LINKS = 100
MAX_LINK_TEXT = 120
MAX_ELEMENTS = 60
MAX_ELEMENT_LABEL = 120
MAX_ELEMENT_VALUE = 60
MAX_WAIT_TEXT = 300
SESSION_RELATIVE_PATH = Path("data") / "browser" / "session.json"

# Elementi con cui si può interagire: campi, aree di scrittura, menu e bottoni.
_ELEMENTS_SELECTOR = (
    "input, textarea, select, button, "
    "[contenteditable='true'], [contenteditable=''], "
    "[role='textbox'], [role='searchbox'], [role='button'], [role='link']"
)
_ELEMENTS_JS = (
    r"""
(el) => {
  const attr = (name) => (el.getAttribute(name) || '').trim();
  const tag = el.tagName.toLowerCase();
  const raw = attr('aria-label') || attr('placeholder') || attr('title')
    || (el.innerText || '').trim() || attr('name') || attr('id');
  return {
    tag: tag,
    role: attr('role') || (tag === 'button' ? 'button' : (tag === 'a' ? 'link' : '')),
    type: attr('type'),
    label: raw.replace(/\s+/g, ' ').slice(0, MAX_LABEL),
    value: (el.isContentEditable ? '' : (el.value || '')).toString().slice(0, MAX_VALUE),
    multiline: tag === 'textarea' || Boolean(el.isContentEditable),
  };
}
""".replace("MAX_LABEL", str(MAX_ELEMENT_LABEL)).replace(
        "MAX_VALUE", str(MAX_ELEMENT_VALUE)
    )
)
_TEXT_PRESENT_JS = (
    "t => !!(document.body && "
    "document.body.innerText.toLowerCase().includes(t))"
)
# Solo tasti di controllo: il testo si scrive con ``fill``, non con ``press``.
_KEY_PATTERN = re.compile(
    r"^(?:(?:Control|Shift|Alt|Meta)\+)*"
    r"(?:Enter|Tab|Escape|Backspace|Delete|Space|ArrowUp|ArrowDown|"
    r"ArrowLeft|ArrowRight|PageUp|PageDown|Home|End)$"
)
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
        session_path: Path | None = None,
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
        self.session_path = (
            Path(session_path).expanduser() if session_path is not None else None
        )

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
            session_path=base / SESSION_RELATIVE_PATH,
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

    def require_enabled(self) -> None:
        """Errore esplicito quando il browser non è abilitato nella policy."""

        if not self.enabled:
            raise BrowserUnavailableError(
                "Browser disabilitato: abilita browser.enabled e configura "
                "la whitelist in config/integrations.json."
            )

    def assert_allowed(self, url: str) -> str:
        self.require_enabled()
        if not self.is_allowed(url):
            raise BrowserBlockedError(
                "URL non consentito dalla whitelist del browser. "
                f"{self.allowed_origins_hint()}"
            )
        return url

    def allowed_origins_hint(self) -> str:
        """Frase pronta con le origini consentite, per messaggi di errore utili."""

        origins = list(self.allowed_origins)
        if not origins:
            return "Nessuna origine è configurata in browser.allowed_origins."
        return "Origini consentite: " + ", ".join(origins) + "."

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

    def elements(self, limit: int) -> dict[str, Any]: ...

    def click_element(self, index: int) -> None: ...

    def fill_element(self, index: int, value: str) -> None: ...

    def press(
        self,
        key: str,
        *,
        selector: str | None = None,
        index: int | None = None,
    ) -> None: ...

    def wait(
        self,
        *,
        selector: str | None = None,
        text: str | None = None,
        timeout_ms: int | None = None,
    ) -> None: ...

    def links(self) -> list[dict[str, str]]: ...

    def screenshot(self, path: Path) -> None: ...

    def close(self) -> None: ...


def _playwright_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


def _element_info(handle: Any) -> dict[str, Any] | None:
    """Descrizione di un elemento interattivo, o ``None`` se non è utilizzabile.

    Un elemento che sparisce fra una query e l'altra non deve interrompere
    l'elenco: viene semplicemente saltato.
    """

    try:
        if not handle.is_visible() or not handle.is_enabled():
            return None
        info = handle.evaluate(_ELEMENTS_JS)
    except Exception:  # noqa: BLE001 - l'elemento non è più raggiungibile
        return None
    return info if isinstance(info, dict) else None


def _storage_state(policy: BrowserPolicy) -> str | None:
    """File di sessione da ricaricare, se esiste."""

    path = policy.session_path
    if path is None or not path.is_file():
        return None
    return str(path)


def _save_storage_state(policy: BrowserPolicy, context: Any) -> bool:
    """Salva cookie e storage locale come credenziale, con permessi ristretti.

    Un salvataggio fallito non deve nascondere l'errore che ha portato alla
    chiusura del browser: il chiamante riceve ``False`` e la sessione precedente
    resta sul disco.
    """

    path = policy.session_path
    if path is None or context is None:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(path))
        os.chmod(path, 0o600)
    except Exception:  # noqa: BLE001 - la sessione è un di più, non un requisito
        return False
    return True


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
        self._elements: list[Any] = []
        self._elements_url = ""
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
                storage_state=_storage_state(policy),
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
        self._elements = []
        self._elements_url = ""

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

    @property
    def elements_count(self) -> int:
        return len(self._elements)

    def elements(self, limit: int) -> dict[str, Any]:
        """Elenca gli elementi interattivi visibili, nell'ordine del documento.

        Gli handle restano nel backend: il modello riceve solo numeri, così non
        deve inventare selettori CSS. Un'azione numerata verifica poi che la
        pagina sia ancora quella dell'elenco.
        """

        page = self._require_page()
        try:
            handles = page.query_selector_all(_ELEMENTS_SELECTOR)
        except Exception as exc:
            raise BrowserError(f"Elementi non leggibili: {exc}") from exc
        collected: list[dict[str, Any]] = []
        kept: list[Any] = []
        total = 0
        for handle in handles:
            info = _element_info(handle)
            if info is None:
                continue
            total += 1
            if len(collected) >= limit:
                continue
            kept.append(handle)
            collected.append(
                {
                    "index": len(collected) + 1,
                    "tag": str(info.get("tag") or ""),
                    "role": str(info.get("role") or ""),
                    "type": str(info.get("type") or ""),
                    "label": str(info.get("label") or ""),
                    "value": str(info.get("value") or ""),
                    "multiline": bool(info.get("multiline")),
                }
            )
        self._elements = kept
        self._elements_url = page.url
        return {"elements": collected, "total": total}

    def _element(self, index: int) -> Any:
        page = self._require_page()
        if not self._elements or self._elements_url != page.url:
            self._elements = []
            self._elements_url = ""
            raise BrowserError(
                "Elenco degli elementi non più valido: chiama browser_elements."
            )
        if not 1 <= index <= len(self._elements):
            raise ValidationError(
                f"index fuori intervallo: l'ultimo browser_elements aveva "
                f"{len(self._elements)} elementi (da 1 a {len(self._elements)})."
            )
        return self._elements[index - 1]

    def click_element(self, index: int) -> None:
        handle = self._element(index)
        try:
            handle.click(timeout=self._timeout_ms)
        except Exception as exc:
            raise BrowserError(f"Click non riuscito: {exc}") from exc
        self.url = self._require_page().url

    def fill_element(self, index: int, value: str) -> None:
        handle = self._element(index)
        try:
            handle.fill(value, timeout=self._timeout_ms)
        except Exception as exc:
            raise BrowserError(f"Compilazione non riuscita: {exc}") from exc
        self.url = self._require_page().url

    def press(
        self,
        key: str,
        *,
        selector: str | None = None,
        index: int | None = None,
    ) -> None:
        page = self._require_page()
        try:
            if index is not None:
                self._element(index).press(key, timeout=self._timeout_ms)
            elif selector is not None:
                page.press(selector, key, timeout=self._timeout_ms)
            else:
                page.keyboard.press(key)
        except AgentMemoryError:
            raise
        except Exception as exc:
            raise BrowserError(f"Tasto non premuto: {exc}") from exc
        self.url = page.url

    def wait(
        self,
        *,
        selector: str | None = None,
        text: str | None = None,
        timeout_ms: int | None = None,
    ) -> None:
        page = self._require_page()
        timeout = self._timeout_ms if timeout_ms is None else timeout_ms
        try:
            if selector is not None:
                page.wait_for_selector(selector, state="visible", timeout=timeout)
            if text is not None:
                page.wait_for_function(
                    _TEXT_PRESENT_JS, arg=text.lower(), timeout=timeout
                )
        except Exception as exc:
            raise BrowserError(f"Attesa non soddisfatta: {exc}") from exc
        self.url = page.url

    def links(self) -> list[dict[str, str]]:
        page = self._require_page()
        try:
            raw = page.eval_on_selector_all(
                "a[href]",
                """els => els.map(el => ({
                    text: (el.innerText || el.getAttribute('aria-label') || '').trim(),
                    url: el.href,
                }))""",
            )
        except Exception as exc:
            raise BrowserError(f"Link non leggibili: {exc}") from exc
        return [
            {
                "text": str(item.get("text") or ""),
                "url": str(item.get("url") or ""),
            }
            for item in raw
            if isinstance(item, dict)
        ]

    def screenshot(self, path: Path) -> None:
        page = self._require_page()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(path), full_page=True)
        except Exception as exc:
            raise BrowserError(f"Screenshot non riuscito: {exc}") from exc

    def close(self) -> None:
        context = getattr(self, "_context", None)
        if context is not None:
            _save_storage_state(self.policy, context)
        for closer in (
            context,
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
        self._elements = []
        self._elements_url = ""
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
        self._history: list[str] = []
        self._links: list[str] = []

    @property
    def current_url(self) -> str | None:
        if self._backend is None or not self._backend.url:
            return None
        return self._backend.url

    def _active_backend(self) -> BrowserBackend:
        """Restituisce il backend aperto, o un errore utile per il modello."""

        self.policy.require_enabled()
        backend = self._backend
        if backend is None or not backend.url:
            raise BrowserUnavailableError(
                "Nessuna pagina aperta: usa prima browser_open."
            )
        return backend

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

    @staticmethod
    def _target(selector: str | None, index: int | None) -> None:
        if (selector is None) == (index is None):
            raise ValidationError(
                "Indica un selettore oppure un indice, non entrambi: usa "
                "browser_elements per ottenere gli indici."
            )

    @staticmethod
    def _target_index(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError("index deve essere un intero.")
        if value < 1:
            raise ValidationError("index parte da 1, come in browser_elements.")
        return value

    @staticmethod
    def _key(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValidationError("Il tasto da premere non può essere vuoto.")
        key = value.strip()
        if not _KEY_PATTERN.match(key):
            raise ValidationError(
                "Tasto non consentito: usa Enter, Tab, Escape, Spazio, le frecce "
                "o le loro combinazioni con Control, Shift, Alt, Meta. Per "
                "scrivere testo usa browser_fill."
            )
        return key

    @staticmethod
    def _timeout(value: float | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError("timeout_seconds deve essere un numero.")
        if not 1 <= float(value) <= 120:
            raise ValidationError("timeout_seconds deve essere tra 1 e 120.")
        return int(float(value) * 1000)

    @staticmethod
    def _value(value: str) -> str:
        if not isinstance(value, str):
            raise ValidationError("Il valore da inserire deve essere testo.")
        if len(value) > MAX_FILL_CHARS:
            raise ValidationError(f"Il valore supera {MAX_FILL_CHARS} caratteri.")
        return value

    def _remember(self, url: str) -> None:
        if not url:
            return
        if self._history and self._history[-1] == url:
            return
        self._history.append(url)
        if len(self._history) > MAX_HISTORY:
            del self._history[: len(self._history) - MAX_HISTORY]
        # Una nuova pagina invalida l'elenco dei link della precedente.
        self._links = []

    def _navigate(self, url: str, *, max_chars: int, remember: bool = True) -> dict[str, Any]:
        self.policy.assert_allowed(url)
        backend = self._ensure_backend()
        backend.open(url)
        self.policy.assert_allowed(backend.url)
        if remember:
            self._remember(backend.url)
        return self.snapshot(max_chars=max_chars)

    def open(self, url: str, *, max_chars: int = 6_000) -> dict[str, Any]:
        max_chars = self._max_chars(max_chars)
        return self._navigate(url, max_chars=max_chars)

    def links(self, *, limit: int = 50) -> dict[str, Any]:
        """Elenca i link della pagina corrente come indice numerato.

        Restano solo i link che la whitelist autorizza: il modello non deve
        indovinare selettori CSS né scoprire a proprie spese un dominio vietato.
        """

        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValidationError("limit deve essere un intero.")
        if not 1 <= limit <= MAX_LINKS:
            raise ValidationError(f"limit deve essere tra 1 e {MAX_LINKS}.")
        backend = self._active_backend()
        self.policy.assert_allowed(backend.url)

        seen: set[str] = set()
        collected: list[dict[str, Any]] = []
        blocked = 0
        for link in backend.links():
            url = str(link.get("url", "")).split("#", 1)[0]
            if not url or url in seen:
                continue
            seen.add(url)
            if not self.policy.is_allowed(url):
                blocked += 1
                continue
            label = " ".join(str(link.get("text", "")).split())
            if len(label) > MAX_LINK_TEXT:
                label = label[: MAX_LINK_TEXT - 1].rstrip() + "…"
            collected.append(
                {
                    "index": len(collected) + 1,
                    "text": label or url,
                    "url": url,
                }
            )
        truncated = len(collected) > limit
        collected = collected[:limit]
        self._links = [item["url"] for item in collected]
        return {
            "url": backend.url,
            "count": len(collected),
            "truncated": truncated,
            "blocked_count": blocked,
            "links": collected,
        }

    def open_link(self, index: int, *, max_chars: int = 6_000) -> dict[str, Any]:
        """Apre il link numero ``index`` restituito da ``browser_links``."""

        max_chars = self._max_chars(max_chars)
        self.policy.require_enabled()
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValidationError("index deve essere un intero.")
        if not self._links:
            raise BrowserUnavailableError(
                "Nessun link da aprire: chiama browser_links; se l'elenco è vuoto, "
                "usa browser_open con un altro URL consentito."
            )
        if not 1 <= index <= len(self._links):
            raise ValidationError(
                f"index fuori intervallo: l'ultimo browser_links ha "
                f"{len(self._links)} link (da 1 a {len(self._links)})."
            )
        return self._navigate(self._links[index - 1], max_chars=max_chars)

    def back(self, *, max_chars: int = 6_000) -> dict[str, Any]:
        """Torna alla pagina precedente della sessione, con la stessa policy."""

        max_chars = self._max_chars(max_chars)
        self.policy.require_enabled()
        if len(self._history) < 2:
            raise BrowserUnavailableError(
                "Nessuna pagina precedente nella sessione: apri una pagina con "
                "browser_open."
            )
        previous = self._history[-2]
        self._history.pop()
        self._links = []
        return self._navigate(previous, max_chars=max_chars, remember=False)

    def snapshot(self, *, max_chars: int = 6_000) -> dict[str, Any]:
        max_chars = self._max_chars(max_chars)
        backend = self._active_backend()
        self.policy.assert_allowed(backend.url)
        full_text = backend.text()
        truncated = len(full_text) > max_chars
        return {
            "url": backend.url,
            "title": backend.title(),
            "text": full_text[:max_chars],
            "truncated": truncated,
        }

    def elements(self, *, limit: int = 40) -> dict[str, Any]:
        """Elenco numerato degli elementi interattivi della pagina corrente.

        È la vista che serve al modello per agire senza configurazione per sito
        né selettori inventati: campi, aree di scrittura e bottoni hanno un
        indice stabile finché la pagina non cambia.
        """

        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValidationError("limit deve essere un intero.")
        if not 1 <= limit <= MAX_ELEMENTS:
            raise ValidationError(f"limit deve essere tra 1 e {MAX_ELEMENTS}.")
        backend = self._active_backend()
        self.policy.assert_allowed(backend.url)
        result = backend.elements(limit)
        items = result["elements"]
        total = int(result["total"])
        return {
            "url": backend.url,
            "count": len(items),
            "total": total,
            "truncated": total > len(items),
            "elements": items,
        }

    def click(
        self, selector: str | None = None, *, index: int | None = None
    ) -> dict[str, Any]:
        backend = self._active_backend()
        self.policy.assert_interaction_allowed(backend.url)
        self._target(selector, index)
        try:
            if index is not None:
                backend.click_element(self._target_index(index))
            else:
                backend.click(self._selector(selector or ""))
        except BrowserError as exc:
            raise BrowserError(
                f"{exc} Se non conosci il selettore, usa browser_links per i "
                "link oppure browser_elements per gli altri elementi."
            ) from exc
        self.policy.assert_allowed(backend.url)
        self._remember(backend.url)
        return self._page_info()

    def fill(
        self,
        selector: str | None = None,
        value: str = "",
        *,
        index: int | None = None,
    ) -> dict[str, Any]:
        backend = self._active_backend()
        self.policy.assert_interaction_allowed(backend.url)
        self._target(selector, index)
        text = self._value(value)
        if index is not None:
            backend.fill_element(self._target_index(index), text)
        else:
            backend.fill(self._selector(selector or ""), text)
        self.policy.assert_allowed(backend.url)
        return self._page_info()

    def press(
        self,
        key: str,
        *,
        selector: str | None = None,
        index: int | None = None,
    ) -> dict[str, Any]:
        """Preme un tasto di controllo, sulla pagina o su un elemento."""

        backend = self._active_backend()
        self.policy.assert_interaction_allowed(backend.url)
        if selector is not None and index is not None:
            raise ValidationError(
                "Indica un selettore oppure un indice, non entrambi."
            )
        pressed = self._key(key)
        target = self._selector(selector) if selector is not None else None
        if index is not None:
            self._target_index(index)
        backend.press(pressed, selector=target, index=index)
        self.policy.assert_allowed(backend.url)
        self._remember(backend.url)
        return self._page_info()

    def wait(
        self,
        *,
        selector: str | None = None,
        text: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Attende che un elemento compaia o che un testo sia nella pagina."""

        backend = self._active_backend()
        self.policy.assert_allowed(backend.url)
        if selector is None and text is None:
            raise ValidationError(
                "Indica un selettore oppure un testo da attendere."
            )
        target = self._selector(selector) if selector is not None else None
        expected: str | None = None
        if text is not None:
            if not isinstance(text, str) or not text.strip():
                raise ValidationError("Il testo da attendere non può essere vuoto.")
            if len(text) > MAX_WAIT_TEXT:
                raise ValidationError(
                    f"Il testo da attendere supera {MAX_WAIT_TEXT} caratteri."
                )
            expected = text.strip()
        backend.wait(
            selector=target,
            text=expected,
            timeout_ms=self._timeout(timeout_seconds),
        )
        self.policy.assert_allowed(backend.url)
        return self._page_info()

    def screenshot(self, path: str | Path) -> str:
        backend = self._active_backend()
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
        self._history = []
        self._links = []

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.policy.enabled,
            "interaction_enabled": self.policy.interaction_enabled,
            "allowed_origins": list(self.policy.allowed_origins),
            "navigation_timeout_seconds": self.policy.timeout_seconds,
            "playwright_available": _playwright_available(),
            "session_saved": bool(
                self.policy.session_path is not None
                and self.policy.session_path.is_file()
            ),
            "session_file": str(SESSION_RELATIVE_PATH),
            "service_workers": "block",
            "current_url": self.current_url,
            "history_count": len(self._history),
            "can_go_back": len(self._history) > 1,
            "links_count": len(self._links),
            "elements_count": getattr(self._backend, "elements_count", 0),
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
    parser.add_argument(
        "--links",
        action="store_true",
        help="Con --open, stampa l'indice numerato dei link consentiti.",
    )
    parser.add_argument(
        "--elements",
        action="store_true",
        help="Con --open, stampa l'indice numerato degli elementi interattivi.",
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
        if args.links:
            print(json.dumps(controller.links(), ensure_ascii=False, indent=2))
        if args.elements:
            print(json.dumps(controller.elements(), ensure_ascii=False, indent=2))
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
