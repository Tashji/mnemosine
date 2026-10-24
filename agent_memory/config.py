"""Caricamento e scrittura delle configurazioni JSON.

La configurazione è composta da cinque documenti. Il caricamento è volutamente
*fail closed*: se un file manca, non è JSON valido o non rispetta le invarianti
di base, il server non parte. In particolare ``shadow_traits.json`` deve
dichiarare che la lettura conscia diretta è disabilitata.

Le scritture sono atomiche (file temporaneo nella stessa directory seguito da
``os.replace``) e protette da un lock di processo, così due aggiornamenti
concorrenti non possono sovrascriversi a vicenda.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import ConfigError, ValidationError


CONFIG_FILENAMES: dict[str, str] = {
    "identity": "identity.json",
    "shadow_traits": "shadow_traits.json",
    "current_state": "current_state.json",
    "world_history": "world_history.json",
    "social_graph": "social_graph.json",
}

DEFAULT_TIMEZONE = "Europe/Rome"


def project_root() -> Path:
    """Restituisce la root del progetto, salvo override esplicito.

    ``AGENT_MEMORY_ROOT`` è utile per test e per eseguire il server su una
    copia dei dati senza toccare il progetto originale.
    """

    override = os.environ.get("AGENT_MEMORY_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def now_iso(timezone_name: str = DEFAULT_TIMEZONE) -> str:
    """Timestamp ISO 8601 nella timezone dell'agente (fallback UTC)."""

    try:
        tz = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone.utc
    return datetime.now(tz).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Validazione
# ---------------------------------------------------------------------------


def _require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: atteso un oggetto JSON.")
    return value


def _require_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigError(f"{path}: attesa una lista JSON.")
    return value


def _require_keys(obj: dict[str, Any], keys: list[str], path: str) -> None:
    for key in keys:
        if key not in obj:
            raise ConfigError(f"{path}: chiave obbligatoria mancante '{key}'.")


def _require_number(value: Any, low: float, high: float, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path}: atteso un numero.")
    if not low <= float(value) <= high:
        raise ConfigError(f"{path}: valore {value} fuori intervallo [{low}, {high}].")


def _require_iso_date(value: Any, path: str) -> None:
    if not isinstance(value, str):
        raise ConfigError(f"{path}: attesa una data ISO 8601 come stringa.")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"{path}: data non valida '{value}'.") from exc


def _require_iso_datetime(value: Any, path: str) -> None:
    if not isinstance(value, str):
        raise ConfigError(f"{path}: atteso un timestamp ISO 8601 come stringa.")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfigError(f"{path}: timestamp non valido '{value}'.") from exc


def _validate_identity(identity: dict[str, Any]) -> None:
    _require_keys(
        identity,
        ["schema_version", "identity_id", "personal_data", "family", "personality"],
        "identity",
    )
    if not isinstance(identity["identity_id"], str) or not identity["identity_id"]:
        raise ConfigError("identity.identity_id: attesa una stringa non vuota.")

    personal = _require_object(identity["personal_data"], "identity.personal_data")
    _require_keys(
        personal,
        ["first_name", "last_name", "birth_date", "birth_place", "timezone"],
        "identity.personal_data",
    )
    _require_iso_date(personal["birth_date"], "identity.personal_data.birth_date")
    _require_object(personal["birth_place"], "identity.personal_data.birth_place")
    if not isinstance(personal["timezone"], str) or not personal["timezone"]:
        raise ConfigError("identity.personal_data.timezone: attesa una stringa IANA.")

    family = _require_object(identity["family"], "identity.family")
    _require_keys(family, ["parents", "siblings"], "identity.family")
    _require_list(family["parents"], "identity.family.parents")
    _require_list(family["siblings"], "identity.family.siblings")

    personality = _require_object(identity["personality"], "identity.personality")
    _require_keys(personality, ["model", "scale", "traits"], "identity.personality")
    traits = _require_object(personality["traits"], "identity.personality.traits")
    for trait_id, trait in traits.items():
        trait_obj = _require_object(trait, f"identity.personality.traits.{trait_id}")
        _require_number(
            trait_obj.get("value"),
            1,
            10,
            f"identity.personality.traits.{trait_id}.value",
        )


