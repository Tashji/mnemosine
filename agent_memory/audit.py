"""Registro di audit locale, append-only e con redazione.

Il registro conserva *metadati* su ciò che il server ha fatto: nome del tool,
esito, durata e chiavi degli argomenti. I valori degli argomenti non vengono
scritti, perché possono contenere messaggi privati, numeri di telefono, token o
contenuti dell'ombra. La scrittura è best-effort: un errore del filesystem non
deve interrompere un tool o un backup.

Il file predefinito è ``data/logs/audit.jsonl`` dentro il profilo. Viene
ruotato localmente e non è mai incluso nei backup: è un registro operativo del
profilo corrente, non un dato da ripristinare.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_ROTATED = 3
MAX_STRING_CHARS = 300
MAX_DICT_ITEMS = 50
MAX_LIST_ITEMS = 20
MAX_EVENT_CHARS = 80
MAX_KEYS = 30

_SENSITIVE_EXACT = frozenset(
    {
        "address",
        "api_key",
        "apikey",
        "authorization",
        "body",
        "content",
        "cookie",
        "display_name",
        "email",
        "key",
        "message",
        "messages",
        "note",
        "notes",
        "password",
        "passwd",
        "phone",
        "prompt",
        "recipient",
        "recipient_id",
        "refresh_token",
        "secret",
        "session",
        "telefono",
        "text",
        "token",
        "user_id",
    }
)
_SENSITIVE_PARTS = (
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "passwd",
    "private",
    "recipient",
    "secret",
    "session",
    "token",
)
_AUTH_RE = re.compile(
    r"(?i)(?:"
    r"bearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|(?:sk|pk|ghp|gho|ghs|github_pat|xox[abp]|AIza)[-_A-Za-z0-9]{8,}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"
    r")"
)
_KV_SECRET_RE = re.compile(
    r"(?i)\b(?:password|passwd|token|secret|api[_-]?key)\s*[:=]\s*\S+"
)
_URL_QUERY_RE = re.compile(r"(https?://[^?\s#]+)\?[^\s#]*", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\w)\+\d[\d\s().-]{7,}\d(?!\w)")


def _normalize_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).casefold())


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalize_key(key)
    if normalized in _SENSITIVE_EXACT:
        return True
    return any(part in normalized for part in _SENSITIVE_PARTS)


def _mask_text(value: str) -> str:
    """Rimuove token e query string note, poi limita la lunghezza."""

    text = _AUTH_RE.sub("<redacted>", value)
    text = _KV_SECRET_RE.sub("<redacted>", text)
    text = _URL_QUERY_RE.sub(r"\1?<redacted>", text)
    text = _PHONE_RE.sub("<redacted>", text)
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = " ".join(text.split())
    if len(text) > MAX_STRING_CHARS:
        text = text[:MAX_STRING_CHARS] + "…"
    return text


def redact(value: Any, *, key: Any = None, depth: int = 0) -> Any:
    """Restituisce una copia innocua di ``value`` per il registro.

    Le chiavi sensibili vengono sostituite integralmente, così un dizionario
    annidato non può rivelare per errore un numero o un testo privato.
    """

    if _is_sensitive_key(key):
        return "<redacted>"
    if depth > 5:
        return "<annidato>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _mask_text(value)
    if isinstance(value, (bytes, bytearray)):
        return f"<bytes:{len(value)}>"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (item_key, item_value) in enumerate(value.items()):
            if index >= MAX_DICT_ITEMS:
                result["_other"] = "<troncato>"
                break
            safe_key = _mask_text(str(item_key))[:80]
            result[safe_key] = redact(item_value, key=item_key, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        result = [
            redact(item, depth=depth + 1) for item in items[:MAX_LIST_ITEMS]
        ]
        if len(items) > MAX_LIST_ITEMS:
            result.append(f"<altri:{len(items) - MAX_LIST_ITEMS}>")
        return result
    return _mask_text(str(value))


def summarize_arguments(arguments: Any) -> dict[str, Any]:
    """Riassume gli argomenti senza copiarne i valori."""

    if isinstance(arguments, dict):
        keys = sorted(str(key)[:60] for key in arguments)
        try:
            size = len(
                json.dumps(arguments, ensure_ascii=False, default=str).encode(
                    "utf-8"
                )
            )
        except (TypeError, ValueError):
            size = None
        summary: dict[str, Any] = {
            "arg_keys": keys[:MAX_KEYS],
            "arg_key_count": len(arguments),
            "arg_bytes": size,
        }
        if len(keys) > MAX_KEYS:
            summary["arg_keys_truncated"] = True
        return summary
    return {"arg_type": type(arguments).__name__}


class AuditLog:
    """Scrittore e lettore del registro JSONL di un profilo."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        path: str | Path | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        rotated: int = DEFAULT_ROTATED,
    ):
        self.root = Path(root).expanduser().resolve() if root else None
        self.path = (
            Path(path).expanduser().resolve()
            if path is not None
            else (self.root or Path(".")) / "data" / "logs" / "audit.jsonl"
        )
        if max_bytes <= 0:
            raise ValueError("max_bytes deve essere positivo.")
        if rotated < 1:
            raise ValueError("rotated deve essere almeno 1.")
        self.max_bytes = max_bytes
        self.rotated = rotated
        self._lock = threading.Lock()

    @property
    def log_dir(self) -> Path:
        return self.path.parent

    def _now(self) -> str:
        return datetime.now(UTC).replace(microsecond=0).isoformat()

    def _rotated_path(self, index: int) -> Path:
        return self.path.with_name(f"{self.path.stem}.{index}{self.path.suffix}")

    def _rotate(self) -> None:
        for index in range(self.rotated, 0, -1):
            destination = self._rotated_path(index)
            source = (
                self.path if index == 1 else self._rotated_path(index - 1)
            )
            try:
                if destination.exists():
                    destination.unlink()
                if source.exists():
                    os.replace(source, destination)
            except OSError:
                # La rotazione è manutenzione: se fallisce, il record corrente
                # può ancora essere scritto senza perdere il tool chiamante.
                return

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        try:
            current = self.path.stat().st_size
        except OSError:
            return
        if current + incoming_bytes > self.max_bytes:
            self._rotate()

    def record(self, event: str, **fields: Any) -> bool:
        """Aggiunge un evento. Restituisce ``False`` se il log non è scrivibile."""

        safe_event = _mask_text(str(event))[:MAX_EVENT_CHARS]
        payload: dict[str, Any] = {"at": self._now(), "event": safe_event}
        for key, value in fields.items():
            if value is None:
                continue
            safe_key = _mask_text(str(key))[:80]
            payload[safe_key] = redact(value, key=key)
        try:
            line = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ) + "\n"
        except (TypeError, ValueError):
            return False
        try:
            with self._lock:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed(len(line.encode("utf-8")))
                with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line)
            return True
        except OSError:
            return False

    def record_tool_call(
        self,
        tool: str,
        arguments: Any,
        *,
        outcome: str,
        duration_ms: float,
        error_type: str | None = None,
    ) -> bool:
        fields: dict[str, Any] = {
            "tool": str(tool)[:80],
            "outcome": outcome,
            "duration_ms": round(max(0.0, float(duration_ms)), 1),
        }
        if error_type:
            fields["error_type"] = str(error_type)[:120]
        fields.update(summarize_arguments(arguments))
        return self.record("tool_call", **fields)

    def status(self) -> dict[str, Any]:
        entries = 0
        last_at: str | None = None
        if self.path.is_file():
            try:
                with self.path.open("rb") as handle:
                    for raw in handle:
                        if not raw.strip():
                            continue
                        entries += 1
                        try:
                            item = json.loads(raw)
                        except (UnicodeDecodeError, ValueError):
                            continue
                        if isinstance(item, dict) and isinstance(
                            item.get("at"), str
                        ):
                            last_at = item["at"]
            except OSError:
                pass
        rotated = [
            self._rotated_path(index).name
            for index in range(1, self.rotated + 1)
            if self._rotated_path(index).is_file()
        ]
        return {
            "path": str(self.path),
            "exists": self.path.is_file(),
            "entries": entries,
            "bytes": self.path.stat().st_size if self.path.is_file() else 0,
            "last_at": last_at,
            "rotated": rotated,
        }

    def tail(self, limit: int = 20) -> list[dict[str, Any]]:
        if limit <= 0 or limit > 1000:
            raise ValueError("limit deve essere compreso tra 1 e 1000.")
        if not self.path.is_file():
            return []
        try:
            with self.path.open("rb") as handle:
                lines = handle.readlines()[-limit:]
        except OSError:
            return []
        result: list[dict[str, Any]] = []
        for raw in lines:
            try:
                item = json.loads(raw)
            except (UnicodeDecodeError, ValueError):
                continue
            if isinstance(item, dict):
                result.append(item)
        return result


__all__ = [
    "AuditLog",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_ROTATED",
    "redact",
    "summarize_arguments",
]
