"""Consegna degli impulsi spontanei, con i controlli prima dell'invio.

L'Heartbeat mette in coda un impulso; questo modulo lo consuma. La separazione
è voluta: la generazione del testo è un'operazione del modello, la consegna è
un'operazione dell'executor, e solo l'executor decide se può avvenire.

La consegna passa da una serie di controlli che falliscono chiusi:

- l'impulso deve esistere, essere recente e riguardare un messaggio;
- il canale deve essere ``whatsapp``: un impulso browser serve a navigare, non
  a scrivere a qualcuno;
- il destinatario deve essere nel grafo delle relazioni **e** nella whitelist
  dei contatti in ``config/integrations.json``;
- l'integrazione deve essere abilitata e il flag
  ``current_state.flags.whatsapp_sending_enabled`` deve essere vero;
- niente invii durante la finestra di silenzio, in manutenzione, oltre il
  limite giornaliero o se le risposte non sono accettate.

Quando tutto è a posto il messaggio viene scritto in ``data/outbox/messages.jsonl``
e l'impulso viene chiuso. Il trasporto verso WhatsApp Web resta un passo
separato e non implementato: qui si produce una consegna durevole e verificabile,
non si apre una sessione esterna.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any

from .config import ConfigStore
from .errors import AccessDeniedError, ConfigError
from .routine import RoutineEngine

INTEGRATIONS_FILENAME = "integrations.json"
OUTBOX_DIR = "outbox"
OUTBOX_FILENAME = "messages.jsonl"
MAX_MESSAGE_CHARS = 2000
MAX_IMPULSE_AGE_MINUTES = 12 * 60.0
# Tolleranza per un orologio leggermente avanti: oltre, la data è sospetta.
FUTURE_TOLERANCE_MINUTES = 5.0
DEFAULT_QUIET_HOURS = ("00:00", "07:00")
DEFAULT_MAX_PER_DAY = 3
CHANNEL = "whatsapp"
TRANSPORT = "outbox"
_CLOCK_RE = re.compile(r"^\d{2}:\d{2}$")


@dataclass(frozen=True)
class Contact:
    """Un contatto autorizzato: l'id è quello del grafo delle relazioni."""

    user_id: str
    display_name: str | None = None
    phone: str | None = None


def _parse_clock(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _CLOCK_RE.fullmatch(value):
        raise ConfigError(f"{label}: usa il formato HH:MM.")
    try:
        time.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"{label}: orario non valido '{value}'.") from exc
    return value


@dataclass(frozen=True)
class WhatsAppPolicy:
    """Configurazione amministrativa dell'integrazione, da ``integrations.json``."""

    enabled: bool = False
    allowed_contacts: tuple[Contact, ...] = ()
    quiet_start: str = DEFAULT_QUIET_HOURS[0]
    quiet_end: str = DEFAULT_QUIET_HOURS[1]
    max_per_day: int = DEFAULT_MAX_PER_DAY

    def contact_for(self, user_id: str) -> Contact | None:
        for contact in self.allowed_contacts:
            if contact.user_id == user_id:
                return contact
        return None

    @classmethod
    def load(cls, root: str | Path) -> WhatsAppPolicy:
        """Legge il blocco ``whatsapp``; il file assente vale integrazione spenta."""

        path = Path(root) / "config" / INTEGRATIONS_FILENAME
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"JSON non valido in {path}: riga {exc.lineno}, "
                f"colonna {exc.colno} ({exc.msg})"
            ) from exc
        except OSError as exc:
            raise ConfigError(f"Impossibile leggere {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: atteso un oggetto JSON.")
        block = data.get("whatsapp")
        if block is None:
            return cls()
        if not isinstance(block, dict):
            raise ConfigError(f"{path}: 'whatsapp' deve essere un oggetto.")

        enabled = block.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ConfigError(f"{path}: whatsapp.enabled deve essere booleano.")

        raw_contacts = block.get("allowed_contacts", [])
        if not isinstance(raw_contacts, list):
            raise ConfigError(
                f"{path}: whatsapp.allowed_contacts deve essere una lista."
            )
        contacts: list[Contact] = []
        seen: set[str] = set()
        for index, item in enumerate(raw_contacts):
            label = f"{path}: whatsapp.allowed_contacts[{index}]"
            if not isinstance(item, dict):
                raise ConfigError(f"{label}: atteso un oggetto.")
            user_id = item.get("user_id")
            if not isinstance(user_id, str) or not user_id.strip():
                raise ConfigError(f"{label}.user_id: attesa una stringa non vuota.")
            if user_id in seen:
                raise ConfigError(f"{label}.user_id duplicato: {user_id}")
            seen.add(user_id)
            display_name = item.get("display_name")
            phone = item.get("phone")
            if display_name is not None and not isinstance(display_name, str):
                raise ConfigError(f"{label}.display_name: attesa una stringa.")
            if phone is not None and not isinstance(phone, str):
                raise ConfigError(f"{label}.phone: attesa una stringa.")
            contacts.append(
                Contact(
                    user_id=user_id.strip(),
                    display_name=display_name.strip() if display_name else None,
                    phone=phone.strip() if phone else None,
                )
            )

        quiet = block.get("quiet_hours", {})
        if not isinstance(quiet, dict):
            raise ConfigError(f"{path}: whatsapp.quiet_hours deve essere un oggetto.")
        quiet_start = _parse_clock(
            quiet.get("start", DEFAULT_QUIET_HOURS[0]),
            f"{path}: whatsapp.quiet_hours.start",
        )
        quiet_end = _parse_clock(
            quiet.get("end", DEFAULT_QUIET_HOURS[1]),
            f"{path}: whatsapp.quiet_hours.end",
        )

        max_per_day = block.get("max_per_day", DEFAULT_MAX_PER_DAY)
        if isinstance(max_per_day, bool) or not isinstance(max_per_day, int):
            raise ConfigError(f"{path}: whatsapp.max_per_day deve essere un intero.")
        if not 0 <= max_per_day <= 24:
            raise ConfigError(f"{path}: whatsapp.max_per_day deve stare tra 0 e 24.")

        return cls(
            enabled=enabled,
            allowed_contacts=tuple(contacts),
            quiet_start=quiet_start,
            quiet_end=quiet_end,
            max_per_day=max_per_day,
        )


