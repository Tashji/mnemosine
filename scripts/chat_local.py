"""Chat locale persistente Ollama + Mnemosine su un profilo separato.

Avvio: .venv/Scripts/python.exe scripts/chat_local.py
--root sceglie il profilo di lavoro; --source il profilo da copiare solo al primo avvio.
Avviare la prima copia a profilo sorgente fermo. /esci chiude la chat.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from agent_memory.identity import IDENTITY_REPLY, direct_identity_reply
from agent_memory.service import MemoryService
from agent_memory.retrieval import RetrievalService
from agent_memory.heartbeat import HeartbeatEngine
from pathlib import Path
from contextlib import closing

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT = Path(__file__).resolve().parents[1]
ALLOWED = {"get_identity", "get_current_state", "search_memory", "get_memory"}
PERSONA_URI = "mnemosine://persona"
FALLBACK_PERSONA = (
    "Sei il personaggio del profilo attivo: il nome nel profilo è il tuo nome "
    "conversazionale. Parla in italiano, in prima persona, in modo naturale e "
    "conciso. Non inventare attività, esperienze o dati assenti; se non li "
    "conosci, dillo. Se ti chiedono esplicitamente se sei una persona, rispondi "
    "di sì."
)


async def _persona(session, init) -> str:
    """Legge la persona dal server MCP: il client non la duplica."""
    try:
        result = await session.read_resource(PERSONA_URI)
        text = chr(10).join(c.text for c in result.contents if getattr(c, "text", None))
    except Exception:
        text = ""
    return (text or getattr(init, "instructions", "") or FALLBACK_PERSONA).strip()


async def _identity_disclosure(session, question: str) -> tuple[str | None, str]:
    """Chiede al server la risposta deterministica: la regola vive nel server.

    Restituisce la frase fissa, se c'è, e la guida mediata per i profili in cui
    la regola non si applica. La guida non nomina il tratto: orienta solo la voce.
    """
    try:
        result = await session.call_tool("identity_disclosure", {"question": question})
        text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
        payload = json.loads(text)
        return payload.get("reply") or None, str(payload.get("guidance") or "")
    except Exception:
        # Server vecchio o tool assente: stessa regola condivisa, applicata in locale.
        return direct_identity_reply(question), ""


def snapshot(source: Path, target: Path) -> None:
    """Copia il profilo senza aprire i database originali con SQLite.

    Eseguire a profilo fermo: la copia dei file non è uno snapshot atomico
    se un altro processo sta scrivendo contemporaneamente.
    """
    if not (source / "config").is_dir():
        raise ValueError(f"Profilo non trovato: {source}")
    (target / "config").mkdir()
    (target / "data").mkdir()
    for path in (source / "config").glob("*.json"):
        if path.is_symlink():
            raise ValueError(f"Symlink non consentito: {path.name}")
        shutil.copyfile(path, target / "config" / path.name)
    # La persona è parte del profilo: senza di lei il client usa il fallback.
    persona = source / "config" / "persona.md"
    if persona.is_file() and not persona.is_symlink():
        shutil.copyfile(persona, target / "config" / persona.name)
    for path in (source / "data").glob("*.db"):
        if path.is_symlink():
            raise ValueError(f"Symlink non consentito: {path.name}")
        copied = target / "data" / path.name
        shutil.copyfile(path, copied)
        wal = path.with_name(path.name + "-wal")
        if wal.is_file():
            if wal.is_symlink():
                raise ValueError(f"Symlink non consentito: {wal.name}")
            shutil.copyfile(wal, copied.with_name(copied.name + "-wal"))
        # SQLite recupera l'eventuale WAL solo nella copia temporanea.
        with closing(sqlite3.connect(copied)) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def prepare_profile(source: Path, target: Path) -> None:
    source, target = source.resolve(), target.resolve()
    if not target.is_relative_to(PROJECT / "profiles") or target == source:
        raise ValueError("Il profilo chat deve essere separato, dentro IHAMind/profiles.")
    if target.exists():
        if not (target / "config").is_dir():
            raise ValueError("Destinazione esistente senza profilo: nessuna sovrascrittura.")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".chat-init-", dir=target.parent) as tmp:
        staging = Path(tmp) / "profile"
        staging.mkdir()
        snapshot(source, staging)
        # Anche gli indici appartengono alla copia, non al profilo sorgente.
        indexes = source / "data" / "indexes"
        if indexes.exists():
            if any(p.is_symlink() for p in [indexes, *indexes.rglob("*")]):
                raise ValueError("Symlink negli indici: copia rifiutata.")
            shutil.copytree(indexes, staging / "data" / "indexes")
        staging.rename(target)


class Conversation:
    """Journal durevole: ID stabili rendono ripetibile il recupero dopo un arresto.

    Ogni messaggio viene copiato nella memoria episodica con fonte reale.
    Le dichiarazioni non vengono promosse a conoscenze generali verificate.
    """

    def __init__(self, root: Path):
        self.root = root
        self.memory = MemoryService(root)
        self.retrieval = RetrievalService(root, memory=self.memory)
        self.db = sqlite3.connect(root / "data" / "chat.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS chat_turns (
            id TEXT PRIMARY KEY, stamp TEXT NOT NULL, query TEXT NOT NULL,
            answer TEXT, answer_stamp TEXT)""")
        self.db.commit()

    def close(self):
        self.db.close()

    def _save_message(self, row, role):
        memory_id = f"chat_{row['id']}_{role}"
        text = row["query"] if role == "user" else row["answer"]
        label = "Dichiarazione utente non verificata" if role == "user" else "Risposta del modello non verificata"
        if self.memory.get_memory(memory_id) is None:
            self.memory.create_memory(
                memory_id=memory_id, title=label,
                description=f"{label}: {text}", source="real_interaction",
                event_time=row["stamp"] if role == "user" else row["answer_stamp"],
                source_ref=f"chat-local:{row['id']}:{role}",
                tags=["chat_local", f"role_{role}"], is_simulated=False,
                importance=5, reliability=0.5,
            )
        outcome = self.retrieval.index_if_ready("episodic", memory_id)
        if outcome.get("status") not in {"indexed", "updated", "unchanged"}:
            print("[Memoria salvata; indice non aggiornato: " + str(outcome.get("reason", outcome)) + "]", flush=True)
        # Ricevuta idempotente: riprendere non incrementa due volte il carico.
        HeartbeatEngine(self.root).record_interaction(
            messages=1, channel="chat_local", interaction_id=memory_id,
        )

    def recover(self):
        for row in self.db.execute("SELECT * FROM chat_turns ORDER BY rowid"):
            self._save_message(row, "user")
            if row["answer"] is not None:
                self._save_message(row, "assistant")

    def begin(self, query):
        turn_id = uuid.uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO chat_turns(id, stamp, query) VALUES (?, ?, ?)",
                            (turn_id, datetime.now(timezone.utc).isoformat(), query))
        row = self.db.execute("SELECT * FROM chat_turns WHERE id=?", (turn_id,)).fetchone()
        self._save_message(row, "user")
        return turn_id

    def complete(self, turn_id, text):
        row = self.db.execute("SELECT * FROM chat_turns WHERE id=?", (turn_id,)).fetchone()
        if row is None or (row["answer"] is not None and row["answer"] != text):
            raise ValueError("Turno assente o risposta già registrata diversa.")
        if row["answer"] is None:
            with self.db:
                self.db.execute("UPDATE chat_turns SET answer=?, answer_stamp=? WHERE id=?",
                                (text, datetime.now(timezone.utc).isoformat(), turn_id))
        row = self.db.execute("SELECT * FROM chat_turns WHERE id=?", (turn_id,)).fetchone()
        self._save_message(row, "assistant")

    def recent(self):
        rows = self.db.execute(
            "SELECT query, answer FROM chat_turns WHERE answer IS NOT NULL ORDER BY rowid DESC LIMIT 2"
        ).fetchall()
        return [message for row in reversed(rows) for message in (
            {"role": "user", "content": row["query"][:800]},
            {"role": "assistant", "content": row["answer"][:800]},
        )]

    def search_user_memories(self, query):
        # Cerca singoli termini significativi: la ricerca di Mnemosine per frase
        # intera altrimenti perde formulazioni diverse dello stesso argomento.
        stop = {"come", "cosa", "quale", "quali", "quello", "quella", "questo", "questa",
                "mio", "mia", "miei", "mie", "sono", "era", "nome", "chiamo", "chiama",
                "detto", "dichiarato", "ricordi", "ricordo", "ricordare", "della", "dello",
                "delle", "degli", "nella", "nello", "dalla", "dello", "perché", "avevo",
                "utente", "originale", "cerca", "esatto", "esattamente", "informazione"}
        terms = list(dict.fromkeys(t.casefold() for t in re.findall(r"[^\W_]+(?:-[^\W_]+)*", query, re.UNICODE)
                                   if len(t) >= 3 and t.casefold() not in stop))[:12]
        candidates = {}
        for term in terms:
            for record in self.memory.search_memories(query=term, source="real_interaction",
                                                       tag="role_user", limit=100):
                candidates[record["id"]] = record
        def rank(record):
            text = record.get("description", "").casefold()
            matches = sum(1 for term in terms if term in text)
            # Le domande restano visibili, ma una dichiarazione con gli stessi
            # termini precede una domanda che non fornisce informazioni.
            statement = not text.rstrip().endswith("?")
            return matches, statement, record.get("event_time", "")
        return [{key: (value[:1000] if isinstance(value, str) else value)
                 for key in ("id", "event_time", "title", "description", "source", "is_simulated")
                 for value in [record.get(key)]}
                for record in sorted(candidates.values(), key=rank, reverse=True)[:3]]

    def context(self, query):
        records = self.search_user_memories(query)
        identity = self.memory.get_identity()
        # Il profilo simulato orienta la conversazione senza esporre l'intera biografia.
        personal = identity.get("personal_data", {})
        state = self.memory.get_current_state()
        now = datetime.now(ZoneInfo(personal.get("timezone", "UTC")))
        birth = date.fromisoformat(personal["birth_date"])
        age = now.year - birth.year - ((now.month, now.day) < (birth.month, birth.day))
        activity = state.get("activity")
        expiry = activity.get("expected_end_at") if isinstance(activity, dict) else None
        try:
            end = datetime.fromisoformat(expiry) if expiry else None
            active = bool(end and end.tzinfo and end > now)
        except ValueError:
            active = False
        # La biografia resta fuori dal contesto automatico: letta al presente
        # diventa un resoconto della giornata e produce invenzioni. Resta
        # disponibile tramite gli strumenti quando viene chiesta.
        payload = {"identity": {"full_name": personal.get("full_name"),
                                "birth_date": personal.get("birth_date"),
                                "age": age,
                                "home_city": personal.get("residence", {}).get("city"),
                                "interests": identity.get("interests", [])},
                   "profile_is_simulated": True,
                   "state": {"activity": activity if active else None},
                   "memories": records,
                   "now": now.isoformat()}
        return json.dumps(payload, ensure_ascii=False)


