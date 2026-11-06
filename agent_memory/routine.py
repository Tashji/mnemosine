"""Routine giornaliera e allineamento dello stato corrente (Fase 6).

Questa prima tranche implementa il generatore di routine e la scelta
dell'attività in corso. Il motore è deterministico: legge identità, interessi,
formazione e lavoro, costruisce il piano della giornata e aggiorna
``current_state.json`` senza chiamare un modello linguistico.

Non avvia un demone e non modifica il carico cognitivo: l'Heartbeat Loop e
l'evoluzione del carico restano ai passi successivi della Fase 6.

Uso:
    python -m agent_memory.routine --at 2026-10-25T15:00
    python -m agent_memory.routine --at 2026-10-25T15:00 --apply
    python -m agent_memory.routine --at 2026-10-25T15:00 --json
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import DEFAULT_TIMEZONE, ConfigStore, project_root
from .errors import AgentMemoryError, ValidationError
from .profile import _profile_lock

ROUTINE_VERSION = "v1"
DAY_LABELS = (
    "lunedì",
    "martedì",
    "mercoledì",
    "giovedì",
    "venerdì",
    "sabato",
    "domenica",
)
DEFAULT_SLEEP_WINDOW = ("00:00", "07:00")
GENERIC_HOBBIES = (
    (
        "Tempo libero",
        "leisure",
        "casa",
        "Si dedica a un interesse personale.",
    ),
    (
        "Lettura",
        "reading",
        "camera",
        "Legge qualcosa senza fretta.",
    ),
    (
        "Ascolta musica",
        "leisure",
        "camera",
        "Ascolta un disco.",
    ),
)
INTEREST_ACTIVITIES = (
    (
        "programm",
        (
            "Programmazione al PC",
            "solitary_focus",
            "camera",
            "Lavora a un piccolo progetto personale.",
        ),
    ),
    (
        "videogioch",
        ("Videogiochi", "gaming", "camera", "Una sessione ai videogiochi."),
    ),
    (
        "fotograf",
        (
            "Fotografia in città",
            "outdoor",
            "città",
            "Esce a fotografare.",
        ),
    ),
    (
        "fantascien",
        (
            "Lettura di fantascienza",
            "reading",
            "camera",
            "Legge un romanzo di fantascienza.",
        ),
    ),
    (
        "passegg",
        ("Passeggiata", "outdoor", "quartiere", "Cammina senza una meta precisa."),
    ),
    (
        "music",
        ("Ascolta musica", "leisure", "camera", "Ascolta un disco."),
    ),
    (
        "cucin",
        ("Cucina", "leisure", "cucina", "Prepara qualcosa."),
    ),
)

ActivitySpec = tuple[str, str, str, str]


def _parse_clock(value: Any, field: str) -> time:
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        raise ValidationError(f"{field}: usa il formato HH:MM.")
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{field}: orario non valido '{value}'.") from exc


def _activity_id(label: str, clock: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_") or "attivita"
    return f"act_{slug[:36]}_{clock.replace(':', '')}"


def _combine(day: date, clock: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, clock).replace(tzinfo=tz)


@dataclass(frozen=True)
class PlannedActivity:
    """Una voce del piano giornaliero, con orario locale in formato HH:MM."""

    clock: str
    label: str
    category: str
    location: str = "casa"
    details: str = ""

    @property
    def id(self) -> str:
        return _activity_id(self.label, self.clock)

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "time": self.clock,
            "label": self.label,
            "category": self.category,
            "location": self.location,
            "details": self.details,
        }


def _interest_hobbies(identity: dict[str, Any]) -> list[ActivitySpec]:
    hobbies: list[ActivitySpec] = []
    interests = identity.get("interests")
    if isinstance(interests, list):
        for interest in interests:
            if not isinstance(interest, str):
                continue
            key = interest.casefold()
            for token, activity in INTEREST_ACTIVITIES:
                if token in key:
                    if activity not in hobbies:
                        hobbies.append(activity)
                    break
    return hobbies or list(GENERIC_HOBBIES)


def _rotated_activities(
    hobbies: list[ActivitySpec], start: int, count: int
) -> list[ActivitySpec]:
    selected: list[ActivitySpec] = []
    for offset in range(len(hobbies) * 2):
        activity = hobbies[(start + offset) % len(hobbies)]
        if activity not in selected:
            selected.append(activity)
        if len(selected) >= count:
            break
    generic_index = 0
    while len(selected) < count:
        candidate = GENERIC_HOBBIES[generic_index % len(GENERIC_HOBBIES)]
        if candidate not in selected:
            selected.append(candidate)
        generic_index += 1
        if generic_index > len(GENERIC_HOBBIES) + count:
            break
    return selected


def _has_ongoing(items: Any) -> bool:
    if not isinstance(items, list):
        return False
    return any(
        isinstance(item, dict) and item.get("end_year") is None for item in items
    )


def _outdoor_break(
    hobbies: list[ActivitySpec], used: list[ActivitySpec]
) -> ActivitySpec:
    for hobby in hobbies:
        if hobby[1] == "outdoor" and hobby not in used:
            return hobby
    return ("Pausa", "rest", "casa", "Si prende una pausa.")


def _daily_activities(day: date, identity: dict[str, Any]) -> list[PlannedActivity]:
    hobbies = _interest_hobbies(identity)
    picks = _rotated_activities(hobbies, day.toordinal() % len(hobbies), 3)
    biography = identity.get("biography")
    profile = biography if isinstance(biography, dict) else {}
    has_study = _has_ongoing(profile.get("education"))
    has_work = _has_ongoing(profile.get("work"))

    if day.weekday() >= 5:
        specs: list[tuple[str, ActivitySpec]] = [
            (
                "07:00",
                ("Risveglio", "routine", "camera", "Si sveglia con calma."),
            ),
            (
                "09:00",
                (
                    "Colazione e lettura",
                    "meal",
                    "cucina",
                    "Colazione lenta e qualche pagina.",
                ),
            ),
            ("10:30", picks[0]),
            ("13:00", ("Pranzo", "meal", "cucina", "Pranzo a casa.")),
            ("15:00", picks[1]),
            ("17:30", _outdoor_break(hobbies, picks)),
            ("20:00", ("Cena", "meal", "cucina", "Cena.")),
            ("21:30", picks[2]),
            (
                "23:15",
                ("Riposo", "routine", "camera", "Rallenta prima di dormire."),
            ),
        ]
    else:
        if has_study:
            morning = (
                "Studio",
                "study",
                "camera",
                "Studia per il percorso universitario.",
            )
        else:
            morning = (
                "Lettura e pianificazione",
                "reading",
                "camera",
                "Legge e organizza la giornata.",
            )
        if has_work:
            afternoon = (
                "Lavoro al PC",
                "work",
                "camera",
                "Lavora al progetto part-time.",
            )
        else:
            afternoon = picks[0]
        specs = [
            (
                "07:00",
                ("Risveglio", "routine", "camera", "Si sveglia e inizia la giornata."),
            ),
            ("07:30", ("Colazione", "meal", "cucina", "Colazione veloce.")),
            ("08:30", morning),
            ("13:00", ("Pranzo", "meal", "cucina", "Pranzo a casa.")),
            ("14:30", afternoon),
            ("18:00", _outdoor_break(hobbies, [afternoon, picks[1]])),
            ("20:00", ("Cena", "meal", "cucina", "Cena.")),
            ("21:30", picks[1]),
            (
                "23:15",
                ("Riposo", "routine", "camera", "Rallenta prima di dormire."),
            ),
        ]

    return [
        PlannedActivity(
            clock=clock,
            label=label,
            category=category,
            location=location,
            details=details,
        )
        for clock, (label, category, location, details) in specs
    ]


def _reuse_plan(
    state: dict[str, Any], day: date
) -> list[PlannedActivity] | None:
    routine = state.get("routine")
    if not isinstance(routine, dict) or routine.get("date") != day.isoformat():
        return None
    raw = routine.get("planned_activities")
    if not isinstance(raw, list) or not raw:
        return None

    planned: list[PlannedActivity] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            return None
        clock = item.get("time")
        label = item.get("label")
        category = item.get("category")
        location = item.get("location") or "casa"
        details = item.get("details") or ""
        if not isinstance(clock, str) or not isinstance(label, str):
            return None
        if not isinstance(category, str) or not isinstance(location, str):
            return None
        if not isinstance(details, str):
            return None
        try:
            _parse_clock(clock, f"routine.planned_activities[{index}].time")
        except ValidationError:
            return None
        planned.append(
            PlannedActivity(
                clock=clock,
                label=label,
                category=category,
                location=location,
                details=details,
            )
        )

    planned.sort(key=lambda item: item.clock)
    if len({item.clock for item in planned}) != len(planned):
        return None
    return planned


def _sleep_window(state: dict[str, Any]) -> tuple[time, time]:
    sleep = state.get("sleep")
    window = sleep.get("window") if isinstance(sleep, dict) else None
    if isinstance(window, dict):
        start_value = window.get("start", DEFAULT_SLEEP_WINDOW[0])
        end_value = window.get("end", DEFAULT_SLEEP_WINDOW[1])
    else:
        start_value, end_value = DEFAULT_SLEEP_WINDOW
    return (
        _parse_clock(start_value, "current_state.sleep.window.start"),
        _parse_clock(end_value, "current_state.sleep.window.end"),
    )


def _in_sleep_window(now: time, start: time, end: time) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


def _sleep_bounds(
    now: datetime, start: time, end: time, tz: ZoneInfo
) -> tuple[datetime, datetime]:
    if start < end:
        return (
            _combine(now.date(), start, tz),
            _combine(now.date(), end, tz),
        )
    if now.time() >= start:
        return (
            _combine(now.date(), start, tz),
            _combine(now.date() + timedelta(days=1), end, tz),
        )
    return (
        _combine(now.date() - timedelta(days=1), start, tz),
        _combine(now.date(), end, tz),
    )


def _select_activity(
    now: datetime,
    day: date,
    planned: list[PlannedActivity],
    sleep_end: time,
    tz: ZoneInfo,
) -> tuple[PlannedActivity, datetime, datetime]:
    starts = [
        (_combine(day, time.fromisoformat(item.clock), tz), item)
        for item in planned
    ]
    if not starts:
        fallback = PlannedActivity(
            "Riposo", "routine", "camera", "Nessuna attività pianificata."
        )
        return (
            fallback,
            _combine(day, time(0, 0), tz),
            _combine(day + timedelta(days=1), time(0, 0), tz),
        )

    active_index: int | None = None
    for index, (start, _) in enumerate(starts):
        if start <= now:
            active_index = index
        else:
            break

    if active_index is None:
        start_dt = _combine(day, sleep_end, tz)
        if start_dt > now:
            start_dt = _combine(day, time(0, 0), tz)
        end_dt = starts[0][0]
        fallback = PlannedActivity(
            "Risveglio", "routine", "camera", "Inizia la giornata."
        )
        return fallback, start_dt, end_dt

    start_dt, item = starts[active_index]
    if active_index + 1 < len(starts):
        end_dt = starts[active_index + 1][0]
    else:
        end_dt = _combine(day + timedelta(days=1), time(0, 0), tz)
    return item, start_dt, end_dt


def _activity_dict(
    item: PlannedActivity,
    start: datetime,
    end: datetime,
    *,
    activity_id: str | None = None,
) -> dict[str, str]:
    return {
        "id": activity_id or item.id,
        "label": item.label,
        "category": item.category,
        "location": item.location,
        "started_at": start.isoformat(),
        "expected_end_at": end.isoformat(),
        "details": item.details,
    }


def availability_for(
    load: Any, thresholds: Any, *, sleeping: bool
) -> dict[str, Any]:
    """Stile di risposta coerente con sonno e carico cognitivo.

    Vive fuori dall'engine perché la usa anche l'Heartbeat, che aggiorna il
    carico senza ricostruire la routine.
    """

    if sleeping:
        return {
            "accepting_messages": False,
            "response_style": "assente",
            "notes": "Finestra di sonno simulato: nessuna risposta spontanea.",
        }
    if not isinstance(thresholds, dict):
        thresholds = {}
    soft = thresholds.get("soft", 60)
    overload = thresholds.get("overload", 80)
    numeric = isinstance(load, (int, float)) and not isinstance(load, bool)
    if numeric and load >= overload:
        return {
            "accepting_messages": True,
            "response_style": "breve",
            "notes": "Carico cognitivo oltre la soglia di sovraccarico.",
        }
    if numeric and load >= soft:
        return {
            "accepting_messages": True,
            "response_style": "ridotta",
            "notes": "Carico cognitivo sopra la soglia morbida.",
        }
    return {
        "accepting_messages": True,
        "response_style": "normale",
        "notes": "Nessun sovraccarico cognitivo attivo.",
    }


def _resolve_now(value: datetime | None, tz: ZoneInfo) -> datetime:
    if value is None:
        return datetime.now(tz).replace(microsecond=0)
    if not isinstance(value, datetime):
        raise ValidationError("--at: attesa una data e ora ISO 8601.")
    if value.tzinfo is None:
        return value.replace(tzinfo=tz, microsecond=0)
    return value.astimezone(tz).replace(microsecond=0)


class RoutineEngine:
    """Costruisce il piano della giornata e propone lo stato corrente."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        at: datetime | None = None,
    ) -> None:
        self.config = ConfigStore(root)
        self.identity = self.config.get("identity")
        self.state = self.config.get("current_state")

        personal = self.identity.get("personal_data")
        if not isinstance(personal, dict):
            raise ValidationError("identity.personal_data: oggetto mancante.")
        timezone_name = personal.get("timezone", DEFAULT_TIMEZONE)
        if not isinstance(timezone_name, str) or not timezone_name:
            raise ValidationError("identity.personal_data.timezone non valido.")
        try:
            self.timezone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
            raise ValidationError(
                f"Fuso orario non valido: {timezone_name!r}."
            ) from exc
        self.timezone_name = timezone_name
        self.now = _resolve_now(at, self.timezone)
        self.day = self.now.date()
        self.sleep_start, self.sleep_end = _sleep_window(self.state)
        self.is_sleep = _in_sleep_window(
            self.now.time(), self.sleep_start, self.sleep_end
        )
        self.planned = _reuse_plan(self.state, self.day) or _daily_activities(
            self.day, self.identity
        )
        self.mode = "weekend" if self.day.weekday() >= 5 else "weekday"
        self.day_label = DAY_LABELS[self.day.weekday()]

        if self.is_sleep:
            sleep_start, sleep_end = _sleep_bounds(
                self.now, self.sleep_start, self.sleep_end, self.timezone
            )
            sleep_item = PlannedActivity(
                "00:00",
                "Dorme",
                "sleep",
                "camera",
                "Sonno simulato.",
            )
            self.activity = _activity_dict(
                sleep_item,
                sleep_start,
                sleep_end,
                activity_id="act_sleep",
            )
            self.next_transition_at = sleep_end.isoformat()
        else:
            item, start, end = _select_activity(
                self.now, self.day, self.planned, self.sleep_end, self.timezone
            )
            self.activity = _activity_dict(item, start, end)
            self.next_transition_at = end.isoformat()

        self.routine = {
            "date": self.day.isoformat(),
            "mode": self.mode,
            "day_label": self.day_label,
            "next_transition_at": self.next_transition_at,
            "planned_activities": [item.to_dict() for item in self.planned],
        }
        sleep = copy.deepcopy(self.state.get("sleep", {}))
        if not isinstance(sleep, dict):
            raise ValidationError("current_state.sleep: atteso un oggetto.")
        sleep["is_sleeping"] = self.is_sleep

        self.patch: dict[str, Any] = {
            "operational_status": "sleeping" if self.is_sleep else "active",
            "clock": {
                "timezone": self.timezone_name,
                "local_time": self.now.isoformat(),
                "is_sleep_window": self.is_sleep,
            },
            "activity": self.activity,
            "routine": self.routine,
            "sleep": sleep,
            "availability": self._availability(),
        }
        self.candidate = {**copy.deepcopy(self.state), **copy.deepcopy(self.patch)}
        self.would_change = any(
            self.candidate.get(key) != self.state.get(key) for key in self.patch
        )

    def _availability(self) -> dict[str, Any]:
        return availability_for(
            self.state.get("mental_load", 0),
            self.state.get("mental_load_thresholds", {}),
            sleeping=self.is_sleep,
        )

    def preview(self) -> dict[str, Any]:
        return {
            "routine_version": ROUTINE_VERSION,
            "now": self.now.isoformat(),
            "timezone": self.timezone_name,
            "date": self.day.isoformat(),
            "mode": self.mode,
            "day_label": self.day_label,
            "is_sleep_window": self.is_sleep,
            "activity": copy.deepcopy(self.activity),
            "next_transition_at": self.next_transition_at,
            "planned_activities": [item.to_dict() for item in self.planned],
            "would_change": self.would_change,
            "state": copy.deepcopy(self.candidate),
        }

    def apply(self) -> dict[str, Any]:
        if not self.would_change:
            result = self.preview()
            result["applied"] = False
            return result
        state = self.config.update_current_state(copy.deepcopy(self.patch))
        result = self.preview()
        result["applied"] = True
        result["would_change"] = False
        result["state"] = state
        return result


