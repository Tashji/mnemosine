"""Incarichi per l'agente MCP: nessun modello o scheduler incorporato.

Il client richiama claim, osserva e decide i passi. Gli effetti remoti non
sono transazionali: un'azione senza ricevuta rimane incerta, mai ripetuta.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from .browser import BrowserController, BrowserPolicy
from .config import ConfigStore
from .errors import AgentMemoryError, ValidationError
from .operational import canonical

MAX_STEPS = 20
MAX_AGE_SECONDS = 30 * 60
ACTIVE = ("ready", "running", "awaiting_verification", "uncertain")


class AutonomyService:
    def __init__(self, root, browser: BrowserController):
        self.config = ConfigStore(root)
        self.browser = browser

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def _policy(self) -> BrowserPolicy:
        # Reload per operation: an administrator's revocation applies immediately.
        policy = BrowserPolicy.from_root(self.config.root)
        self.browser.policy = policy
        backend = self.browser._backend
        if backend is not None and hasattr(backend, "policy"):
            backend.policy = policy
        return policy

    def _allowed(self, run: dict[str, Any]) -> None:
        state = self.config.get("current_state")
        if state.get("flags", {}).get("maintenance_mode"):
            raise ValidationError("Modalità manutenzione: incarico sospeso.")
        created = datetime.fromisoformat(run["created_at"])
        if (self._now() - created).total_seconds() > MAX_AGE_SECONDS:
            raise ValidationError("Incarico scaduto: non ripetere le azioni.")
        if state.get("heartbeat", {}).get("pending_impulse") != json.loads(run["impulse"]):
            raise ValidationError("L'impulso originale non è più in attesa.")
        self._policy().require_enabled()
        self.browser.policy.assert_allowed(run["url"])

    @staticmethod
    def _run(conn, run_id: str) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM autonomous_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValidationError("Incarico non trovato.")
        return dict(row)

    @staticmethod
    def _view(run: dict[str, Any]) -> dict[str, Any]:
        return {**run, "impulse": json.loads(run["impulse"]),
                "observation": json.loads(run["observation"]) if run.get("observation") else None,
                "max_steps": MAX_STEPS, "remote_delivery_verified": False}

    def claim(self, url: str) -> dict[str, Any]:
        """Acquisisce l'impulso, senza aprire il browser né inviare messaggi."""
        policy = self._policy()
        policy.require_enabled()
        policy.assert_allowed(url)
        with self.config.transaction() as conn:
            state = self.config.get("current_state")
            impulse = state.get("heartbeat", {}).get("pending_impulse")
            if not isinstance(impulse, dict):
                return {"available": False}
            created = impulse.get("created_at")
            try:
                moment = datetime.fromisoformat(created)
                if moment.tzinfo is None:
                    raise ValueError("timezone missing")
                age = (self._now() - moment).total_seconds()
                if not -300 <= age <= 12 * 3600:
                    raise ValueError("expired")
            except (TypeError, ValueError) as exc:
                raise ValidationError("Impulso scaduto o data non valida.") from exc
            existing = conn.execute("SELECT * FROM autonomous_runs WHERE state IN ('ready','running','awaiting_verification','uncertain')").fetchone()
            if existing:
                run = dict(existing)
                if run["impulse"] != canonical(impulse):
                    raise ValidationError("Un altro incarico è ancora attivo: controllalo e interrompilo esplicitamente.")
                return {"available": True, **self._view(run)}
            previous = conn.execute("SELECT id FROM autonomous_runs WHERE impulse=?", (canonical(impulse),)).fetchone()
            if previous:
                raise ValidationError("Questo impulso è già stato acquisito: usa l'incarico esistente.")
            run = {"id": uuid.uuid4().hex, "impulse": canonical(impulse), "url": url,
                   "created_at": self._now().isoformat(), "state": "ready", "steps": 0, "observation": None}
            self._allowed(run)
            conn.execute("INSERT INTO autonomous_runs(id, impulse, state, url, created_at) VALUES (?, ?, ?, ?, ?)",
                         (run["id"], run["impulse"], run["state"], url, run["created_at"]))
            return {"available": True, **self._view(run)}

    def status(self, run_id: str) -> dict[str, Any]:
        from .profile import _profile_lock
        with _profile_lock(self.config.root, timeout=30):
            return self._status(run_id)

    def _status(self, run_id: str) -> dict[str, Any]:
        with self.config.transaction() as conn:
            run = self._run(conn, run_id)
            pending = conn.execute("SELECT 1 FROM autonomous_actions WHERE run_id=? AND result IS NULL", (run_id,)).fetchone()
            if pending and run["state"] in ACTIVE:
                run["state"] = "uncertain"
                conn.execute("UPDATE autonomous_runs SET state='uncertain' WHERE id=?", (run_id,))
            return self._view(run)

    def observe(self, run_id: str) -> dict[str, Any]:
        with self.config.transaction() as conn:
            run = self._run(conn, run_id)
            if run["state"] not in ACTIVE:
                raise ValidationError("Incarico non attivo.")
            self._allowed(run)
        observation = self.browser.snapshot()
        observation["elements"] = self.browser.elements()["elements"]
        with self.config.transaction() as conn:
            conn.execute("UPDATE autonomous_runs SET observation=? WHERE id=?", (canonical(observation), run_id))
        return {"observation": observation, "remote_delivery_verified": False}

    def act(self, run_id: str, action_id: str, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        from .profile import _profile_lock
        with _profile_lock(self.config.root, timeout=30):
            return self._act(run_id, action_id, action, arguments)

    def _act(self, run_id: str, action_id: str, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(action_id, str) or not action_id.strip() or len(action_id) > 100:
            raise ValidationError("action_id obbligatorio, massimo 100 caratteri.")
        operations = {"open": self.browser.open, "click": self.browser.click,
                      "fill": self.browser.fill, "press": self.browser.press,
                      "wait": self.browser.wait}
        if action not in operations or not isinstance(arguments, dict):
            raise ValidationError("Azione o argomenti non validi.")
        allowed_arguments = {"open": {"url"}, "click": {"selector", "index"},
                             "fill": {"selector", "index", "value"}, "press": {"key", "selector", "index"},
                             "wait": {"selector", "text", "timeout_seconds"}}
        if set(arguments) - allowed_arguments[action]:
            raise ValidationError("Argomenti non consentiti per questa azione.")
        payload = canonical({"action": action, "arguments": arguments})
        with self.config.transaction() as conn:
            run = self._run(conn, run_id)
            previous = conn.execute("SELECT payload,result FROM autonomous_actions WHERE run_id=? AND id=?", (run_id, action_id)).fetchone()
            if previous:
                if previous["payload"] != payload:
                    raise ValidationError("action_id riutilizzato con dati diversi.")
                return json.loads(previous["result"]) if previous["result"] else {"state": "uncertain", "retry_allowed": False}
            if run["state"] not in ("ready", "awaiting_verification"):
                raise ValidationError("Incarico non pronto; esito incerto o azione in corso. Non ripetere gli effetti.")
            if run["steps"] >= MAX_STEPS:
                raise ValidationError("Limite di passi raggiunto.")
            self._allowed(run)
            if action == "open":
                self.browser.policy.assert_allowed(arguments.get("url", ""))
            elif action in ("click", "fill", "press"):
                self.browser.policy.assert_interaction_allowed(self.browser.current_url or "")
            conn.execute("INSERT INTO autonomous_actions(run_id,id,payload) VALUES (?,?,?)", (run_id, action_id, payload))
            conn.execute("UPDATE autonomous_runs SET state='running', steps=steps+1 WHERE id=?", (run_id,))
        try:
            operations[action](**arguments)
            observation = self.browser.snapshot()
            result = {"state": "awaiting_verification", "observation": observation, "remote_delivery_verified": False}
        except (AgentMemoryError, TypeError, ValueError) as exc:
            # Even a failed observation can follow a successful external effect.
            result = {"state": "uncertain", "error": str(exc), "retry_allowed": False}
        with self.config.transaction() as conn:
            conn.execute("UPDATE autonomous_actions SET result=? WHERE run_id=? AND id=?", (canonical(result), run_id, action_id))
            conn.execute("UPDATE autonomous_runs SET state=?, observation=? WHERE id=?",
                         (result["state"], canonical(result.get("observation")), run_id))
        return result

    def verify(self, run_id: str, text: str) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip() or len(text) > 300:
            raise ValidationError("Testo di verifica obbligatorio, massimo 300 caratteri.")
        with self.config.transaction() as conn:
            run = self._run(conn, run_id)
            if run["state"] != "awaiting_verification":
                raise ValidationError("Solo un'azione terminata può essere verificata; un esito incerto richiede intervento umano.")
            self._allowed(run)
            expected_url = json.loads(run["observation"])["url"]
        observation = self.browser.snapshot()
        matched = observation["url"] == expected_url and text.casefold() in observation.get("text", "").casefold()
        with self.config.transaction() as conn:
            current = self._run(conn, run_id)
            self._allowed(current)
            if current["state"] != "awaiting_verification" or current["steps"] != run["steps"]:
                raise ValidationError("Incarico cambiato durante la verifica.")
            if matched:
                conn.execute("UPDATE autonomous_runs SET state='completed', observation=? WHERE id=?", (canonical(observation), run_id))
                self.config.update_current_state({"heartbeat": {"pending_impulse": None,
                    "spontaneous_message_pending": False, "last_decision": "browser_ui_verified"}})
        return {"ui_verified": matched, "remote_delivery_verified": False, "observation": observation}

    def abort(self, run_id: str) -> dict[str, Any]:
        """Interrompe l'incarico senza confermare consegne o cancellare impulsi."""
        with self.config.transaction() as conn:
            run = self._run(conn, run_id)
            if run["state"] == "running":
                raise ValidationError("Azione in corso: controlla lo stato prima di interrompere.")
            if run["state"] in ACTIVE:
                conn.execute("UPDATE autonomous_runs SET state='blocked' WHERE id=?", (run_id,))
        return {"state": "blocked", "remote_delivery_verified": False}
