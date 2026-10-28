"""Incarichi per l'agente MCP: nessun modello o scheduler incorporato.

Il client richiama claim, osserva e decide i passi. Gli effetti remoti non
sono transazionali: un'azione senza ricevuta rimane incerta, mai ripetuta.
"""
from __future__ import annotations

import json
import uuid
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from .browser import BrowserController, BrowserPolicy
from .config import ConfigStore
from .errors import AgentMemoryError, ConfigError, ValidationError
from .operational import canonical

MAX_STEPS = 20
MAX_AGE_SECONDS = 30 * 60
ACTIVE = ("ready", "running", "awaiting_verification", "uncertain")


class AutonomyService:
    def __init__(self, root, browser: BrowserController | None):
        self.config = ConfigStore(root)
        self.browser = browser

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def _policy(self) -> BrowserPolicy:
        # Reload per operation: an administrator's revocation applies immediately.
        policy = BrowserPolicy.from_root(self.config.root)
        if self.browser is None:
            raise ValidationError("Controller browser non disponibile.")
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

    @staticmethod
    def _action_uncertain(action: Any) -> bool:
        return action["result"] is None or json.loads(action["result"]).get("state") == "uncertain"

    def _review_reason(self, run: dict[str, Any], actions: list) -> str | None:
        if run["state"] not in ACTIVE and run["state"] != "blocked":
            return None
        if any(self._action_uncertain(row) for row in actions):
            return "uncertain"
        if run["state"] == "blocked":
            return "blocked"
        if run["state"] in ("ready", "awaiting_verification"):
            try:
                created = datetime.fromisoformat(run["created_at"])
                if created.tzinfo is None:
                    return "expired"
                if (self._now() - created).total_seconds() > MAX_AGE_SECONDS:
                    return "expired"
            except (TypeError, ValueError):
                return "expired"
        return None

    def review_summary(self, *, limit: int = 10) -> dict[str, Any]:
        """Vista amministrativa limitata: nessun testo di pagina o parametro delle azioni."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValidationError("limit deve essere tra 1 e 50.")
        self.config.load_all()
        conn = self.config.operational.connect()
        try:
            conn.execute("BEGIN")
            rows = conn.execute(
                "SELECT r.*, v.reviewed_at, v.note AS review_note, v.impulse_cleared "
                "FROM autonomous_runs r LEFT JOIN autonomous_reviews v ON v.run_id=r.id "
                "ORDER BY CASE "
                "WHEN v.run_id IS NULL AND r.state IN ('ready','running','awaiting_verification','uncertain','blocked') "
                "AND EXISTS (SELECT 1 FROM autonomous_actions a WHERE a.run_id=r.id "
                "AND (a.result IS NULL OR json_extract(a.result, '$.state')='uncertain')) THEN 0 "
                "WHEN v.run_id IS NULL AND (r.state='blocked' OR "
                "(r.state IN ('ready','awaiting_verification') AND "
                "(julianday(r.created_at) IS NULL OR julianday(r.created_at)<julianday(?)))) THEN 1 "
                "WHEN r.state IN ('ready','running','awaiting_verification','uncertain') THEN 2 "
                "ELSE 3 END, r.rowid DESC LIMIT ?", ((self._now() - timedelta(seconds=MAX_AGE_SECONDS)).isoformat(), limit),
            ).fetchall()
            # Keep every actionable review ahead of ordinary or already reviewed
            # records before applying the display limit.
            runs = []
            for row in rows:
                actions = conn.execute(
                    "SELECT id, payload, result FROM autonomous_actions WHERE run_id=? ORDER BY rowid",
                    (row["id"],),
                ).fetchall()
                last = actions[-1] if actions else None
                reason = self._review_reason(dict(row), actions) if not row["reviewed_at"] else None
                try:
                    parsed = urlsplit(row["url"])
                    origin = f"{parsed.scheme}://{parsed.hostname}"
                    if parsed.port:
                        origin += f":{parsed.port}"
                except ValueError:
                    origin = "Origine non disponibile"
                impulse = json.loads(row["impulse"])
                runs.append({
                    "id": row["id"], "state": row["state"], "created_at": row["created_at"],
                    "origin": origin, "trigger": impulse.get("trigger"), "steps": row["steps"],
                    "last_action": {"id": last["id"], "action": json.loads(last["payload"]).get("action"),
                                    "uncertain": self._action_uncertain(last)} if last else None,
                    "review_required": reason is not None, "review_reason": reason,
                    "review": {"reviewed_at": row["reviewed_at"], "note": row["review_note"],
                               "impulse_cleared": bool(row["impulse_cleared"])} if row["reviewed_at"] else None,
                })
            return {"runs": runs, "limit": limit}
        finally:
            conn.close()

    def review_uncertain(self, run_id: str, confirmation: str, note: str) -> dict[str, Any]:
        """Chiude un incarico incerto, scaduto o interrotto senza riattivare azioni."""
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValidationError("Identificativo incarico obbligatorio.")
        if confirmation != "HO VERIFICATO":
            raise ValidationError("Scrivi HO VERIFICATO per confermare la revisione.")
        if not isinstance(note, str) or not note.strip() or len(note.strip()) > 1000:
            raise ValidationError("Nota di revisione obbligatoria, massimo 1000 caratteri.")
        from .profile import _profile_lock
        with ExitStack() as stack:
            try:
                stack.enter_context(_profile_lock(self.config.root))
            except ConfigError as exc:
                raise ValidationError(f"Impossibile revisionare ora: {exc}") from exc
            return self._review_uncertain(run_id, note.strip())

    def _review_uncertain(self, run_id: str, note: str) -> dict[str, Any]:
        with self.config.transaction() as conn:
            run = self._run(conn, run_id)
            if conn.execute("SELECT 1 FROM autonomous_reviews WHERE run_id=?", (run_id,)).fetchone():
                raise ValidationError("Incarico già revisionato; aggiorna la dashboard.")
            actions = conn.execute("SELECT result FROM autonomous_actions WHERE run_id=?", (run_id,)).fetchall()
            if not self._review_reason(run, actions):
                raise ValidationError("L'incarico non è incerto, scaduto o interrotto.")
            state = self.config.get("current_state")
            clear_impulse = state.get("heartbeat", {}).get("pending_impulse") == json.loads(run["impulse"])
            if clear_impulse:
                self.config.update_current_state({"heartbeat": {"pending_impulse": None,
                    "spontaneous_message_pending": False, "last_decision": "browser_reviewed_without_delivery"}})
            reviewed_at = self._now().isoformat()
            conn.execute("UPDATE autonomous_runs SET state='blocked' WHERE id=?", (run_id,))
            conn.execute("INSERT INTO autonomous_reviews(run_id, reviewed_at, note, impulse_cleared) VALUES (?, ?, ?, ?)",
                         (run_id, reviewed_at, note, int(clear_impulse)))
            return {"state": "blocked", "reviewed_at": reviewed_at, "impulse_cleared": clear_impulse,
                    "remote_delivery_verified": False}

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
            unresolved = conn.execute(
                "SELECT 1 FROM autonomous_runs r "
                "JOIN autonomous_actions a ON a.run_id=r.id "
                "LEFT JOIN autonomous_reviews v ON v.run_id=r.id "
                "WHERE r.state='blocked' AND v.run_id IS NULL "
                "AND (a.result IS NULL OR json_extract(a.result, '$.state')='uncertain') LIMIT 1"
            ).fetchone()
            if unresolved:
                raise ValidationError("Un'azione incerta deve essere revisionata dalla dashboard prima di un nuovo incarico.")
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
        from .profile import _profile_lock
        with _profile_lock(self.config.root, timeout=30):
            return self._abort(run_id)

    def _abort(self, run_id: str) -> dict[str, Any]:
        with self.config.transaction() as conn:
            run = self._run(conn, run_id)
            if run["state"] == "running":
                raise ValidationError("Azione in corso: controlla lo stato prima di interrompere.")
            if run["state"] in ACTIVE:
                conn.execute("UPDATE autonomous_runs SET state='blocked' WHERE id=?", (run_id,))
        return {"state": "blocked", "remote_delivery_verified": False}