def _validate_shadow_traits(shadow: dict[str, Any]) -> None:
    _require_keys(
        shadow,
        [
            "schema_version",
            "identity_id",
            "access_policy",
            "traits",
            "internal_conflicts",
            "traumas",
        ],
        "shadow_traits",
    )
    policy = _require_object(shadow["access_policy"], "shadow_traits.access_policy")
    if policy.get("conscious_direct_read") is not False:
        raise ConfigError(
            "shadow_traits.access_policy.conscious_direct_read deve essere false."
        )
    if policy.get("rag_exposure") != "never_by_default":
        raise ConfigError(
            "shadow_traits.access_policy.rag_exposure deve essere 'never_by_default'."
        )

    traits = _require_list(shadow["traits"], "shadow_traits.traits")
    seen: set[str] = set()
    for index, item in enumerate(traits):
        path = f"shadow_traits.traits[{index}]"
        trait = _require_object(item, path)
        _require_keys(trait, ["id", "intensity", "triggers"], path)
        trait_id = trait["id"]
        if not isinstance(trait_id, str) or not trait_id:
            raise ConfigError(f"{path}.id: attesa una stringa non vuota.")
        if trait_id in seen:
            raise ConfigError(f"{path}.id duplicato: {trait_id}")
        seen.add(trait_id)
        _require_number(trait["intensity"], 1, 10, f"{path}.intensity")
        _require_list(trait["triggers"], f"{path}.triggers")


def _validate_current_state(state: dict[str, Any]) -> None:
    _require_keys(
        state,
        [
            "schema_version",
            "state_id",
            "identity_id",
            "updated_at",
            "operational_status",
            "clock",
            "activity",
            "emotion",
            "mental_load",
            "mental_load_thresholds",
            "routine",
            "sleep",
            "availability",
            "heartbeat",
            "flags",
        ],
        "current_state",
    )
    _require_iso_datetime(state["updated_at"], "current_state.updated_at")
    _require_number(state["mental_load"], 0, 100, "current_state.mental_load")
    thresholds = _require_object(
        state["mental_load_thresholds"], "current_state.mental_load_thresholds"
    )
    _require_keys(thresholds, ["soft", "overload"], "current_state.mental_load_thresholds")
    soft = thresholds["soft"]
    overload = thresholds["overload"]
    if (
        isinstance(soft, bool)
        or not isinstance(soft, (int, float))
        or isinstance(overload, bool)
        or not isinstance(overload, (int, float))
    ):
        raise ConfigError("current_state.mental_load_thresholds: attesi due numeri.")
    if float(soft) >= float(overload):
        raise ConfigError(
            "current_state.mental_load_thresholds: 'soft' deve essere minore di 'overload'."
        )
    for key in [
        "clock",
        "activity",
        "emotion",
        "routine",
        "sleep",
        "availability",
        "heartbeat",
        "flags",
    ]:
        _require_object(state[key], f"current_state.{key}")

    flags = state["flags"]
    if flags.get("shadow_direct_read_enabled") is not False:
        raise ConfigError(
            "current_state.flags.shadow_direct_read_enabled deve essere false."
        )