def _in_window(now: time, start: time, end: time) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


class MessageExecutor:
    """Consuma un impulso e consegna il messaggio, oppure dice perché no."""

    def __init__(
        self, root: str | Path | None = None, *, at: datetime | None = None
    ) -> None:
        self.config = ConfigStore(root)
        self.root = self.config.root
        self.routine = RoutineEngine(self.root, at=at)
        self.now = self.routine.now
        self.state = self.routine.state
        self.policy = WhatsAppPolicy.load(self.root)
        self.social = self.config.get("social_graph")

    # -- lettura dello stato ------------------------------------------------

    def pending_impulse(self) -> dict[str, Any] | None:
        heartbeat = self.state.get("heartbeat")
        if not isinstance(heartbeat, dict):
            return None
        impulse = heartbeat.get("pending_impulse")
        return impulse if isinstance(impulse, dict) else None

    def outbox_path(self) -> Path:
        return self.root / "data" / OUTBOX_DIR / OUTBOX_FILENAME

    def delivered_today(self) -> int:
        """Quante consegne ci sono già oggi, lette dall'outbox."""

        path = self.outbox_path()
        if not path.exists():
            return 0
        today = self.now.date().isoformat()
        count = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    stamp = record.get("delivered_at")
                    if isinstance(stamp, str) and stamp.startswith(today):
                        count += 1
        except OSError as exc:
            raise ConfigError(f"Impossibile leggere {path}: {exc}") from exc
        return count

    # -- controlli ----------------------------------------------------------

    @staticmethod
    def _clean_text(text: Any) -> tuple[str | None, str | None]:
        if not isinstance(text, str):
            return None, "testo: attesa una stringa."
        cleaned = text.strip()
        if not cleaned:
            return None, "testo: il messaggio è vuoto."
        if len(cleaned) > MAX_MESSAGE_CHARS:
            return None, f"testo: massimo {MAX_MESSAGE_CHARS} caratteri."
        if "\x00" in cleaned:
            return None, "testo: contiene caratteri non ammessi."
        return cleaned, None

    def _person(self, user_id: str) -> dict[str, Any] | None:
        people = self.social.get("people")
        if not isinstance(people, list):
            return None
        for person in people:
            if isinstance(person, dict) and person.get("user_id") == user_id:
                return person
        return None

    def authorize(self, text: Any) -> dict[str, Any]:
        """Verifica ogni condizione e restituisce il motivo di un eventuale blocco."""

        checks: list[dict[str, Any]] = []

        def check(name: str, ok: bool, detail: str | None = None) -> None:
            checks.append({"check": name, "ok": ok, "detail": detail})

        cleaned, text_error = self._clean_text(text)
        check("testo", text_error is None, text_error)

        impulse = self.pending_impulse()
        check("impulso", impulse is not None, "nessun impulso in attesa")

        channel = None
        target: dict[str, Any] | None = None
        if impulse is not None:
            delivery = impulse.get("delivery")
            delivery = delivery if isinstance(delivery, dict) else {}
            channel = delivery.get("channel")
            check(
                "canale",
                channel == CHANNEL,
                None
                if channel == CHANNEL
                else f"il canale {channel!r} non consegna messaggi",
            )
            raw_target = impulse.get("target")
            target = raw_target if isinstance(raw_target, dict) else None
            user_id = target.get("user_id") if target else None
            check(
                "destinatario",
                isinstance(user_id, str) and bool(user_id),
                "l'impulso non indica un destinatario",
            )

            person = self._person(user_id) if isinstance(user_id, str) else None
            check(
                "relazione",
                person is not None,
                f"{user_id!r} non è nel grafo delle relazioni"
                if isinstance(user_id, str)
                else None,
            )

            contact = (
                self.policy.contact_for(user_id) if isinstance(user_id, str) else None
            )
            check(
                "whitelist",
                contact is not None,
                f"{user_id!r} non è nella whitelist dei contatti"
                if isinstance(user_id, str)
                else None,
            )

            flags = self.state.get("flags")
            flags = flags if isinstance(flags, dict) else {}
            check(
                "abilitazione",
                bool(self.policy.enabled and flags.get("whatsapp_sending_enabled")),
                "integrazione whatsapp spenta o invio non autorizzato dal profilo",
            )
            check(
                "manutenzione",
                not bool(flags.get("maintenance_mode")),
                "manutenzione attiva",
            )

            availability = self.state.get("availability")
            availability = availability if isinstance(availability, dict) else {}
            check(
                "disponibilita",
                bool(availability.get("accepting_messages", False)),
                "l'agente non accetta messaggi in questo momento",
            )

            quiet = _in_window(
                self.now.time(),
                time.fromisoformat(self.policy.quiet_start),
                time.fromisoformat(self.policy.quiet_end),
            )
            check(
                "orario",
                not quiet,
                f"finestra di silenzio {self.policy.quiet_start}-{self.policy.quiet_end}",
            )

            created = impulse.get("created_at")
            age = self._impulse_age_minutes(created)
            fresh = (
                age is not None
                and -FUTURE_TOLERANCE_MINUTES <= age <= MAX_IMPULSE_AGE_MINUTES
            )
            if fresh:
                freshness = None
            elif age is None:
                freshness = "impulso senza data di creazione"
            elif age < 0:
                freshness = "impulso datato nel futuro: controlla l'orologio"
            else:
                freshness = "impulso troppo vecchio: va rigenerato"
            check("freschezza", fresh, freshness)

            delivered = self.delivered_today()
            check(
                "limite_giornaliero",
                delivered < self.policy.max_per_day,
                f"limite di {self.policy.max_per_day} consegne al giorno raggiunto",
            )

        reasons = [
            item["detail"] for item in checks if not item["ok"] and item["detail"]
        ]
        return {
            "allowed": all(item["ok"] for item in checks),
            "reasons": reasons,
            "checks": checks,
            "channel": channel,
            "target": target,
            "text": cleaned,
            "text_length": len(cleaned) if cleaned else 0,
        }

    def _impulse_age_minutes(self, created: Any) -> float | None:
        if not isinstance(created, str):
            return None
        try:
            moment = datetime.fromisoformat(created)
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self.now.tzinfo)
        return (self.now - moment).total_seconds() / 60.0

    # -- consegna -----------------------------------------------------------

    def _append_outbox(self, record: dict[str, Any]) -> None:
        path = self.outbox_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise ConfigError(f"Impossibile scrivere in {path}: {exc}") from exc

    def deliver(self, text: Any) -> dict[str, Any]:
        """Consegna il messaggio oppure solleva un errore con tutti i motivi."""

        report = self.authorize(text)
        if not report["allowed"]:
            raise AccessDeniedError(
                "Consegna bloccata: " + "; ".join(report["reasons"])
            )

        impulse = self.pending_impulse()
        assert impulse is not None  # garantito da authorize
        target = report["target"] or {}
        contact = self.policy.contact_for(target.get("user_id", ""))
        stamp = self.now.replace(microsecond=0).isoformat()
        record = {
            "message_id": f"msg_{uuid.uuid4().hex[:12]}",
            "delivered_at": stamp,
            "channel": CHANNEL,
            "transport": TRANSPORT,
            "target": {
                "user_id": target.get("user_id"),
                "display_name": target.get("display_name"),
                "phone": contact.phone if contact else None,
            },
            "trigger": impulse.get("trigger"),
            "impulse": impulse,
            "text": report["text"],
        }

        # Prima la consegna, poi la chiusura dell'impulso: se la scrittura
        # fallisce, l'impulso resta in coda invece di sparire.
        self._append_outbox(record)

        self.config.update_current_state(
            {
                "heartbeat": {
                    "pending_impulse": None,
                    "spontaneous_message_pending": False,
                    "last_decision": "impulso_consegnato",
                    "last_handled_at": stamp,
                    "last_handling_note": (
                        f"consegnato a "
                        f"{target.get('display_name') or target.get('user_id')}"
                    ),
                    "last_delivery_id": record["message_id"],
                    "last_delivered_at": stamp,
                }
            }
        )
        return {"delivered": True, **record}


def delivered_messages(root: str | Path | None = None) -> list[dict[str, Any]]:
    """Legge l'outbox, utile per diagnostica e test. Righe illeggibili saltate."""

    base = ConfigStore(root).root
    path = base / "data" / OUTBOX_DIR / OUTBOX_FILENAME
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


__all__ = [
    "MAX_MESSAGE_CHARS",
    "Contact",
    "MessageExecutor",
    "WhatsAppPolicy",
    "delivered_messages",
]
