"""Trasporto dell'outbox verso WhatsApp Web, con ricevute e stop sull'incerto.

L'executor registra una consegna in ``data/outbox/messages.jsonl``; questo
modulo è il passo successivo: apre WhatsApp Web in una finestra **visibile**,
scrive al contatto autorizzato e osserva l'esito. Non è un tool MCP e non gira
dentro il server: è un comando separato, eseguito quando una persona decide
che è il momento.

Perché un comando separato:

- WhatsApp Web rifiuta lo user agent ``HeadlessChrome``: serve una finestra
  vera, quindi uno schermo e una sessione salvata con
  ``mnemosine-browser-login``;
- l'effetto remoto non è transazionale con SQLite. Il registro locale marca
  ``sending`` **prima** dell'azione; se il processo muore, al riavvio quel
  record diventa ``uncertain`` e non viene ritentato da solo;
- una sola esecuzione per profilo, garantita da un lock del sistema operativo
  che si rilascia anche se il processo termina.

Il registro ``data/outbox/status.json`` contiene solo identificativi, stato,
tentativi e codici: mai testo, telefono o nome del destinatario. La conferma è
una lettura dell'interfaccia (un messaggio uscente in più), non una ricevuta di
WhatsApp: ``remote_delivery_verified`` resta falso.

Uso:
    mnemosine-outbox --root <profilo> --check
    mnemosine-outbox --root <profilo> --send
    mnemosine-outbox --root <profilo> --send --limit 3
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time as time_module
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .audit import AuditLog
from .browser import (
    BrowserPolicy,
    _playwright_available,
    _save_storage_state,
    _storage_state,
)
from .config import ConfigStore
from .errors import (
    AgentMemoryError,
    ConfigError,
    OutboxBlockedError,
    OutboxBusyError,
    OutboxUncertainError,
    ValidationError,
)
from .executor import (
    CHANNEL,
    FUTURE_TOLERANCE_MINUTES,
    MAX_MESSAGE_CHARS,
    OUTBOX_DIR,
    OUTBOX_FILENAME,
    TRANSPORT,
    WhatsAppPolicy,
    delivered_messages,
)
from .routine import RoutineEngine

STATUS_FILENAME = "status.json"
LOCK_FILENAME = ".transport.lock"
DELIVERY_TRANSPORT = "whatsapp_web"
WHATSAPP_URL = "https://web.whatsapp.com/"
CHAT_URL = "https://web.whatsapp.com/send?phone={phone}"
DEFAULT_LIMIT = 1
MAX_LIMIT = 20
MAX_QUEUE_AGE_HOURS = 24.0
ACTION_TIMEOUT_MS = 20_000
CONFIRM_TIMEOUT_MS = 12_000
POLL_SECONDS = 0.25
STATUS_VERSION = 1
TERMINAL_STATES = frozenset({"delivered_ui", "uncertain"})

# Campo di scrittura della chat. Il footer esclude la casella di ricerca, che
# è anch'essa ``contenteditable`` ma vive nella colonna delle chat.
_COMPOSER_SELECTOR = "footer div[contenteditable='true']"
_SEND_SELECTORS = (
    "button[aria-label='Invia']",
    "button[aria-label='Send']",
    "button[data-testid='send']",
    "span[data-icon='send']",
)
_OUTGOING_COUNT_JS = r"""
() => {
  const selectors = ['div.message-out', 'div[class*="message-out"]'];
  const nodes = new Set();
  for (const selector of selectors) {
    document.querySelectorAll(selector).forEach((node) => nodes.add(node));
  }
  return nodes.size;
}
"""
_LOGGED_OUT_MARKERS = (
    "scan the qr code",
    "collega il dispositivo",
    "log in with phone number",
    "use whatsapp on your phone",
)
_INVALID_CHAT_MARKERS = ("invalid", "non valido")
_PHONE_RE = re.compile(r"\D+")


def _parse_moment(value: Any, now: datetime) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=now.tzinfo)
    return moment


def _phone_digits(value: Any) -> str | None:
    """Solo cifre, come le vuole il link di WhatsApp; ``None`` se non valido."""

    if not isinstance(value, str):
        return None
    digits = _PHONE_RE.sub("", value)
    if not 7 <= len(digits) <= 15:
        return None
    return digits


class OutboxLock:
    """Lock esclusivo per profilo, rilasciato dal sistema operativo.

    Un lock su file tenuto con ``flock``/``msvcrt`` non lascia stati
    ``sending`` orfani: se il processo muore, il sistema chiude il descrittore
    e il lock sparisce. Non serve indovinare se un pid è ancora vivo.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._handle: Any = None

    def __enter__(self) -> "OutboxLock":
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+b")
        except OSError as exc:
            raise OutboxBusyError(
                f"Impossibile creare il lock {self.path}: {exc}"
            ) from exc
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise OutboxBusyError(
                "Un'altra consegna è già in corso per questo profilo."
            ) from exc
        self._handle = handle
        return self

    def __exit__(self, *_exc: Any) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()