def _validate_world_history(world: dict[str, Any]) -> None:
    _require_keys(
        world,
        ["schema_version", "dataset_id", "identity_id", "birth_date", "events"],
        "world_history",
    )
    _require_iso_date(world["birth_date"], "world_history.birth_date")
    events = _require_list(world["events"], "world_history.events")
    seen: set[str] = set()
    for index, item in enumerate(events):
        path = f"world_history.events[{index}]"
        event = _require_object(item, path)
        _require_keys(event, ["id", "date", "title"], path)
        event_id = event["id"]
        if not isinstance(event_id, str) or not event_id:
            raise ConfigError(f"{path}.id: attesa una stringa non vuota.")
        if event_id in seen:
            raise ConfigError(f"{path}.id duplicato: {event_id}")
        seen.add(event_id)
        _require_iso_date(event["date"], f"{path}.date")
        _require_number(event.get("global_relevance"), 0, 10, f"{path}.global_relevance")
        _require_number(event.get("italy_relevance"), 0, 10, f"{path}.italy_relevance")


def _validate_social_graph(social: dict[str, Any]) -> None:
    _require_keys(
        social, ["schema_version", "identity_id", "scales", "people"], "social_graph"
    )
    _require_object(social["scales"], "social_graph.scales")
    people = _require_list(social["people"], "social_graph.people")
    seen: set[str] = set()
    for index, item in enumerate(people):
        path = f"social_graph.people[{index}]"
        person = _require_object(item, path)
        _require_keys(person, ["user_id", "display_name", "trust", "intimacy"], path)
        user_id = person["user_id"]
        if not isinstance(user_id, str) or not user_id:
            raise ConfigError(f"{path}.user_id: attesa una stringa non vuota.")
        if user_id in seen:
            raise ConfigError(f"{path}.user_id duplicato: {user_id}")
        seen.add(user_id)
        _require_number(person["trust"], 0, 100, f"{path}.trust")
        _require_number(person["intimacy"], 0, 100, f"{path}.intimacy")


def validate_configs(configs: dict[str, Any]) -> None:
    """Valida i cinque documenti e le invarianti incrociate."""

    for name in CONFIG_FILENAMES:
        if name not in configs:
            raise ConfigError(f"Configurazione mancante: {name}.")
        _require_object(configs[name], name)

    identity = configs["identity"]
    _validate_identity(identity)
    _validate_shadow_traits(configs["shadow_traits"])
    _validate_current_state(configs["current_state"])
    _validate_world_history(configs["world_history"])
    _validate_social_graph(configs["social_graph"])

    identity_id = identity["identity_id"]
    for name in ("shadow_traits", "current_state", "world_history", "social_graph"):
        if configs[name].get("identity_id") != identity_id:
            raise ConfigError(
                f"{name}.identity_id ({configs[name].get('identity_id')!r}) non corrisponde "
                f"a identity.identity_id ({identity_id!r})."
            )

    if configs["world_history"].get("birth_date") != identity["personal_data"]["birth_date"]:
        raise ConfigError(
            "world_history.birth_date non corrisponde a identity.personal_data.birth_date."
        )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"File di configurazione non trovato: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"JSON non valido in {path}: riga {exc.lineno}, colonna {exc.colno} ({exc.msg})"
        ) from exc
    except OSError as exc:
        raise ConfigError(f"Impossibile leggere {path}: {exc}") from exc


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        current = base.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            _deep_merge(current, value)
        else:
            base[key] = copy.deepcopy(value)


