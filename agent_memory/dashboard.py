"""Dashboard amministrativa locale.

Il modulo espone una piccola applicazione HTTP su loopback che legge i
database e i file di configurazione già usati dai servizi. Le scritture
applicative sono la creazione iniziale del profilo, la generazione della
biografia e dell'indice vettoriale dal passaggio finale del wizard, le
impostazioni di browser, WhatsApp e comportamento, la gestione dei contatti
e l'archiviazione esplicita del profilo. Biografia e indice vengono eseguiti
in un thread di background: la pagina di completamento segue lo stato con
polling. La dashboard permette anche la gestione amministrativa dei tratti
dell'ombra e del profilo visivo, con conferme per le operazioni esterne.
Ricordi e conoscenze non vengono modificati dalle altre viste. La rimozione di un
contatto elimina il suo storico relazionale, non i ricordi episodici.

Uso:
    python -m agent_memory.dashboard
    python -m agent_memory.dashboard --port 8765 --open

La dashboard contiene anche i tratti dell'ombra, quindi per impostazione
predefinita ascolta solo su 127.0.0.1 e non deve essere pubblicata su Internet.
"""

from __future__ import annotations

import argparse
import html
import json
import secrets
import sys
import threading
import time
import traceback
import webbrowser
from collections.abc import Iterable
from datetime import date, datetime
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .autonomy import AutonomyService
from .biography import BiographyService
from .config import project_root
from .dashboard_auth import SESSION_COOKIE, DashboardAuth, resolve_auth
from .dashboard_settings import ARCHIVE_CONFIRMATION, DashboardSettings
from .dashboard_tls import DashboardTLS, ensure_dashboard_tls
from .db import decode_memory
from .errors import AgentMemoryError, ConfigError, NotFoundError, ValidationError
from .evolution import EvolutionService
from .executor import delivered_messages
from .files import SANDBOX_DIRNAME, FileSandbox
from .profile import (
    PROFILE_STATE_INCOMPLETE,
    PROFILE_STATE_MISSING,
    PROFILE_STATE_READY,
    _profile_lock,
    build_profile_args,
    create_profile,
    profile_state,
)
from .retrieval import RetrievalService
from .semantic import KNOWLEDGE_TYPES, SEMANTIC_STATUSES, SemanticService
from .service import MEMORY_SOURCES, MemoryService, ShadowService
from .setup_options import (
    COUNTRIES,
    GENDER_OPTIONS,
    ITALIAN_PROVINCES,
    ITALIAN_REGIONS,
    LANGUAGE_LEVEL_OPTIONS,
    LANGUAGES,
    PRONOUN_OPTIONS,
    timezone_groups,
)
from .visual import VisualService

