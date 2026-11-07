"""Episodi deliberativi e salienza (Fase 2).

La Fase 2 registra il ciclo minimo di un episodio: valutazione della
salienza, apertura, scadenza con default conservativo e chiusura dopo una
decisione ``continue`` o ``unstructured``. Non espone ancora ``life_decide``,
non applica effetti ai bisogni e non esegue intenti esterni: quelle parti
arrivano nelle fasi successive.

Il rapporto di situazione resta la fonte della fotografia iniziale; il modulo
scrive solo il riepilogo in ``current_state.deliberation`` e le righe di audit
nel database della timeline. Nessuna riga dell'ombra o memoria riservata entra
in questi dati.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import ConfigStore
from .db import Database
from .errors import (
    AgentMemoryError,
    DatabaseUnavailable,
    NotFoundError,
    ValidationError,
)
from .life import NEED_NAMES
from .operational import canonical
from .situation import SituationBuilder

EPISODE_STATES = (
    "open",
    "decided",
    "executing",
    "closed",
    "expired",
    "cancelled",
)
ACTIVE_STATES = ("open", "decided", "executing")
DECISIONS = ("continue", "unstructured")
MAX_TEXT = 500
MAX_REASON = 200
DEFAULT_EPISODE_MINUTES = 15
DEFAULT_NEXT_REVIEW_MINUTES = 45
MIN_INTERVAL_MINUTES = 90
MAX_PER_DAY = 6
MAX_UNSTRUCTURED_MINUTES = 240
MAX_EFFECT = 20

NEED_URGENCY = {
    "energy": ("low", 30),
    "hunger": ("high", 70),
    "social": ("high", 70),
    "stress": ("high", 75),
    "curiosity": ("low", 20),
}


def _parse(value: Any, timezone: ZoneInfo) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone)
    return moment.astimezone(timezone)


def _number(value: Any, *, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _clean_text(value: Any, field: str, *, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise ValidationError(f"{field}: attesa una stringa non vuota.")
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field}: attesa una stringa.")
    cleaned = value.strip()
    if not cleaned:
        if required:
            raise ValidationError(f"{field}: attesa una stringa non vuota.")
        return None
    if len(cleaned) > MAX_TEXT:
        raise ValidationError(f"{field}: massimo {MAX_TEXT} caratteri.")
    return cleaned


def _duration(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field}: attesa un intero in minuti.")
    if not 1 <= value <= MAX_UNSTRUCTURED_MINUTES:
        raise ValidationError(
            f"{field}: deve stare tra 1 e {MAX_UNSTRUCTURED_MINUTES} minuti."
        )
    return value


def _optional_minutes(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1440:
        raise ValidationError(f"{field}: attesa un intero tra 1 e 1440 minuti.")
    return value


def _expected_effects(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValidationError("decision.expected_effects: atteso un oggetto.")
    effects: dict[str, int] = {}
    for name, raw in value.items():
        if name not in NEED_NAMES:
            raise ValidationError(
                "decision.expected_effects: bisogno sconosciuto "
                f"'{name}'."
            )
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValidationError(
                f"decision.expected_effects.{name}: attesa una variazione intera."
            )
        if not -MAX_EFFECT <= raw <= MAX_EFFECT:
            raise ValidationError(
                f"decision.expected_effects.{name}: variazione fuori limite "
                f"[-{MAX_EFFECT}, {MAX_EFFECT}]."
            )
        effects[name] = raw
    return effects


def validate_decision(decision: Any) -> dict[str, Any]:
    """Valida una decisione della Fase 2, senza applicarne gli effetti."""

    if not isinstance(decision, dict):
        raise ValidationError("decision: atteso un oggetto JSON.")
    kind = decision.get("decision")
    if kind == "do_nothing":
        raise ValidationError("do_nothing non è una decisione valida.")
    if kind not in DECISIONS:
        if kind == "intent":
            raise ValidationError(
                "intent non è disponibile nella Fase 2: arriva con la Fase 3."
            )
        raise ValidationError(
            "decision: ammessi continue e unstructured nella Fase 2."
        )

    normalized: dict[str, Any] = {"decision": kind}
    rationale = decision.get("rationale")
    if rationale is not None:
        normalized["rationale"] = _clean_text(
            rationale, "decision.rationale", required=False
        )
    confidence = decision.get("confidence")
    if confidence is not None:
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= float(confidence) <= 1
        ):
            raise ValidationError("decision.confidence: attesa un numero tra 0 e 1.")
        normalized["confidence"] = float(confidence)
    normalized["next_review_minutes"] = _optional_minutes(
        decision.get("next_review_minutes"),
        "decision.next_review_minutes",
    ) or DEFAULT_NEXT_REVIEW_MINUTES

    if kind == "unstructured":
        block = decision.get("unstructured")
        if not isinstance(block, dict):
            raise ValidationError(
                "decision.unstructured: atteso un oggetto con activity e attention."
            )
        normalized["unstructured"] = {
            "activity": _clean_text(
                block.get("activity"), "decision.unstructured.activity"
            ),
            "attention": _clean_text(
                block.get("attention"), "decision.unstructured.attention"
            ),
            "mental_content": _clean_text(
                block.get("mental_content"),
                "decision.unstructured.mental_content",
                required=False,
            ),
            "duration_minutes": _duration(
                block.get("duration_minutes", 30),
                "decision.unstructured.duration_minutes",
            ),
            "expected_effects": _expected_effects(
                block.get("expected_effects")
            ),
        }
    return normalized


def evaluate_salience(
    report: dict[str, Any],
    *,
    now: datetime,
    seed: int | None = None,
) -> dict[str, Any]:
    """Valuta la salienza del rapporto senza aprire episodi.

    Le soglie sono deliberate e prudenti: la maggior parte dei tick non deve
    produrre un episodio. ``seed`` è l'unico modo per richiedere l'esplorazione
    a bassa probabilità, così il risultato resta riproducibile nei test.
    """

    candidates: list[dict[str, Any]] = []
    body = report.get("body")
    body = body if isinstance(body, dict) else {}
    for name, (direction, threshold) in NEED_URGENCY.items():
        value = body.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if direction == "low" and value <= threshold:
            candidates.append(
                {
                    "reason": f"need_{name}",
                    "priority": 100,
                    "detail": f"{name}={int(value)} sotto {threshold}",
                }
            )
        elif direction == "high" and value >= threshold:
            candidates.append(
                {
                    "reason": f"need_{name}",
                    "priority": 100,
                    "detail": f"{name}={int(value)} sopra {threshold}",
                }
            )

    timezone = now.tzinfo
    loops = report.get("open_loops")
    loops = loops if isinstance(loops, list) else []
    for loop in loops:
        if not isinstance(loop, dict):
            continue
        title = loop.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        due = _parse(loop.get("due_at"), timezone)
        if due is not None and due <= now + timedelta(hours=24):
            candidates.append(
                {
                    "reason": "loop_due",
                    "priority": 90,
                    "detail": title.strip()[:MAX_TEXT],
                }
            )
            continue
        touched = _parse(loop.get("last_touched_at"), timezone)
        priority = _number(loop.get("priority"), default=0.0)
        if (
            touched is not None
            and priority >= 50
            and now - touched >= timedelta(hours=72)
        ):
            candidates.append(
                {
                    "reason": "loop_stalled",
                    "priority": 60,
                    "detail": title.strip()[:MAX_TEXT],
                }
            )

    affordances = report.get("affordances")
    affordances = affordances if isinstance(affordances, dict) else {}
    budget = affordances.get("time_budget_minutes")
    if isinstance(budget, int) and not isinstance(budget, bool) and budget <= 10:
        candidates.append(
            {
                "reason": "routine_transition",
                "priority": 70,
                "detail": f"transizione tra {budget} minuti",
            }
        )

    if candidates:
        candidates.sort(key=lambda item: (-item["priority"], item["reason"]))
        return {
            "salient": True,
            "reason": candidates[0]["reason"],
            "reasons": candidates,
            "exploration": False,
        }

    if (
        isinstance(seed, int)
        and not isinstance(seed, bool)
        and seed % 100 < 5
    ):
        return {
            "salient": True,
            "reason": "exploration",
            "reasons": [
                {
                    "reason": "exploration",
                    "priority": 1,
                    "detail": "esplorazione richiesta dal seed",
                }
            ],
            "exploration": True,
        }

    return {"salient": False, "reason": None, "reasons": [], "exploration": False}


class DeliberationService:
    """Storage e macchina a stati degli episodi deliberativi."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        at: datetime | str | None = None,
    ) -> None:
        self.builder = SituationBuilder(root, at=at)
        self.config = self.builder.config
        self.root = self.builder.root
        self.now = self.builder.now
        self.timezone = self.builder.timezone
        self.database = Database(self.root)

    @property
    def enabled(self) -> bool:
        state = self.config.get("current_state")
        flags = state.get("flags")
        return isinstance(flags, dict) and flags.get("life_engine_enabled") is True

    def _state(self) -> dict[str, Any]:
        return self.config.get("current_state")

    @staticmethod
    def _decode(row: Any) -> dict[str, Any]:
        data = dict(row)
        for key in ("situation", "decision", "outcome"):
            raw = data.get(key)
            if raw is None:
                data[key] = None
            elif isinstance(raw, str):
                try:
                    data[key] = json.loads(raw)
                except json.JSONDecodeError:
                    data[key] = None
        return data

    @staticmethod
    def _view(row: Any) -> dict[str, Any]:
        data = DeliberationService._decode(row)
        return {
            "episode_id": data.get("id"),
            "state": data.get("state"),
            "reason": data.get("reason"),
            "opened_at": data.get("opened_at"),
            "expires_at": data.get("expires_at"),
            "decided_at": data.get("decided_at"),
            "closed_at": data.get("closed_at"),
            "decision": data.get("decision"),
            "outcome": data.get("outcome"),
        }

    @staticmethod
    def _summary(view: dict[str, Any]) -> dict[str, Any]:
        decision = view.get("decision")
        decision_kind = (
            decision.get("decision")
            if isinstance(decision, dict)
            else None
        )
        return {
            "episode_id": view.get("episode_id"),
            "state": view.get("state"),
            "reason": view.get("reason"),
            "opened_at": view.get("opened_at"),
            "expires_at": view.get("expires_at"),
            "decided_at": view.get("decided_at"),
            "closed_at": view.get("closed_at"),
            "decision": decision_kind,
        }

    def _event(
        self,
        conn: Any,
        episode_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> None:
        conn.execute(
            "INSERT INTO deliberation_events(episode_id, at, type, payload_json) "
            "VALUES (?, ?, ?, ?)",
            (episode_id, self.now.isoformat(), kind, canonical(payload)),
        )

    def _update_summary(self, view: dict[str, Any]) -> None:
        self.config.update_current_state(
            {"deliberation": self._summary(view)}
        )

    def _episode_id(self, conn: Any) -> str:
        base = f"del_{self.now.strftime('%Y%m%d_%H%M%S')}"
        candidate = base
        suffix = 2
        while conn.execute(
            "SELECT 1 FROM deliberation_episodes WHERE id = ?", (candidate,)
        ).fetchone():
            candidate = f"{base}_{suffix}"
            suffix += 1
        return candidate

    def _active_row(self, conn: Any) -> Any:
        return conn.execute(
            "SELECT * FROM deliberation_episodes "
            "WHERE state IN ('open', 'decided', 'executing') "
            "ORDER BY opened_at DESC LIMIT 1"
        ).fetchone()

    def _gate(self, conn: Any, state: dict[str, Any]) -> str | None:
        flags = state.get("flags")
        flags = flags if isinstance(flags, dict) else {}
        if flags.get("life_engine_enabled") is not True:
            return "life_engine_disabled"
        sleeping = state.get("sleep")
        sleeping = sleeping if isinstance(sleeping, dict) else {}
        clock = state.get("clock")
        clock = clock if isinstance(clock, dict) else {}
        if sleeping.get("is_sleeping") is True or clock.get("is_sleep_window") is True:
            return "sleep"
        if flags.get("maintenance_mode") is True:
            return "maintenance"
        if self._active_row(conn) is not None:
            return "episode_active"

        uncertain = conn.execute(
            "SELECT 1 FROM autonomous_runs AS r "
            "LEFT JOIN autonomous_reviews AS v ON v.run_id = r.id "
            "WHERE r.state = 'uncertain' "
            "OR (r.state = 'blocked' AND v.run_id IS NULL AND EXISTS ("
            "  SELECT 1 FROM autonomous_actions AS a WHERE a.run_id = r.id "
            "  AND (a.result IS NULL OR json_extract(a.result, '$.state') = 'uncertain')"
            ")) LIMIT 1"
        ).fetchone()
        if uncertain is not None:
            return "uncertain_action"

        last = conn.execute(
            "SELECT opened_at, closed_at FROM deliberation_episodes "
            "ORDER BY opened_at DESC LIMIT 1"
        ).fetchone()
        if last is not None:
            last_moment = _parse(
                last["closed_at"] or last["opened_at"], self.timezone
            )
            if (
                last_moment is not None
                and self.now - last_moment < timedelta(minutes=MIN_INTERVAL_MINUTES)
            ):
                return "cooldown"

        today = self.now.date()
        count = 0
        for row in conn.execute(
            "SELECT opened_at FROM deliberation_episodes"
        ).fetchall():
            moment = _parse(row["opened_at"], self.timezone)
            if moment is not None and moment.date() == today:
                count += 1
        if count >= MAX_PER_DAY:
            return "daily_budget"
        return None

    def _default_decision(
        self, episode_id: str, state: dict[str, Any]
    ) -> dict[str, Any]:
        flags = state.get("flags")
        flags = flags if isinstance(flags, dict) else {}
        activity = state.get("activity")
        activity = activity if isinstance(activity, dict) else {}
        sleeping = state.get("sleep")
        sleeping = sleeping if isinstance(sleeping, dict) else {}
        label = activity.get("label")
        safe_activity = (
            isinstance(label, str)
            and bool(label.strip())
            and sleeping.get("is_sleeping") is not True
            and flags.get("maintenance_mode") is not True
        )
        if safe_activity:
            return {
                "episode_id": episode_id,
                "decision": "continue",
                "rationale": (
                    "L'episodio è scaduto senza decisione; l'attività in corso "
                    "è sicura e continua."
                ),
                "confidence": 0.5,
                "next_review_minutes": DEFAULT_NEXT_REVIEW_MINUTES,
            }
        return {
            "episode_id": episode_id,
            "decision": "unstructured",
            "unstructured": {
                "activity": "Riposo",
                "attention": "pausa",
                "mental_content": None,
                "duration_minutes": DEFAULT_EPISODE_MINUTES,
                "expected_effects": None,
            },
            "rationale": (
                "L'episodio è scaduto senza decisione; l'attività in corso non "
                "è disponibile, quindi si registra una pausa descritta."
            ),
            "confidence": 0.4,
            "next_review_minutes": DEFAULT_NEXT_REVIEW_MINUTES,
        }

    def _expire_locked(self, conn: Any) -> list[dict[str, Any]]:
        state = self._state()
        expired: list[dict[str, Any]] = []
        for row in conn.execute(
            "SELECT * FROM deliberation_episodes WHERE state = 'open' "
            "ORDER BY opened_at ASC"
        ).fetchall():
            expires = _parse(row["expires_at"], self.timezone)
            if expires is None or expires > self.now:
                continue
            decision = self._default_decision(row["id"], state)
            outcome = {
                "applied": False,
                "simulated": True,
                "reason": "expired",
                "note": "Fase 2: decisione registrata senza esecuzione.",
            }
            conn.execute(
                "UPDATE deliberation_episodes SET state = 'expired', "
                "decided_at = ?, closed_at = ?, decision_json = ?, outcome_json = ? "
                "WHERE id = ?",
                (
                    self.now.isoformat(),
                    self.now.isoformat(),
                    canonical(decision),
                    canonical(outcome),
                    row["id"],
                ),
            )
            self._event(
                conn,
                row["id"],
                "expired",
                {"decision": decision["decision"], "outcome": outcome},
            )
            expired.append(
                {
                    "episode_id": row["id"],
                    "state": "expired",
                    "reason": row["reason"],
                    "opened_at": row["opened_at"],
                    "expires_at": row["expires_at"],
                    "decided_at": self.now.isoformat(),
                    "closed_at": self.now.isoformat(),
                    "decision": decision,
                    "outcome": outcome,
                }
            )
        if expired:
            self._update_summary(expired[-1])
        return expired

    def tick(self, *, seed: int | None = None) -> dict[str, Any]:
        """Valuta la salienza e, se le porte sono aperte, apre un episodio."""

        if not self.enabled:
            return {
                "opened": False,
                "reason": "life_engine_disabled",
                "salience": None,
            }
        report = SituationBuilder(self.root, at=self.now).build()
        salience = evaluate_salience(report, now=self.now, seed=seed)
        with self.config.transaction() as conn:
            if conn is None:
                raise DatabaseUnavailable(
                    "Il motore vita richiede il database timeline del profilo."
                )
            expired = self._expire_locked(conn)
            state = self._state()
            gate = self._gate(conn, state)
            if gate is not None:
                return {
                    "opened": False,
                    "reason": gate,
                    "expired": expired,
                    "salience": salience,
                }
            if not salience["salient"]:
                return {
                    "opened": False,
                    "reason": "not_salient",
                    "expired": expired,
                    "salience": salience,
                }
            episode_id = self._episode_id(conn)
            report = copy.deepcopy(report)
            report["episode_id"] = episode_id
            expires_at = (self.now + timedelta(minutes=DEFAULT_EPISODE_MINUTES)).isoformat()
            conn.execute(
                "INSERT INTO deliberation_episodes("
                "id, state, reason, situation_json, opened_at, expires_at"
                ") VALUES (?, 'open', ?, ?, ?, ?)",
                (
                    episode_id,
                    salience["reason"],
                    canonical(report),
                    self.now.isoformat(),
                    expires_at,
                ),
            )
            self._event(
                conn,
                episode_id,
                "opened",
                {"reason": salience["reason"], "reasons": salience["reasons"]},
            )
            view = self._view(
                {
                    "id": episode_id,
                    "state": "open",
                    "reason": salience["reason"],
                    "opened_at": self.now.isoformat(),
                    "expires_at": expires_at,
                    "decided_at": None,
                    "closed_at": None,
                    "decision": None,
                    "outcome": None,
                }
            )
            self._update_summary(view)
            return {
                "opened": True,
                "reason": "opened",
                "expired": expired,
                "salience": salience,
                "episode": view,
                "situation": report,
            }

    def expire(self) -> dict[str, Any]:
        """Chiude gli episodi scaduti con il default conservativo."""

        with self.config.transaction() as conn:
            if conn is None:
                raise DatabaseUnavailable(
                    "Il motore vita richiede il database timeline del profilo."
                )
            expired = self._expire_locked(conn)
            return {"expired": expired, "count": len(expired)}

    def decide(self, decision: Any) -> dict[str, Any]:
        """Registra una decisione ``continue`` o ``unstructured``.

        Non applica effetti: la Fase 3 aggiungerà esecuzione e riflessione.
        """

        normalized = validate_decision(decision)
        self.expire()
        with self.config.transaction() as conn:
            if conn is None:
                raise DatabaseUnavailable(
                    "Il motore vita richiede il database timeline del profilo."
                )
            row = self._active_row(conn)
            if row is None:
                raise NotFoundError("Nessun episodio deliberativo aperto.")
            if row["state"] != "open":
                raise ValidationError(
                    "L'episodio non è aperto: non può accettare una decisione."
                )
            normalized["episode_id"] = row["id"]
            outcome = {
                "applied": False,
                "simulated": True,
                "reason": "phase_2",
                "note": "Fase 2: decisione registrata senza esecuzione.",
            }
            conn.execute(
                "UPDATE deliberation_episodes SET state = 'closed', "
                "decided_at = ?, closed_at = ?, decision_json = ?, outcome_json = ? "
                "WHERE id = ?",
                (
                    self.now.isoformat(),
                    self.now.isoformat(),
                    canonical(normalized),
                    canonical(outcome),
                    row["id"],
                ),
            )
            self._event(
                conn,
                row["id"],
                "decided",
                {"decision": normalized["decision"], "outcome": outcome},
            )
            view = self._view(
                {
                    "id": row["id"],
                    "state": "closed",
                    "reason": row["reason"],
                    "opened_at": row["opened_at"],
                    "expires_at": row["expires_at"],
                    "decided_at": self.now.isoformat(),
                    "closed_at": self.now.isoformat(),
                    "decision": normalized,
                    "outcome": outcome,
                }
            )
            self._update_summary(view)
            return {"accepted": True, "episode": view, "decision": normalized}

    def status(self) -> dict[str, Any]:
        """Legge lo stato degli episodi senza aprire nulla."""

        result: dict[str, Any] = {
            "enabled": self.enabled,
            "storage": True,
            "active": None,
            "last": None,
            "today": 0,
            "next_eligible_at": None,
        }
        try:
            with self.database.timeline() as conn:
                active = self._active_row(conn)
                last = conn.execute(
                    "SELECT * FROM deliberation_episodes "
                    "ORDER BY opened_at DESC LIMIT 1"
                ).fetchone()
                count = 0
                for row in conn.execute(
                    "SELECT opened_at FROM deliberation_episodes"
                ).fetchall():
                    moment = _parse(row["opened_at"], self.timezone)
                    if moment is not None and moment.date() == self.now.date():
                        count += 1
        except AgentMemoryError:
            result["storage"] = False
            return result

        if active is not None:
            result["active"] = self._view(active)
            expires = _parse(active["expires_at"], self.timezone)
            result["expired_pending"] = bool(expires and expires <= self.now)
        if last is not None:
            result["last"] = self._view(last)
            base = _parse(last["closed_at"] or last["opened_at"], self.timezone)
            if base is not None:
                result["next_eligible_at"] = (
                    base + timedelta(minutes=MIN_INTERVAL_MINUTES)
                ).isoformat()
        result["today"] = count
        return result


__all__ = [
    "ACTIVE_STATES",
    "DECISIONS",
    "DEFAULT_EPISODE_MINUTES",
    "EPISODE_STATES",
    "MAX_PER_DAY",
    "MIN_INTERVAL_MINUTES",
    "DeliberationService",
    "evaluate_salience",
    "validate_decision",
]
