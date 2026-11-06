"""Stato di vita e normalizzazione dei suoi dati (Fase 1).

Questo modulo non decide e non esegue: descrive bisogni, loop aperti,
ambiente, attenzione e stream. Il rapporto di situazione lo legge senza
modificare il profilo. Un profilo vecchio può non avere ``current_state.life``:
in quel caso il chiamante usa i default, senza riscrivere lo stato.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from .errors import ConfigError

LIFE_SCHEMA_VERSION = "1.0"
NEED_NAMES = ("energy", "hunger", "social", "curiosity", "stress")
TRENDS = {"up", "down", "stable", "unknown"}
MODES = {"routine", "unstructured", "intent", "sleep"}
LOOP_STATUSES = {"active", "paused", "done"}
MAX_OPEN_LOOPS = 20
MAX_STREAM = 8
MAX_TEXT = 500
MAX_ENVIRONMENT_ITEMS = 20
DEFAULT_TOOLS = ("browser", "files", "whatsapp_outbox")


def _clamp(value: float) -> int:
    return int(max(0.0, min(100.0, round(float(value)))))


def _number(value: Any, *, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _iso(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value.strip()


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"life.{field}: attesa una lista di stringhe.")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"life.{field}[{index}]: attesa una stringa non vuota.")
        if len(item.strip()) > MAX_TEXT:
            raise ConfigError(f"life.{field}[{index}]: testo troppo lungo.")
        result.append(item.strip())
    if len(result) > MAX_ENVIRONMENT_ITEMS:
        raise ConfigError(
            f"life.{field}: massimo {MAX_ENVIRONMENT_ITEMS} elementi."
        )
    return result


def default_needs(now: str, mental_load: float = 0.0) -> dict[str, dict[str, Any]]:
    """Bisogni iniziali ragionevoli, senza fingere una misura precisa."""

    load = max(0.0, min(100.0, _number(mental_load)))
    energy = _clamp(70.0 - max(0.0, load - 20.0) * 0.4)
    return {
        "energy": {
            "value": energy,
            "trend": "stable",
            "updated_at": now,
            "source": "derived" if load else "default",
        },
        "hunger": {
            "value": 50,
            "trend": "stable",
            "updated_at": now,
            "source": "default",
        },
        "social": {
            "value": 50,
            "trend": "stable",
            "updated_at": now,
            "source": "default",
        },
        "curiosity": {
            "value": 50,
            "trend": "stable",
            "updated_at": now,
            "source": "default",
        },
        "stress": {
            "value": _clamp(load),
            "trend": "stable",
            "updated_at": now,
            "source": "derived" if load else "default",
        },
    }


def default_life(
    now: str,
    *,
    mental_load: float = 0.0,
    mode: str = "routine",
    location: str | None = None,
) -> dict[str, Any]:
    """Blocco ``current_state.life`` per un profilo che non ne ha ancora uno."""

    stamp = _iso(now)
    if stamp is None:
        stamp = datetime.now().astimezone().replace(microsecond=0).isoformat()
    clean_location = location.strip() if isinstance(location, str) and location.strip() else None
    return {
        "schema_version": LIFE_SCHEMA_VERSION,
        "updated_at": stamp,
        "mode": mode if mode in MODES else "routine",
        "needs": default_needs(stamp, mental_load),
        "open_loops": [],
        "environment": {
            "location": clean_location,
            "time_budget_minutes": None,
            "tools": list(DEFAULT_TOOLS),
            "known_resources": [],
            "unknowns": [],
        },
        "attention": None,
        "mental_content": None,
        "stream": [],
    }


def _normalize_stream(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        at = _iso(item.get("at"))
        kind = item.get("kind")
        text = item.get("text")
        if at is None or not isinstance(kind, str) or not kind.strip():
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        entries.append(
            {
                "at": at,
                "kind": kind.strip()[:80],
                "text": text.strip()[:MAX_TEXT],
            }
        )
    return entries[-MAX_STREAM:]


def normalize_life(
    raw: Any,
    *,
    now: str,
    mental_load: float = 0.0,
    mode: str = "routine",
) -> dict[str, Any]:
    """Restituisce un blocco completo, senza modificare ``raw``.

    È volutamente tollerante: i profili vecchi e i patch parziali devono poter
    essere letti. La validazione stretta avviene in ``validate_life`` quando il
    documento viene scritto.
    """

    base = default_life(now, mental_load=mental_load, mode=mode)
    if not isinstance(raw, dict):
        return base

    result = copy.deepcopy(base)
    if raw.get("mode") in MODES:
        result["mode"] = raw["mode"]
    updated = _iso(raw.get("updated_at"))
    if updated is not None:
        result["updated_at"] = updated

    raw_needs = raw.get("needs")
    if isinstance(raw_needs, dict):
        for name, item in raw_needs.items():
            if not isinstance(name, str) or not name.strip():
                continue
            base_item = result["needs"].get(name)
            if base_item is None:
                base_item = {
                    "value": 50,
                    "trend": "stable",
                    "updated_at": result["updated_at"],
                    "source": "profile",
                }
            if isinstance(item, dict):
                merged = {**copy.deepcopy(base_item), **copy.deepcopy(item)}
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                merged = {**copy.deepcopy(base_item), "value": item}
            else:
                continue
            trend = merged.get("trend")
            if trend not in TRENDS:
                trend = "stable"
            stamp = _iso(merged.get("updated_at")) or result["updated_at"]
            source = merged.get("source")
            if not isinstance(source, str) or not source.strip():
                source = "profile"
            result["needs"][name] = {
                "value": _clamp(_number(merged.get("value"), default=50.0)),
                "trend": trend,
                "updated_at": stamp,
                "source": source.strip(),
            }

    raw_loops = raw.get("open_loops")
    if isinstance(raw_loops, list):
        result["open_loops"] = [
            copy.deepcopy(item)
            for item in raw_loops
            if isinstance(item, dict)
        ][:MAX_OPEN_LOOPS]

    raw_environment = raw.get("environment")
    if isinstance(raw_environment, dict):
        environment = {
            **copy.deepcopy(result["environment"]),
            **copy.deepcopy(raw_environment),
        }
        location = environment.get("location")
        environment["location"] = (
            location.strip()
            if isinstance(location, str) and location.strip()
            else None
        )
        budget = environment.get("time_budget_minutes")
        if isinstance(budget, bool) or not isinstance(budget, (int, float)):
            environment["time_budget_minutes"] = None
        else:
            environment["time_budget_minutes"] = max(0, min(24 * 60, int(budget)))
        for key in ("tools", "known_resources", "unknowns"):
            values = environment.get(key)
            if not isinstance(values, list):
                environment[key] = []
                continue
            environment[key] = [
                item.strip()
                for item in values
                if isinstance(item, str) and item.strip()
            ][:MAX_ENVIRONMENT_ITEMS]
        result["environment"] = environment

    if "stream" in raw:
        result["stream"] = _normalize_stream(raw.get("stream"))

    for key in ("attention", "mental_content"):
        if key not in raw:
            continue
        raw_value = raw.get(key)
        if raw_value is None:
            result[key] = None
        elif isinstance(raw_value, str):
            cleaned = raw_value.strip()
            result[key] = cleaned[:MAX_TEXT] or None

    result["schema_version"] = LIFE_SCHEMA_VERSION
    return result


def validate_life(life: Any) -> None:
    """Valida il blocco ``life`` quando viene scritto nel profilo.

    I campi sono opzionali per permettere patch parziali; ``normalize_life``
    completa ciò che manca al momento della lettura.
    """

    if not isinstance(life, dict):
        raise ConfigError("current_state.life: atteso un oggetto.")

    if "schema_version" in life and not isinstance(life["schema_version"], str):
        raise ConfigError("current_state.life.schema_version: attesa una stringa.")
    if "updated_at" in life and _iso(life["updated_at"]) is None:
        raise ConfigError("current_state.life.updated_at: timestamp ISO non valido.")
    if "mode" in life and life["mode"] not in MODES:
        raise ConfigError(
            "current_state.life.mode: ammessi routine, unstructured, intent, sleep."
        )

    needs = life.get("needs")
    if needs is not None:
        if not isinstance(needs, dict):
            raise ConfigError("current_state.life.needs: atteso un oggetto.")
        for name, item in needs.items():
            if not isinstance(name, str) or not name.strip():
                raise ConfigError("current_state.life.needs: nome non valido.")
            if not isinstance(item, dict):
                raise ConfigError(f"current_state.life.needs.{name}: atteso un oggetto.")
            value = item.get("value")
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= float(value) <= 100
            ):
                raise ConfigError(
                    f"current_state.life.needs.{name}.value: atteso un numero 0-100."
                )
            if "trend" in item and item["trend"] not in TRENDS:
                raise ConfigError(
                    f"current_state.life.needs.{name}.trend: valore non valido."
                )
            if "updated_at" in item and _iso(item["updated_at"]) is None:
                raise ConfigError(
                    f"current_state.life.needs.{name}.updated_at: timestamp non valido."
                )
            if "source" in item and not isinstance(item["source"], str):
                raise ConfigError(
                    f"current_state.life.needs.{name}.source: attesa una stringa."
                )

    loops = life.get("open_loops")
    if loops is not None:
        if not isinstance(loops, list):
            raise ConfigError("current_state.life.open_loops: attesa una lista.")
        if len(loops) > MAX_OPEN_LOOPS:
            raise ConfigError(
                f"current_state.life.open_loops: massimo {MAX_OPEN_LOOPS} elementi."
            )
        seen: set[str] = set()
        for index, loop in enumerate(loops):
            if not isinstance(loop, dict):
                raise ConfigError(
                    f"current_state.life.open_loops[{index}]: atteso un oggetto."
                )
            loop_id = loop.get("id")
            if not isinstance(loop_id, str) or not loop_id.strip():
                raise ConfigError(
                    f"current_state.life.open_loops[{index}].id: attesa una stringa."
                )
            if loop_id in seen:
                raise ConfigError(
                    f"current_state.life.open_loops[{index}].id: duplicato."
                )
            seen.add(loop_id)
            title = loop.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ConfigError(
                    f"current_state.life.open_loops[{index}].title: attesa una stringa."
                )
            if "status" in loop and loop["status"] not in LOOP_STATUSES:
                raise ConfigError(
                    f"current_state.life.open_loops[{index}].status: valore non valido."
                )
            if "priority" in loop:
                priority = loop["priority"]
                if (
                    isinstance(priority, bool)
                    or not isinstance(priority, (int, float))
                    or not 0 <= float(priority) <= 100
                ):
                    raise ConfigError(
                        f"current_state.life.open_loops[{index}].priority: 0-100."
                    )
            for key in ("due_at", "last_touched_at"):
                if key in loop and loop[key] is not None and _iso(loop[key]) is None:
                    raise ConfigError(
                        f"current_state.life.open_loops[{index}].{key}: timestamp non valido."
                    )
            if "next_step" in loop and not isinstance(loop["next_step"], str):
                raise ConfigError(
                    f"current_state.life.open_loops[{index}].next_step: attesa una stringa."
                )
            if "visibility" in loop and loop["visibility"] != "conscious":
                raise ConfigError(
                    f"current_state.life.open_loops[{index}].visibility: ammesso solo conscious."
                )

    environment = life.get("environment")
    if environment is not None:
        if not isinstance(environment, dict):
            raise ConfigError("current_state.life.environment: atteso un oggetto.")
        if "location" in environment and environment["location"] is not None:
            if not isinstance(environment["location"], str):
                raise ConfigError(
                    "current_state.life.environment.location: attesa una stringa o null."
                )
        if "time_budget_minutes" in environment and environment["time_budget_minutes"] is not None:
            budget = environment["time_budget_minutes"]
            if (
                isinstance(budget, bool)
                or not isinstance(budget, int)
                or not 0 <= budget <= 24 * 60
            ):
                raise ConfigError(
                    "current_state.life.environment.time_budget_minutes: intero 0-1440."
                )
        for key in ("tools", "known_resources", "unknowns"):
            if key in environment:
                _string_list(environment[key], f"environment.{key}")

    stream = life.get("stream")
    if stream is not None:
        if not isinstance(stream, list):
            raise ConfigError("current_state.life.stream: attesa una lista.")
        if len(stream) > MAX_STREAM:
            raise ConfigError(
                f"current_state.life.stream: massimo {MAX_STREAM} elementi."
            )
        for index, item in enumerate(stream):
            if not isinstance(item, dict):
                raise ConfigError(
                    f"current_state.life.stream[{index}]: atteso un oggetto."
                )
            if _iso(item.get("at")) is None:
                raise ConfigError(
                    f"current_state.life.stream[{index}].at: timestamp non valido."
                )
            if not isinstance(item.get("kind"), str) or not item["kind"].strip():
                raise ConfigError(
                    f"current_state.life.stream[{index}].kind: attesa una stringa."
                )
            if not isinstance(item.get("text"), str) or not item["text"].strip():
                raise ConfigError(
                    f"current_state.life.stream[{index}].text: attesa una stringa."
                )
            if len(item["text"].strip()) > MAX_TEXT:
                raise ConfigError(
                    f"current_state.life.stream[{index}].text: massimo {MAX_TEXT} caratteri."
                )

    for key in ("attention", "mental_content"):
        if key not in life:
            continue
        value = life[key]
        if value is not None and not isinstance(value, str):
            raise ConfigError(
                f"current_state.life.{key}: attesa una stringa o null."
            )
        if isinstance(value, str) and len(value.strip()) > MAX_TEXT:
            raise ConfigError(
                f"current_state.life.{key}: massimo {MAX_TEXT} caratteri."
            )


__all__ = [
    "LIFE_SCHEMA_VERSION",
    "LOOP_STATUSES",
    "MAX_OPEN_LOOPS",
    "MAX_STREAM",
    "MODES",
    "NEED_NAMES",
    "TRENDS",
    "default_life",
    "default_needs",
    "normalize_life",
    "validate_life",
]