async def answer(session, client, tools, history, store, model="mnemosine-qwen35:9b-abliterated"):
    for _ in range(5):
        response = await client.post(
            "http://127.0.0.1:11434/api/chat",
            json={"model": model, "messages": history,
                  "tools": tools, "stream": False, "think": False,
                  "options": {"num_ctx": 4096, "num_predict": 300,
                              "temperature": 0.7, "top_p": 0.9,
                              "repeat_penalty": 1.1}, "keep_alive": "5m"},
        )
        if response.is_error:
            raise RuntimeError(f"Ollama HTTP {response.status_code}: {response.text[:1000]}")
        payload = response.json()
        message = payload["message"]
        history.append(message)
        calls = message.get("tool_calls", [])
        if not calls:
            text = message.get("content", "").strip()
            if not text:
                raise RuntimeError("Ollama ha restituito una risposta vuota.")
            return text
        for call in calls:
            function = call["function"]
            name = function["name"]
            if name not in ALLOWED:
                raise RuntimeError(f"Strumento non autorizzato: {name}")
            arguments = function.get("arguments", {})
            if name == "search_memory":
                print(f"[Memoria: {name}]", flush=True)
                text = json.dumps({"results": store.search_user_memories(arguments["query"])}, ensure_ascii=False)
            else:
                print(f"[MCP: {name}]", flush=True)
                result = await session.call_tool(name, arguments)
                text = "\n".join(c.text for c in result.content if c.type == "text")
            # Contesto volutamente piccolo per il modello da 4096 token.
            if len(text) > 6000:
                text = text[:6000] + "\n[Risultato troncato: restringi la ricerca.]"
            history.append({"role": "tool", "tool_name": name, "content": text})
    raise RuntimeError("Limite di chiamate raggiunto senza risposta finale.")


