"""Carico cognitivo dinamico e Heartbeat Loop (Fase 6, seconda parte).

Il motore è deterministico e non chiama un modello linguistico. A ogni battito:

1. allinea routine e stato corrente all'orologio;
2. recupera carico cognitivo per il tempo trascorso (molto durante il sonno,
   poco da svegli);
3. valuta i trigger interni — ricorrenze dalla timeline, silenzio di una
   relazione significativa, noia per un'attività troppo lunga;
4. se un trigger è attivo e non ci sono freni (sonno, manutenzione, cooldown,
   limite giornaliero, sovraccarico) mette in coda un **impulso**, non un
   messaggio: la generazione del testo e l'invio restano due operazioni
   distinte, e nessun canale viene aperto da qui.

Durante la finestra di sonno il battito non produce impulsi e riduce il
carico. Il consolidamento produce una sintesi estrattiva dei ricordi consci,
con riferimenti alle fonti, senza inventare fatti o esporre l'ombra.

Uso:
    python -m agent_memory.heartbeat --at 2026-10-25T15:00
    python -m agent_memory.heartbeat --at 2026-10-25T15:00 --apply
    python -m agent_memory.heartbeat --loop --interval 30
    python -m agent_memory.heartbeat --interaction 2 --complexity complessa
    python -m agent_memory.heartbeat --deliver "Ciao, come stai?"
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import time as time_module
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .browser import BrowserPolicy
from .config import ConfigStore
from .db import Database
from .errors import AgentMemoryError, ValidationError
from .executor import MessageExecutor
from .operational import canonical
from .routine import RoutineEngine, availability_for

HEARTBEAT_VERSION = "v1"
DEFAULT_INTERVAL_MINUTES = 30
DEFAULT_COOLDOWN_MINUTES = 180
DEFAULT_MAX_IMPULSES_PER_DAY = 3
BOREDOM_MINUTES = 90.0
SOCIAL_SILENCE_DAYS = 3.0
SOCIAL_MIN_TRUST = 60
ANNIVERSARY_MIN_IMPORTANCE = 7
MAX_ELAPSED_MINUTES = 24 * 60.0
FULL_NIGHT_MINUTES = 360.0
MAX_MESSAGES_PER_CALL = 100
MAX_INTERACTION_SUMMARY_CHARS = 500
MAX_INTERACTION_META_CHARS = 80
DEFAULT_INTERACTION_SUMMARY = "Interazione registrata."
DEFAULT_INTERACTION_CHANNEL = "chat"

# Quanto pesa un messaggio sul carico cognitivo, secondo la complessità.
COMPLEXITY_LOAD: dict[str, float] = {
    "leggera": 2.0,
    "normale": 4.0,
    "complessa": 7.0,
}
AWAKE_RECOVERY_PER_HOUR = 2.0
SLEEP_RECOVERY_PER_HOUR = 15.0

# Valori scritti nei profili nuovi e recuperati dalle migrazioni.
HEARTBEAT_DEFAULTS: dict[str, Any] = {
    "interval_minutes": DEFAULT_INTERVAL_MINUTES,
    "cooldown_minutes": DEFAULT_COOLDOWN_MINUTES,
    "max_impulses_per_day": DEFAULT_MAX_IMPULSES_PER_DAY,
    "pending_impulse": None,
    "last_trigger": None,
    "last_impulse_at": None,
    "last_interaction_at": None,
    "impulses_today": {"date": None, "count": 0},
}


def _clamp_load(value: float) -> int:
    return int(max(0.0, min(100.0, round(value))))


def _parse_int(value: Any, default: int, label: str, low: int, high: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{label}: atteso un numero intero.")
    if not low <= value <= high:
        raise ValidationError(f"{label}: valore fuori intervallo [{low}, {high}].")
    return value


def _parse_moment(value: Any, now: datetime) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=now.tzinfo)
    return parsed


def elapsed_minutes(since: Any, now: datetime) -> float:
    """Minuti trascorsi da un istante ISO, mai negativi; ``None`` vale zero."""

    moment = _parse_moment(since, now)
    if moment is None:
        return 0.0
    return max(0.0, (now - moment).total_seconds() / 60.0)


def apply_interactions(load: float, messages: int, complexity: str) -> int:
    """Carico dopo ``messages`` messaggi della complessità indicata."""

    if complexity not in COMPLEXITY_LOAD:
        raise ValidationError(
            f"Complessità sconosciuta: {complexity!r}. "
            f"Valori ammessi: {', '.join(sorted(COMPLEXITY_LOAD))}."
        )
    if isinstance(messages, bool) or not isinstance(messages, int):
        raise ValidationError("messages: atteso un numero intero.")
    if not 1 <= messages <= MAX_MESSAGES_PER_CALL:
        raise ValidationError(f"messages: usa un valore tra 1 e {MAX_MESSAGES_PER_CALL}.")
    return _clamp_load(float(load) + COMPLEXITY_LOAD[complexity] * messages)


def recover_load(load: float, minutes: float, *, sleeping: bool) -> int:
    """Carico dopo un periodo di riposo: lento da svegli, rapido nel sonno."""

    if minutes <= 0:
        return _clamp_load(float(load))
    if sleeping and minutes >= FULL_NIGHT_MINUTES:
        return 0
    rate = SLEEP_RECOVERY_PER_HOUR if sleeping else AWAKE_RECOVERY_PER_HOUR
    return _clamp_load(float(load) - rate * minutes / 60.0)


@dataclass(frozen=True)
class HeartbeatPolicy:
    """Ritmo e freni del ciclo, leggibili da ``current_state.heartbeat``."""

    interval_minutes: int = DEFAULT_INTERVAL_MINUTES
    cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES
    max_impulses_per_day: int = DEFAULT_MAX_IMPULSES_PER_DAY

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> HeartbeatPolicy:
        raw = state.get("heartbeat")
        raw = raw if isinstance(raw, dict) else {}
        return cls(
            interval_minutes=_parse_int(
                raw.get("interval_minutes"),
                DEFAULT_INTERVAL_MINUTES,
                "heartbeat.interval_minutes",
                1,
                24 * 60,
            ),
            cooldown_minutes=_parse_int(
                raw.get("cooldown_minutes"),
                DEFAULT_COOLDOWN_MINUTES,
                "heartbeat.cooldown_minutes",
                0,
                7 * 24 * 60,
            ),
            max_impulses_per_day=_parse_int(
                raw.get("max_impulses_per_day"),
                DEFAULT_MAX_IMPULSES_PER_DAY,
                "heartbeat.max_impulses_per_day",
                0,
                24,
            ),
        )


class HeartbeatEngine:
    """Un battito del ciclo autonomo: stato, carico cognitivo e impulsi."""

    def __init__(
        self, root: str | Path | None = None, *, at: datetime | None = None
    ) -> None:
        self.config = ConfigStore(root)
        self.root = self.config.root
        self.routine = RoutineEngine(self.root, at=at)
        self.state = self.routine.state
        self.now = self.routine.now
        self.timezone = self.routine.timezone
        self.sleeping = self.routine.is_sleep
        self.policy = HeartbeatPolicy.from_state(self.state)
        self.heartbeat = self.state.get("heartbeat")
        if not isinstance(self.heartbeat, dict):
            self.heartbeat = {}
        self._patch: dict[str, Any] = {}
        self._candidate: dict[str, Any] = {}

    # -- carico cognitivo ---------------------------------------------------

    def _load(self) -> int:
        value = self.state.get("mental_load", 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0
        return _clamp_load(float(value))

    def _thresholds(self) -> dict[str, Any]:
        thresholds = self.state.get("mental_load_thresholds")
        return thresholds if isinstance(thresholds, dict) else {}

    @staticmethod
    def _clean_interaction_summary(summary: Any) -> str:
        if summary is None:
            return DEFAULT_INTERACTION_SUMMARY
        if not isinstance(summary, str):
            raise ValidationError("summary: attesa una stringa.")
        cleaned = summary.strip()
        if len(cleaned) > MAX_INTERACTION_SUMMARY_CHARS:
            raise ValidationError(
                f"summary: massimo {MAX_INTERACTION_SUMMARY_CHARS} caratteri."
            )
        if "\x00" in cleaned:
            raise ValidationError("summary: contiene caratteri non ammessi.")
        return cleaned or DEFAULT_INTERACTION_SUMMARY

    @staticmethod
    def _clean_optional_token(
        value: Any, label: str, *, default: str | None = None
    ) -> str | None:
        if value is None:
            return default
        if not isinstance(value, str):
            raise ValidationError(f"{label}: attesa una stringa.")
        cleaned = value.strip()
        if len(cleaned) > MAX_INTERACTION_META_CHARS:
            raise ValidationError(
                f"{label}: massimo {MAX_INTERACTION_META_CHARS} caratteri."
            )
        if "\x00" in cleaned or "\n" in cleaned or "\r" in cleaned:
            raise ValidationError(f"{label}: contiene caratteri non ammessi.")
        return cleaned or default

    def _record_social_interaction(
        self,
        user_id: str,
        stamp: str,
        summary: str,
        *,
        channel: str = DEFAULT_INTERACTION_CHANNEL,
        sentiment: str | None = None,
        memory_id: str | None = None,
    ) -> dict[str, Any]:
        """Aggiorna il contatto e fa ripartire la finestra di silenzio."""

        graph = self.config.get("social_graph")
        people = graph.get("people")
        if not isinstance(people, list):
            raise ValidationError("social_graph.people: attesa una lista.")
        person = next(
            (
                item
                for item in people
                if isinstance(item, dict) and item.get("user_id") == user_id
            ),
            None,
        )
        if person is None:
            raise ValidationError(
                f"user_id {user_id!r} non è nel grafo delle relazioni."
            )

        count = person.get("interaction_count", 0)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            count = 0
        person["interaction_count"] = count + 1
        person["last_interaction_at"] = stamp
        first = person.get("first_interaction_at")
        if not isinstance(first, str) or not first:
            person["first_interaction_at"] = stamp

        entry = {"timestamp": stamp, "channel": channel, "summary": summary,
                 "memory_id": memory_id, "sentiment": sentiment}
        conn = self.config._transaction_conn
        assert conn is not None
        conn.execute("INSERT INTO interaction_events(event_id, user_id, timestamp, entry) VALUES (?, ?, ?, ?)",
                     (uuid.uuid4().hex, user_id, stamp, canonical(entry)))

        self.config.write("social_graph", graph)
        return {
            "user_id": user_id,
            "display_name": person.get("display_name"),
            "last_interaction_at": stamp,
            "interaction_count": person["interaction_count"],
            "channel": channel,
            "sentiment": sentiment,
            "memory_id": memory_id,
        }

    def record_interaction(
        self, messages: int = 1, complexity: str = "normale", *,
        user_id: str | None = None, summary: str = "", channel: str = DEFAULT_INTERACTION_CHANNEL,
        sentiment: str | None = None, memory_id: str | None = None,
        interaction_id: str | None = None,
    ) -> dict[str, Any]:
        """Commit unico; lo stesso ID e payload restituiscono la ricevuta originale."""
        request_id = self._clean_optional_token(interaction_id, "interaction_id")
        if interaction_id is not None and request_id is None:
            raise ValidationError("interaction_id non può essere vuoto.")
        payload = canonical({"messages": messages, "complexity": complexity,
            "user_id": user_id.strip() if isinstance(user_id, str) else user_id,
            "summary": self._clean_interaction_summary(summary),
            "channel": self._clean_optional_token(channel, "channel", default=DEFAULT_INTERACTION_CHANNEL),
            "sentiment": self._clean_optional_token(sentiment, "sentiment"),
            "memory_id": self._clean_optional_token(memory_id, "memory_id")})
        with self.config.transaction() as conn:
            if request_id is not None:
                row = conn.execute("SELECT payload, result FROM interaction_receipts WHERE id=?", (request_id,)).fetchone()
                if row is not None:
                    if row["payload"] != payload:
                        raise ValidationError("interaction_id già utilizzato con dati diversi.")
                    return json.loads(row["result"])
            self.state = self.config.get("current_state")
            result = self._record_interaction(messages, complexity, user_id=user_id,
                summary=summary, channel=channel, sentiment=sentiment, memory_id=memory_id)
            if request_id is not None:
                conn.execute("INSERT INTO interaction_receipts(id, payload, result) VALUES (?, ?, ?)",
                             (request_id, payload, canonical(result)))
            return result

    def _record_interaction(
        self,
        messages: int = 1,
        complexity: str = "normale",
        *,
        user_id: str | None = None,
        summary: str = "",
        channel: str = DEFAULT_INTERACTION_CHANNEL,
        sentiment: str | None = None,
        memory_id: str | None = None,
    ) -> dict[str, Any]:
        """Aumenta il carico e, se c'è un contatto, aggiorna la relazione.

        ``user_id`` è facoltativo per non rompere i chiamanti che registrano
        soltanto il carico. Quando è presente, il contatto deve esistere nel
        grafo: un id sconosciuto viene rifiutato prima di qualsiasi scrittura.
        """

        clean_memory_id = self._clean_optional_token(memory_id, "memory_id")
        if clean_memory_id is not None:
            if user_id is None:
                raise ValidationError("memory_id richiede user_id per collegare il ricordo alla relazione.")
            with Database(self.root).timeline() as conn:
                exists = conn.execute("SELECT 1 FROM conscious_episodic_memories WHERE id = ?", (clean_memory_id,)).fetchone()
            if exists is None:
                raise ValidationError("memory_id: ricordo inesistente o non accessibile.")
        load = apply_interactions(self._load(), messages, complexity)
        stamp = self.now.replace(microsecond=0).isoformat()
        relationship: dict[str, Any] | None = None
        if user_id is not None:
            if not isinstance(user_id, str) or not user_id.strip():
                raise ValidationError("user_id: attesa una stringa non vuota.")
            user_id = user_id.strip()
            clean_channel = self._clean_optional_token(
                channel, "channel", default=DEFAULT_INTERACTION_CHANNEL
            )
            assert clean_channel is not None
            clean_sentiment = self._clean_optional_token(sentiment, "sentiment")
            relationship = self._record_social_interaction(
                user_id,
                stamp,
                self._clean_interaction_summary(summary),
                channel=clean_channel,
                sentiment=clean_sentiment,
                memory_id=clean_memory_id,
            )

        state = self.config.update_current_state(
            {
                "mental_load": load,
                "availability": availability_for(
                    load, self._thresholds(), sleeping=self.sleeping
                ),
                "heartbeat": {"last_interaction_at": stamp},
            }
        )
        result: dict[str, Any] = {
            "mental_load": state["mental_load"],
            "availability": state["availability"],
            "last_interaction_at": state["heartbeat"]["last_interaction_at"],
            "messages": messages,
            "complexity": complexity,
        }
        if relationship is not None:
            result["relationship"] = relationship
        guidance = self._shadow_guidance(summary)
        if guidance is not None:
            result["response_guidance"] = guidance
        return result

    def _shadow_guidance(self, summary: str) -> str | None:
        """Media i trigger senza esporre tratti, diario o cause private."""
        flags = self.state.get("flags", {})
        if not flags.get("shadow_engine_enabled") or flags.get("maintenance_mode"):
            return None
        text = self._clean_interaction_summary(summary).casefold()
        for trait in self.config.get("shadow_traits").get("traits", []):
            safety = trait.get("safety", {})
            if not isinstance(safety, dict) or safety.get("allowed") is not True:
                continue
            for trigger in trait.get("triggers", []):
                if not isinstance(trigger, dict):
                    continue
                examples = trigger.get("examples", [])
                if not isinstance(examples, list):
                    continue
                if any(isinstance(example, str) and len(example.strip()) >= 4
                       and example.strip().casefold() in text for example in examples):
                    return "Rispondi con calma e tatto, senza presumere intenzioni o giudicare."
        return None

    def acknowledge_impulse(self, note: str = "") -> dict[str, Any]:
        """Chiude l'impulso in attesa, dopo che qualcuno se n'è occupato."""

        if not isinstance(note, str):
            raise ValidationError("note: attesa una stringa.")
        note = note.strip()
        if len(note) > 500:
            raise ValidationError("note: massimo 500 caratteri.")
        stamp = self.now.replace(microsecond=0).isoformat()
        state = self.config.update_current_state(
            {
                "heartbeat": {
                    "pending_impulse": None,
                    "spontaneous_message_pending": False,
                    "last_decision": "impulso_gestito",
                    "last_handled_at": stamp,
                    "last_handling_note": note,
                }
            }
        )
        return state["heartbeat"]

    # -- trigger ------------------------------------------------------------

    def _anniversary(self, warnings: list[str]) -> dict[str, Any] | None:
        day_key = self.now.strftime("%m-%d")
        try:
            with Database(self.root).timeline() as conn:
                row = conn.execute(
                    "SELECT id, event_date, title, importance "
                    "FROM conscious_episodic_memories "
                    "WHERE importance >= ? AND substr(event_date, 6, 5) = ? "
                    "AND event_date < ? "
                    "ORDER BY importance DESC, event_date DESC LIMIT 1",
                    (
                        ANNIVERSARY_MIN_IMPORTANCE,
                        day_key,
                        self.now.date().isoformat(),
                    ),
                ).fetchone()
        except AgentMemoryError as exc:
            warnings.append(f"ricorrenze non valutate: {exc}")
            return None
        if row is None:
            return None
        event_date = row["event_date"]
        years_ago = self.now.year - int(event_date[:4])
        return {
            "trigger": "ricorrenza",
            "target": None,
            "context": {
                "memory_id": row["id"],
                "title": row["title"],
                "event_date": event_date,
                "years_ago": years_ago,
                "importance": row["importance"],
            },
        }

    def _social(self) -> dict[str, Any] | None:
        try:
            graph = self.config.get("social_graph")
        except AgentMemoryError:
            return None
        people = graph.get("people")
        if not isinstance(people, list):
            return None
        best: dict[str, Any] | None = None
        best_days = 0.0
        for person in people:
            if not isinstance(person, dict):
                continue
            trust = person.get("trust")
            if isinstance(trust, bool) or not isinstance(trust, (int, float)):
                continue
            if trust < SOCIAL_MIN_TRUST:
                continue
            last = person.get("last_interaction_at") or person.get("first_interaction_at")
            if not isinstance(last, str):
                continue
            days = elapsed_minutes(last, self.now) / (24 * 60)
            if days < SOCIAL_SILENCE_DAYS or days <= best_days:
                continue
            best_days = days
            best = {
                "trigger": "relazione",
                "target": {
                    "user_id": person.get("user_id"),
                    "display_name": person.get("display_name"),
                },
                "context": {
                    "days_since_contact": round(days, 1),
                    "last_interaction_at": last,
                    "trust": trust,
                    "intimacy": person.get("intimacy"),
                },
            }
        return best

    def _boredom(self) -> dict[str, Any] | None:
        activity = self.routine.activity
        minutes = elapsed_minutes(activity.get("started_at"), self.now)
        if minutes < BOREDOM_MINUTES:
            return None
        return {
            "trigger": "noia",
            "target": None,
            "context": {
                "activity": activity.get("label"),
                "category": activity.get("category"),
                "minutes": round(minutes),
            },
        }

    def _delivery(self, target: dict[str, Any] | None) -> dict[str, Any]:
        """Canale suggerito e se è già autorizzato. Nessun invio parte da qui."""

        flags = self.state.get("flags")
        flags = flags if isinstance(flags, dict) else {}
        if target is not None:
            authorized = bool(flags.get("whatsapp_sending_enabled"))
            return {
                "channel": "whatsapp",
                "authorized": authorized,
                "note": (
                    "Serve un executor autorizzato e il contatto in whitelist; "
                    "il testo del messaggio non viene generato dal battito."
                ),
            }
        try:
            policy = BrowserPolicy.from_root(self.root)
            authorized = bool(policy.enabled and policy.allowed_origins)
        except AgentMemoryError:
            authorized = False
        return {
            "channel": "browser",
            "authorized": authorized,
            "note": "Navigazione solo verso le origini in whitelist.",
        }

    def _impulses_today(self, heartbeat: dict[str, Any]) -> dict[str, Any]:
        today = self.now.date().isoformat()
        raw = heartbeat.get("impulses_today")
        if not isinstance(raw, dict):
            return {"date": today, "count": 0}
        count = raw.get("count", 0)
        if raw.get("date") != today or isinstance(count, bool) or not isinstance(
            count, int
        ):
            return {"date": today, "count": 0}
        return {"date": today, "count": max(0, count)}

    def _cooldown_active(self, heartbeat: dict[str, Any]) -> bool:
        last = _parse_moment(heartbeat.get("last_impulse_at"), self.now)
        if last is None:
            return False
        return (self.now - last) < timedelta(minutes=self.policy.cooldown_minutes)

    # -- battito ------------------------------------------------------------

    def plan(self) -> dict[str, Any]:
        """Calcola patch e decisione senza scrivere nulla."""

        warnings: list[str] = []
        minutes = min(
            MAX_ELAPSED_MINUTES,
            elapsed_minutes(self.heartbeat.get("last_run_at"), self.now),
        )
        load_before = self._load()
        load_after = recover_load(
            load_before, minutes, sleeping=self.sleeping
        )
        thresholds = self._thresholds()

        impulses_today = self._impulses_today(self.heartbeat)
        pending = self.heartbeat.get("pending_impulse")
        pending = pending if isinstance(pending, dict) else None
        flags = self.state.get("flags")
        flags = flags if isinstance(flags, dict) else {}

        trigger: dict[str, Any] | None = None
        if self.sleeping:
            decision = "consolidamento"
        elif pending is not None:
            decision = "impulso_in_attesa"
        elif flags.get("maintenance_mode"):
            decision = "manutenzione"
        elif load_after >= float(thresholds.get("overload", 80)):
            decision = "riposo_cognitivo"
        elif (
            self._cooldown_active(self.heartbeat)
            or impulses_today["count"] >= self.policy.max_impulses_per_day
        ):
            decision = "continuare_l_attivita"
        else:
            trigger = (
                self._anniversary(warnings)
                or self._social()
                or self._boredom()
            )
            decision = "impulso" if trigger is not None else "continuare_l_attivita"

        impulse: dict[str, Any] | None = None
        stamp = self.now.replace(microsecond=0).isoformat()
        if trigger is not None:
            impulse = {
                **trigger,
                "created_at": stamp,
                "delivery": self._delivery(trigger.get("target")),
                "requires_message": True,
            }
            impulses_today = {
                "date": impulses_today["date"],
                "count": impulses_today["count"] + 1,
            }

        next_run = (self.now + timedelta(minutes=self.policy.interval_minutes)).replace(
            microsecond=0
        )
        # L'impulso in attesa sopravvive a un battito senza nuovi trigger: non
        # va perso solo perché il ciclo è passato di nuovo.
        pending_patch = impulse if impulse is not None else pending
        # Si parte dal battito esistente: le chiavi non gestite qui (ultima
        # interazione, ultima gestione di un impulso) restano intatte invece di
        # sparire a ogni riscrittura.
        heartbeat_patch: dict[str, Any] = copy.deepcopy(self.heartbeat)
        heartbeat_patch.update(
            {
                "interval_minutes": self.policy.interval_minutes,
                "cooldown_minutes": self.policy.cooldown_minutes,
                "max_impulses_per_day": self.policy.max_impulses_per_day,
                "last_run_at": stamp,
                "next_run_at": next_run.isoformat(),
                "last_decision": decision,
                "last_trigger": (
                    trigger["trigger"] if trigger else self.heartbeat.get("last_trigger")
                ),
                "pending_impulse": pending_patch,
                "spontaneous_message_pending": pending_patch is not None,
                "impulses_today": impulses_today,
            }
        )
        if impulse is not None:
            heartbeat_patch["last_impulse_at"] = stamp

        sleep = copy.deepcopy(self.routine.patch.get("sleep", {}))
        if not isinstance(sleep, dict):
            sleep = {}
        sleep["is_sleeping"] = self.sleeping
        patch: dict[str, Any] = copy.deepcopy(self.routine.patch)
        patch["mental_load"] = load_after
        patch["availability"] = availability_for(
            load_after, thresholds, sleeping=self.sleeping
        )
        patch["sleep"] = sleep
        patch["heartbeat"] = heartbeat_patch

        candidate = {**copy.deepcopy(self.state), **copy.deepcopy(patch)}
        would_change = any(
            candidate.get(key) != self.state.get(key) for key in patch
        )

        report = {
            "heartbeat_version": HEARTBEAT_VERSION,
            "now": self.now.isoformat(),
            "timezone": self.routine.timezone_name,
            "sleeping": self.sleeping,
            "minutes_since_last_run": round(minutes, 1),
            "mental_load_before": load_before,
            "mental_load_after": load_after,
            "decision": decision,
            "trigger": trigger,
            "impulse": impulse,
            "next_run_at": next_run.isoformat(),
            "warnings": warnings,
            "would_change": would_change,
        }
        self._patch = patch
        self._candidate = candidate
        return report

    def preview(self) -> dict[str, Any]:
        report = self.plan()
        return {**report, "state": copy.deepcopy(self._candidate)}

    def apply(self) -> dict[str, Any]:
        with self.config.transaction() as conn:
            # Recompute under the same write lock used for memory and state.
            self.state = self.config.get("current_state")
            self.routine = RoutineEngine(self.root, at=self.now)
            self.sleeping = self.routine.is_sleep
            self.heartbeat = self.state.get("heartbeat", {})
            self.policy = HeartbeatPolicy.from_state(self.state)
            report = self.plan()
            state_now = self.state
            consolidation = None
            if (self.sleeping and conn is not None
                    and not state_now.get("flags", {}).get("maintenance_mode")):
                consolidation = self._consolidate(conn)
                if consolidation is not None:
                    self._patch["sleep"]["last_consolidation_at"] = self.now.replace(microsecond=0).isoformat()
            if not report["would_change"] and consolidation is None:
                return {**report, "applied": False}
            state = self.config.update_current_state(copy.deepcopy(self._patch))
        return {
            **report,
            "applied": True,
            "would_change": False,
            "mental_load_after": state["mental_load"],
            "heartbeat": state["heartbeat"],
            "consolidation": consolidation,
        }

    def _consolidate(self, conn: Any) -> dict[str, Any] | None:
        """Consolida fino a 20 ricordi consci in un'unica transazione SQLite."""
        rows = conn.execute(
            "SELECT * FROM conscious_episodic_memories WHERE is_consolidated = 0 "
            "AND importance >= 5 AND reliability >= 0.5 AND julianday(event_time) <= julianday(?) "
            "ORDER BY event_time, id LIMIT 20", (self.now.isoformat(),)
        ).fetchall()
        if not rows:
            return None
        stamp = self.now.replace(microsecond=0).isoformat()
        ids = [row["id"] for row in rows]
        digest = hashlib.sha256(canonical([[row["id"], row["version"]] for row in rows]).encode("utf-8")).hexdigest()[:24]
        summary_id = f"mem_consolidation_{digest}"
        description = "Sintesi estrattiva di ricordi consci; non è una nuova esperienza:\n" + "\n".join(
            f"- {row['event_date']}: {row['title'][:200]} — {row['description'][:1000]}"
            for row in rows
        )
        conn.execute(
            "INSERT INTO episodic_memories (id, event_time, event_date, title, description, "
            "importance, source, source_ref, reliability, is_simulated, access_level, "
            "is_consolidated, tags_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'generated', ?, ?, 1, 'conscious', 1, ?, ?, ?)",
            (summary_id, stamp, stamp[:10], "Sintesi dei ricordi", description,
             max(row["importance"] for row in rows), canonical(ids),
             min(row["reliability"] for row in rows), canonical(["consolidamento"]), stamp, stamp)
        )
        inserted = dict(conn.execute("SELECT * FROM episodic_memories WHERE id=?", (summary_id,)).fetchone())
        conn.execute(
            "INSERT INTO memory_revisions(memory_id, revision_number, changed_at, changed_by, "
            "change_reason, previous_snapshot_json, new_snapshot_json) VALUES (?, 1, ?, ?, ?, NULL, ?)",
            (summary_id, stamp, "heartbeat", "Sintesi estrattiva notturna.", canonical(inserted))
        )
        for row in rows:
            before = dict(row)
            conn.execute("UPDATE episodic_memories SET is_consolidated=1, version=version+1, updated_at=? WHERE id=?", (stamp, row["id"]))
            after = dict(conn.execute("SELECT * FROM episodic_memories WHERE id=?", (row["id"],)).fetchone())
            conn.execute(
                "INSERT INTO memory_revisions(memory_id, revision_number, changed_at, changed_by, "
                "change_reason, previous_snapshot_json, new_snapshot_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row["id"], after["version"], stamp, "heartbeat", "Consolidamento notturno.", canonical(before), canonical(after))
            )
            conn.execute(
                "INSERT INTO memory_links(from_memory_id, to_memory_id, relation_type, strength, note, created_at) "
                "VALUES (?, ?, 'associated', 1.0, ?, ?)",
                (summary_id, row["id"], "Fonte della sintesi estrattiva.", stamp)
            )
        return {"summary_id": summary_id, "source_count": len(rows)}


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
            "Esegue un battito del ciclo autonomo: aggiorna il carico cognitivo, "
            "allinea la routine e valuta gli impulsi spontanei (Fase 6)."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory del profilo (default: quella che contiene il pacchetto).",
    )
    parser.add_argument(
        "--at",
        type=_parse_at,
        default=None,
        metavar="ISO8601",
        help="Istante di riferimento; default: adesso.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Scrive lo stato aggiornato. Senza questo flag mostra solo l'anteprima.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Stampa il risultato in JSON.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Ripete il battito ogni --interval minuti finché non viene interrotto.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        metavar="MINUTI",
        help="Minuti tra due battiti in modalità --loop (default: dal profilo).",
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=None,
        metavar="N",
        help="Numero massimo di battiti in modalità --loop (utile per le prove).",
    )
    parser.add_argument(
        "--interaction",
        type=int,
        default=None,
        metavar="MESSAGGI",
        help="Registra subito un'interazione e aumenta il carico cognitivo.",
    )
    parser.add_argument(
        "--complexity",
        choices=sorted(COMPLEXITY_LOAD),
        default="normale",
        help="Complessità dei messaggi registrati con --interaction.",
    )
    parser.add_argument(
        "--user-id",
        default=None,
        metavar="ID",
        help="Contatto del grafo da aggiornare con --interaction.",
    )
    parser.add_argument(
        "--summary",
        default="",
        metavar="TESTO",
        help="Nota facoltativa per lo storico dell'interazione.",
    )
    parser.add_argument(
        "--channel",
        default=DEFAULT_INTERACTION_CHANNEL,
        metavar="CANALE",
        help="Canale dell'interazione (default: chat).",
    )
    parser.add_argument(
        "--sentiment",
        default=None,
        metavar="TONO",
        help="Tono facoltativo dell'interazione (es. positivo, neutro).",
    )
    parser.add_argument("--interaction-id", default=None, help="ID stabile per retry senza doppi conteggi.")
    parser.add_argument(
        "--memory-id",
        default=None,
        metavar="ID",
        help="Ricordo episodico facoltativo collegato all'interazione.",
    )
    parser.add_argument(
        "--deliver",
        default=None,
        metavar="TESTO",
        help=(
            "Consegna il messaggio all'impulso in attesa, se whitelist e "
            "flag lo permettono."
        ),
    )
    return parser