class DeliveryStatusStore:
    """Registro locale delle consegne, senza testo né dati del destinatario."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        if not self.path.exists():
            self._data = {
                "version": STATUS_VERSION,
                "updated_at": None,
                "messages": {},
            }
            return self._data
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Fail closed: un registro illeggibile potrebbe far rinviare
            # messaggi già consegnati.
            raise ConfigError(
                f"Registro delle consegne illeggibile in {self.path}: {exc}"
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("messages"), dict):
            raise ConfigError(f"{self.path}: atteso un oggetto con 'messages'.")
        if data.get("version") != STATUS_VERSION:
            raise ConfigError(
                f"{self.path}: versione del registro non supportata "
                f"({data.get('version')!r})."
            )
        self._data = data
        return data

    def entries(self) -> dict[str, dict[str, Any]]:
        return self.load()["messages"]

    def state_of(self, message_id: str) -> str | None:
        entry = self.entries().get(message_id)
        if not isinstance(entry, dict):
            return None
        state = entry.get("state")
        return state if isinstance(state, str) else None

    def update(
        self,
        message_id: str,
        state: str,
        now: datetime,
        *,
        reason: str | None = None,
        attempt: bool = False,
        remote_delivery_verified: bool | None = None,
    ) -> dict[str, Any]:
        data = self.load()
        messages = data["messages"]
        entry = messages.get(message_id)
        if not isinstance(entry, dict):
            entry = {"state": "pending", "attempts": 0}
        stamp = now.replace(microsecond=0).isoformat()
        entry["state"] = state
        entry["updated_at"] = stamp
        entry["reason"] = reason
        if attempt:
            entry["attempts"] = int(entry.get("attempts") or 0) + 1
            entry.setdefault("first_attempt_at", stamp)
        if remote_delivery_verified is not None:
            entry["remote_delivery_verified"] = bool(remote_delivery_verified)
        messages[message_id] = entry
        data["updated_at"] = stamp
        self._write(data)
        return entry

    def recover_interrupted(self, now: datetime) -> int:
        """Trasforma i ``sending`` rimasti da un processo morto in ``uncertain``."""

        recovered = 0
        stamp = now.replace(microsecond=0).isoformat()
        for entry in self.entries().values():
            if not isinstance(entry, dict) or entry.get("state") != "sending":
                continue
            entry["state"] = "uncertain"
            entry["reason"] = "interrupted"
            entry["updated_at"] = stamp
            recovered += 1
        if recovered:
            data = self.load()
            data["updated_at"] = stamp
            self._write(data)
        return recovered

    def sent_today(self, now: datetime) -> int:
        count = 0
        for entry in self.entries().values():
            if not isinstance(entry, dict) or entry.get("state") != "delivered_ui":
                continue
            moment = _parse_moment(entry.get("updated_at"), now)
            if moment is not None and moment.date() == now.date():
                count += 1
        return count

    def _write(self, data: dict[str, Any]) -> None:
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(
                f".{self.path.name}.{os.getpid()}.tmp"
            )
            payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
            temporary.write_text(payload + "\n", encoding="utf-8")
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except OSError as exc:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink()
            raise ConfigError(
                f"Impossibile scrivere il registro delle consegne in {self.path}: {exc}"
            ) from exc


class WhatsAppTransportBackend(Protocol):
    """Backend minimo: preflight senza effetti, poi una consegna osservata."""

    def preflight(self) -> None: ...

    def deliver(self, phone: str, text: str) -> dict[str, Any]: ...

    def close(self) -> None: ...


class PlaywrightWhatsAppBackend:
    """Apre WhatsApp Web con una finestra visibile e osserva l'invio.

    Non esiste un'API di WhatsApp: la conferma è un messaggio uscente in più
    nel DOM. Se il campo o il pulsante non compaiono, l'errore è ``blocked``;
    se il click parte ma la conferma manca, l'errore è ``uncertain`` e il
    messaggio non viene ritentato.
    """

    def __init__(self, policy: BrowserPolicy) -> None:
        self.policy = policy
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._timeout_ms = int(policy.timeout_seconds * 1000)

    # -- ciclo di vita ------------------------------------------------------

    def preflight(self) -> None:
        if not _playwright_available():
            raise OutboxBlockedError(
                "Playwright non è installato. Esegui "
                "`pip install -r requirements-browser.txt` e "
                "`python -m playwright install chromium`."
            )
        try:
            self.policy.require_enabled()
            self.policy.assert_allowed(WHATSAPP_URL)
        except AgentMemoryError as exc:
            raise OutboxBlockedError(str(exc)) from exc
        session = self.policy.session_path
        if session is None or not session.is_file():
            raise OutboxBlockedError(
                "Nessuna sessione browser salvata: esegui "
                "`mnemosine-browser-login --origin https://web.whatsapp.com` "
                "e completa l'accesso."
            )

    def _start(self) -> Any:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - difesa dal runtime
            raise OutboxBlockedError("Playwright non è disponibile.") from exc
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=False,
                executable_path=self.policy.executable_path,
            )
            self._context = self._browser.new_context(
                accept_downloads=False,
                ignore_https_errors=False,
                service_workers=self.policy.service_worker_mode,
                user_agent=self.policy.user_agent,
                storage_state=_storage_state(self.policy),
            )
            self._context.set_default_timeout(self._timeout_ms)
            self._page = self._context.new_page()
        except Exception as exc:
            self.close()
            raise OutboxBlockedError(
                "Browser visibile non avviato: serve uno schermo e Chromium "
                "installato per Playwright."
            ) from exc
        return self._page

    def close(self) -> None:
        context = self._context
        if context is not None:
            _save_storage_state(self.policy, context)
        for closer in (context, self._browser):
            if closer is not None:
                with suppress(Exception):
                    closer.close()
        playwright = self._playwright
        if playwright is not None:
            with suppress(Exception):
                playwright.stop()
        self._context = None
        self._browser = None
        self._playwright = None
        self._page = None

    # -- invio --------------------------------------------------------------

    def deliver(self, phone: str, text: str) -> dict[str, Any]:
        page = self._page or self._start()
        clicked = False
        try:
            page.goto(
                CHAT_URL.format(phone=phone),
                wait_until="domcontentloaded",
                timeout=self._timeout_ms,
            )
            if not self._wait_composer(page):
                self._raise_for_page_state(page)
            composer = page.query_selector(_COMPOSER_SELECTOR)
            if composer is None:
                raise OutboxBlockedError(
                    "WhatsApp Web non ha mostrato il campo di scrittura."
                )
            before = self._outgoing_count(page)
            composer.click(timeout=ACTION_TIMEOUT_MS)
            composer.fill(text, timeout=ACTION_TIMEOUT_MS)
            button = self._find_send_button(page)
            if button is None:
                raise OutboxBlockedError(
                    "Pulsante di invio non trovato: il messaggio resta in outbox."
                )
            # Da qui in poi un errore non prova che nulla sia partito.
            clicked = True
            button.click(timeout=ACTION_TIMEOUT_MS)
        except OutboxBlockedError:
            raise
        except OutboxUncertainError:
            raise
        except Exception as exc:
            if clicked:
                raise OutboxUncertainError(
                    "Invio avviato ma esito non verificabile."
                ) from exc
            raise OutboxBlockedError(
                f"Preparazione della chat non riuscita ({type(exc).__name__})."
            ) from exc
        confirmed = self._wait_confirmation(page, before)
        return {
            "ui_confirmed": confirmed,
            "remote_delivery_verified": False,
        }

    def _wait_composer(self, page: Any) -> bool:
        try:
            page.wait_for_selector(
                _COMPOSER_SELECTOR, state="visible", timeout=self._timeout_ms
            )
            return True
        except Exception:
            return False

    def _raise_for_page_state(self, page: Any) -> None:
        text = ""
        try:
            text = str(
                page.evaluate("document.body ? document.body.innerText : ''")
            ).lower()
        except Exception:
            pass
        if not text.strip():
            raise OutboxBlockedError(
                "WhatsApp Web non ha caricato la pagina: controlla la rete."
            )
        if any(marker in text for marker in _LOGGED_OUT_MARKERS):
            raise OutboxBlockedError(
                "Sessione WhatsApp Web non autenticata: esegui "
                "`mnemosine-browser-login --origin https://web.whatsapp.com`."
            )
        if any(marker in text for marker in _INVALID_CHAT_MARKERS):
            raise OutboxBlockedError(
                "WhatsApp Web non ha aperto la chat: numero non valido o "
                "non raggiungibile."
            )
        raise OutboxBlockedError(
            "Chat non aperta: WhatsApp Web non ha mostrato il campo di scrittura."
        )

    def _outgoing_count(self, page: Any) -> int:
        try:
            return int(page.evaluate(_OUTGOING_COUNT_JS))
        except Exception:
            return 0

    def _find_send_button(self, page: Any) -> Any:
        for selector in _SEND_SELECTORS:
            locator = page.locator(selector).first
            try:
                if locator.count() and locator.is_visible() and locator.is_enabled():
                    return locator
            except Exception:
                continue
        return None

    def _wait_confirmation(self, page: Any, before: int) -> bool:
        deadline = time_module.monotonic() + CONFIRM_TIMEOUT_MS / 1000
        while time_module.monotonic() < deadline:
            try:
                if self._outgoing_count(page) > before:
                    return True
            except Exception:
                pass
            time_module.sleep(POLL_SECONDS)
        return False


def _build_backend(root: Path) -> WhatsAppTransportBackend:
    return PlaywrightWhatsAppBackend(BrowserPolicy.from_root(root))


class OutboxTransport:
    """Consuma l'outbox e consegna un messaggio alla volta, fallendo chiuso."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        backend: WhatsAppTransportBackend | None = None,
        at: datetime | None = None,
    ) -> None:
        self.config = ConfigStore(root)
        self.root = self.config.root
        self.audit = AuditLog(self.root)
        self.routine = RoutineEngine(self.root, at=at)
        self.now = self.routine.now
        self.state = self.routine.state
        self.policy = WhatsAppPolicy.load(self.root)
        self._backend = backend
        self.status = DeliveryStatusStore(
            self.root / "data" / OUTBOX_DIR / STATUS_FILENAME
        )

    # -- lettura ------------------------------------------------------------

    def outbox_path(self) -> Path:
        return self.root / "data" / OUTBOX_DIR / OUTBOX_FILENAME

    def _records(self) -> list[dict[str, Any]]:
        return delivered_messages(self.root)

    def _pending_records(self) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = []
        for record in self._records():
            message_id = record.get("message_id")
            if not isinstance(message_id, str) or not message_id:
                continue
            if self.status.state_of(message_id) in TERMINAL_STATES:
                continue
            pending.append(record)
        pending.sort(key=lambda item: str(item.get("delivered_at") or ""))
        return pending

    def _flags(self) -> dict[str, Any]:
        flags = self.state.get("flags")
        return flags if isinstance(flags, dict) else {}

    def _availability(self) -> dict[str, Any]:
        availability = self.state.get("availability")
        return availability if isinstance(availability, dict) else {}

    def _gate(self) -> str | None:
        """Condizione globale che ferma il trasporto, o ``None`` se si può."""

        flags = self._flags()
        if not self.policy.enabled:
            return "integration_disabled"
        if not flags.get("whatsapp_sending_enabled"):
            return "sending_disabled"
        if flags.get("maintenance_mode"):
            return "maintenance"
        if not self._availability().get("accepting_messages", False):
            return "not_accepting"
        if self.policy.is_quiet(self.now.time()):
            return "quiet_hours"
        if self.status.sent_today(self.now) >= self.policy.max_per_day:
            return "daily_limit"
        return None

    def _inspect(
        self, record: dict[str, Any]
    ) -> tuple[str | None, str | None, str | None, str | None]:
        """Restituisce ``(user_id, telefono, testo, motivo)`` senza effetti."""

        if record.get("channel") != CHANNEL:
            return None, None, None, "channel"
        if record.get("transport") != TRANSPORT:
            return None, None, None, "transport"
        text = record.get("text")
        if not isinstance(text, str) or not text.strip():
            return None, None, None, "text_missing"
        cleaned = text.strip()
        if len(cleaned) > MAX_MESSAGE_CHARS:
            return None, None, None, "text_too_long"
        if "\x00" in cleaned:
            return None, None, None, "text_invalid"
        target = record.get("target")
        if not isinstance(target, dict):
            return None, None, None, "target_missing"
        user_id = target.get("user_id")
        if not isinstance(user_id, str) or not user_id.strip():
            return None, None, None, "target_missing"
        stamp = _parse_moment(record.get("delivered_at"), self.now)
        if stamp is None:
            return None, None, None, "timestamp_missing"
        age_minutes = (self.now - stamp).total_seconds() / 60.0
        if age_minutes < -FUTURE_TOLERANCE_MINUTES:
            return None, None, None, "future_timestamp"
        if age_minutes > MAX_QUEUE_AGE_HOURS * 60:
            return None, None, None, "stale"
        contact = self.policy.contact_for(user_id.strip())
        if contact is None:
            return None, None, None, "contact_not_allowed"
        phone = _phone_digits(contact.phone)
        if phone is None:
            reason = "phone_missing" if not contact.phone else "phone_invalid"
            return None, None, None, reason
        return user_id.strip(), phone, cleaned, None

    # -- audit --------------------------------------------------------------

    def _audit_delivery(
        self,
        outcome: str,
        message_id: str,
        *,
        reason: str | None = None,
        error_type: str | None = None,
    ) -> None:
        self.audit.record(
            "external_action",
            action="whatsapp.transport",
            outcome=outcome,
            message_id=message_id,
            transport=DELIVERY_TRANSPORT,
            reason=reason,
            error_type=error_type,
        )

    def _audit_run(self, report: dict[str, Any]) -> None:
        outcome = "uncertain" if report["uncertain"] else "ok"
        self.audit.record(
            "external_action",
            action="whatsapp.transport_run",
            outcome=outcome,
            sent=report["sent"],
            blocked=report["blocked"],
            uncertain=report["uncertain"],
            recovered=report["recovered"],
            stopped=report["stopped"],
        )

    # -- esecuzione ---------------------------------------------------------

    def run(self, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValidationError("limit deve essere un intero.")
        if not 1 <= limit <= MAX_LIMIT:
            raise ValidationError(f"limit deve essere tra 1 e {MAX_LIMIT}.")

        lock_path = self.root / "data" / OUTBOX_DIR / LOCK_FILENAME
        with OutboxLock(lock_path):
            recovered = self.status.recover_interrupted(self.now)
            records = self._records()
            pending = self._pending_records()
            report: dict[str, Any] = {
                "checked": len(records),
                "pending": len(pending),
                "recovered": recovered,
                "sent": 0,
                "blocked": 0,
                "uncertain": 0,
                "invalid": 0,
                "stopped": None,
                "messages": [],
            }
            if not pending:
                return report

            gate = self._gate()
            if gate is not None:
                report["stopped"] = gate
                return report

            backend = self._backend or _build_backend(self.root)
            try:
                backend.preflight()
                for record in pending[:limit]:
                    gate = self._gate()
                    if gate is not None:
                        report["stopped"] = gate
                        break
                    outcome, message_id, reason = self._deliver_one(backend, record)
                    if outcome is None:
                        report["invalid"] += 1
                        continue
                    report["messages"].append(
                        {"message_id": message_id, "state": outcome, "reason": reason}
                    )
                    if outcome == "sent":
                        report["sent"] += 1
                    elif outcome == "blocked":
                        report["blocked"] += 1
                    elif outcome == "uncertain":
                        report["uncertain"] += 1
                        report["stopped"] = "uncertain"
                        break
            finally:
                with suppress(Exception):
                    backend.close()

            self._audit_run(report)
            return report

    def _deliver_one(
        self, backend: WhatsAppTransportBackend, record: dict[str, Any]
    ) -> tuple[str | None, str | None, str | None]:
        message_id = record.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            return None, None, None

        _user_id, phone, text, reason = self._inspect(record)
        if reason is not None:
            self.status.update(message_id, "blocked", self.now, reason=reason)
            self._audit_delivery("blocked", message_id, reason=reason)
            return "blocked", message_id, reason
        assert phone is not None and text is not None

        # Prima l'azione, poi l'esito: un processo interrotto qui resta
        # ``sending`` e il giro successivo lo trasforma in ``uncertain``.
        self.status.update(
            message_id, "sending", self.now, reason=None, attempt=True
        )
        try:
            result = backend.deliver(phone, text)
        except OutboxBlockedError as exc:
            self.status.update(
                message_id, "blocked", self.now, reason="transport_unavailable"
            )
            self._audit_delivery(
                "blocked",
                message_id,
                reason="transport_unavailable",
                error_type=type(exc).__name__,
            )
            return "blocked", message_id, "transport_unavailable"
        except OutboxUncertainError as exc:
            self.status.update(
                message_id, "uncertain", self.now, reason="effect_uncertain"
            )
            self._audit_delivery(
                "uncertain",
                message_id,
                reason="effect_uncertain",
                error_type=type(exc).__name__,
            )
            return "uncertain", message_id, "effect_uncertain"
        except Exception as exc:  # noqa: BLE001 - dopo l'azione non si ritenta
            self.status.update(
                message_id, "uncertain", self.now, reason="unexpected_error"
            )
            self._audit_delivery(
                "uncertain",
                message_id,
                reason="unexpected_error",
                error_type=type(exc).__name__,
            )
            return "uncertain", message_id, "unexpected_error"

        if isinstance(result, dict) and result.get("ui_confirmed"):
            self.status.update(
                message_id,
                "delivered_ui",
                self.now,
                reason=None,
                remote_delivery_verified=False,
            )
            self._audit_delivery("ok", message_id)
            return "sent", message_id, None
        self.status.update(
            message_id, "uncertain", self.now, reason="ui_not_confirmed"
        )
        self._audit_delivery("uncertain", message_id, reason="ui_not_confirmed")
        return "uncertain", message_id, "ui_not_confirmed"

    # -- ispezione amministrativa ------------------------------------------

    def status_report(self) -> dict[str, Any]:
        """Stato leggibile senza aprire il browser e senza dati personali."""

        records = self._records()
        entries = self.status.entries()
        items: list[dict[str, Any]] = []
        invalid = 0
        for record in records:
            message_id = record.get("message_id")
            if not isinstance(message_id, str) or not message_id:
                invalid += 1
                continue
            entry = entries.get(message_id)
            entry = entry if isinstance(entry, dict) else {}
            state = entry.get("state") or "pending"
            _user, phone, _text, _reason = self._inspect(record)
            items.append(
                {
                    "message_id": message_id,
                    "state": state,
                    "attempts": int(entry.get("attempts") or 0),
                    "updated_at": entry.get("updated_at"),
                    "reason": entry.get("reason"),
                    "phone_configured": phone is not None,
                }
            )
        counts = {
            "total": len(records),
            "invalid": invalid,
            "pending": 0,
            "sending": 0,
            "delivered_ui": 0,
            "blocked": 0,
            "uncertain": 0,
        }
        for item in items:
            state = item["state"]
            counts[state if state in counts else "pending"] += 1

        return {
            "generated_at": self.now.replace(microsecond=0).isoformat(),
            "policy": {
                "enabled": self.policy.enabled,
                "contacts": len(self.policy.allowed_contacts),
                "quiet_hours": {
                    "start": self.policy.quiet_start,
                    "end": self.policy.quiet_end,
                },
                "max_per_day": self.policy.max_per_day,
            },
            "flags": {
                "sending_enabled": bool(
                    self._flags().get("whatsapp_sending_enabled")
                ),
                "maintenance": bool(self._flags().get("maintenance_mode")),
                "accepting_messages": bool(
                    self._availability().get("accepting_messages", False)
                ),
            },
            "gate": self._gate(),
            "sent_today": self.status.sent_today(self.now),
            "counts": counts,
            "messages": items[-50:],
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Consegna l'outbox del profilo a WhatsApp Web, con finestra "
            "visibile, lock e stop sull'esito incerto."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory del profilo (default: quella che contiene il pacchetto).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Mostra cosa è in attesa e quale condizione ferma il trasporto.",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Invia i messaggi in attesa; richiede una sessione browser salvata.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Numero massimo di messaggi per giro (default {DEFAULT_LIMIT}).",
    )
    parser.add_argument(
        "--at",
        metavar="ISO",
        default=None,
        help="Fissa l'orologio del trasporto (per diagnostica e test).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.check == args.send:
        parser.error("indica --check oppure --send")
    at = None
    if args.at:
        try:
            at = datetime.fromisoformat(args.at)
        except ValueError:
            print("ERRORE: --at vuole un timestamp ISO 8601.", file=sys.stderr)
            return 1
    try:
        transport = OutboxTransport(args.root, at=at)
        if args.check:
            print(
                json.dumps(
                    transport.status_report(), ensure_ascii=False, indent=2
                )
            )
            return 0
        report = transport.run(limit=args.limit)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2 if report["uncertain"] else 0
    except AgentMemoryError as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_LIMIT",
    "DeliveryStatusStore",
    "MAX_LIMIT",
    "MAX_QUEUE_AGE_HOURS",
    "OutboxLock",
    "OutboxTransport",
    "PlaywrightWhatsAppBackend",
    "STATUS_FILENAME",
    "WhatsAppTransportBackend",
    "main",
]
