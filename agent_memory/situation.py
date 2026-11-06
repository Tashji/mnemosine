"""Rapporto di situazione bounded, deterministico e in sola lettura (Fase 1).

Il rapporto risponde a una domanda limitata: «dove sono, come sto, cosa è
aperto, chi c'è, cosa posso fare e cosa non so?». Non delibera, non propone
azioni e non scrive nel profilo. I dati riservati dell'ombra non vengono
interrogati: i ricordi recenti arrivano esclusivamente dalla vista conscia.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .browser import BrowserPolicy
from .config import DEFAULT_TIMEZONE, ConfigStore
from .db import Database
from .errors import AgentMemoryError, ValidationError
from .executor import WhatsAppPolicy, _in_window
from .life import NEED_NAMES, normalize_life
from .routine import DAY_LABELS

SITUATION_VERSION = "1.0"
REPORT_TTL_MINUTES = 15
MAX_OPEN_LOOPS = 8
MAX_STREAM = 8
MAX_RECENT_EVENTS = 8
MAX_RELATIONSHIPS = 5
MAX_STEPS = 20
MAX_TEXT = 500


def _parse_iso(value: Any, timezone: ZoneInfo) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone)
    return moment.astimezone(timezone)


def _resolve_now(value: datetime | str | None, timezone: ZoneInfo) -> datetime:
    if value is None:
        return datetime.now(timezone).replace(microsecond=0)
    if isinstance(value, str):
        moment = _parse_iso(value, timezone)
        if moment is None:
            raise ValidationError("at: timestamp ISO 8601 non valido.")
        return moment.replace(microsecond=0)
    if not isinstance(value, datetime):
        raise ValidationError("at: atteso un timestamp ISO 8601 o un datetime.")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone)
    return value.astimezone(timezone).replace(microsecond=0)


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _closeness(trust: Any, intimacy: Any) -> str:
    values = [
        _number(value, default=-1.0)
        for value in (trust, intimacy)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not values:
        return "persona"
    score = max(values)
    if score >= 80:
        return "molto stretto"
    if score >= 65:
        return "stretto"
    if score >= 45:
        return "amico"
    if score >= 25:
        return "conoscente"
    return "persona"


def _age(birth_date: Any, today: date) -> int | None:
    if not isinstance(birth_date, str):
        return None
    try:
        born = date.fromisoformat(birth_date)
    except ValueError:
        return None
    age = today.year - born.year
    if (today.month, today.day) < (born.month, born.day):
        age -= 1
    return age if age >= 0 else None


class SituationBuilder:
    """Costruisce un rapporto senza modificare lo stato del profilo."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        at: datetime | str | None = None,
    ) -> None:
        self.config = ConfigStore(root)
        self.root = self.config.root
        self.identity = self.config.get("identity")
        self.state = self.config.get("current_state")
        self.social = self.config.get("social_graph")
        self.timezone_name = self.identity["personal_data"].get(
            "timezone", DEFAULT_TIMEZONE
        )
        try:
            self.timezone = ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValidationError(
                f"identity.personal_data.timezone: timezone non valida '{self.timezone_name}'."
            ) from exc
        self.now = _resolve_now(at, self.timezone)
        self.stamp = self.now.isoformat()

    # -- componenti ---------------------------------------------------------

    def _mode(self) -> str:
        raw_life = self.state.get("life")
        if isinstance(raw_life, dict) and raw_life.get("mode") in {
            "routine",
            "unstructured",
            "intent",
            "sleep",
        }:
            return str(raw_life["mode"])
        if self.state.get("operational_status") == "sleeping":
            return "sleep"
        return "routine"

    def _life(self) -> dict[str, Any]:
        return normalize_life(
            self.state.get("life"),
            now=self.stamp,
            mental_load=_number(self.state.get("mental_load")),
            mode=self._mode(),
        )

    def _activity(self, life: dict[str, Any]) -> dict[str, Any]:
        raw = self.state.get("activity")
        raw = raw if isinstance(raw, dict) else {}
        label = raw.get("label")
        if not isinstance(label, str) or not label.strip():
            label = "Presente"
        started = _parse_iso(raw.get("started_at"), self.timezone)
        expected = _parse_iso(raw.get("expected_end_at"), self.timezone)
        location = raw.get("location")
        environment = life.get("environment")
        environment = environment if isinstance(environment, dict) else {}
        if not isinstance(location, str) or not location.strip():
            location = environment.get("location")
        return {
            "id": raw.get("id"),
            "label": label.strip(),
            "mode": life["mode"],
            "category": raw.get("category"),
            "started_at": started.isoformat() if started else self.stamp,
            "expected_end_at": expected.isoformat() if expected else None,
            "location": location if isinstance(location, str) and location.strip() else None,
        }

    def _body(self, life: dict[str, Any]) -> dict[str, int]:
        needs = life.get("needs")
        needs = needs if isinstance(needs, dict) else {}
        return {
            name: int(_number(needs.get(name, {}).get("value"), default=50.0))
            for name in NEED_NAMES
        }

    def _open_loops(self, life: dict[str, Any]) -> list[dict[str, Any]]:
        raw_loops = life.get("open_loops")
        if not isinstance(raw_loops, list):
            return []
        loops: list[dict[str, Any]] = []
        for item in raw_loops:
            if not isinstance(item, dict):
                continue
            if item.get("status", "active") != "active":
                continue
            loop_id = item.get("id")
            title = item.get("title")
            if not isinstance(loop_id, str) or not loop_id.strip():
                continue
            if not isinstance(title, str) or not title.strip():
                continue
            due = _parse_iso(item.get("due_at"), self.timezone)
            touched = _parse_iso(item.get("last_touched_at"), self.timezone)
            loops.append(
                {
                    "id": loop_id.strip(),
                    "title": title.strip()[:MAX_TEXT],
                    "priority": max(
                        0, min(100, int(_number(item.get("priority"), default=50.0)))
                    ),
                    "due_at": due.isoformat() if due else None,
                    "next_step": (
                        item["next_step"].strip()[:MAX_TEXT]
                        if isinstance(item.get("next_step"), str)
                        and item["next_step"].strip()
                        else None
                    ),
                    "last_touched_at": touched.isoformat() if touched else None,
                }
            )
        loops.sort(
            key=lambda item: (
                -item["priority"],
                item["due_at"] is None,
                item["due_at"] or "",
            )
        )
        return loops[:MAX_OPEN_LOOPS]

    def _stream(self, life: dict[str, Any]) -> list[dict[str, Any]]:
        raw_stream = life.get("stream")
        if not isinstance(raw_stream, list):
            return []
        stream: list[dict[str, Any]] = []
        for item in raw_stream:
            if not isinstance(item, dict):
                continue
            at = _parse_iso(item.get("at"), self.timezone)
            kind = item.get("kind")
            text = item.get("text")
            if at is None or not isinstance(kind, str) or not isinstance(text, str):
                continue
            if not kind.strip() or not text.strip():
                continue
            stream.append(
                {
                    "at": at.isoformat(),
                    "kind": kind.strip()[:80],
                    "text": text.strip()[:MAX_TEXT],
                }
            )
        return stream[-MAX_STREAM:]

    def _relationships(self) -> list[dict[str, Any]]:
        people = self.social.get("people")
        if not isinstance(people, list):
            return []
        result: list[dict[str, Any]] = []
        for person in people:
            if not isinstance(person, dict):
                continue
            user_id = person.get("user_id")
            if not isinstance(user_id, str) or not user_id.strip():
                continue
            display_name = person.get("display_name")
            if not isinstance(display_name, str) or not display_name.strip():
                display_name = person.get("full_name")
            if not isinstance(display_name, str) or not display_name.strip():
                display_name = user_id
            last = _parse_iso(
                person.get("last_interaction_at")
                or person.get("first_interaction_at"),
                self.timezone,
            )
            if last is None:
                last_contact = "nessun contatto registrato"
                last_timestamp = 0.0
            else:
                days = max(0.0, (self.now - last).total_seconds() / 86_400)
                if days < 1:
                    last_contact = "oggi"
                elif days < 2:
                    last_contact = "ieri"
                else:
                    last_contact = f"{int(days)} giorni fa"
                last_timestamp = last.timestamp()
            trust = person.get("trust")
            intimacy = person.get("intimacy")
            result.append(
                {
                    "user_id": user_id.strip(),
                    "display_name": display_name.strip()[:80],
                    "closeness": _closeness(trust, intimacy),
                    "last_contact": last_contact,
                    "open_thread": (
                        person["open_thread"].strip()[:MAX_TEXT]
                        if isinstance(person.get("open_thread"), str)
                        and person["open_thread"].strip()
                        else None
                    ),
                    "_last_timestamp": last_timestamp,
                }
            )
        result.sort(
            key=lambda item: (item["_last_timestamp"], item["display_name"]),
            reverse=True,
        )
        for item in result:
            item.pop("_last_timestamp", None)
        return result[:MAX_RELATIONSHIPS]

    def _recent_events(self) -> list[dict[str, Any]]:
        database = Database(self.root)
        if not database.timeline_path.is_file():
            return []
        try:
            with database.timeline() as conn:
                rows = conn.execute(
                    "SELECT event_time, title, importance "
                    "FROM conscious_episodic_memories "
                    "ORDER BY event_time DESC, id DESC LIMIT ?",
                    (MAX_RECENT_EVENTS,),
                ).fetchall()
        except AgentMemoryError:
            return []
        events: list[dict[str, Any]] = []
        for row in rows:
            title = row["title"]
            if not isinstance(title, str) or not title.strip():
                continue
            events.append(
                {
                    "at": row["event_time"],
                    "kind": "memory",
                    "text": title.strip()[:MAX_TEXT],
                    "importance": row["importance"],
                }
            )
        return events

    def _time_budget(self, activity: dict[str, Any]) -> int | None:
        routine = self.state.get("routine")
        candidates = [activity.get("expected_end_at")]
        if isinstance(routine, dict):
            candidates.append(routine.get("next_transition_at"))
        for candidate in candidates:
            moment = _parse_iso(candidate, self.timezone)
            if moment is None:
                continue
            minutes = int((moment - self.now).total_seconds() // 60)
            if minutes > 0:
                return min(minutes, 24 * 60)
        return None

    def _affordances(
        self,
        life: dict[str, Any],
        activity: dict[str, Any],
        relationships: list[dict[str, Any]],
    ) -> dict[str, Any]:
        environment = life.get("environment")
        environment = environment if isinstance(environment, dict) else {}
        tools = environment.get("tools")
        tools = (
            [tool.strip() for tool in tools if isinstance(tool, str) and tool.strip()]
            if isinstance(tools, list)
            else []
        )
        known_resources = environment.get("known_resources")
        known_resources = (
            [
                resource.strip()
                for resource in known_resources
                if isinstance(resource, str) and resource.strip()
            ]
            if isinstance(known_resources, list)
            else []
        )
        unknowns = environment.get("unknowns")
        unknowns = (
            [
                item.strip()
                for item in unknowns
                if isinstance(item, str) and item.strip()
            ]
            if isinstance(unknowns, list)
            else []
        )
        if not known_resources:
            unknowns.append("cosa c'è in casa")
        unknowns.extend(["messaggi non letti", "meteo"])
        deduped: list[str] = []
        for item in unknowns:
            if item not in deduped:
                deduped.append(item)

        try:
            browser = BrowserPolicy.from_root(self.root)
            browser_origins = len(browser.allowed_origins) if browser.enabled else 0
        except AgentMemoryError:
            browser_origins = 0

        contacts = [person["display_name"] for person in relationships]
        return {
            "location": activity.get("location"),
            "time_budget_minutes": self._time_budget(activity),
            "tools": tools,
            "contacts": contacts,
            "browser_origins_allowed": browser_origins,
            "known_resources": known_resources,
            "unknowns": deduped[:8],
        }

    def _constraints(self) -> dict[str, Any]:
        heartbeat = self.state.get("heartbeat")
        heartbeat = heartbeat if isinstance(heartbeat, dict) else {}
        availability = self.state.get("availability")
        availability = availability if isinstance(availability, dict) else {}
        flags = self.state.get("flags")
        flags = flags if isinstance(flags, dict) else {}

        quiet_hours = False
        try:
            policy = WhatsAppPolicy.load(self.root)
            quiet_hours = _in_window(
                self.now.time(),
                time.fromisoformat(policy.quiet_start),
                time.fromisoformat(policy.quiet_end),
            )
        except (AgentMemoryError, ValueError):
            quiet_hours = False

        return {
            "maintenance_mode": bool(flags.get("maintenance_mode", False)),
            "quiet_hours": quiet_hours,
            "accepting_messages": bool(
                availability.get("accepting_messages", False)
            ),
            "pending_action": bool(heartbeat.get("pending_impulse")),
            "max_steps": MAX_STEPS,
        }

    def _self(self) -> dict[str, Any]:
        personal = self.identity.get("personal_data")
        personal = personal if isinstance(personal, dict) else {}
        first_name = personal.get("first_name")
        last_name = personal.get("last_name")
        full_name = personal.get("full_name")
        if not isinstance(full_name, str) or not full_name.strip():
            full_name = " ".join(
                part.strip()
                for part in (first_name, last_name)
                if isinstance(part, str) and part.strip()
            )
        biography = self.identity.get("biography")
        biography = biography if isinstance(biography, dict) else {}
        current_situation = biography.get("current_situation")
        if not isinstance(current_situation, str) or not current_situation.strip():
            current_situation = None
        return {
            "display_name": full_name.strip() if full_name.strip() else None,
            "age": _age(personal.get("birth_date"), self.now.date()),
            "current_situation": (
                current_situation.strip()[:MAX_TEXT] if current_situation else None
            ),
        }

    def _emotion(self) -> dict[str, Any]:
        raw = self.state.get("emotion")
        raw = raw if isinstance(raw, dict) else {}
        label = raw.get("label")
        return {
            "label": label.strip()
            if isinstance(label, str) and label.strip()
            else "neutro",
            "valence": _number(raw.get("valence"), default=0.0),
            "arousal": _number(raw.get("arousal"), default=0.3),
            "intensity": int(_number(raw.get("intensity"), default=0.0)),
        }

    # -- costruzione --------------------------------------------------------

    def build(self) -> dict[str, Any]:
        life = self._life()
        activity = self._activity(life)
        relationships = self._relationships()
        affordances = self._affordances(life, activity, relationships)
        recent_events = self._recent_events()
        constraints = self._constraints()
        return {
            "situation_version": SITUATION_VERSION,
            "situation_id": f"sit_{self.now.strftime('%Y%m%d_%H%M%S')}",
            "episode_id": None,
            "generated_at": self.stamp,
            "expires_at": (self.now + timedelta(minutes=REPORT_TTL_MINUTES)).isoformat(),
            "mode": "simulated",
            "now": {
                "local_time": self.stamp,
                "day": DAY_LABELS[self.now.weekday()],
                "timezone": self.timezone_name,
            },
            "self": self._self(),
            "body": self._body(life),
            "emotion": self._emotion(),
            "activity": activity,
            "stream": self._stream(life),
            "open_loops": self._open_loops(life),
            "relationships": relationships,
            "affordances": affordances,
            "recent_events": recent_events,
            "constraints": constraints,
            "unknowns": copy.deepcopy(affordances["unknowns"]),
            "privacy": {
                "shadow_included": False,
                "hidden_memories_included": False,
                "secrets_included": False,
            },
        }


def _time_label(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return moment.strftime("%H:%M")


def _sentence(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    if cleaned[-1] in ".!?":
        return cleaned
    return f"{cleaned}."


def render_situation(report: dict[str, Any]) -> str:
    """Rende il rapporto leggibile senza aggiungere suggerimenti o decisioni."""

    now = report.get("now")
    now = now if isinstance(now, dict) else {}
    local_time = _time_label(now.get("local_time"))
    day = now.get("day")
    if isinstance(day, str) and day:
        opening = f"Sono le {local_time} di {day}." if local_time else f"È {day}."
    else:
        opening = f"Sono le {local_time}." if local_time else "Adesso."
    parts = [opening]

    activity = report.get("activity")
    activity = activity if isinstance(activity, dict) else {}
    label = activity.get("label")
    if isinstance(label, str) and label.strip():
        if activity.get("mode") == "unstructured":
            parts.append(_sentence(f"Sei {label.strip().lower()}"))
        else:
            parts.append(_sentence(f"Adesso: {label.strip()}"))

    body = report.get("body")
    body = body if isinstance(body, dict) else {}
    if body:
        names = {
            "energy": "energia",
            "hunger": "fame",
            "social": "socialità",
            "curiosity": "curiosità",
            "stress": "stress",
        }
        parts.append(
            "Corpo: "
            + ", ".join(
                f"{names.get(key, key)} {value}/100"
                for key, value in body.items()
                if key in names
            )
            + "."
        )

    stream = report.get("stream")
    if isinstance(stream, list) and stream:
        lines = []
        for item in stream[-4:]:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            at = _time_label(item.get("at"))
            if isinstance(text, str) and text.strip():
                lines.append(f"- {at + ' ' if at else ''}{text.strip()}")
        if lines:
            parts.append("Poco fa:\n" + "\n".join(lines))

    loops = report.get("open_loops")
    if isinstance(loops, list) and loops:
        lines = []
        for item in loops:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            if not isinstance(title, str) or not title.strip():
                continue
            line = f"- {title.strip()}"
            if isinstance(item.get("next_step"), str) and item["next_step"].strip():
                line += f" (prossimo passo: {item['next_step'].strip()})"
            lines.append(line)
        if lines:
            parts.append("Loop aperti:\n" + "\n".join(lines))

    relationships = report.get("relationships")
    if isinstance(relationships, list) and relationships:
        lines = []
        for person in relationships:
            if not isinstance(person, dict):
                continue
            name = person.get("display_name")
            if not isinstance(name, str) or not name.strip():
                continue
            detail = person.get("closeness")
            detail = detail if isinstance(detail, str) else "persona"
            last = person.get("last_contact")
            if isinstance(last, str) and last:
                lines.append(f"- {name.strip()}, {detail}, ultimo contatto {last}.")
            else:
                lines.append(f"- {name.strip()}, {detail}.")
        if lines:
            parts.append("Relazioni:\n" + "\n".join(lines))

    affordances = report.get("affordances")
    affordances = affordances if isinstance(affordances, dict) else {}
    tools = affordances.get("tools")
    if isinstance(tools, list) and tools:
        parts.append("Hai a disposizione: " + ", ".join(str(tool) for tool in tools) + ".")
    budget = affordances.get("time_budget_minutes")
    if isinstance(budget, int) and budget > 0:
        parts.append(f"Tempo stimato prima del prossimo passaggio: {budget} minuti.")
    unknowns = report.get("unknowns")
    if isinstance(unknowns, list) and unknowns:
        parts.append(
            "Non sai: " + ", ".join(str(item) for item in unknowns) + "."
        )

    constraints = report.get("constraints")
    constraints = constraints if isinstance(constraints, dict) else {}
    notes = []
    if constraints.get("maintenance_mode"):
        notes.append("manutenzione attiva")
    if constraints.get("quiet_hours"):
        notes.append("ore di silenzio")
    if constraints.get("pending_action"):
        notes.append("c'è un'azione in attesa")
    if not constraints.get("accepting_messages", True):
        notes.append("non stai accettando messaggi")
    if notes:
        parts.append("Vincoli: " + ", ".join(notes) + ".")

    parts.append("Cosa vuoi fare adesso?")
    return "\n\n".join(part for part in parts if part)


__all__ = ["SituationBuilder", "render_situation"]