ASSETS_DIR = Path(__file__).resolve().parent / "dashboard_assets"
ACCESS_LEVELS = {"conscious", "latent", "repressed"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
MAX_TIMELINE_ROWS = 500
MAX_SEMANTIC_ROWS = 100
MAX_FORM_BYTES = 64 * 1024
LOGIN_PATH = "/login"
LOGOUT_PATH = "/logout"
SETUP_PATH = "/setup"
FINALIZE_PATH = "/setup/finalize"
FINALIZE_STATUS_PATH = "/setup/finalize/status"
SESSION_PATH = "/api/session"
RELATIONS_PATH = "/api/relations"
SHADOW_TRAITS_PATH = "/api/traits/shadow"
SETTINGS_PATH = "/api/settings"
SETTINGS_BROWSER_PATH = "/api/settings/browser"
SETTINGS_WHATSAPP_PATH = "/api/settings/whatsapp"
SETTINGS_BEHAVIOR_PATH = "/api/settings/behavior"
PROFILE_ARCHIVE_PATH = "/api/profile/archive"
AUTONOMY_REVIEW_PATH = "/api/autonomy/review"
VISUAL_PROFILE_PATH = "/api/visual/profile"
VISUAL_REFERENCE_PATH = "/api/visual/reference"
VISUAL_REFERENCE_REMOVE_PATH = "/api/visual/reference/remove"
VISUAL_GENERATE_PATH = "/api/visual/generate"
VISUAL_IMAGE_DELETE_PATH = "/api/visual/image/delete"
VISUAL_TOKEN_CHECK_PATH = "/api/visual/token/check"
VISUAL_POST_PATHS = {
    VISUAL_PROFILE_PATH,
    VISUAL_REFERENCE_REMOVE_PATH,
    VISUAL_GENERATE_PATH,
    VISUAL_IMAGE_DELETE_PATH,
    VISUAL_TOKEN_CHECK_PATH,
}
SETTINGS_POST_PATHS = {
    RELATIONS_PATH,
    SHADOW_TRAITS_PATH,
    SETTINGS_BROWSER_PATH,
    SETTINGS_WHATSAPP_PATH,
    SETTINGS_BEHAVIOR_PATH,
    PROFILE_ARCHIVE_PATH,
    AUTONOMY_REVIEW_PATH,
}
FORM_TOKEN_TTL = 30 * 60
FINALIZE_ACTIONS = ("biography", "index")
FINALIZE_LABELS = {
    "biography": "Biografia",
    "index": "Indice vettoriale",
}
FINALIZE_RUNNING_TEXT = {
    "biography": "Generazione dei ricordi biografici in corso…",
    "index": (
        "Costruzione dell'indice in corso. La prima esecuzione può "
        "richiedere qualche minuto per caricare il modello locale."
    ),
}


class _FinalizeJob:
    """Stato condiviso di un completamento eseguito in background."""

    def __init__(self, actions: Iterable[str]) -> None:
        selected = set(actions)
        ordered = tuple(
            action for action in FINALIZE_ACTIONS if action in selected
        )
        if not ordered:
            raise ValidationError("Nessuna operazione di completamento scelta.")
        self.id = secrets.token_urlsafe(9)
        self.actions = ordered
        self._started_at = time.monotonic()
        self._finished_at: float | None = None
        self._state = "running"
        self._steps = [
            {
                "key": action,
                "label": FINALIZE_LABELS[action],
                "state": "pending",
                "text": "In attesa.",
            }
            for action in ordered
        ]
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        with self._lock:
            return self._state == "running"

    def mark_running(self, action: str) -> None:
        with self._lock:
            step = self._step(action)
            step["state"] = "running"
            step["text"] = FINALIZE_RUNNING_TEXT[action]

    def mark_finished(self, action: str, result: dict[str, Any]) -> None:
        state, text = _finalize_result_view(action, result)
        with self._lock:
            step = self._step(action)
            step["state"] = "success" if state == "is-success" else "error"
            step["text"] = text

    def fail(self, action: str, message: str) -> None:
        with self._lock:
            step = self._step(action)
            step["state"] = "error"
            step["text"] = message

    def finish(self, error: str = "") -> None:
        with self._lock:
            for step in self._steps:
                if step["state"] in {"pending", "running"}:
                    step["state"] = "error"
                    step["text"] = error or "Operazione interrotta."
            self._state = "done"
            self._finished_at = time.monotonic()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._finished_at or time.monotonic()
            completed = sum(
                step["state"] in {"success", "error"} for step in self._steps
            )
            running = any(step["state"] == "running" for step in self._steps)
            if self._state == "done":
                progress = 100
            else:
                progress = min(
                    95,
                    round(
                        (completed + (0.45 if running else 0.0))
                        / len(self._steps)
                        * 100
                    ),
                )
            return {
                "id": self.id,
                "state": self._state,
                "ok": self._state == "done"
                and all(step["state"] == "success" for step in self._steps),
                "progress": progress,
                "elapsed": round(now - self._started_at, 1),
                "steps": [dict(step) for step in self._steps],
            }

    def _step(self, action: str) -> dict[str, Any]:
        for step in self._steps:
            if step["key"] == action:
                return step
        raise ValidationError(f"Azione di completamento non valida: {action}.")


class _FormTokenStore:
    """Token CSRF monouso per i moduli senza sessione autenticata."""

    def __init__(self, ttl: int = FORM_TOKEN_TTL) -> None:
        self._ttl = ttl
        self._tokens: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(self) -> str:
        token = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            self._tokens = {
                key: expires
                for key, expires in self._tokens.items()
                if expires > now
            }
            self._tokens[token] = now + self._ttl
        return token

    def consume(self, token: str | None) -> bool:
        if not token:
            return False
        now = time.monotonic()
        with self._lock:
            expires = self._tokens.pop(token, None)
            return expires is not None and expires > now


def _finalize_actions_from_form(form: dict[str, str]) -> list[str]:
    actions: list[str] = []
    if form.get("generate_biography") == "1":
        actions.append("biography")
    if form.get("build_index") == "1":
        actions.append("index")
    return actions


def _option_tag(
    value: str, label: str, selected: str, *, data_code: str = ""
) -> str:
    mark = " selected" if value == selected else ""
    escaped = html.escape(value, quote=True)
    code = (
        f' data-code="{html.escape(data_code, quote=True)}"'
        if data_code
        else ""
    )
    return (
        f'<option value="{escaped}"{code}{mark}>'
        f"{html.escape(label)}</option>"
    )


def _select_options(
    options: Iterable[tuple[str, str]], selected: str
) -> str:
    entries = list(options)
    if selected and all(value != selected for value, _ in entries):
        # Un valore non più in elenco (per esempio un profilo precedente)
        # resta visibile invece di essere sostituito in silenzio.
        entries.append((selected, selected))
    return "".join(
        _option_tag(value, label, selected) for value, label in entries
    )


def _country_options(selected: str, *, empty_label: str = "") -> str:
    parts: list[str] = []
    if empty_label:
        parts.append(_option_tag("", empty_label, selected))
    # Il valore inviato è il nome italiano: è quello che finisce in
    # identity.json, mentre il codice ISO viene ricavato dal server. Il
    # codice resta disponibile nel DOM per caricare le suddivisioni estere.
    parts.extend(
        _option_tag(name, name, selected, data_code=code)
        for code, name in COUNTRIES
    )
    if selected and all(name != selected for _code, name in COUNTRIES):
        parts.append(_option_tag(selected, selected, selected))
    return "".join(parts)


def _region_options(selected: str) -> str:
    """Menu delle regioni italiane; la voce vuota è una scelta esplicita."""

    parts = [_option_tag("", "Non indicata", selected)]
    parts.extend(
        _option_tag(region, region, selected) for region in ITALIAN_REGIONS
    )
    return "".join(parts)


def _province_options(selected: str) -> str:
    """Menu delle province italiane, raggruppate per regione."""

    selected_code = selected.strip().upper()
    parts = [_option_tag("", "Non indicata", selected_code)]
    current_region = ""
    for code, name, region in ITALIAN_PROVINCES:
        if region != current_region:
            if current_region:
                parts.append("</optgroup>")
            parts.append(f'<optgroup label="{html.escape(region)}">')
            current_region = region
        mark = " selected" if code == selected_code else ""
        parts.append(
            f'<option value="{code}" data-region="{html.escape(region, quote=True)}"'
            f"{mark}>{html.escape(name)} ({code})</option>"
        )
    if current_region:
        parts.append("</optgroup>")
    return "".join(parts)


def _language_options(selected: str = "") -> str:
    return _select_options(
        (
            (code, f"{name[:1].upper()}{name[1:]} ({code})")
            for code, name in LANGUAGES
        ),
        selected,
    )


def _timezone_options(selected: str) -> str:
    groups = timezone_groups()
    known = {zone for _, zones in groups for zone in zones}
    parts: list[str] = []
    if selected and selected not in known:
        parts.append(_option_tag(selected, selected, selected))
    for label, zones in groups:
        parts.append(f'<optgroup label="{html.escape(label)}">')
        parts.extend(_option_tag(zone, zone, selected) for zone in zones)
        parts.append("</optgroup>")
    return "".join(parts)


def _biography_result_view(result: dict[str, Any]) -> tuple[str, str]:
    if result.get("ok"):
        data = result.get("result") or {}
        return (
            "is-success",
            (
                f"{data.get('created', 0)} ricordi creati, "
                f"{data.get('skipped', 0)} già presenti, "
                f"{data.get('total', 0)} in timeline."
            ),
        )
    return "is-error", str(result.get("error") or "Errore nella biografia.")


def _index_payload_view(payload: dict[str, Any] | None) -> tuple[str, str]:
    payload = payload or {}
    indexes = payload.get("indexes") or {}
    parts = []
    for kind, label in (("episodic", "ricordi"), ("semantic", "conoscenze")):
        item = indexes.get(kind) or {}
        count = item.get("count", item.get("indexed", 0))
        parts.append(f"{label}: {count}")
    counts = ", ".join(parts)
    items = list(indexes.values())
    statuses = {str(item.get("status", "")) for item in items}
    has_ready = any(
        item.get("status") == "ready" and int(item.get("count", 0)) > 0
        for item in items
    )
    has_error = any(
        item.get("error")
        or item.get("status") in {"stale", "inconsistent"}
        for item in items
    )
    # Un archivio semantico vuoto non deve impedire di considerare
    # pronto l'indice episodico appena costruito.
    if payload.get("ok") or (has_ready and not has_error):
        return "is-success", f"Indice pronto ({counts})."
    issues: list[str] = []
    for item in items:
        for issue in item.get("issues") or []:
            text = str(issue)
            if text not in issues:
                issues.append(text)
        if item.get("error"):
            text = str(item["error"])
            if text not in issues:
                issues.append(text)
    if not items or statuses == {"empty"}:
        return (
            "is-pending",
            "Indice non ancora pronto. Esegui la costruzione.",
        )
    detail = "; ".join(issues[:3])
    return (
        "is-error" if has_error else "is-pending",
        "Indice non pronto. " + (detail or "Esegui la costruzione."),
    )


def _index_result_view(result: dict[str, Any]) -> tuple[str, str]:
    if not result.get("ok"):
        return "is-error", str(result.get("error") or "Errore nell'indice.")
    return _index_payload_view(result.get("result") or {})


def _format_index_errors(result: dict[str, Any]) -> str:
    errors: list[str] = []
    for kind, item in (result.get("indexes") or {}).items():
        if item.get("error"):
            errors.append(f"{kind}: {item['error']}")
        for issue in item.get("issues") or []:
            errors.append(f"{kind}: {issue}")
    return "; ".join(errors) or "Costruzione dell'indice non riuscita."


def _finalize_result_view(
    action: str, result: dict[str, Any]
) -> tuple[str, str]:
    if action == "biography":
        return _biography_result_view(result)
    if action == "index":
        return _index_result_view(result)
    raise ValidationError(f"Azione di completamento non valida: {action}.")


def _step_state_class(state: str) -> str:
    return {
        "pending": "is-pending",
        "running": "is-running",
        "success": "is-success",
        "error": "is-error",
    }.get(state, "is-pending")


def _finalize_button_label(action: str, state: str) -> str:
    if action == "biography":
        if state == "success":
            return "Rigenera biografia"
        if state == "running":
            return "Generazione in corso…"
        return "Genera biografia"
    if state == "success":
        return "Ricostruisci indice"
    if state == "running":
        return "Costruzione in corso…"
    return "Costruisci indice"


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def _origin_key(value: str) -> tuple[str, str, int]:
    """Normalizza un'origine in (schema, host, porta)."""

    try:
        parsed = urlparse(value.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "attesa un'origine nella forma http(s)://host[:porta]"
        )
    return parsed.scheme, host.lower(), port or _default_port(parsed.scheme)


def _origin_matches_authority(origin: Any, authority: str | None) -> bool:
    if not authority:
        return False
    try:
        candidate = urlparse(f"//{authority.strip()}")
        origin_host = origin.hostname
        candidate_host = candidate.hostname
        origin_port = origin.port
        candidate_port = candidate.port
    except ValueError:
        return False
    if not candidate_host or not origin_host:
        return False
    if origin_host.lower() != candidate_host.lower():
        return False
    expected_port = origin_port or _default_port(origin.scheme)
    actual_port = candidate_port or _default_port(origin.scheme)
    return expected_port == actual_port


def _same_origin(
    origin: str | None,
    host: str | None,
    forwarded_host: str | None = None,
    trusted_origins: Iterable[str] = (),
    allow_null_origin: bool = False,
) -> bool:
    """Verifica l'Origin anche dietro un reverse proxy che riscrive Host."""

    if not origin:
        return True
    if origin.strip().lower() == "null":
        return allow_null_origin
    try:
        parsed = urlparse(origin)
        origin_host = parsed.hostname
        origin_key = _origin_key(origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not origin_host:
        return False
    authorities: list[str] = []
    if forwarded_host:
        authorities.extend(
            item.strip() for item in forwarded_host.split(",") if item.strip()
        )
    if host:
        authorities.append(host)
    if any(_origin_matches_authority(parsed, item) for item in authorities):
        return True
    for trusted in trusted_origins:
        try:
            if _origin_key(trusted) == origin_key:
                return True
        except ValueError:
            continue
    return False


def _like_pattern(value: str) -> str:
    """Crea un pattern LIKE letterale, con ESCAPE '\\'."""

    escaped = (
        value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


def _as_int(value: str | None, default: int, field: str) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field}: atteso un numero intero.") from exc


def _age_on(birth_date: str, today: date) -> int:
    born = date.fromisoformat(birth_date)
    return today.year - born.year - (
        (today.month, today.day) < (born.month, born.day)
    )


def _loads_snapshot(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        data = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


class _DashboardServices:
    """Servizi caricati solo quando il profilo è pronto."""

    def __init__(self, root: Path) -> None:
        self.memory = MemoryService(root)
        self.semantic = SemanticService(root)
        self.shadow = ShadowService(root)
        self.evolution_service = EvolutionService(root)


class DashboardData:
    """Letture aggregate per l'interfaccia amministrativa."""

    def __init__(self, root: str | Path | None = None):
        self.root = (
            Path(root).expanduser().resolve() if root is not None else project_root()
        )
        self._services: _DashboardServices | None = None
        self._services_lock = threading.Lock()

    def profile_status(self) -> str:
        return profile_state(self.root)

    def reload(self) -> None:
        with self._services_lock:
            self._services = None

    def _ensure_services(self) -> _DashboardServices:
        services = self._services
        if services is None:
            with self._services_lock:
                if self._services is None:
                    self._services = _DashboardServices(self.root)
                services = self._services
        return services

    @property
    def memory(self) -> MemoryService:
        return self._ensure_services().memory

    @property
    def semantic(self) -> SemanticService:
        return self._ensure_services().semantic

    @property
    def shadow(self) -> ShadowService:
        return self._ensure_services().shadow

    @property
    def evolution_service(self) -> EvolutionService:
        return self._ensure_services().evolution_service

    # -- panoramica ---------------------------------------------------------

    @staticmethod
    def _text_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [
            item.strip()
            for item in value
            if isinstance(item, str) and item.strip()
        ]

    def _contacts(self, *, include_details: bool = False) -> list[dict[str, Any]]:
        graph = self.memory.get_social_graph()
        people = graph.get("people")
        contacts: list[dict[str, Any]] = []
        if not isinstance(people, list):
            return contacts
        for person in people:
            if not isinstance(person, dict):
                continue
            user_id = person.get("user_id")
            if not isinstance(user_id, str) or not user_id:
                continue
            count = person.get("interaction_count", 0)
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                count = 0
            history = self.memory.config.operational.history(user_id, limit=1)["entries"]
            last_summary = ""
            if history:
                summary = history[0].get("summary")
                if isinstance(summary, str):
                    last_summary = summary
            contact: dict[str, Any] = {
                "user_id": user_id,
                "display_name": person.get("display_name") or user_id,
                "relation": person.get("relation"),
                "trust": person.get("trust"),
                "intimacy": person.get("intimacy"),
                "last_interaction_at": person.get("last_interaction_at"),
                "last_interaction_summary": last_summary,
                "interaction_count": count,
            }
            if include_details:
                contact.update(
                    {
                        "full_name": person.get("full_name"),
                        "first_interaction_at": person.get(
                            "first_interaction_at"
                        ),
                        "preferences": self._text_list(
                            person.get("preferences")
                        ),
                        "confirmed_facts": self._text_list(
                            person.get("confirmed_facts")
                        ),
                        "boundaries": self._text_list(
                            person.get("boundaries")
                        ),
                        "notes": person.get("notes"),
                        "interactions": history,
                    }
                )
            contacts.append(contact)
        contacts.sort(
            key=lambda contact: str(contact["last_interaction_at"] or ""),
            reverse=True,
        )
        return contacts

    def _outbox_summary(self, *, limit: int = 5) -> dict[str, Any]:
        try:
            messages = delivered_messages(self.root)
        except AgentMemoryError:
            messages = []
        recent: list[dict[str, Any]] = []
        for item in reversed(messages):
            if not isinstance(item, dict):
                continue
            target = item.get("target")
            target = target if isinstance(target, dict) else {}
            recent.append(
                {
                    "message_id": item.get("message_id"),
                    "delivered_at": item.get("delivered_at"),
                    "channel": item.get("channel"),
                    "trigger": item.get("trigger"),
                    "target_user_id": target.get("user_id"),
                    "target_name": target.get("display_name")
                    or target.get("user_id"),
                    "text": item.get("text"),
                }
            )
            if len(recent) >= limit:
                break
        return {
            "count": len(messages),
            "recent": recent,
        }

    def _files_summary(self, *, limit: int = 10) -> dict[str, Any]:
        sandbox_dir = self.root / SANDBOX_DIRNAME
        if not sandbox_dir.exists() or sandbox_dir.is_symlink() or not sandbox_dir.is_dir():
            return {"count": 0, "truncated": False, "entries": []}
        try:
            listing = FileSandbox(self.root).list_files(limit=limit)
            total = sum(1 for _ in sandbox_dir.iterdir())
        except (AgentMemoryError, OSError):
            return {"count": 0, "truncated": False, "entries": []}
        return {
            "count": total,
            "truncated": total > len(listing["entries"]),
            "entries": listing["entries"],
        }

    def relations(self, *, offset: int = 0, limit: int = 50, user_id: str | None = None) -> dict[str, Any]:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValidationError("offset deve essere un intero non negativo.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValidationError("limit deve essere tra 1 e 100.")
        contacts = self._contacts(include_details=True)
        for contact in contacts:
            page = self.memory.config.operational.history(contact["user_id"], offset=offset, limit=limit) if user_id is None or contact["user_id"] == user_id else {"total": self.memory.config.operational.history(contact["user_id"], limit=1)["total"], "entries": []}
            contact["history_total"] = page["total"]
            contact["history_offset"] = offset
            contact["history_limit"] = limit
            contact["interactions"] = page["entries"]
        return {
            "generated_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "count": len(contacts),
            "total_interactions": sum(
                contact["interaction_count"] for contact in contacts
            ),
            "contacts": contacts,
        }

    def overview(self) -> dict[str, Any]:
        identity = self.memory.get_identity()
        personal = identity.get("personal_data", {})
        personality = identity.get("personality", {})
        state = self.memory.get_current_state()

        with self.memory.db.timeline() as conn:
            access_rows = conn.execute(
                "SELECT access_level, COUNT(*) AS total "
                "FROM episodic_memories GROUP BY access_level"
            ).fetchall()
            timeline_total = conn.execute(
                "SELECT COUNT(*) FROM episodic_memories"
            ).fetchone()[0]
            last_updated = conn.execute(
                "SELECT MAX(updated_at) FROM episodic_memories"
            ).fetchone()[0]
            memory_cascades = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT cascade_id FROM memory_revisions "
                    "WHERE cascade_id IS NOT NULL"
                ).fetchall()
            }

        with self.semantic.db.semantic() as conn:
            status_rows = conn.execute(
                "SELECT status, COUNT(*) AS total "
                "FROM semantic_items GROUP BY status"
            ).fetchall()
            semantic_total = conn.execute(
                "SELECT COUNT(*) FROM semantic_items"
            ).fetchone()[0]
            semantic_cascades = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT cascade_id FROM semantic_revisions "
                    "WHERE cascade_id IS NOT NULL"
                ).fetchall()
            }

        with self.shadow.db.shadow() as conn:
            shadow_entries = conn.execute(
                "SELECT COUNT(*) FROM shadow_entries"
            ).fetchone()[0]

        access_counts = {
            row["access_level"]: int(row["total"]) for row in access_rows
        }
        status_counts = {
            row["status"]: int(row["total"]) for row in status_rows
        }
        history = self.evolution_service.history().get("changes", [])
        recent_memories = self.timeline(limit=5)["memories"]
        outbox = self._outbox_summary()
        autonomy = AutonomyService(self.root, None).review_summary()
        files = self._files_summary()

        return {
            "generated_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "identity": {
                "identity_id": identity.get("identity_id"),
                "full_name": personal.get("full_name"),
                "first_name": personal.get("first_name"),
                "age": _age_on(personal["birth_date"], date.today())
                if personal.get("birth_date")
                else None,
                "birth_date": personal.get("birth_date"),
                "birth_place": personal.get("birth_place", {}),
                "residence": personal.get("residence", {}),
                "timezone": personal.get("timezone"),
            },
            "state": {
                "operational_status": state.get("operational_status"),
                "clock": state.get("clock", {}),
                "activity": state.get("activity", {}),
                "emotion": state.get("emotion", {}),
                "mental_load": state.get("mental_load"),
                "mental_load_thresholds": state.get(
                    "mental_load_thresholds", {}
                ),
                "routine": state.get("routine", {}),
                "availability": state.get("availability", {}),
                "heartbeat": state.get("heartbeat", {}),
            },
            "counts": {
                "memories": int(timeline_total),
                "conscious": access_counts.get("conscious", 0),
                "latent": access_counts.get("latent", 0),
                "repressed": access_counts.get("repressed", 0),
                "semantic_items": int(semantic_total),
                "active_semantic": status_counts.get("active", 0),
                "shadow_traits": len(self.shadow.list_traits()),
                "shadow_entries": int(shadow_entries),
                "identity_traits": len(personality.get("traits", {})),
                "cascades": len(memory_cascades | semantic_cascades),
                "history_changes": len(history),
                "outbox_messages": outbox["count"],
                "files": files["count"],
            },
            "last_memory_update": last_updated,
            "recent_memories": recent_memories,
            "contacts": self._contacts(),
            "outbox": outbox,
            "autonomy": autonomy,
            "files": files,
            "recent_history": sorted(
                history,
                key=lambda change: str(change.get("changed_at", "")),
                reverse=True,
            )[:5],
        }

    # -- timeline -----------------------------------------------------------

    def timeline(
        self,
        *,
        query: str | None = None,
        source: str | None = None,
        access: str | None = None,
        include_hidden: bool = False,
        limit: int = 200,
    ) -> dict[str, Any]:
        if source is not None and source not in MEMORY_SOURCES:
            raise ValidationError(f"source: valore non valido '{source}'.")
        if access is not None and access not in ACCESS_LEVELS:
            raise ValidationError(f"access: valore non valido '{access}'.")
        limit = max(1, min(int(limit), MAX_TIMELINE_ROWS))

        clauses: list[str] = []
        params: list[Any] = []
        if access is not None:
            clauses.append("m.access_level = ?")
            params.append(access)
        elif not include_hidden:
            clauses.append("m.access_level = 'conscious'")
        if source is not None:
            clauses.append("m.source = ?")
            params.append(source)
        if query:
            pattern = _like_pattern(query.strip())
            clauses.append(
                "(m.title LIKE ? ESCAPE '\\' OR m.description LIKE ? ESCAPE '\\' "
                "OR m.place LIKE ? ESCAPE '\\' OR m.tags_json LIKE ? ESCAPE '\\' "
                "OR m.people_json LIKE ? ESCAPE '\\' OR m.source_ref LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern] * 6)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT m.*, "
            "(SELECT COUNT(*) FROM memory_revisions r "
            " WHERE r.memory_id = m.id) AS revision_count, "
            "(SELECT MAX(r.changed_at) FROM memory_revisions r "
            " WHERE r.memory_id = m.id) AS last_revision_at "
            f"FROM episodic_memories m {where} "
            "ORDER BY m.event_time DESC, m.id DESC LIMIT ?"
        )
        params.append(limit)
        with self.memory.db.timeline() as conn:
            rows = conn.execute(sql, params).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) FROM episodic_memories"
            ).fetchone()[0]
            source_rows = conn.execute(
                "SELECT DISTINCT source FROM episodic_memories ORDER BY source"
            ).fetchall()
            access_rows = conn.execute(
                "SELECT access_level, COUNT(*) AS total "
                "FROM episodic_memories GROUP BY access_level"
            ).fetchall()

        memories = [decode_memory(row) for row in rows]
        return {
            "memories": memories,
            "count": len(memories),
            "total": int(total),
            "include_hidden": include_hidden,
            "sources": [row["source"] for row in source_rows],
            "access_counts": {
                row["access_level"]: int(row["total"])
                for row in access_rows
            },
        }

    def memory_detail(self, memory_id: str) -> dict[str, Any]:
        if not memory_id:
            raise ValidationError("memory_id mancante.")
        with self.memory.db.timeline() as conn:
            row = conn.execute(
                "SELECT m.*, "
                "(SELECT COUNT(*) FROM memory_revisions r "
                " WHERE r.memory_id = m.id) AS revision_count "
                "FROM episodic_memories m WHERE m.id = ?",
                (memory_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Ricordo non trovato: {memory_id}.")
            revision_rows = conn.execute(
                "SELECT * FROM memory_revisions WHERE memory_id = ? "
                "ORDER BY revision_number DESC, id DESC",
                (memory_id,),
            ).fetchall()
            link_rows = conn.execute(
                "SELECT *, CASE WHEN from_memory_id = ? THEN 'out' "
                "ELSE 'in' END AS direction, "
                "CASE WHEN from_memory_id = ? THEN to_memory_id "
                "ELSE from_memory_id END AS other_id "
                "FROM memory_links WHERE from_memory_id = ? OR to_memory_id = ? "
                "ORDER BY strength DESC, id DESC",
                (memory_id, memory_id, memory_id, memory_id),
            ).fetchall()

        revisions = []
        for revision in revision_rows:
            item = dict(revision)
            item["previous_snapshot"] = _loads_snapshot(
                item.pop("previous_snapshot_json", None)
            )
            item["new_snapshot"] = _loads_snapshot(
                item.pop("new_snapshot_json", None)
            )
            revisions.append(item)

        with self.semantic.db.semantic() as conn:
            semantic_rows = conn.execute(
                "SELECT l.*, s.title, s.knowledge_type, s.status "
                "FROM semantic_episodic_links l "
                "JOIN semantic_items s ON s.id = l.semantic_id "
                "WHERE l.memory_id = ? ORDER BY l.strength DESC, l.id DESC",
                (memory_id,),
            ).fetchall()

        return {
            "memory": decode_memory(row),
            "revisions": revisions,
            "links": [dict(link) for link in link_rows],
            "semantic_links": [dict(link) for link in semantic_rows],
        }

    # -- tratti -------------------------------------------------------------

    def traits(self) -> dict[str, Any]:
        identity = self.memory.get_identity()
        personality = identity.get("personality", {})
        scale = personality.get("scale", {})
        scale_min = int(scale.get("min", 1))
        scale_max = int(scale.get("max", 10))
        span = max(1, scale_max - scale_min)

        identity_traits = []
        for trait_id, trait in personality.get("traits", {}).items():
            value = float(trait.get("value", scale_min))
            identity_traits.append(
                {
                    "id": trait_id,
                    "value": value,
                    "left": trait.get("left"),
                    "right": trait.get("right"),
                    "description": trait.get("description"),
                    "position": round((value - scale_min) / span * 100, 1),
                }
            )

        shadow_doc = ShadowService(self.root).get_shadow_traits()
        shadow_traits = []
        for trait in shadow_doc.get("traits", []):
            shadow_traits.append(
                {
                    "id": trait.get("id"),
                    "name": trait.get("name"),
                    "description": trait.get("description"),
                    "origin": trait.get("origin"),
                    "intensity": trait.get("intensity"),
                    "conscious_awareness": trait.get("conscious_awareness"),
                    "triggers": trait.get("triggers", []),
                    "defense_mechanisms": trait.get("defense_mechanisms", []),
                    "behavioral_rules": trait.get("behavioral_rules", []),
                    "linked_memory_ids": trait.get("linked_memory_ids", []),
                    "safety": trait.get("safety", {}),
                }
            )

        history = self.evolution_service.history().get("changes", [])
        return {
            "identity": {
                "identity_id": identity.get("identity_id"),
                "full_name": identity.get("personal_data", {}).get("full_name"),
            },
            "model": personality.get("model"),
            "scale": {
                "min": scale_min,
                "max": scale_max,
                "midpoint": scale.get("midpoint"),
                "semantics": scale.get("semantics"),
            },
            "traits": identity_traits,
            "shadow": {
                "access_policy": shadow_doc.get("access_policy", {}),
                "traits": shadow_traits,
                "internal_conflicts": shadow_doc.get("internal_conflicts", []),
            },
            "history_count": len(history),
        }

    # -- conoscenze ---------------------------------------------------------

    def semantic_items(
        self,
        *,
        query: str | None = None,
        knowledge_type: str | None = None,
        status: str | None = "active",
    ) -> dict[str, Any]:
        if knowledge_type is not None and knowledge_type not in KNOWLEDGE_TYPES:
            raise ValidationError(
                f"knowledge_type: valore non valido '{knowledge_type}'."
            )
        if status in {"all", ""}:
            status = None
        if status is not None and status not in SEMANTIC_STATUSES:
            raise ValidationError(f"status: valore non valido '{status}'.")

        items = self.semantic.search_items(
            query=query or None,
            knowledge_type=knowledge_type,
            status=status,
            limit=MAX_SEMANTIC_ROWS,
        )
        with self.semantic.db.semantic() as conn:
            status_rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM semantic_items "
                "GROUP BY status"
            ).fetchall()
            type_rows = conn.execute(
                "SELECT knowledge_type, COUNT(*) AS total FROM semantic_items "
                "GROUP BY knowledge_type"
            ).fetchall()
            category_rows = conn.execute(
                "SELECT DISTINCT category FROM semantic_items ORDER BY category"
            ).fetchall()

        return {
            "items": items,
            "count": len(items),
            "status_counts": {
                row["status"]: int(row["total"]) for row in status_rows
            },
            "type_counts": {
                row["knowledge_type"]: int(row["total"])
                for row in type_rows
            },
            "categories": [row["category"] for row in category_rows],
        }

    # -- evoluzioni ---------------------------------------------------------

    def evolution(self) -> dict[str, Any]:
        history = self.evolution_service.history()
        changes = sorted(
            history.get("changes", []),
            key=lambda change: str(change.get("changed_at", "")),
            reverse=True,
        )

        with self.memory.db.timeline() as conn:
            seed_rows = conn.execute(
                "SELECT * FROM episodic_memories WHERE source = 'admin_seed' "
                "ORDER BY event_time DESC, id DESC"
            ).fetchall()
            memory_cascade_rows = conn.execute(
                "SELECT cascade_id, COUNT(*) AS revisions, "
                "COUNT(DISTINCT memory_id) AS memories, "
                "MIN(changed_at) AS first_at, MAX(changed_at) AS last_at "
                "FROM memory_revisions WHERE cascade_id IS NOT NULL "
                "GROUP BY cascade_id ORDER BY last_at DESC"
            ).fetchall()
            revision_rows = conn.execute(
                "SELECT r.id, r.memory_id, m.title, m.event_date, "
                "r.revision_number, r.changed_at, r.changed_by, "
                "r.change_reason, r.cascade_id "
                "FROM memory_revisions r "
                "JOIN episodic_memories m ON m.id = r.memory_id "
                "WHERE r.cascade_id IS NOT NULL "
                "ORDER BY r.changed_at DESC, r.id DESC LIMIT 50"
            ).fetchall()

        with self.semantic.db.semantic() as conn:
            semantic_cascade_rows = conn.execute(
                "SELECT cascade_id, COUNT(*) AS revisions, "
                "COUNT(DISTINCT item_id) AS items, "
                "MIN(changed_at) AS first_at, MAX(changed_at) AS last_at "
                "FROM semantic_revisions WHERE cascade_id IS NOT NULL "
                "GROUP BY cascade_id ORDER BY last_at DESC"
            ).fetchall()

        cascades: dict[str, dict[str, Any]] = {}
        for row in memory_cascade_rows:
            cascade_id = str(row["cascade_id"])
            cascades[cascade_id] = {
                "cascade_id": cascade_id,
                "memory_revisions": int(row["revisions"]),
                "memories": int(row["memories"]),
                "semantic_revisions": 0,
                "semantic_items": 0,
                "first_at": row["first_at"],
                "last_at": row["last_at"],
            }
        for row in semantic_cascade_rows:
            cascade_id = str(row["cascade_id"])
            entry = cascades.setdefault(
                cascade_id,
                {
                    "cascade_id": cascade_id,
                    "memory_revisions": 0,
                    "memories": 0,
                    "semantic_revisions": 0,
                    "semantic_items": 0,
                    "first_at": row["first_at"],
                    "last_at": row["last_at"],
                },
            )
            entry["semantic_revisions"] = int(row["revisions"])
            entry["semantic_items"] = int(row["items"])
            starts = [
                value
                for value in (entry["first_at"], row["first_at"])
                if value
            ]
            ends = [
                value
                for value in (entry["last_at"], row["last_at"])
                if value
            ]
            if starts:
                entry["first_at"] = min(starts)
            if ends:
                entry["last_at"] = max(ends)

        ordered_cascades = sorted(
            cascades.values(),
            key=lambda item: str(item.get("last_at") or ""),
            reverse=True,
        )

        return {
            "history": {
                "schema_version": history.get("schema_version"),
                "identity_id": history.get("identity_id"),
                "created_at": history.get("created_at"),
                "updated_at": history.get("updated_at"),
                "changes": changes,
                "count": len(changes),
            },
            "seeds": [decode_memory(row) for row in seed_rows],
            "cascades": ordered_cascades,
            "recent_revisions": [dict(row) for row in revision_rows],
        }


class DashboardHTTPServer(ThreadingHTTPServer):
    """Server HTTP con i dati della dashboard e gli asset statici."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        data: DashboardData,
        *,
        assets_dir: Path = ASSETS_DIR,
        quiet: bool = False,
        auth: DashboardAuth | None = None,
        tls: DashboardTLS | None = None,
        trusted_origins: tuple[str, ...] = (),
        allow_null_origin: bool = False,
    ):
        self.data = data
        self.assets_dir = assets_dir.resolve()
        self.quiet = quiet
        self.auth = auth
        self.tls = tls
        self.trusted_origins = trusted_origins
        self.allow_null_origin = allow_null_origin
        self.form_tokens = _FormTokenStore()
        self._finalize_lock = threading.Lock()
        self._finalize_job: _FinalizeJob | None = None
        super().__init__(server_address, DashboardRequestHandler)
        if tls is not None:
            self.socket = tls.context.wrap_socket(
                self.socket, server_side=True
            )

    def start_finalize_job(
        self, actions: Iterable[str]
    ) -> _FinalizeJob | None:
        """Avvia biografia e indice in un thread, senza bloccare la risposta."""

        requested = set(actions)
        selected = tuple(
            action for action in FINALIZE_ACTIONS if action in requested
        )
        if not selected:
            return None
        with self._finalize_lock:
            current = self._finalize_job
            if current is not None and current.is_running():
                return current
            job = _FinalizeJob(selected)
            self._finalize_job = job
        thread = threading.Thread(
            target=self._run_finalize_job,
            args=(job,),
            name="mnemosine-finalize",
            daemon=True,
        )
        thread.start()
        return job

    def finalize_snapshot(self) -> dict[str, Any] | None:
        with self._finalize_lock:
            job = self._finalize_job
        return job.snapshot() if job is not None else None

    def _run_finalize_job(self, job: _FinalizeJob) -> None:
        try:
            for action in job.actions:
                job.mark_running(action)
                try:
                    result = self._execute_finalize_action(action)
                except Exception as exc:  # noqa: BLE001 - il job non deve morire in silenzio
                    traceback.print_exc(file=sys.stderr)
                    job.fail(action, f"Errore imprevisto: {exc}")
                else:
                    job.mark_finished(action, result)
                    self._log_finalize_result(action, result)
                finally:
                    self.data.reload()
        finally:
            job.finish("Operazione interrotta.")

    def _execute_finalize_action(self, action: str) -> dict[str, Any]:
        if action == "biography":
            return self._execute_biography()
        if action == "index":
            return self._execute_index()
        raise ValidationError(f"Azione di completamento non valida: {action}.")

    def _execute_biography(self) -> dict[str, Any]:
        try:
            with _profile_lock(self.data.root):
                result = BiographyService(self.data.root).apply()
        except AgentMemoryError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "result": result}

    def _execute_index(self) -> dict[str, Any]:
        try:
            with _profile_lock(self.data.root):
                result = RetrievalService(self.data.root).sync_index(
                    rebuild=True
                )
        except AgentMemoryError as exc:
            return {"ok": False, "error": str(exc)}
        if not result.get("ok"):
            return {
                "ok": False,
                "error": _format_index_errors(result),
                "result": result,
            }
        return {"ok": True, "result": result}

    def _log_finalize_result(
        self, action: str, result: dict[str, Any]
    ) -> None:
        if self.quiet or not result.get("ok"):
            return
        if action == "biography":
            data = result.get("result") or {}
            sys.stderr.write(
                "Biografia generata dalla dashboard: "
                f"{data.get('created', 0)} nuovi ricordi\n"
            )
        else:
            sys.stderr.write("Indice vettoriale costruito dalla dashboard.\n")
        sys.stderr.flush()


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Router minimale per la dashboard."""

    server: DashboardHTTPServer
    server_version = "AgentMemoryDashboard/1.0"
    protocol_version = "HTTP/1.1"

    # -- HTTP ---------------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query, keep_blank_values=False)
        try:
            if path.startswith("/static/"):
                self._serve_file(unquote(path[len("/static/") :]))
                return
            if path == LOGIN_PATH:
                self._handle_login_page(params)
                return
            if path == LOGOUT_PATH:
                self._redirect(LOGIN_PATH if self.server.auth else "/")
                return
            if path == SESSION_PATH:
                payload = self._session_payload()
                if payload.get("authenticated"):
                    payload["csrf"] = self.server.form_tokens.issue()
                self._send_json(payload)
                return
            if not self._is_authenticated():
                if path.startswith("/api/"):
                    self._send_json(
                        {"error": "Autenticazione richiesta."},
                        status=HTTPStatus.UNAUTHORIZED,
                    )
                    return
                next_path = path
                if parsed.query:
                    next_path = f"{path}?{parsed.query}"
                self._redirect(
                    f"{LOGIN_PATH}?next={quote(self._safe_next(next_path), safe='')}"
                )
                return

            if path == FINALIZE_PATH:
                self._handle_finalize_page()
                return
            if path == SETUP_PATH:
                self._handle_setup_page()
                return
            if path == FINALIZE_STATUS_PATH:
                self._handle_finalize_status()
                return

            profile_status = self.server.data.profile_status()
            if path == "/api/health":
                self._send_json({"ok": True, "profile": profile_status})
                return
            if profile_status != PROFILE_STATE_READY:
                if path.startswith("/api/"):
                    self._send_json(
                        {
                            "error": (
                                "Profilo non configurato. Completa la prima "
                                "configurazione."
                            ),
                            "profile": profile_status,
                        },
                        status=HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                else:
                    self._redirect(SETUP_PATH)
                return

            if path == "/":
                self._serve_file("index.html")
            elif path == "/api/overview":
                self._send_json(self.server.data.overview())
            elif path == "/api/visual/status":
                self._send_json(VisualService(self.server.data.root).status())
            elif path == "/api/visual/image/reference":
                self._send_private_image(VisualService(self.server.data.root), reference=True)
            elif path.startswith("/api/visual/image/"):
                image_id = path.removeprefix("/api/visual/image/")
                self._send_private_image(VisualService(self.server.data.root), image_id=image_id)
            elif path == RELATIONS_PATH:
                try:
                    offset = int(self._first(params, "offset", "0"))
                    limit = int(self._first(params, "limit", "50"))
                except ValueError as exc:
                    raise ValidationError("offset e limit devono essere interi.") from exc
                self._send_json(self.server.data.relations(offset=offset, limit=limit, user_id=self._first(params, "user_id") or None))
            elif path == SETTINGS_PATH:
                self._send_json(DashboardSettings(self.server.data.root).snapshot())
            elif path == "/api/timeline":
                self._send_json(
                    self.server.data.timeline(
                        query=self._first(params, "q"),
                        source=self._first(params, "source"),
                        access=self._first(params, "access"),
                        include_hidden=self._flag(params, "include_hidden"),
                        limit=_as_int(
                            self._first(params, "limit"),
                            200,
                            "limit",
                        ),
                    )
                )
            elif path.startswith("/api/memory/"):
                memory_id = unquote(path[len("/api/memory/") :])
                self._send_json(self.server.data.memory_detail(memory_id))
            elif path == "/api/traits":
                self._send_json(self.server.data.traits())
            elif path == "/api/semantic":
                self._send_json(
                    self.server.data.semantic_items(
                        query=self._first(params, "q"),
                        knowledge_type=self._first(params, "type"),
                        status=self._first(params, "status", "active"),
                    )
                )
            elif path == "/api/evolution":
                self._send_json(self.server.data.evolution())
            else:
                self._send_json(
                    {"error": "Risorsa non trovata."},
                    status=HTTPStatus.NOT_FOUND,
                )
        except NotFoundError as exc:
            self._send_json(
                {"error": str(exc)}, status=HTTPStatus.NOT_FOUND
            )
        except ValidationError as exc:
            self._send_json(
                {"error": str(exc)}, status=HTTPStatus.BAD_REQUEST
            )
        except AgentMemoryError as exc:
            self._send_json(
                {"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR
            )
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:  # noqa: BLE001 - confine del server: risposta JSON, mai stack trace al client
            traceback.print_exc(file=sys.stderr)
            self._send_json(
                {"error": "Errore interno della dashboard."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if not self._is_same_origin():
                if not self.server.quiet:
                    sys.stderr.write(
                        "Origine rifiutata: "
                        f"Origin={self.headers.get('Origin')!r} "
                        f"Host={self.headers.get('Host')!r} "
                        "X-Forwarded-Host="
                        f"{self.headers.get('X-Forwarded-Host')!r}\n"
                    )
                    sys.stderr.flush()
                self._discard_body()
                self._send_json(
                    {"error": "Origine della richiesta non consentita."},
                    status=HTTPStatus.FORBIDDEN,
                )
                return
            if path == LOGIN_PATH:
                self._handle_login_post()
            elif path == LOGOUT_PATH:
                self._handle_logout()
            elif path in {SETUP_PATH, FINALIZE_PATH}:
                if (
                    self.server.auth is not None
                    and not self._is_authenticated()
                ):
                    self._discard_body()
                    self._send_json(
                        {"error": "Autenticazione richiesta."},
                        status=HTTPStatus.UNAUTHORIZED,
                    )
                    return
                if path == FINALIZE_PATH:
                    self._handle_finalize_post()
                else:
                    self._handle_setup_post()
            elif path == VISUAL_REFERENCE_PATH:
                if self.server.auth is not None and not self._is_authenticated():
                    self._discard_body()
                    self._send_json({"error": "Autenticazione richiesta."}, status=HTTPStatus.UNAUTHORIZED)
                    return
                if self.server.data.profile_status() != PROFILE_STATE_READY:
                    self._discard_body()
                    self._send_json({"error": "Profilo non configurato."}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                    return
                self._handle_visual_reference_upload()
            elif path in SETTINGS_POST_PATHS or path in VISUAL_POST_PATHS:
                if (
                    self.server.auth is not None
                    and not self._is_authenticated()
                ):
                    self._discard_body()
                    self._send_json(
                        {"error": "Autenticazione richiesta."},
                        status=HTTPStatus.UNAUTHORIZED,
                    )
                    return
                if self.server.data.profile_status() != PROFILE_STATE_READY:
                    self._discard_body()
                    self._send_json(
                        {
                            "error": "Profilo non configurato.",
                            "profile": self.server.data.profile_status(),
                        },
                        status=HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                    return
                self._handle_settings_post(path)
            else:
                self._send_json(
                    {"error": "Metodo non consentito."},
                    status=HTTPStatus.METHOD_NOT_ALLOWED,
                )
        except ValidationError as exc:
            self._send_json(
                {"error": str(exc)}, status=HTTPStatus.BAD_REQUEST
            )
        except AgentMemoryError as exc:
            self._send_json(
                {"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR
            )
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:  # noqa: BLE001 - confine del server: risposta JSON, mai stack trace al client
            traceback.print_exc(file=sys.stderr)
            self._send_json(
                {"error": "Errore interno della dashboard."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def log_message(self, format: str, *args: Any) -> None:
        if self.server.quiet:
            return
        sys.stderr.write(f"{self.address_string()} - {format % args}\n")

    # -- autenticazione -----------------------------------------------------

    def _session_token(self) -> str | None:
        raw_cookie = self.headers.get("Cookie")
        if not raw_cookie:
            return None
        cookies = SimpleCookie()
        try:
            cookies.load(raw_cookie)
        except CookieError:
            return None
        morsel = cookies.get(SESSION_COOKIE)
        return morsel.value if morsel is not None else None

    def _is_authenticated(self) -> bool:
        auth = self.server.auth
        if auth is None:
            return True
        return auth.validate_session(self._session_token())

    def _session_payload(self) -> dict[str, Any]:
        auth = self.server.auth
        return {
            "auth_enabled": auth is not None,
            "authenticated": True if auth is None else self._is_authenticated(),
        }

    def _handle_login_page(self, params: dict[str, list[str]]) -> None:
        auth = self.server.auth
        if auth is None:
            self._redirect("/")
            return
        next_path = self._safe_next(self._first(params, "next", "/"))
        if self._is_authenticated():
            self._redirect(next_path)
            return
        self._render_login_page(auth, next_path)

    def _handle_login_post(self) -> None:
        auth = self.server.auth
        if auth is None:
            self._redirect("/")
            return
        if self._is_authenticated():
            self._redirect("/")
            return

        client = self.client_address[0]
        if not auth.allow_login(client):
            self._render_login_page(
                auth,
                "/",
                "Troppi tentativi di accesso. Attendi qualche minuto.",
                status=HTTPStatus.TOO_MANY_REQUESTS,
            )
            return

        form = self._read_form()
        next_path = self._safe_next(form.get("next", "/"))
        if not auth.consume_login_token(form.get("csrf", "")):
            self._render_login_page(
                auth,
                next_path,
                "Modulo scaduto o non valido. Riprova.",
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        if not auth.verify_password(form.get("password", "")):
            auth.register_failure(client)
            self._render_login_page(
                auth,
                next_path,
                "Password non corretta.",
                status=HTTPStatus.UNAUTHORIZED,
            )
            return

        auth.reset_failures(client)
        token = auth.issue_session()
        self._redirect(next_path, cookie=self._session_cookie(token))

    def _handle_settings_post(self, path: str) -> None:
        payload = self._read_json()
        token = payload.get("csrf")
        if not isinstance(token, str) or not self.server.form_tokens.consume(token):
            raise ValidationError("Modulo scaduto o non valido. Riprova.")

        settings = DashboardSettings(self.server.data.root)
        if path == VISUAL_PROFILE_PATH:
            result = VisualService(self.server.data.root).configure(
                payload.get("profile", {}),
                enabled=payload.get("enabled"),
                confirmation=payload.get("confirmation", ""),
            )
        elif path == VISUAL_REFERENCE_REMOVE_PATH:
            result = VisualService(self.server.data.root).remove_reference()
        elif path == VISUAL_GENERATE_PATH:
            result = VisualService(self.server.data.root).generate(
                payload.get("scene", ""),
                consent_to_external_processing=payload.get("consent_to_external_processing"),
            )
        elif path == VISUAL_IMAGE_DELETE_PATH:
            result = VisualService(self.server.data.root).delete_image(payload.get("id"))
        elif path == VISUAL_TOKEN_CHECK_PATH:
            result = VisualService(self.server.data.root).check_provider()
        elif path == RELATIONS_PATH:
            result = settings.save_contact(payload)
        elif path == SHADOW_TRAITS_PATH:
            result = settings.save_shadow_trait(payload)
            self.server.data.reload()
        elif path == SETTINGS_BROWSER_PATH:
            result = settings.save_browser(payload)
        elif path == SETTINGS_WHATSAPP_PATH:
            result = settings.save_whatsapp(payload)
        elif path == SETTINGS_BEHAVIOR_PATH:
            result = settings.save_behavior(payload)
        elif path == AUTONOMY_REVIEW_PATH:
            result = AutonomyService(self.server.data.root, None).review_uncertain(
                payload.get("run_id"), payload.get("confirmation"), payload.get("note")
            )
        else:
            confirmation = payload.get("confirmation")
            if not isinstance(confirmation, str):
                raise ValidationError(
                    f"Scrivi {ARCHIVE_CONFIRMATION} per confermare "
                    "l'archiviazione."
                )
            result = settings.archive_profile(confirmation)
            self.server.data.reload()
            if not self.server.quiet:
                sys.stderr.write(
                    "Profilo archiviato dalla dashboard: "
                    f"{result.get('archive')}\n"
                )
                sys.stderr.flush()
        self._send_json(result)

    def _send_private_image(
        self,
        service: VisualService,
        *,
        image_id: str = "",
        reference: bool = False,
    ) -> None:
        data, content_type = service.image_bytes(image_id, reference=reference)
        self._send_bytes(data, content_type)

    def _handle_visual_reference_upload(self) -> None:
        token = self.headers.get("X-CSRF-Token")
        if not token or not self.server.form_tokens.consume(token):
            self._discard_body()
            raise ValidationError("Modulo scaduto o non valido. Riprova.")
        content_type = self.headers.get_content_type()
        if content_type not in {"image/png", "image/jpeg", "image/webp"}:
            self._discard_body()
            raise ValidationError("Formato immagine non supportato: usa PNG, JPEG o WebP.")
        from .visual import MAX_REFERENCE_BYTES

        body = self._read_request_body(max_bytes=MAX_REFERENCE_BYTES)
        self._send_json(VisualService(self.server.data.root).save_reference(body, content_type))

    def _handle_logout(self) -> None:
        auth = self.server.auth
        if auth is None:
            self._redirect("/")
            return
        auth.revoke_session(self._session_token())
        self._redirect(LOGIN_PATH, cookie=self._expired_cookie())

    # -- prima configurazione -----------------------------------------------

    def _handle_setup_page(self) -> None:
        status = self.server.data.profile_status()
        if status == PROFILE_STATE_READY:
            self._redirect("/")
            return
        if status == PROFILE_STATE_INCOMPLETE:
            self._render_setup_page(
                error=(
                    "La directory contiene un profilo incompleto. Non verrà "
                    "sovrascritta: usa `profile init --force` da terminale."
                ),
                status=HTTPStatus.CONFLICT,
                form_visible=False,
            )
            return
        self._render_setup_page()

    def _render_setup_page(
        self,
        values: dict[str, str] | None = None,
        error: str = "",
        *,
        status: HTTPStatus = HTTPStatus.OK,
        form_visible: bool = True,
    ) -> None:
        try:
            template = (self.server.assets_dir / "setup.html").read_text(
                encoding="utf-8"
            )
        except OSError:
            self._send_json(
                {"error": "Pagina di configurazione non disponibile."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

        preserved = {
            key: value
            for key, value in (values or {}).items()
            if key != "csrf"
        }
        payload = json.dumps(preserved, ensure_ascii=False)
        birth_country = preserved.get("birth_country") or "Italia"
        residence_country = preserved.get("residence_country") or ""
        citizenship = preserved.get("citizenship") or "Italia"
        birth_region = preserved.get("birth_region") or ""
        birth_province = preserved.get("birth_province") or ""
        residence_region = preserved.get("residence_region") or ""
        residence_province = preserved.get("residence_province") or ""
        timezone_name = preserved.get("timezone") or "Europe/Rome"
        gender = preserved.get("gender") or "non specificato"
        pronouns = preserved.get("pronouns") or ""
        page = (
            template.replace(
                "{{csrf}}", html.escape(self.server.form_tokens.issue())
            )
            .replace("{{root}}", html.escape(str(self.server.data.root)))
            .replace("{{error}}", html.escape(error))
            .replace("{{form_hidden}}", "" if form_visible else "hidden")
            .replace(
                "{{gender_options}}",
                _select_options(GENDER_OPTIONS, gender),
            )
            .replace(
                "{{pronoun_options}}",
                _select_options(PRONOUN_OPTIONS, pronouns),
            )
            .replace("{{timezone_options}}", _timezone_options(timezone_name))
            .replace(
                "{{birth_country_options}}",
                _country_options(birth_country),
            )
            .replace(
                "{{birth_region_options}}",
                _region_options(birth_region),
            )
            .replace(
                "{{birth_province_options}}",
                _province_options(birth_province),
            )
            .replace(
                "{{birth_region_foreign}}",
                html.escape(
                    preserved.get("birth_region_foreign", ""),
                    quote=True,
                ),
            )
            .replace(
                "{{birth_province_foreign}}",
                html.escape(
                    preserved.get("birth_province_foreign", ""),
                    quote=True,
                ),
            )
            .replace(
                "{{residence_region_options}}",
                _region_options(residence_region),
            )
            .replace(
                "{{residence_province_options}}",
                _province_options(residence_province),
            )
            .replace(
                "{{residence_region_foreign}}",
                html.escape(
                    preserved.get("residence_region_foreign", ""),
                    quote=True,
                ),
            )
            .replace(
                "{{residence_province_foreign}}",
                html.escape(
                    preserved.get("residence_province_foreign", ""),
                    quote=True,
                ),
            )
            .replace(
                "{{residence_country_options}}",
                _country_options(
                    residence_country,
                    empty_label="Uguale al paese di nascita",
                ),
            )
            .replace(
                "{{citizenship_options}}",
                _country_options(citizenship),
            )
            .replace("{{language_options}}", _language_options())
            .replace(
                "{{language_level_options}}",
                _select_options(LANGUAGE_LEVEL_OPTIONS, ""),
            )
            .replace("{{values_json}}", html.escape(payload, quote=True))
        )
        self._send_html(page, status=status)

    def _handle_setup_post(self) -> None:
        form = self._read_form()
        status = self.server.data.profile_status()
        if status == PROFILE_STATE_READY:
            self._redirect("/")
            return
        if status != PROFILE_STATE_MISSING:
            self._render_setup_page(
                values=form,
                error=(
                    "La directory contiene un profilo incompleto. Non verrà "
                    "sovrascritta: usa `profile init --force` da terminale."
                ),
                status=HTTPStatus.CONFLICT,
                form_visible=False,
            )
            return
        if not self.server.form_tokens.consume(form.get("csrf", "")):
            self._render_setup_page(
                values=form,
                error="Modulo scaduto o non valido. Riprova.",
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        try:
            args = build_profile_args(form)
            create_profile(self.server.data.root, args, force=False)
        except (ConfigError, ValidationError) as exc:
            self._render_setup_page(
                values=form,
                error=str(exc),
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        self.server.data.reload()
        if not self.server.quiet:
            sys.stderr.write(
                f"Profilo creato dalla dashboard: {self.server.data.root}\n"
            )
            sys.stderr.flush()

        actions = _finalize_actions_from_form(form)
        if actions:
            self.server.start_finalize_job(actions)
        self._redirect(FINALIZE_PATH)

    # -- completamento del profilo ------------------------------------------

    def _handle_finalize_page(self) -> None:
        if self.server.data.profile_status() != PROFILE_STATE_READY:
            self._redirect(SETUP_PATH)
            return
        self._render_finalize_page()

    def _handle_finalize_status(self) -> None:
        status = self.server.data.profile_status()
        if status != PROFILE_STATE_READY:
            self._send_json(
                {
                    "ok": False,
                    "profile": status,
                    "job": None,
                    "error": "Profilo non configurato.",
                },
                status=HTTPStatus.CONFLICT,
            )
            return
        self._send_json(
            {
                "ok": True,
                "profile": status,
                "job": self.server.finalize_snapshot(),
            }
        )

    def _handle_finalize_post(self) -> None:
        form = self._read_form()
        if self.server.data.profile_status() != PROFILE_STATE_READY:
            self._redirect(SETUP_PATH)
            return
        if not self.server.form_tokens.consume(form.get("csrf", "")):
            self._render_finalize_page(
                error="Modulo scaduto o non valido. Riprova.",
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        action = form.get("action")
        if action not in FINALIZE_ACTIONS:
            self._render_finalize_page(
                error="Azione di completamento non riconosciuta.",
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        self.server.start_finalize_job((action,))
        self._redirect(FINALIZE_PATH)

    def _biography_view(self) -> tuple[str, str]:
        try:
            count = int(self.server.data.overview()["counts"]["memories"])
        except AgentMemoryError as exc:
            return "is-error", str(exc)
        if count:
            return "is-success", f"{count} ricordi presenti nella timeline."
        return (
            "is-pending",
            "Nessun ricordo biografico: la timeline è ancora vuota.",
        )

    def _index_view(self) -> tuple[str, str]:
        try:
            payload = RetrievalService(self.server.data.root).index.status()
        except AgentMemoryError as exc:
            return "is-error", str(exc)
        return _index_payload_view(payload)

    def _render_finalize_page(
        self,
        *,
        error: str = "",
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        if self.server.data.profile_status() != PROFILE_STATE_READY:
            self._redirect(SETUP_PATH)
            return
        try:
            template = (self.server.assets_dir / "setup_complete.html").read_text(
                encoding="utf-8"
            )
        except OSError:
            self._send_json(
                {"error": "Pagina di completamento non disponibile."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        job = self.server.finalize_snapshot()
        job_running = bool(job and job.get("state") == "running")
        steps = {step["key"]: step for step in job["steps"]} if job else {}
        biography_step = steps.get("biography")
        index_step = steps.get("index")
        if biography_step:
            biography_state = _step_state_class(biography_step["state"])
            biography_text = biography_step["text"]
        else:
            biography_state, biography_text = self._biography_view()
        if index_step:
            index_state = _step_state_class(index_step["state"])
            index_text = index_step["text"]
        else:
            index_state, index_text = self._index_view()
        if job:
            elapsed_label = f"{float(job['elapsed']):.1f}".replace(
                ".", ","
            )
            if job_running:
                job_class = "is-running"
                job_state_label = "In corso"
                job_summary = (
                    "La pagina si aggiorna automaticamente mentre il "
                    "completamento procede."
                )
                job_detail = (
                    "La prima costruzione dell'indice può richiedere qualche "
                    "minuto per caricare il modello locale."
                )
            elif job.get("ok"):
                job_class = "is-success"
                job_state_label = "Completato"
                job_summary = (
                    f"Completamento terminato in {elapsed_label} s."
                )
                job_detail = "Puoi riprovare o aprire la dashboard."
            else:
                job_class = "is-error"
                job_state_label = "Terminato con errori"
                job_summary = (
                    "Completamento terminato con errori in "
                    f"{elapsed_label} s."
                )
                job_detail = (
                    "Il profilo resta valido; riprova l'operazione non "
                    "riuscita."
                )
            job_hidden = ""
            job_progress = str(job["progress"])
        else:
            job_class = "is-pending"
            job_state_label = "Non avviato"
            job_summary = "Nessun completamento in corso."
            job_detail = "Scegli un'operazione per popolare il profilo."
            job_hidden = "hidden"
            job_progress = "0"
        biography_state_name = biography_state.removeprefix("is-")
        index_state_name = index_state.removeprefix("is-")
        page = (
            template.replace("{{root}}", html.escape(str(self.server.data.root)))
            .replace("{{error}}", html.escape(error))
            .replace("{{job_hidden}}", job_hidden)
            .replace("{{job_class}}", html.escape(job_class))
            .replace("{{job_progress}}", job_progress)
            .replace("{{job_state_label}}", html.escape(job_state_label))
            .replace("{{job_summary}}", html.escape(job_summary))
            .replace("{{job_detail}}", html.escape(job_detail))
            .replace(
                "{{job_running}}", "1" if job_running else "0"
            )
            .replace("{{job_busy}}", "true" if job_running else "false")
            .replace(
                "{{job_status_url}}", html.escape(FINALIZE_STATUS_PATH)
            )
            .replace("{{biography_state}}", html.escape(biography_state))
            .replace("{{biography_text}}", html.escape(biography_text))
            .replace(
                "{{biography_button}}",
                html.escape(
                    _finalize_button_label(
                        "biography", biography_state_name
                    )
                ),
            )
            .replace(
                "{{biography_disabled}}",
                "disabled" if job_running else "",
            )
            .replace("{{index_state}}", html.escape(index_state))
            .replace("{{index_text}}", html.escape(index_text))
            .replace(
                "{{index_button}}",
                html.escape(_finalize_button_label("index", index_state_name)),
            )
            .replace(
                "{{index_disabled}}", "disabled" if job_running else ""
            )
            .replace(
                "{{csrf_biography}}",
                html.escape(self.server.form_tokens.issue()),
            )
            .replace("{{csrf_index}}", html.escape(self.server.form_tokens.issue()))
        )
        self._send_html(page, status=status)

    def _render_login_page(
        self,
        auth: DashboardAuth,
        next_path: str,
        error: str = "",
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        try:
            template = (self.server.assets_dir / "login.html").read_text(
                encoding="utf-8"
            )
        except OSError:
            self._send_json(
                {"error": "Pagina di accesso non disponibile."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        page = (
            template.replace("{{csrf}}", html.escape(auth.create_login_token()))
            .replace("{{next}}", html.escape(self._safe_next(next_path)))
            .replace("{{error}}", html.escape(error))
        )
        self._send_html(page, status=status)

    def _read_request_body(self, *, max_bytes: int = MAX_FORM_BYTES) -> bytes:
        transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
        if transfer_encoding:
            if "chunked" not in transfer_encoding:
                raise ValidationError("Transfer-Encoding non supportato.")
            return self._read_chunked_body(max_bytes=max_bytes)

        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length or "0")
        except ValueError as exc:
            raise ValidationError("Content-Length non valido.") from exc
        if length < 0 or length > max_bytes:
            raise ValidationError("Richiesta troppo grande.")
        if length == 0:
            return b""
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValidationError("Corpo della richiesta incompleto.")
        return body

    def _read_chunked_body(self, *, max_bytes: int = MAX_FORM_BYTES) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            line = self.rfile.readline(256)
            if not line:
                raise ValidationError("Corpo chunked incompleto.")
            size_text = line.split(b";", 1)[0].strip()
            try:
                size = int(size_text, 16)
            except ValueError as exc:
                raise ValidationError("Dimensione del chunk non valida.") from exc
            if size < 0:
                raise ValidationError("Dimensione del chunk non valida.")
            if size == 0:
                while True:
                    trailer = self.rfile.readline(8192)
                    if trailer in {b"\r\n", b"\n", b""}:
                        break
                break
            total += size
            if total > max_bytes:
                raise ValidationError("Richiesta troppo grande.")
            chunk = self.rfile.read(size)
            if len(chunk) != size:
                raise ValidationError("Corpo chunked incompleto.")
            chunks.append(chunk)
            if self.rfile.read(2) not in {b"\r\n", b"\n"}:
                raise ValidationError("Chunk non terminato correttamente.")
        return b"".join(chunks)

    def _discard_body(self) -> None:
        """Consuma il corpo di una richiesta rifiutata prima della risposta.

        Senza questo, una connessione keep-alive può interpretare il corpo
        del POST come una nuova richiesta HTTP. Gestisce anche il corpo
        chunked usato da alcuni reverse proxy.
        """

        try:
            self._read_request_body()
        except ValidationError:
            self.close_connection = True

    def _read_json(self) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise ValidationError(
                "La richiesta deve usare application/json."
            )
        body = self._read_request_body()
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("JSON non valido.") from exc
        if not isinstance(payload, dict):
            raise ValidationError("Atteso un oggetto JSON.")
        return payload

    def _read_form(self) -> dict[str, str]:
        if self.headers.get_content_type() != "application/x-www-form-urlencoded":
            raise ValidationError(
                "Il modulo deve usare application/x-www-form-urlencoded."
            )
        body = self._read_request_body()
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("Modulo non codificato in UTF-8.") from exc
        values = parse_qs(text, keep_blank_values=True)
        return {
            key: (items[0] if items else "") for key, items in values.items()
        }

    def _is_same_origin(self) -> bool:
        return _same_origin(
            self.headers.get("Origin"),
            self.headers.get("Host"),
            self.headers.get("X-Forwarded-Host"),
            self.server.trusted_origins,
            self.server.allow_null_origin,
        )

    def _session_cookie(self, token: str) -> str:
        auth = self.server.auth
        ttl = auth.session_ttl if auth is not None else 0
        secure = "; Secure" if self.server.tls is not None else ""
        return (
            f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; "
            f"Path=/; Max-Age={ttl}{secure}"
        )

    def _expired_cookie(self) -> str:
        secure = "; Secure" if self.server.tls is not None else ""
        return (
            f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; "
            f"Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT{secure}"
        )

    @staticmethod
    def _safe_next(value: str | None) -> str:
        if not value:
            return "/"
        value = value.strip()
        if len(value) > 2048:
            return "/"
        if "\\" in value or any(ord(character) < 32 for character in value):
            return "/"
        if not value.startswith("/") or value.startswith("//"):
            return "/"
        decoded = unquote(value)
        if decoded.startswith("//") or "\\" in decoded:
            return "/"
        parsed = urlparse(value)
        if parsed.scheme or parsed.netloc:
            return "/"
        return value

    def _redirect(self, location: str, *, cookie: str | None = None) -> None:
        if not location.startswith("/"):
            location = "/"
        self.close_connection = True
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Connection", "close")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

    # -- utilità ------------------------------------------------------------

    @staticmethod
    def _first(
        params: dict[str, list[str]], name: str, default: str | None = None
    ) -> str | None:
        values = params.get(name)
        if not values:
            return default
        value = values[0].strip()
        return value or default

    @staticmethod
    def _flag(params: dict[str, list[str]], name: str) -> bool:
        values = params.get(name)
        if not values:
            return False
        return values[0].strip().lower() in {"1", "true", "yes", "on"}

    def _send_json(
        self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(
            body, "application/json; charset=utf-8", status=status
        )

    def _send_html(
        self, page: str, *, status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        self._send_bytes(
            page.encode("utf-8"), "text/html; charset=utf-8", status=status
        )

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, name: str) -> None:
        candidate = (self.server.assets_dir / name).resolve()
        if candidate.name in {
            "login.html",
            "setup.html",
            "setup_complete.html",
        }:
            self._send_json(
                {"error": "File non trovato."},
                status=HTTPStatus.NOT_FOUND,
            )
            return
        try:
            candidate.relative_to(self.server.assets_dir)
        except ValueError:
            self._send_json(
                {"error": "File non trovato."},
                status=HTTPStatus.NOT_FOUND,
            )
            return
        if not candidate.is_file():
            self._send_json(
                {"error": "File non trovato."},
                status=HTTPStatus.NOT_FOUND,
            )
            return

        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }
        content_type = content_types.get(
            candidate.suffix.lower(), "application/octet-stream"
        )
        self._send_bytes(candidate.read_bytes(), content_type)


def create_server(
    root: str | Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    assets_dir: Path = ASSETS_DIR,
    quiet: bool = False,
    auth: DashboardAuth | None = None,
    https: bool = False,
    tls_dir: str | Path | None = None,
    tls_sans: tuple[str, ...] = (),
    trusted_origins: tuple[str, ...] = (),
    allow_null_origin: bool = False,
) -> DashboardHTTPServer:
    data = DashboardData(root)
    for origin in trusted_origins:
        try:
            _origin_key(origin)
        except ValueError as exc:
            raise ValidationError(f"--trusted-origin non valida: {exc}") from exc
    tls = None
    if https:
        directory = (
            Path(tls_dir)
            if tls_dir is not None
            else data.root / "data" / "dashboard_tls"
        )
        tls = ensure_dashboard_tls(directory, extra_names=tls_sans)
    return DashboardHTTPServer(
        (host, port),
        data,
        assets_dir=assets_dir,
        quiet=quiet,
        auth=auth,
        tls=tls,
        trusted_origins=trusted_origins,
        allow_null_origin=allow_null_origin,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dashboard amministrativa locale per timeline, tratti, "
            "conoscenze ed evoluzioni, con prima configurazione del profilo."
        ),
        epilog=(
            "Su un host non loopback l'autenticazione è sempre attiva: se non "
            "fornisci una password ne viene generata una temporanea all'avvio. "
            "Usa --https per cifrare il traffico; senza TLS la password "
            "viaggia in chiaro sulla rete."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory del progetto (default: directory corrente).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Indirizzo di ascolto (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port", type=int, default=8765, help="Porta HTTP (default: 8765)."
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Apre il browser sulla dashboard dopo l'avvio.",
    )
    parser.add_argument(
        "--trusted-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help=(
            "Origine esatta (https://host[:porta]) da accettare per i POST "
            "quando un reverse proxy riscrive l'header Host. Ripetibile."
        ),
    )
    parser.add_argument(
        "--allow-null-origin",
        action="store_true",
        help=(
            "Accetta Origin: null, usato dagli iframe sandbox. Il token CSRF "
            "resta obbligatorio; usalo solo per una preview locale fidata."
        ),
    )
    parser.add_argument(
        "--auth",
        action="store_true",
        help="Richiede la password anche quando ascolta su loopback.",
    )
    parser.add_argument(
        "--password-file",
        type=Path,
        default=None,
        help=(
            "File con la password della dashboard (consigliato chmod 600). "
            "In alternativa usa AGENT_MEMORY_DASHBOARD_PASSWORD."
        ),
    )
    parser.add_argument(
        "--https",
        action="store_true",
        help="Abilita HTTPS con un certificato self-signed dedicato.",
    )
    parser.add_argument(
        "--tls-dir",
        type=Path,
        default=None,
        help=(
            "Directory di certificato e chiave TLS "
            "(default: data/dashboard_tls)."
        ),
    )
    parser.add_argument(
        "--tls-san",
        action="append",
        default=[],
        metavar="HOST_OR_IP",
        help=(
            "Aggiunge un nome o indirizzo IP al certificato. "
            "Ripetibile; utile per l'IP LAN usato dagli altri dispositivi."
        ),
    )
    args = parser.parse_args(argv)

    if (args.tls_dir is not None or args.tls_san) and not args.https:
        print(
            "--tls-dir e --tls-san richiedono --https.",
            file=sys.stderr,
        )
        return 2

    try:
        auth, generated_password = resolve_auth(
            require_auth=args.auth or args.host not in LOOPBACK_HOSTS,
            password_file=args.password_file,
        )
    except AgentMemoryError as exc:
        print(f"Errore di autenticazione: {exc}", file=sys.stderr)
        return 2

    if args.host not in LOOPBACK_HOSTS:
        print(
            "Dashboard esposta sulla rete: autenticazione obbligatoria.",
            file=sys.stderr,
        )
    if args.host not in LOOPBACK_HOSTS and not args.https:
        print(
            "ATTENZIONE: senza --https la password di accesso viaggia in "
            "chiaro. Abilita --https o usa una rete fidata.",
            file=sys.stderr,
        )

    try:
        server = create_server(
            args.root,
            host=args.host,
            port=args.port,
            auth=auth,
            https=args.https,
            tls_dir=args.tls_dir,
            tls_sans=tuple(args.tls_san),
            trusted_origins=tuple(args.trusted_origin),
            allow_null_origin=args.allow_null_origin,
        )
    except AgentMemoryError as exc:
        print(f"Errore TLS: {exc}", file=sys.stderr)
        return 2
    display_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    if ":" in display_host and not display_host.startswith("["):
        display_host = f"[{display_host}]"
    scheme = "https" if args.https else "http"
    url = f"{scheme}://{display_host}:{server.server_address[1]}/"
    print(f"Dashboard disponibile su {url}", flush=True)
    if server.tls is not None:
        print(
            f"Certificato TLS: {server.tls.certificate_path}",
            flush=True,
        )
        print(
            "Certificato self-signed: il browser mostrerà un avviso finché "
            "non lo consideri attendibile.",
            flush=True,
        )
        print(
            "Per esportarlo: python -m agent_memory.dashboard_cert export",
            flush=True,
        )
    if args.host in {"0.0.0.0", "::"}:
        print(
            "Da un altro dispositivo usa l'indirizzo IP della macchina.",
            flush=True,
        )
    if generated_password is not None:
        print(
            "Autenticazione attiva. Password temporanea per questo avvio:",
            flush=True,
        )
        print(f"  {generated_password}", flush=True)
    elif auth is not None:
        print("Autenticazione attiva: password configurata.", flush=True)
    else:
        print(
            "Autenticazione disattivata: accesso consentito solo da loopback.",
            flush=True,
        )
    print(
        "Lettura dati. Scrittura consentita per configurazione, impostazioni, "
        "biografia, indice e archiviazione del profilo.",
        flush=True,
    )
    print("Premi CTRL+C per terminare.", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard arrestata.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
