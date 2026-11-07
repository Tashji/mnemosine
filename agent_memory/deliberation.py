"""Episodi deliberativi, effetti interni e riflessione (Fase 3).

La Fase 3 completa il ciclo di un episodio: valutazione della salienza,
apertura, scadenza con default conservativo, decisione ``continue``,
``unstructured`` o ``intent``, applicazione di effetti limitati, aggiornamento
di stream, bisogni, emozione e loop, e riflessione deterministica.

Gli intenti con ``tool: none`` restano simulati: aggiornano lo stato interno e
vengono etichettati come tali, senza essere presentati come fatti fisici. Gli
intenti esterni (``files``, ``whatsapp``, ``browser``) passano dai controlli
esistenti: conferma esplicita, sandbox dei file, whitelist e flag WhatsApp,
policy del browser. Un intento bloccato non applica effetti; uno eseguito
registra l'esito senza esporre telefoni o credenziali.

Il rapporto di situazione resta la fonte della fotografia iniziale; il modulo
scrive il riepilogo in ``current_state.deliberation`` e le righe di audit nel
database della timeline. Nessuna riga dell'ombra o memoria riservata entra in
questi dati.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .browser import BrowserPolicy
from .config import ConfigStore
from .db import Database
from .errors import (
    AgentMemoryError,
    DatabaseUnavailable,
    NotFoundError,
    ValidationError,
)
from .executor import MessageExecutor
from .files import FileSandbox
from .life import NEED_NAMES, normalize_life
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
DECISIONS = ("continue", "unstructured", "intent")
INTENT_TOOLS = ("none", "browser", "files", "whatsapp")
INTENT_RISKS = ("low", "medium", "high")
MAX_TEXT = 500
MAX_STREAM = 8
MAX_INTENT_STEPS = 10
MAX_INTENT_ARGUMENTS = 20
MAX_REASON = 200
DEFAULT_EPISODE_MINUTES = 15
DEFAULT_NEXT_REVIEW_MINUTES = 45
MIN_INTERVAL_MINUTES = 90
MAX_PER_DAY = 6
MAX_UNSTRUCTURED_MINUTES = 240
MAX_EFFECT = 25

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


def _expected_effects(
    value: Any, field: str = "decision.expected_effects"
) -> dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValidationError(f"{field}: atteso un oggetto.")
    effects: dict[str, int] = {}
    for name, raw in value.items():
        if name not in NEED_NAMES:
            raise ValidationError(
                f"{field}: bisogno sconosciuto '{name}'."
            )
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValidationError(
                f"{field}.{name}: attesa una variazione intera."
            )
        if not -MAX_EFFECT <= raw <= MAX_EFFECT:
            raise ValidationError(
                f"{field}.{name}: variazione fuori limite "
                f"[-{MAX_EFFECT}, {MAX_EFFECT}]."
            )
        effects[name] = raw
    return effects


def _validate_intent(block: Any) -> dict[str, Any]:
    if not isinstance(block, dict):
        raise ValidationError("decision.intent: atteso un oggetto.")
    title = _clean_text(block.get("title"), "decision.intent.title")
    goal = _clean_text(block.get("goal"), "decision.intent.goal")
    why_now = _clean_text(block.get("why_now"), "decision.intent.why_now")
    tool = block.get("tool")
    if tool is None:
        tool = "none"
    if tool not in INTENT_TOOLS:
        raise ValidationError(
            "decision.intent.tool: ammessi none, browser, files e whatsapp."
        )
    target = _clean_text(
        block.get("target"), "decision.intent.target", required=False
    )
    intent_id = _clean_text(
        block.get("intent_id"), "decision.intent.intent_id", required=False
    )
    raw_arguments = block.get("arguments", {})
    if raw_arguments is None:
        raw_arguments = {}
    if not isinstance(raw_arguments, dict):
        raise ValidationError("decision.intent.arguments: atteso un oggetto.")
    if len(raw_arguments) > MAX_INTENT_ARGUMENTS:
        raise ValidationError(
            f"decision.intent.arguments: massimo {MAX_INTENT_ARGUMENTS} chiavi."
        )
    arguments: dict[str, Any] = {}
    for key, value in raw_arguments.items():
        if not isinstance(key, str) or not key.strip():
            raise ValidationError(
                "decision.intent.arguments: le chiavi devono essere stringhe non vuote."
            )
        if isinstance(value, (str, bool, int, float)) or value is None:
            arguments[key] = value
        elif isinstance(value, list) and all(
            isinstance(item, str) for item in value
        ):
            arguments[key] = value
        else:
            raise ValidationError(
                f"decision.intent.arguments.{key}: valore non ammesso."
            )
    if tool == "files":
        path = target or arguments.get("path")
        content = arguments.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise ValidationError(
                "Un intento files richiede target/path e arguments.content."
            )
        if len(content) > 262_144:
            raise ValidationError(
                "decision.intent.arguments.content: massimo 262144 caratteri."
            )
    if tool == "whatsapp":
        recipient = target or arguments.get("user_id")
        text = arguments.get("text")
        if not isinstance(recipient, str) or not isinstance(text, str):
            raise ValidationError(
                "Un intento whatsapp richiede target/user_id e arguments.text."
            )
        if len(text) > 2000:
            raise ValidationError(
                "decision.intent.arguments.text: massimo 2000 caratteri."
            )
    if tool == "browser":
        url = target or arguments.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValidationError(
                "Un intento browser richiede target o arguments.url."
            )
    raw_steps = block.get("steps")
    if raw_steps is None:
        steps: list[str] = []
    else:
        if not isinstance(raw_steps, list):
            raise ValidationError("decision.intent.steps: attesa una lista.")
        if len(raw_steps) > MAX_INTENT_STEPS:
            raise ValidationError(
                f"decision.intent.steps: massimo {MAX_INTENT_STEPS} passi."
            )
        steps = [
            _clean_text(item, f"decision.intent.steps[{index}]")
            for index, item in enumerate(raw_steps)
        ]
    risk = block.get("risk", "low")
    if risk not in INTENT_RISKS:
        raise ValidationError(
            "decision.intent.risk: ammessi low, medium e high."
        )
    requires_confirmation = block.get(
        "requires_confirmation", tool != "none"
    )
    if not isinstance(requires_confirmation, bool):
        raise ValidationError(
            "decision.intent.requires_confirmation: atteso un booleano."
        )
    return {
        "intent_id": intent_id,
        "title": title,
        "goal": goal,
        "why_now": why_now,
        "tool": tool,
        "target": target,
        "steps": steps,
        "arguments": arguments,
        "expected_effects": _expected_effects(
            block.get("expected_effects"), "decision.intent.expected_effects"
        ),
        "risk": risk,
        "requires_confirmation": requires_confirmation,
    }


def validate_decision(decision: Any) -> dict[str, Any]:
    """Valida una decisione senza applicarne gli effetti."""

    if not isinstance(decision, dict):
        raise ValidationError("decision: atteso un oggetto JSON.")
    kind = decision.get("decision")
    if kind == "do_nothing":
        raise ValidationError("do_nothing non è una decisione valida.")
    if kind not in DECISIONS:
        raise ValidationError(
            "decision: ammessi continue, unstructured e intent."
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
                block.get("expected_effects"),
                "decision.unstructured.expected_effects",
            ),
        }
    if kind == "intent":
        normalized["intent"] = _validate_intent(decision.get("intent"))
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
            "outcome": view.get("outcome"),
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

    def _life_block(self, state: dict[str, Any]) -> dict[str, Any]:
        return normalize_life(
            state.get("life"),
            now=self.now.isoformat(),
            mental_load=_number(state.get("mental_load")),
        )

    def _apply_effects(
        self, life: dict[str, Any], effects: dict[str, int]
    ) -> dict[str, dict[str, Any]]:
        applied: dict[str, dict[str, Any]] = {}
        for name, delta in effects.items():
            item = life["needs"][name]
            before = float(item["value"])
            after = max(0.0, min(100.0, before + float(delta)))
            item["value"] = round(after, 2)
            item["trend"] = (
                "up" if delta > 0 else "down" if delta < 0 else "stable"
            )
            item["updated_at"] = self.now.isoformat()
            item["source"] = "decision"
            applied[name] = {
                "before": round(before, 2),
                "delta": delta,
                "after": round(after, 2),
                "clamped": round(before + float(delta), 2) != round(after, 2),
            }
        return applied

    def _append_stream(
        self, life: dict[str, Any], kind: str, text: str
    ) -> None:
        cleaned = text.strip()[:MAX_TEXT]
        if not cleaned:
            cleaned = "Attività registrata."
        stream = life.get("stream")
        if not isinstance(stream, list):
            stream = []
        stream.append(
            {"at": self.now.isoformat(), "kind": kind, "text": cleaned}
        )
        life["stream"] = stream[-MAX_STREAM:]
        life["updated_at"] = self.now.isoformat()

    def _touch_loop(
        self, life: dict[str, Any], target: str | None
    ) -> str | None:
        if not target:
            return None
        wanted = target.casefold()
        for loop in life["open_loops"]:
            if not isinstance(loop, dict):
                continue
            if loop.get("status") not in (None, "active"):
                continue
            loop_id = loop.get("id")
            title = loop.get("title")
            if not isinstance(loop_id, str) or not isinstance(title, str):
                continue
            if wanted in {loop_id.casefold(), title.casefold()} or wanted in title.casefold():
                loop["last_touched_at"] = self.now.isoformat()
                loop["status"] = "active"
                return loop_id
        return None

    def _activity_patch(
        self,
        state: dict[str, Any],
        *,
        episode_id: str,
        label: str,
        category: str,
        attention: str,
        duration_minutes: int,
    ) -> dict[str, Any]:
        current = state.get("activity")
        current = current if isinstance(current, dict) else {}
        location = current.get("location")
        if not isinstance(location, str) or not location.strip():
            environment = self._life_block(state)["environment"]
            location = environment.get("location")
        return {
            "id": f"act_{episode_id}",
            "label": label,
            "category": category,
            "location": location,
            "started_at": self.now.isoformat(),
            "expected_end_at": (
                self.now + timedelta(minutes=duration_minutes)
            ).isoformat(),
            "details": attention,
        }

    def _reflection_patch(
        self, state: dict[str, Any], applied: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        if not applied:
            return {}
        emotion = state.get("emotion")
        if not isinstance(emotion, dict):
            return {}
        stress = applied.get("stress")
        if not stress:
            return {}
        delta = stress.get("delta", 0)
        if not isinstance(delta, (int, float)) or isinstance(delta, bool):
            return {}
        result = copy.deepcopy(emotion)
        valence = _number(result.get("valence"), default=0.0) - delta * 0.01
        arousal = _number(result.get("arousal"), default=0.3) + delta * 0.005
        result["valence"] = round(max(-1.0, min(1.0, valence)), 3)
        result["arousal"] = round(max(0.0, min(1.0, arousal)), 3)
        return {"emotion": result}

    def _execute_intent(self, intent: dict[str, Any]) -> dict[str, Any]:
        """Esegue un intento esterno e restituisce solo dati non sensibili.

        Gli effetti remoti non sono transazionali: se la scrittura successiva
        fallisce, il file o la riga di outbox restano. Gli errori vengono
        intercettati dal chiamante e registrati come intento bloccato.
        """

        tool = intent["tool"]
        arguments = intent["arguments"]
        if tool == "files":
            path = intent.get("target") or arguments.get("path")
            result = FileSandbox(self.root).write_file(
                path,
                arguments["content"],
                overwrite=arguments.get("overwrite") is True,
            )
            return {
                "path": result["path"],
                "size": result["size"],
                "created": result["created"],
                "overwritten": result["overwritten"],
            }
        if tool == "whatsapp":
            user_id = intent.get("target") or arguments.get("user_id")
            result = MessageExecutor(self.root, at=self.now).deliver_intent(
                arguments["text"], user_id, intent_id=intent["intent_id"]
            )
            target = result.get("target") or {}
            return {
                "message_id": result.get("message_id"),
                "delivered_at": result.get("delivered_at"),
                "channel": result.get("channel"),
                "recipient": target.get("display_name") or target.get("user_id"),
            }

        url = intent.get("target") or arguments.get("url")
        policy = BrowserPolicy.from_root(self.root)
        policy.require_enabled()
        policy.assert_allowed(url)
        state = self._state()
        heartbeat = state.get("heartbeat")
        heartbeat = heartbeat if isinstance(heartbeat, dict) else {}
        if isinstance(heartbeat.get("pending_impulse"), dict):
            raise ValidationError(
                "C'è già un impulso in attesa: va gestito prima di accodarne "
                "un altro."
            )
        stamp = self.now.replace(microsecond=0).isoformat()
        impulse = {
            "trigger": "life_intent",
            "target": None,
            "context": {
                "intent_id": intent["intent_id"],
                "title": intent["title"],
                "goal": intent["goal"],
            },
            "created_at": stamp,
            "delivery": {
                "channel": "browser",
                "authorized": True,
                "url": url,
                "note": "Navigazione solo verso le origini in whitelist.",
            },
            "requires_message": False,
        }
        self.config.update_current_state(
            {
                "heartbeat": {
                    "pending_impulse": impulse,
                    "spontaneous_message_pending": True,
                }
            }
        )
        return {"queued": True, "trigger": "life_intent", "created_at": stamp}

    def _apply_decision_locked(
        self, decision: dict[str, Any], *, episode_id: str
    ) -> dict[str, Any]:
        """Applica gli effetti interni e restituisce l'esito leggibile."""

        state = self._state()
        life = self._life_block(state)
        kind = decision["decision"]
        effects: dict[str, int] = {}
        applied: dict[str, dict[str, Any]] = {}
        patch: dict[str, Any] = {}
        loop_id: str | None = None

        if kind == "continue":
            activity = state.get("activity")
            activity = activity if isinstance(activity, dict) else {}
            label = activity.get("label")
            label = label.strip() if isinstance(label, str) and label.strip() else "attività in corso"
            self._append_stream(life, "continue", f"Continua: {label}.")
            reflection = "L'attività in corso continua senza cambiamenti."
            note = "Nessuna esecuzione: l'attività in corso continua."
        elif kind == "unstructured":
            block = decision["unstructured"]
            effects = block.get("expected_effects") or {}
            applied = self._apply_effects(life, effects)
            activity = block["activity"]
            attention = block["attention"]
            mental_content = block.get("mental_content")
            life["mode"] = "unstructured"
            life["attention"] = attention
            life["mental_content"] = mental_content
            stream_text = f"{activity}; attenzione: {attention}"
            if mental_content:
                stream_text += f"; mente: {mental_content}"
            self._append_stream(life, "unstructured", stream_text + ".")
            patch["activity"] = self._activity_patch(
                state,
                episode_id=episode_id,
                label=activity,
                category="unstructured",
                attention=attention,
                duration_minutes=block["duration_minutes"],
            )
            reflection = (
                f"Tempo non strutturato: {activity}; attenzione su {attention}."
            )
            note = "Attività non strutturata registrata; nessun tool esterno eseguito."
        else:
            intent = decision["intent"]
            if not intent.get("intent_id"):
                intent["intent_id"] = f"int_{episode_id}"
            tool = intent["tool"]
            title = intent["title"]
            if tool != "none" and intent["requires_confirmation"]:
                self._append_stream(
                    life,
                    "intent_blocked",
                    f"Intento {tool} in attesa di conferma: {title}.",
                )
                patch["life"] = life
                self.config.update_current_state(patch)
                return {
                    "applied": False,
                    "simulated": False,
                    "blocked": True,
                    "reason": kind,
                    "tool": tool,
                    "confirmation_required": True,
                    "effects": {},
                    "reflection": (
                        f"Intento {tool} non eseguito: richiede una conferma "
                        "amministrativa esplicita."
                    ),
                    "note": (
                        "Nessun effetto applicato e nessun tool eseguito: "
                        "l'intento esterno richiede conferma."
                    ),
                }
            execution: dict[str, Any] | None = None
            if tool != "none":
                try:
                    execution = self._execute_intent(intent)
                except AgentMemoryError as exc:
                    message = str(exc)[:MAX_TEXT]
                    self._append_stream(
                        life,
                        "intent_blocked",
                        f"Intento {tool} non eseguito: {message}",
                    )
                    patch["life"] = life
                    self.config.update_current_state(patch)
                    return {
                        "applied": False,
                        "simulated": False,
                        "blocked": True,
                        "reason": kind,
                        "tool": tool,
                        "confirmation_required": False,
                        "effects": {},
                        "error": message,
                        "reflection": f"Intento {tool} non eseguito: {message}",
                        "note": (
                            "Nessun effetto applicato; l'errore è registrato "
                            "senza ripetere l'azione."
                        ),
                    }
            effects = intent.get("expected_effects") or {}
            applied = self._apply_effects(life, effects)
            loop_id = self._touch_loop(life, intent.get("target"))
            life["mode"] = "intent"
            life["attention"] = title
            life["mental_content"] = intent["goal"]
            if tool == "none":
                stream_text = f"Intento simulato: {title}; {intent['goal']}"
            else:
                stream_text = f"Intento {tool} eseguito: {title}; {intent['goal']}"
            self._append_stream(
                life,
                "intent" if tool == "none" else f"intent_{tool}",
                stream_text,
            )
            patch["activity"] = self._activity_patch(
                state,
                episode_id=episode_id,
                label=title,
                category="intent",
                attention=title,
                duration_minutes=decision["next_review_minutes"],
            )
            if tool == "none":
                reflection = f"Intento interno simulato: {title}. {intent['goal']}"
                note = (
                    "Intento con tool none: stato e loop aggiornati; "
                    "l'azione resta simulata e non verificata fisicamente."
                )
            else:
                reflection = f"Intento {tool} eseguito: {title}. {intent['goal']}"
                note = {
                    "files": "File scritto nella sandbox files/ dell'agente.",
                    "whatsapp": (
                        "Messaggio accodato nell'outbox dopo i controlli di "
                        "whitelist, flag e limiti."
                    ),
                    "browser": (
                        "Navigazione accodata come impulso: l'esecuzione spetta "
                        "ai tool autonomi, con la policy del browser."
                    ),
                }[tool]

        patch["life"] = life
        patch.update(self._reflection_patch(state, applied))
        self.config.update_current_state(patch)
        outcome: dict[str, Any] = {
            "applied": True,
            "simulated": True,
            "reason": kind,
            "effects": applied,
            "reflection": reflection,
            "note": note,
        }
        if kind == "intent":
            intent = decision["intent"]
            outcome["tool"] = intent["tool"]
            outcome["loop_id"] = loop_id
            if intent["tool"] != "none":
                outcome["simulated"] = False
                outcome["execution"] = execution
        return outcome

    def _expire_locked(self, conn: Any) -> list[dict[str, Any]]:
        expired: list[dict[str, Any]] = []
        for row in conn.execute(
            "SELECT * FROM deliberation_episodes WHERE state = 'open' "
            "ORDER BY opened_at ASC"
        ).fetchall():
            expires = _parse(row["expires_at"], self.timezone)
            if expires is None or expires > self.now:
                continue
            decision = self._default_decision(row["id"], self._state())
            outcome = self._apply_decision_locked(
                decision, episode_id=row["id"]
            )
            outcome["expired"] = True
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
        """Registra una decisione e applica gli effetti.

        Gli intenti esterni vengono eseguiti solo con ``requires_confirmation``
        falso; in caso contrario l'esito è bloccato e nessun effetto viene
        applicato. Un errore dell'esecuzione chiude l'episodio come bloccato,
        senza ritentare l'azione.
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
            outcome = self._apply_decision_locked(
                normalized, episode_id=row["id"]
            )
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