def _print_report(result: dict[str, Any]) -> None:
    print(f"Heartbeat {result['heartbeat_version']} — {result['now']}")
    print(
        f"Carico cognitivo: {result['mental_load_before']} → "
        f"{result['mental_load_after']}/100"
    )
    if result["sleeping"]:
        print("Sonno simulato: nessun impulso, carico in recupero.")
    else:
        print(f"Battito precedente: {result['minutes_since_last_run']} minuti fa")
    print(f"Decisione: {result['decision']}")
    impulse = result.get("impulse")
    if impulse:
        target = impulse.get("target") or {}
        who = target.get("display_name") or target.get("user_id")
        detail = f" verso {who}" if who else ""
        delivery = impulse.get("delivery", {})
        state = "autorizzato" if delivery.get("authorized") else "da autorizzare"
        print(
            f"Impulso: {impulse['trigger']}{detail} — canale "
            f"{delivery.get('channel')} ({state})"
        )
    print(f"Prossimo battito: {result['next_run_at']}")
    for warning in result.get("warnings", []):
        print(f"Avviso: {warning}")
    if result.get("applied") is True:
        print("Stato aggiornato.")
    elif result.get("applied") is False:
        print("Nessuna modifica: lo stato è già allineato.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.root

    try:
        if args.deliver is not None:
            if args.interaction is not None:
                raise ValidationError(
                    "Usa --deliver oppure --interaction, non entrambi."
                )
            return _deliver(args)

        if args.interaction is not None:
            result = HeartbeatEngine(root, at=args.at).record_interaction(
                messages=args.interaction,
                complexity=args.complexity,
                user_id=args.user_id,
                summary=args.summary,
                channel=args.channel,
                sentiment=args.sentiment,
                memory_id=args.memory_id,
                interaction_id=args.interaction_id,
            )
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(
                    f"Interazione registrata: carico cognitivo "
                    f"{result['mental_load']}/100 "
                    f"({result['messages']} × {result['complexity']})."
                )
                relationship = result.get("relationship")
                if relationship:
                    who = relationship.get("display_name") or relationship["user_id"]
                    print(
                        f"Relazione aggiornata: {who} "
                        f"({relationship['interaction_count']} interazioni)."
                    )
            return 0

        if args.loop:
            return _run_loop(args)

        engine = HeartbeatEngine(root, at=args.at)
        result = engine.apply() if args.apply else engine.preview()
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_report(result)
        return 0
    except AgentMemoryError as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def _deliver(args: argparse.Namespace) -> int:
    result = MessageExecutor(args.root, at=args.at).deliver(args.deliver)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        target = result["target"]
        print(
            f"Messaggio consegnato a {target['display_name']} "
            f"({target['user_id']}) su {result['channel']}."
        )
        print(f"Canale: {result['transport']} — {result['message_id']}")
        print(f"Impulso: {result['trigger']}")
    return 0


def _run_loop(args: argparse.Namespace) -> int:
    engine = HeartbeatEngine(args.root, at=args.at)
    interval = args.interval or engine.policy.interval_minutes
    if interval < 1:
        raise ValidationError("--interval: usa almeno un minuto.")
    ticks = 0
    try:
        while True:
            result = HeartbeatEngine(args.root).apply()
            ticks += 1
            if args.json:
                print(json.dumps(result, ensure_ascii=False), flush=True)
            else:
                impulse = result.get("impulse") or {}
                note = f" — impulso: {impulse['trigger']}" if impulse else ""
                print(
                    f"[{result['now']}] {result['decision']}"
                    f" — carico {result['mental_load_after']}/100{note}",
                    flush=True,
                )
            if args.max_ticks is not None and ticks >= args.max_ticks:
                return 0
            time_module.sleep(interval * 60)
    except KeyboardInterrupt:
        print("\nCiclo interrotto.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
