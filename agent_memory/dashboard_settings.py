"""Impostazioni amministrative della dashboard.

La dashboard non è più soltanto una finestra sui dati: questa classe espone
le configurazioni che il resto del progetto legge a runtime. Le scritture
restano confinate a ``config/integrations.json``, ai soli campi modificabili
di ``config/current_state.json`` e all'archiviazione esplicita del profilo.

L'archiviazione sposta ``config``, ``data`` e ``files`` in
``backups/profile-...`` e conserva ``data/dashboard_tls`` e ``data/models``:
così certificato TLS e cache del modello non vengono persi, e la dashboard
resta raggiungibile dopo il riavvio. Il profilo archiviato non viene
cancellato: può essere ripristinato a mano, mentre la dashboard torna al
wizard di prima configurazione.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .browser import (
    DEFAULT_TIMEOUT_SECONDS,
    INTEGRATIONS_FILENAME,
    BrowserPolicy,
)
from .config import ConfigStore
from .errors import ConfigError, ValidationError
from .executor import WhatsAppPolicy
from .heartbeat import HeartbeatPolicy
from .migrations import migrate_integrations, write_json_atomic
from .profile import (
    PROFILE_STATE_READY,
    PROJECT_ROOT,
    profile_state,
)

ARCHIVE_CONFIRMATION = "ELIMINA"
SENDING_CONFIRMATION = "ABILITA"
PROFILE_ENTRIES = ("config", "data", "files")
# Cache condivisa e certificato non sono dati personali: restano nel profilo
# nuovo, come fa già ``profile init --force``.
PRESERVED_DATA = ("dashboard_tls", "models")
EDITABLE_FLAGS = (
    "visual_generation_enabled",
    "shadow_engine_enabled",
    "maintenance_mode",
)
LOCKED_FLAGS = ("shadow_direct_read_enabled",)


def _default_document() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "browser": {
            "enabled": False,
            "interaction_enabled": False,
            "service_workers": False,
            "user_agent": None,
            "allowed_origins": [],
            "navigation_timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        },
        "whatsapp": {
            "enabled": False,
            "allowed_contacts": [],
            "quiet_hours": {"start": "00:00", "end": "07:00"},
            "max_per_day": 3,
        },
    }


def _safe_int(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    return fallback


def _require_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValidationError(f"{key}: atteso true o false.")
    return value


def _require_int(
    payload: dict[str, Any], key: str, *, minimum: int, maximum: int
) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{key}: atteso un numero intero.")
    if not minimum <= value <= maximum:
        raise ValidationError(
            f"{key}: valore fuori intervallo [{minimum}, {maximum}]."
        )
    return value


class DashboardSettings:
    """Lettura e scrittura delle impostazioni del profilo attivo."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.config = ConfigStore(self.root)
        self.path = self.root / "config" / INTEGRATIONS_FILENAME

    def save_contact(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Crea o aggiorna un contatto senza alterarne storico e contatori."""
        action = payload.get("action")
        if not isinstance(action, str) or action not in {"create", "update", "remove"}:
            raise ValidationError("Azione contatto non valida.")
        user_id = payload.get("user_id")
        if not isinstance(user_id, str) or not user_id.strip() or len(user_id) > 100:
            raise ValidationError("user_id: attesa una stringa di 1–100 caratteri.")
        user_id = user_id.strip()
        with self.config._lock:
            graph = self.config.get("social_graph")
            person = next((p for p in graph["people"] if p["user_id"] == user_id), None)
            if action == "create" and person is not None:
                raise ValidationError("Esiste già un contatto con questo user_id.")
            if action != "create" and person is None:
                raise ValidationError("Contatto non trovato.")
            if action == "remove":
                if payload.get("confirmation") != "RIMUOVI":
                    raise ValidationError("Scrivi RIMUOVI per confermare.")
                graph["people"] = [p for p in graph["people"] if p["user_id"] != user_id]
            else:
                candidate = copy.deepcopy(person) if person is not None else {
                    "user_id": user_id,
                    "first_interaction_at": None,
                    "last_interaction_at": None,
                    "interaction_count": 0,
                    "interaction_history": [],
                }
                for key in ("display_name", "full_name", "relation", "notes"):
                    value = payload.get(key, candidate.get(key, ""))
                    if not isinstance(value, str) or len(value) > 2000:
                        raise ValidationError(f"{key}: attesa una stringa, massimo 2000 caratteri.")
                    candidate[key] = value.strip()
                if not candidate["display_name"]:
                    raise ValidationError("Il nome visualizzato è obbligatorio.")
                for key in ("trust", "intimacy"):
                    candidate[key] = _require_int(payload, key, minimum=0, maximum=100)
                for key in ("preferences", "confirmed_facts", "boundaries"):
                    value = payload.get(key, candidate.get(key, []))
                    if not isinstance(value, list) or len(value) > 100 or any(
                        not isinstance(item, str) or len(item) > 2000 for item in value
                    ):
                        raise ValidationError(f"{key}: attesa una lista di massimo 100 testi.")
                    candidate[key] = [item.strip() for item in value if item.strip()]
                if person is None:
                    graph["people"].append(candidate)
                else:
                    person.update(candidate)
            self.config.write("social_graph", graph)
        return {"saved": True, "action": action, "user_id": user_id}

    # -- lettura ------------------------------------------------------------

    def _read_integrations(self) -> dict[str, Any]:
        if not self.path.exists():
            return _default_document()
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                document = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"JSON non valido in {self.path}: riga {exc.lineno}, "
                f"colonna {exc.colno} ({exc.msg})"
            ) from exc
        except OSError as exc:
            raise ConfigError(f"Impossibile leggere {self.path}: {exc}") from exc
        if not isinstance(document, dict):
            raise ConfigError(f"{self.path}: atteso un oggetto JSON.")
        for block_name in ("browser", "whatsapp"):
            block = document.get(block_name)
            if block is not None and not isinstance(block, dict):
                raise ConfigError(
                    f"{self.path}: '{block_name}' deve essere un oggetto."
                )
        return migrate_integrations(document)

    def snapshot(self) -> dict[str, Any]:
        """Restituisce ciò che la vista Impostazioni può mostrare e modificare."""

        try:
            browser = BrowserPolicy.from_root(self.root)
            whatsapp = WhatsAppPolicy.load(self.root)
        except ConfigError as exc:
            raise ConfigError(
                f"Configurazione delle integrazioni non valida: {exc}"
            ) from exc

        state = self.config.get("current_state")
        identity = self.config.get("identity")
        personal = identity.get("personal_data", {})
        if not isinstance(personal, dict):
            personal = {}
        flags = state.get("flags", {})
        flags = flags if isinstance(flags, dict) else {}
        heartbeat = state.get("heartbeat", {})
        heartbeat = heartbeat if isinstance(heartbeat, dict) else {}
        status = profile_state(self.root)
        can_archive = self.root != PROJECT_ROOT and status == PROFILE_STATE_READY
        if self.root == PROJECT_ROOT:
            archive_blocked = (
                "La dashboard sta usando il dataset demo del repository: "
                "avviala su un profilo separato per poterlo archiviare."
            )
        elif status != PROFILE_STATE_READY:
            archive_blocked = "Il profilo non è completo."
        else:
            archive_blocked = ""

        return {
            "browser": {
                "enabled": browser.enabled,
                "interaction_enabled": browser.interaction_enabled,
                "interaction_origins": list(browser.interaction_origins),
                "service_workers": browser.service_workers,
                "user_agent": browser.user_agent,
                "allowed_origins": list(browser.allowed_origins),
                "navigation_timeout_seconds": browser.timeout_seconds,
                "session_saved": (
                    self.root / "data" / "browser" / "session.json"
                ).is_file(),
            },
            "whatsapp": {
                "enabled": whatsapp.enabled,
                "allowed_contacts": [
                    {
                        "user_id": contact.user_id,
                        "display_name": contact.display_name,
                        "phone": contact.phone,
                    }
                    for contact in whatsapp.allowed_contacts
                ],
                "quiet_hours": {
                    "start": whatsapp.quiet_start,
                    "end": whatsapp.quiet_end,
                },
                "max_per_day": whatsapp.max_per_day,
            },
            "behavior": {
                "flags": {
                    key: bool(flags.get(key, False)) for key in EDITABLE_FLAGS
                }
                | {
                    "whatsapp_sending_enabled": bool(
                        flags.get("whatsapp_sending_enabled", False)
                    ),
                    "shadow_direct_read_enabled": bool(
                        flags.get("shadow_direct_read_enabled", False)
                    ),
                },
                "heartbeat": {
                    field: _safe_int(heartbeat.get(field), fallback)
                    for field, fallback in (
                        ("interval_minutes", 30),
                        ("cooldown_minutes", 180),
                        ("max_impulses_per_day", 3),
                    )
                },
            },
            "profile": {
                "root": str(self.root),
                "identity_id": identity.get("identity_id"),
                "full_name": personal.get("full_name")
                or " ".join(
                    part
                    for part in (
                        personal.get("first_name"),
                        personal.get("last_name"),
                    )
                    if part
                )
                or "—",
                "status": status,
                "backup_dir": str(self.root / "backups"),
                "can_archive": can_archive,
                "archive_blocked": archive_blocked,
            },
        }

    # -- scrittura ----------------------------------------------------------

    def _validate_document(self, document: dict[str, Any]) -> None:
        """Valida il documento candidato con le stesse classi del runtime.

        Usa una directory temporanea per non lasciare un file a metà nel
        profilo se la validazione fallisce.
        """

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir(parents=True)
            write_json_atomic(config_dir / INTEGRATIONS_FILENAME, document)
            BrowserPolicy.from_root(tmp)
            WhatsAppPolicy.load(tmp)

    def _write_document(self, document: dict[str, Any]) -> None:
        self._validate_document(document)
        document["schema_version"] = 2
        write_json_atomic(self.path, document)

    def save_browser(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("Attesa una configurazione del browser.")
        enabled = _require_bool(payload, "enabled")
        interaction_enabled = _require_bool(payload, "interaction_enabled")
        service_workers = _require_bool(payload, "service_workers")
        if interaction_enabled and not enabled:
            raise ValidationError(
                "Abilita prima il browser, poi l'interazione."
            )

        user_agent = payload.get("user_agent")
        if user_agent is not None and not isinstance(user_agent, str):
            raise ValidationError("user_agent: attesa una stringa o null.")
        origins = payload.get("allowed_origins")
        if not isinstance(origins, list) or not all(
            isinstance(origin, str) for origin in origins
        ):
            raise ValidationError(
                "allowed_origins: attesa una lista di stringhe."
            )
        interaction_origins = payload.get("interaction_origins", [])
        if not isinstance(interaction_origins, list) or not all(isinstance(item, str) for item in interaction_origins):
            raise ValidationError("interaction_origins: attesa una lista di origini esatte.")
        previous = BrowserPolicy.from_root(self.root).interaction_origins
        if set(interaction_origins) - set(previous) and payload.get("interaction_confirmation") != "AUTORIZZO":
            raise ValidationError("Scrivi AUTORIZZO: le azioni dirette non sono coperte dai limiti dell'outbox.")
        timeout = payload.get("navigation_timeout_seconds")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValidationError(
                "navigation_timeout_seconds: atteso un numero."
            )

        document = copy.deepcopy(self._read_integrations())
        browser = document.get("browser")
        browser = dict(browser) if isinstance(browser, dict) else {}
        browser.update(
            {
                "enabled": enabled,
                "interaction_enabled": interaction_enabled,
                "interaction_origins": interaction_origins,
                "service_workers": service_workers,
                "user_agent": user_agent,
                "allowed_origins": [origin.strip() for origin in origins if origin.strip()],
                "navigation_timeout_seconds": timeout,
            }
        )
        document["browser"] = browser
        self._write_document(document)
        return self.snapshot()

    def save_whatsapp(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("Attesa una configurazione di WhatsApp.")
        enabled = _require_bool(payload, "enabled")
        max_per_day = _require_int(payload, "max_per_day", minimum=0, maximum=24)

        raw_contacts = payload.get("allowed_contacts")
        if not isinstance(raw_contacts, list):
            raise ValidationError(
                "allowed_contacts: attesa una lista di contatti."
            )
        contacts: list[dict[str, str]] = []
        seen: set[str] = set()
        for index, item in enumerate(raw_contacts):
            if not isinstance(item, dict):
                raise ValidationError(
                    f"allowed_contacts[{index}]: atteso un oggetto."
                )
            user_id = item.get("user_id")
            if not isinstance(user_id, str) or not user_id.strip():
                raise ValidationError(
                    f"allowed_contacts[{index}].user_id: attesa una stringa "
                    "non vuota."
                )
            user_id = user_id.strip()
            if user_id in seen:
                raise ValidationError(
                    f"allowed_contacts[{index}].user_id duplicato: {user_id}"
                )
            seen.add(user_id)
            contact: dict[str, str] = {"user_id": user_id}
            for field in ("display_name", "phone"):
                value = item.get(field)
                if value is None:
                    continue
                if not isinstance(value, str):
                    raise ValidationError(
                        f"allowed_contacts[{index}].{field}: attesa una stringa."
                    )
                if value.strip():
                    contact[field] = value.strip()
            contacts.append(contact)

        quiet = payload.get("quiet_hours")
        if not isinstance(quiet, dict):
            raise ValidationError("quiet_hours: atteso un oggetto.")
        start = quiet.get("start")
        end = quiet.get("end")
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValidationError(
                "quiet_hours: start ed end devono essere orari HH:MM."
            )

        document = copy.deepcopy(self._read_integrations())
        document["whatsapp"] = {
            "enabled": enabled,
            "allowed_contacts": contacts,
            "quiet_hours": {"start": start, "end": end},
            "max_per_day": max_per_day,
        }
        self._write_document(document)
        return self.snapshot()

    def save_behavior(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("Attesa una configurazione del comportamento.")
        raw_flags = payload.get("flags")
        raw_heartbeat = payload.get("heartbeat")
        if not isinstance(raw_flags, dict) or not isinstance(
            raw_heartbeat, dict
        ):
            raise ValidationError(
                "flags e heartbeat devono essere due oggetti."
            )

        for locked in LOCKED_FLAGS:
            if raw_flags.get(locked) is True:
                raise ValidationError(
                    f"{locked} resta bloccato: si modifica solo a mano nel "
                    "profilo, con una scelta esplicita dell'amministratore."
                )

        flags_patch: dict[str, bool] = {}
        for key in EDITABLE_FLAGS:
            if key in raw_flags:
                flags_patch[key] = _require_bool(raw_flags, key)

        state = self.config.get("current_state")
        current_flags = state.get("flags")
        current_flags = current_flags if isinstance(current_flags, dict) else {}
        current_sending = bool(current_flags.get("whatsapp_sending_enabled", False))
        if "whatsapp_sending_enabled" in raw_flags:
            requested_sending = _require_bool(
                raw_flags, "whatsapp_sending_enabled"
            )
            if requested_sending != current_sending:
                if requested_sending:
                    confirmation = payload.get("confirmation")
                    if (
                        not isinstance(confirmation, str)
                        or confirmation.strip() != SENDING_CONFIRMATION
                    ):
                        raise ValidationError(
                            "Per attivare l'invio WhatsApp scrivi "
                            f"{SENDING_CONFIRMATION}: l'executor potrà "
                            "consegnare messaggi ai contatti autorizzati."
                        )
                flags_patch["whatsapp_sending_enabled"] = requested_sending

        current_heartbeat = state.get("heartbeat")
        current_heartbeat = (
            dict(current_heartbeat)
            if isinstance(current_heartbeat, dict)
            else {}
        )
        heartbeat_patch: dict[str, int] = {}
        ranges = {
            "interval_minutes": (1, 24 * 60),
            "cooldown_minutes": (0, 7 * 24 * 60),
            "max_impulses_per_day": (0, 24),
        }
        for field, (minimum, maximum) in ranges.items():
            if field in raw_heartbeat:
                heartbeat_patch[field] = _require_int(
                    raw_heartbeat, field, minimum=minimum, maximum=maximum
                )
        candidate = copy.deepcopy(state)
        candidate["heartbeat"] = current_heartbeat | heartbeat_patch
        HeartbeatPolicy.from_state(candidate)

        patch: dict[str, Any] = {}
        if flags_patch:
            patch["flags"] = flags_patch
        if heartbeat_patch:
            patch["heartbeat"] = heartbeat_patch
        if not patch:
            raise ValidationError("Nessuna impostazione da salvare.")
        self.config.update_current_state(
            patch,
            allow_whatsapp_sending=(
                flags_patch.get("whatsapp_sending_enabled") is True
            ),
        )
        return self.snapshot()

    # -- ciclo di vita del profilo -----------------------------------------

    def archive_profile(self, confirmation: str) -> dict[str, Any]:
        """Archivia il profilo attivo e lascia la directory pronta a /setup."""

        if confirmation.strip() != ARCHIVE_CONFIRMATION:
            raise ValidationError(
                f"Scrivi {ARCHIVE_CONFIRMATION} per confermare l'archiviazione."
            )
        if self.root == PROJECT_ROOT:
            raise ConfigError(
                "La dashboard sta usando il dataset demo del repository: "
                "avviala su un profilo separato per poterlo archiviare."
            )
        if profile_state(self.root) != PROFILE_STATE_READY:
            raise ConfigError("Il profilo non è completo: non c'è nulla da archiviare.")

        backups = self.root / "backups"
        try:
            backups.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(f"Impossibile creare {backups}: {exc}") from exc

        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        archive = backups / f"profile-{stamp}"
        suffix = 1
        while archive.exists():
            suffix += 1
            archive = backups / f"profile-{stamp}-{suffix}"
        try:
            archive.mkdir()
        except OSError as exc:
            raise ConfigError(f"Impossibile creare {archive}: {exc}") from exc

        moved: list[str] = []
        try:
            for name in PROFILE_ENTRIES:
                source = self.root / name
                if not source.exists():
                    continue
                os.replace(source, archive / name)
                moved.append(name)

            data_dir = self.root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            for name in PRESERVED_DATA:
                preserved = archive / "data" / name
                if preserved.exists():
                    os.replace(preserved, data_dir / name)
        except OSError as exc:
            self._rollback_archive(archive, moved)
            raise ConfigError(
                f"Archiviazione incompleta: {exc}. Il profilo è stato "
                "ripristinato; controlla la directory prima di riprovare."
            ) from exc

        return {
            "ok": True,
            "archive": str(archive),
            "moved": moved,
            "redirect": "/setup",
        }

    def _rollback_archive(self, archive: Path, moved: list[str]) -> None:
        """Riporta indietro ciò che è stato spostato prima dell'errore."""

        data_dir = self.root / "data"
        archived_data = archive / "data"
        if archived_data.is_dir():
            for name in PRESERVED_DATA:
                restored = data_dir / name
                if restored.exists():
                    try:
                        os.replace(restored, archived_data / name)
                    except OSError:
                        pass
        if data_dir.is_dir():
            try:
                next(data_dir.iterdir())
            except StopIteration:
                try:
                    data_dir.rmdir()
                except OSError:
                    pass
            except OSError:
                pass
        for name in reversed(moved):
            destination = archive / name
            source = self.root / name
            if destination.exists() and not source.exists():
                try:
                    os.replace(destination, source)
                except OSError:
                    continue
        try:
            next(archive.iterdir())
        except StopIteration:
            shutil.rmtree(archive, ignore_errors=True)
        except OSError:
            pass