class ConfigStore:
    """Accesso in lettura/scrittura ai cinque file di configurazione."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root).expanduser().resolve() if root else project_root()
        self.config_dir = self.root / "config"
        self._lock = threading.RLock()

    def path_for(self, name: str) -> Path:
        if name not in CONFIG_FILENAMES:
            raise ConfigError(f"Configurazione sconosciuta: {name}.")
        return self.config_dir / CONFIG_FILENAMES[name]

    def load_all(self) -> dict[str, dict[str, Any]]:
        """Carica e valida tutti i documenti (senza cache)."""

        with self._lock:
            configs: dict[str, Any] = {}
            for name, filename in CONFIG_FILENAMES.items():
                configs[name] = _read_json(self.config_dir / filename)
            validate_configs(configs)
            return configs

    def get(self, name: str) -> dict[str, Any]:
        if name not in CONFIG_FILENAMES:
            raise ConfigError(f"Configurazione sconosciuta: {name}.")
        with self._lock:
            return copy.deepcopy(self.load_all()[name])

    @property
    def identity_id(self) -> str:
        return self.get("identity")["identity_id"]

    def timezone_name(self) -> str:
        identity = self.get("identity")
        return identity["personal_data"].get("timezone", DEFAULT_TIMEZONE)

    def write(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        """Scrive un documento in modo atomico, dopo aver validato l'intero set."""

        if name not in CONFIG_FILENAMES:
            raise ConfigError(f"Configurazione sconosciuta: {name}.")
        if not isinstance(data, dict):
            raise ValidationError(f"{name}: atteso un oggetto JSON.")

        with self._lock:
            configs = self.load_all()
            candidate = copy.deepcopy(data)
            candidate["updated_at"] = now_iso(
                configs["identity"]["personal_data"].get("timezone", DEFAULT_TIMEZONE)
            )
            configs[name] = candidate
            validate_configs(configs)
            self._atomic_write(self.path_for(name), candidate)
            return copy.deepcopy(candidate)

    def update_identity(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Aggiornamento amministrativo dell'identità.

        Non viene registrato come tool MCP. Il patch può modificare anagrafica,
        famiglia e personalità, ma non ``identity_id``. Se cambia la data di
        nascita, la validazione incrociata richiede che anche
        ``world_history.birth_date`` venga aggiornata coerentemente.
        """

        if not isinstance(patch, dict) or not patch:
            raise ValidationError(
                "Il patch dell'identità deve essere un oggetto non vuoto."
            )

        with self._lock:
            configs = self.load_all()
            identity = copy.deepcopy(configs["identity"])
            requested_identity = patch.get("identity_id")
            if requested_identity is not None and requested_identity != identity["identity_id"]:
                raise ValidationError("identity_id non può essere modificato.")

            _deep_merge(identity, patch)
            identity["identity_id"] = configs["identity"]["identity_id"]
            identity["updated_at"] = now_iso(
                configs["identity"]["personal_data"].get("timezone", DEFAULT_TIMEZONE)
            )
            configs["identity"] = identity
            try:
                validate_configs(configs)
            except ConfigError as exc:
                raise ValidationError(f"Identità non valida: {exc}") from exc
            self._atomic_write(self.path_for("identity"), identity)
            return copy.deepcopy(identity)

    def update_current_state(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Applica un patch ricorsivo a ``current_state.json``.

        I campi di sicurezza non possono essere alterati dal server: in
        particolare la lettura diretta dell'ombra resta sempre disabilitata.
        """

        if not isinstance(patch, dict) or not patch:
            raise ValidationError("Il patch dello stato corrente deve essere un oggetto non vuoto.")

        with self._lock:
            configs = self.load_all()
            state = copy.deepcopy(configs["current_state"])

            requested_identity = patch.get("identity_id")
            if requested_identity is not None and requested_identity != state["identity_id"]:
                raise ValidationError("identity_id non può essere modificato dallo stato corrente.")

            flags_patch = patch.get("flags")
            if isinstance(flags_patch, dict):
                if flags_patch.get("shadow_direct_read_enabled") not in (None, False):
                    raise ValidationError(
                        "shadow_direct_read_enabled non può essere abilitato dal server."
                    )

            _deep_merge(state, patch)
            state["identity_id"] = configs["identity"]["identity_id"]
            state["updated_at"] = now_iso(
                configs["identity"]["personal_data"].get("timezone", DEFAULT_TIMEZONE)
            )
            configs["current_state"] = state
            try:
                validate_configs(configs)
            except ConfigError as exc:
                raise ValidationError(f"Stato corrente non valido: {exc}") from exc
            self._atomic_write(self.path_for("current_state"), state)
            return copy.deepcopy(state)

    @staticmethod
    def _atomic_write(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            raise ConfigError(f"Impossibile scrivere {path}: {exc}") from exc
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