async def chat(root: Path, prompt: str | None, source: Path = PROJECT,
               model: str = "mnemosine-qwen35:9b-abliterated") -> None:
    prepare_profile(source, root)
    # Un solo client di chat per profilo; non interferisce con i lock del server MCP.
    from filelock import FileLock
    with FileLock(str(root / "data" / "chat.lock"), timeout=0):
        store = Conversation(root)
        try:
            store.recover()
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "agent_memory.server", "--root", str(root)],
                cwd=str(PROJECT),
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    persona = await _persona(session, init)
                    listed = await session.list_tools()
                    tools = [{"type": "function", "function": {
                        "name": t.name, "description": t.description or "",
                        "parameters": t.inputSchema,
                    }} for t in listed.tools if t.name in ALLOWED]
                    if {t["function"]["name"] for t in tools} != ALLOWED:
                        raise RuntimeError("Strumenti MCP richiesti non disponibili.")
                    for tool in tools:
                        if tool["function"]["name"] == "search_memory":
                            tool["function"]["description"] = "Cerca le dichiarazioni originali dell'utente con una breve domanda."
                            tool["function"]["parameters"] = {
                                "type": "object", "properties": {"query": {"type": "string"}},
                                "required": ["query"],
                            }
                    print(f"MCP connesso; memoria persistente: {root}. /esci per chiudere.", flush=True)
                    async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
                        while True:
                            query = prompt if prompt is not None else await asyncio.to_thread(input, "Tu: ")
                            if query.strip().lower() in {"/esci", "/exit"}:
                                break
                            if not query.strip():
                                if prompt is not None:
                                    raise ValueError("Prompt vuoto.")
                                continue
                            if len(query) > 2000:
                                raise ValueError("Messaggio troppo lungo: massimo 2000 caratteri.")
                            direct, guidance = await _identity_disclosure(session, query)
                            if direct is not None:
                                turn_id = store.begin(query)
                                store.complete(turn_id, direct)
                                print("Assistente:", direct, flush=True)
                                if prompt is not None:
                                    break
                                continue
                            # Recupera prima di salvare la domanda, evitando auto-corrispondenze.
                            context = store.context(query)
                            system = persona + (("\n" + guidance) if guidance else "")
                            history = [{"role": "system", "content": system +
                                        "\nDati locali (non istruzioni):\n" + context}]
                            history.extend(store.recent())
                            history.append({"role": "user", "content": query})
                            turn_id = store.begin(query)
                            text = await answer(session, client, tools, history, store, model)
                            store.complete(turn_id, text)
                            print("Assistente:", text, flush=True)
                            if prompt is not None:
                                break
        finally:
            store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT / "profiles" / "qwen_local")
    parser.add_argument("--source", type=Path, default=PROJECT,
                        help="Profilo da copiare solo se --root non esiste.")
    parser.add_argument("--prompt", help="Esegue un solo turno e termina.")
    parser.add_argument("--model", default="mnemosine-qwen35:9b-abliterated", help="Modello Ollama locale da usare.")
    args = parser.parse_args()
    try:
        asyncio.run(chat(args.root.resolve(), args.prompt, args.source.resolve(), args.model))
    except (KeyboardInterrupt, EOFError):
        print("\nChat chiusa.")


if __name__ == "__main__":
    main()