def _parse_at(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Data e ora non valide: {value!r}. Usa ISO 8601, "
            "ad esempio 2026-10-25T15:00."
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Genera il piano della giornata e allinea current_state.json "
            "all'orario richiesto (Fase 6)."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory del progetto (default: quella che contiene il pacchetto).",
    )
    parser.add_argument(
        "--at",
        type=_parse_at,
        default=None,
        metavar="ISO8601",
        help=(
            "Data e ora di riferimento. Se manca il fuso orario viene usato "
            "quello dell'identità; default: adesso."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Scrive lo stato aggiornato. Senza questo flag mostra solo l'anteprima.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Stampa il piano o il risultato in JSON.",
    )
    return parser


def _print_result(result: dict[str, Any]) -> None:
    activity = result["activity"]
    print(
        f"Routine {result['routine_version']} — "
        f"{result['day_label']} {result['date']} ({result['mode']})"
    )
    print(f"Ora locale: {result['now']} ({result['timezone']})")
    print(f"Attività: {activity['label']} [{activity['category']}]")
    print(f"  {activity['started_at']} → {activity['expected_end_at']}")
    print(f"Prossimo cambio: {result['next_transition_at']}")
    print(f"Sonno: {'sì' if result['is_sleep_window'] else 'no'}")
    print(f"Carico cognitivo: {result['state'].get('mental_load')}/100")
    if result.get("applied") is True:
        print("Stato aggiornato.")
    elif result.get("applied") is False:
        print("Nessuna modifica: lo stato è già allineato.")
    print("Piano:")
    for item in result["planned_activities"]:
        marker = "→" if item["id"] == activity["id"] else " "
        print(f" {marker} {item['time']} {item['label']} [{item['category']}]")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = (
        Path(args.root).expanduser().resolve()
        if args.root is not None
        else project_root()
    )
    try:
        if args.apply:
            with _profile_lock(root):
                result = RoutineEngine(root, at=args.at).apply()
        else:
            result = RoutineEngine(root, at=args.at).preview()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_result(result)
        return 0
    except AgentMemoryError as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
