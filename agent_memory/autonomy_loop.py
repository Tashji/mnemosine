"""Ciclo autonomo locale: battito, messaggio e trasporto outbox.

Questo modulo è un client amministrativo opzionale, non un tool MCP e non un
server. Il server resta senza LLM e senza invio diretto: il ciclo legge la
situazione, chiede al modello locale un testo breve, costruisce un intento
WhatsApp con ``requires_confirmation=False`` e lo fa eseguire dal motore vita.
L'esecuzione scrive nell'outbox; il trasporto verso WhatsApp Web usa il
trasporto esistente, con i suoi gate e il suo lock.

Uso:
    python -m agent_memory.autonomy_loop --root <profilo> --once
    python -m agent_memory.autonomy_loop --root <profilo> --once --dry-run --seed 0
    python -m agent_memory.autonomy_loop --root <profilo> --loop --interval 30

Il ciclo si ferma se l'outbox produce un esito incerto: un invio non confermato
non va ripetuto e richiede una revisione umana. Il limite predefinito è un
messaggio autonomo al giorno; il limite dell'outbox resta comunque attivo.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import ConfigStore
from .deliberation import DeliberationService
from .errors import AgentMemoryError, ValidationError
from .executor import Contact, WhatsAppPolicy
from .heartbeat import HeartbeatEngine
from .outbox import OutboxTransport
from .persona import load_persona
from .situation import SituationBuilder

DEFAULT_MODEL = "mnemosine-qwen35:9b-abliterated"
DEFAULT_INTERVAL_MINUTES = 30
DEFAULT_MAX_PER_DAY = 1
MAX_MESSAGE_CHARS = 700
MAX_PROMPT_SITUATION_CHARS = 4500
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
FALLBACK_MESSAGE = "Ciao {name}, come stai? Ti va di raccontarmi come procede?"


def clean_message(text: Any) -> str:
    """Ripulisce l'uscita del modello senza inventare contenuti."""

    if not isinstance(text, str):
        return ""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:[A-Za-z0-9_-]+)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    if len(cleaned) >= 2 and cleaned[0] in "\"'“”‘’" and cleaned[-1] in "\"'“”‘’":
        cleaned = cleaned[1:-1].strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for prefix in ("messaggio:", "testo:", "whatsapp:"):
        if cleaned.casefold().startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    if len(cleaned) > MAX_MESSAGE_CHARS:
        cleaned = cleaned[:MAX_MESSAGE_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return cleaned


def prompt_situation(report: dict[str, Any]) -> str:
    """Riduce il rapporto ai soli dati utili, senza incognite o dati interni."""

    now = report.get("now") if isinstance(report.get("now"), dict) else {}
    lines: list[str] = []
    local_time = now.get("local_time")
    day = now.get("day")
    if isinstance(local_time, str) and local_time.strip():
        suffix = f" ({day})" if isinstance(day, str) and day.strip() else ""
        lines.append(f"Ora: {local_time}{suffix}.")
    activity = report.get("activity") if isinstance(report.get("activity"), dict) else {}
    label = activity.get("label")
    if isinstance(label, str) and label.strip():
        place = activity.get("location")
        place_text = f" a {place}" if isinstance(place, str) and place.strip() else ""
        lines.append(f"Attività in corso: {label}{place_text}.")
    relationships = report.get("relationships")
    if isinstance(relationships, list):
        for person in relationships:
            if not isinstance(person, dict):
                continue
            name = person.get("display_name")
            if not isinstance(name, str) or not name.strip():
                continue
            closeness = person.get("closeness")
            detail = f", {closeness}" if isinstance(closeness, str) and closeness.strip() else ""
            lines.append(f"Relazione: {name}{detail}.")
    return "\n".join(lines) or "Nessun dettaglio aggiuntivo."


def build_whatsapp_decision(
    *, episode: dict[str, Any], user_id: str, display_name: str, text: str
) -> dict[str, Any]:
    """Costruisce una decisione valida per un intento WhatsApp autonomo."""

    episode_id = str(episode.get("episode_id") or "episode")
    reason = str(episode.get("reason") or "life_engine")
    intent_id = "auto_" + re.sub(r"[^A-Za-z0-9_-]", "_", episode_id)[:48]
    name = display_name.strip() or user_id
    return {
        "decision": "intent",
        "rationale": f"Episodio {episode_id} aperto per {reason}.",
        "confidence": 0.7,
        "next_review_minutes": 60,
        "intent": {
            "intent_id": intent_id,
            "title": f"Scrivere a {name}",
            "goal": f"Coltivare la relazione con {name}.",
            "why_now": f"La vita interna ha aperto un episodio per {reason}.",
            "tool": "whatsapp",
            "target": user_id,
            "steps": [
                "Scegliere un messaggio breve e naturale",
                "Accodarlo nell'outbox del profilo",
            ],
            "arguments": {"text": text},
            "expected_effects": {"social": 5},
            "risk": "low",
            "requires_confirmation": False,
        },
    }


class OllamaChat:
    """Chiamata minima all'API locale di Ollama, senza dipendenze aggiuntive."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        url: str = OLLAMA_URL,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.url = url
        self.timeout = timeout

    def generate(self, persona: str, situation: str, recipient: str) -> str:
        system = (
            persona.strip()
            + "\n\nStai scrivendo spontaneamente un messaggio WhatsApp a una "
            "persona reale. Scrivi solo il testo del messaggio, in italiano, "
            "in prima persona, breve e naturale. Usa al massimo due frasi e "
            "chiudi con una domanda semplice. Il messaggio deve essere un "
            "saluto: chiedi come sta, racconta in una frase cosa stai facendo "
            "adesso e proponi due chiacchiere. Non parlare del passato, di "
            "cambiamenti, di come vi conoscete o di cosa è successo prima. Non "
            "inventare fatti, luoghi, incontri o ricordi che non sono nel "
            "contesto. Non parlare di bisogni, dati interni, casa, meteo, "
            "incognite o cose che non sai. Non scusarti, non spiegare perché "
            "scrivi e non dire che scrivi a caso. Controlla la grammatica. Non "
            "citare telefoni, indirizzi, credenziali o il fatto di essere un "
            "modello. Non usare segnaposto e non aggiungere spiegazioni.\n\n"
            "Esempi di tono, non copiarli: \"Ciao Angelo, come stai? Io sono a "
            "casa a rilassarmi un po'. Ti va di fare due chiacchiere?\""
        )
        situation = situation[:MAX_PROMPT_SITUATION_CHARS]
        user = (
            f"Destinatario: {recipient}.\n"
            f"Situazione attuale:\n{situation}\n\n"
            "Scrivi un messaggio di massimo 300 caratteri. Rispondi solo con "
            "il testo, senza virgolette, senza titolo e senza spiegazioni."
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "think": False,
            "options": {
                "num_ctx": 4096,
                "num_predict": 180,
                "temperature": 0.4,
                "top_p": 0.8,
                "repeat_penalty": 1.1,
            },
            "keep_alive": "5m",
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                "Ollama non ha risposto: controlla che il servizio locale sia avviato."
            ) from exc
        message = data.get("message") if isinstance(data, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        text = clean_message(content)
        if not text:
            raise RuntimeError("Ollama ha restituito un messaggio vuoto.")
        return text

    def __call__(self, persona: str, situation: str, recipient: str) -> str:
        return self.generate(persona, situation, recipient)


class AutonomyLoop:
    """Esegue un giro completo, riusando i gate e i servizi del profilo."""

    def __init__(
        self,
        root: str | Path,
        *,
        model: str = DEFAULT_MODEL,
        interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
        max_per_day: int = DEFAULT_MAX_PER_DAY,
        seed: int | None = None,
        user_id: str | None = None,
        dry_run: bool = False,
        allow_fallback: bool = True,
        at: datetime | None = None,
        generator: Callable[[str, str, str], str] | None = None,
    ) -> None:
        if interval_minutes < 1:
            raise ValidationError("interval: usa almeno un minuto.")
        if isinstance(max_per_day, bool) or not 0 <= max_per_day <= 24:
            raise ValidationError("max_per_day deve stare tra 0 e 24.")
        self.config = ConfigStore(root)
        self.root = self.config.root
        self.policy = WhatsAppPolicy.load(self.root)
        self.interval_minutes = interval_minutes
        self.max_per_day = max_per_day
        self.seed = seed
        self.dry_run = dry_run
        self.allow_fallback = allow_fallback
        self.at = at
        self._generator = generator or OllamaChat(model)
        self.user_id = self._select_contact(user_id)

    # -- configurazione ----------------------------------------------------

    def _select_contact(self, requested: str | None) -> str:
        if not self.policy.allowed_contacts:
            raise ValidationError(
                "Nessun contatto WhatsApp autorizzato: aggiungerne uno prima "
                "di avviare il ciclo autonomo."
            )
        if requested is not None:
            contact = self.policy.contact_for(requested)
            if contact is None:
                raise ValidationError(
                    f"Il contatto {requested!r} non è nella whitelist WhatsApp."
                )
            return contact.user_id
        if len(self.policy.allowed_contacts) != 1:
            raise ValidationError(
                "Più contatti autorizzati: indica --user-id per il ciclo autonomo."
            )
        return self.policy.allowed_contacts[0].user_id

    def _contact(self) -> Contact:
        contact = self.policy.contact_for(self.user_id)
        if contact is None:
            raise ValidationError("Il contatto scelto non è più autorizzato.")
        return contact

    def _state_flags(self) -> dict[str, Any]:
        state = self.config.get("current_state")
        flags = state.get("flags")
        return flags if isinstance(flags, dict) else {}

    def _gate(self) -> str | None:
        flags = self._state_flags()
        if not self.policy.enabled:
            return "integration_disabled"
        if flags.get("life_engine_enabled") is not True:
            return "life_engine_disabled"
        if not self.dry_run and flags.get("whatsapp_sending_enabled") is not True:
            return "sending_disabled"
        return None

    def _transport(self) -> OutboxTransport:
        return OutboxTransport(self.root, at=self.at)

    def _attempts_today(self, transport: OutboxTransport) -> int:
        """Conta anche gli esiti incerti: un invio non confermato occupa il giorno."""

        today = transport.now.date()
        count = 0
        for entry in transport.status.entries().values():
            if not isinstance(entry, dict):
                continue
            if entry.get("state") not in {"delivered_ui", "uncertain"}:
                continue
            raw = entry.get("updated_at")
            if not isinstance(raw, str):
                continue
            try:
                moment = datetime.fromisoformat(raw)
            except ValueError:
                continue
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=transport.now.tzinfo)
            if moment.date() == today:
                count += 1
        return count

    def _heartbeat(self) -> HeartbeatEngine:
        return HeartbeatEngine(
            self.root, at=self.at.isoformat() if self.at is not None else None
        )

    # -- composizione ------------------------------------------------------

    def compose(self, report: dict[str, Any], contact: Contact) -> str:
        situation = prompt_situation(report)
        try:
            text = self._generator(
                load_persona(self.root), situation, contact.display_name
            )
        except Exception as exc:
            if not self.allow_fallback:
                raise
            print(
                f"[autonomia] generazione non riuscita ({type(exc).__name__}): "
                "uso il messaggio di riserva.",
                file=sys.stderr,
                flush=True,
            )
            text = FALLBACK_MESSAGE.format(name=contact.display_name)
        text = clean_message(text)
        if not text:
            raise RuntimeError("Il testo del messaggio autonomo è vuoto.")
        return text

    # -- esecuzione --------------------------------------------------------

    def run_once(self) -> dict[str, Any]:
        result: dict[str, Any] = {"started_at": self._stamp()}
        try:
            heartbeat = self._heartbeat().apply()
            result["heartbeat"] = heartbeat.get("decision")
            result["life"] = heartbeat.get("life")
            gate = self._gate()
            if gate is not None:
                result["stopped"] = gate
                return result

            transport = self._transport()
            pending = transport.status_report()["counts"]["pending"]
            if pending:
                if self.dry_run:
                    result["stopped"] = "outbox_pending"
                    result["pending"] = pending
                    return result
                flushed = transport.run(limit=1)
                result["flush"] = flushed
                if flushed.get("uncertain"):
                    result["stopped"] = "outbox_uncertain"
                    return result
                pending = flushed.get("pending", 0)
                if pending:
                    result["stopped"] = "outbox_pending"
                    result["pending"] = pending
                    return result

            transport = self._transport()
            if self._attempts_today(transport) >= self.max_per_day:
                result["stopped"] = "daily_limit"
                return result
            gate = transport.status_report().get("gate")
            if gate is not None and not self.dry_run:
                result["stopped"] = gate
                return result

            status = DeliberationService(self.root, at=self.at).status()
            if status.get("active") is None:
                tick = DeliberationService(self.root, at=self.at).tick(seed=self.seed)
                result["tick"] = tick
                status = DeliberationService(self.root, at=self.at).status()
            if status.get("active") is None:
                result["stopped"] = "no_episode"
                return result

            episode = status["active"]
            contact = self._contact()
            report = SituationBuilder(self.root, at=self.at).build()
            text = self.compose(report, contact)
            decision = build_whatsapp_decision(
                episode=episode,
                user_id=contact.user_id,
                display_name=contact.display_name,
                text=text,
            )
            result["episode_id"] = episode.get("episode_id")
            if self.dry_run:
                result["planned"] = decision
                result["text"] = text
                return result

            outcome = DeliberationService(self.root, at=self.at).decide(decision)
            episode_outcome = (outcome.get("episode") or {}).get("outcome") or {}
            result["decision"] = outcome.get("decision")
            result["outcome"] = episode_outcome
            if episode_outcome.get("blocked") or not episode_outcome.get("applied"):
                result["stopped"] = "decision_blocked"
                return result

            send = self._transport().run(limit=1)
            result["outbox"] = send
            if send.get("uncertain"):
                result["stopped"] = "outbox_uncertain"
                return result
            if send.get("sent"):
                with suppress(Exception):
                    self._heartbeat().record_interaction(
                        messages=1,
                        channel="whatsapp",
                        user_id=contact.user_id,
                        summary="Messaggio autonomo inviato.",
                    )
                result["sent"] = send.get("sent")
            elif send.get("pending"):
                result["stopped"] = "outbox_pending"
            else:
                result["stopped"] = "outbox_no_delivery"
            return result
        except (AgentMemoryError, RuntimeError) as exc:
            result["error"] = str(exc)
            return result

    def run_forever(self, *, max_ticks: int | None = None) -> int:
        ticks = 0
        while True:
            result = self.run_once()
            print(json.dumps(result, ensure_ascii=False), flush=True)
            ticks += 1
            if result.get("stopped") == "outbox_uncertain":
                return 2
            if max_ticks is not None and ticks >= max_ticks:
                return 0
            time.sleep(self.interval_minutes * 60)

    def _stamp(self) -> str:
        moment = self.at or datetime.now().astimezone()
        return moment.replace(microsecond=0).isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Ciclo autonomo locale: battito, messaggio Ollama e trasporto outbox. "
            "Non è un tool MCP."
        )
    )
    parser.add_argument("--root", required=True, type=Path, help="Profilo da usare.")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help="Modello Ollama locale."
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_MINUTES,
        help="Minuti tra due giri in modalità --loop (default 30).",
    )
    parser.add_argument(
        "--max-per-day",
        type=int,
        default=DEFAULT_MAX_PER_DAY,
        help="Massimo messaggi autonomi al giorno (default 1).",
    )
    parser.add_argument("--user-id", help="Contatto autorizzato da usare.")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed di esplorazione per aprire un episodio nel primo giro.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Genera il messaggio e mostra la decisione senza accodarla.",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="Non usare il messaggio di riserva se Ollama non risponde.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Ripete il giro ogni --interval minuti.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Esegue un solo giro (predefinito senza --loop).",
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=None,
        help="Numero massimo di giri in --loop (utile per le prove).",
    )
    parser.add_argument(
        "--at",
        help="Timestamp ISO 8601 per una prova deterministica (solo --once).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    at = None
    if args.at:
        try:
            at = datetime.fromisoformat(args.at)
        except ValueError:
            parser.error("--at: timestamp ISO 8601 non valido")
    if args.loop and at is not None:
        parser.error("--at è supportato solo con --once")
    if args.loop and args.once:
        parser.error("--loop e --once sono alternativi")
    if args.loop and args.max_ticks is not None and args.max_ticks < 1:
        parser.error("--max-ticks deve essere almeno 1")
    try:
        loop = AutonomyLoop(
            args.root,
            model=args.model,
            interval_minutes=args.interval,
            max_per_day=args.max_per_day,
            seed=args.seed,
            user_id=args.user_id,
            dry_run=args.dry_run,
            allow_fallback=not args.no_fallback,
            at=at,
        )
    except (AgentMemoryError, RuntimeError) as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 1
    if args.loop:
        return loop.run_forever(max_ticks=args.max_ticks)
    result = loop.run_once()
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if result.get("stopped") == "outbox_uncertain":
        return 2
    if result.get("error"):
        return 1
    return 0


__all__ = [
    "AutonomyLoop",
    "DEFAULT_MAX_PER_DAY",
    "OllamaChat",
    "build_whatsapp_decision",
    "clean_message",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
