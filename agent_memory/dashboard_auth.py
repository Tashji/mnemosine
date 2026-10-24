"""Autenticazione locale per la dashboard amministrativa.

La dashboard non usa un provider esterno: la password viene configurata
tramite variabile d'ambiente o file, oppure generata all'avvio quando il
server viene esposto sulla rete. Le sessioni restano in memoria e decadono
automaticamente; un riavvio del server le invalida tutte.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from .errors import ConfigError, ValidationError

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 1024
PASSWORD_ITERATIONS = 200_000
DEFAULT_SESSION_TTL = 12 * 60 * 60
LOGIN_TOKEN_TTL = 10 * 60
MAX_LOGIN_ATTEMPTS = 8
LOGIN_WINDOW_SECONDS = 5 * 60
SESSION_COOKIE = "agent_memory_dashboard_session"


class DashboardAuth:
    """Verifica password, sessioni e tentativi di accesso."""

    def __init__(
        self,
        password: str,
        *,
        session_ttl: int = DEFAULT_SESSION_TTL,
        max_login_attempts: int = MAX_LOGIN_ATTEMPTS,
        now: Callable[[], float] = time.monotonic,
    ):
        if not isinstance(password, str) or not password:
            raise ValidationError("La password della dashboard non può essere vuota.")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValidationError(
                f"La password della dashboard deve contenere almeno "
                f"{MIN_PASSWORD_LENGTH} caratteri."
            )
        if len(password) > MAX_PASSWORD_LENGTH:
            raise ValidationError(
                f"La password della dashboard non può superare "
                f"{MAX_PASSWORD_LENGTH} caratteri."
            )
        if session_ttl <= 0:
            raise ValidationError("session_ttl deve essere positivo.")
        if max_login_attempts <= 0:
            raise ValidationError("max_login_attempts deve essere positivo.")

        self._salt = os.urandom(16)
        self._digest = self._derive(password)
        self._session_ttl = int(session_ttl)
        self._max_login_attempts = int(max_login_attempts)
        self._now = now
        self._lock = threading.Lock()
        self._sessions: dict[str, float] = {}
        self._login_tokens: dict[str, float] = {}
        self._failures: dict[str, list[float]] = {}

    def __repr__(self) -> str:
        return "<DashboardAuth enabled=True>"

    @property
    def session_ttl(self) -> int:
        return self._session_ttl

    def _derive(self, password: str) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            self._salt,
            PASSWORD_ITERATIONS,
        )

    # -- password -----------------------------------------------------------

    def verify_password(self, candidate: str) -> bool:
        if not isinstance(candidate, str) or not candidate:
            return False
        if len(candidate) > MAX_PASSWORD_LENGTH:
            return False
        return hmac.compare_digest(self._digest, self._derive(candidate))

    # -- sessioni -----------------------------------------------------------

    def issue_session(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge_locked()
            self._sessions[token] = self._now() + self._session_ttl
        return token

    def validate_session(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            expires_at = self._sessions.get(token)
            if expires_at is None:
                return False
            if expires_at <= self._now():
                self._sessions.pop(token, None)
                return False
            return True

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    # -- token del modulo di accesso ---------------------------------------

    def create_login_token(self) -> str:
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._purge_locked()
            self._login_tokens[token] = self._now() + LOGIN_TOKEN_TTL
        return token

    def consume_login_token(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            expires_at = self._login_tokens.pop(token, None)
            return expires_at is not None and expires_at > self._now()

    # -- limite dei tentativi ----------------------------------------------

    def allow_login(self, client: str) -> bool:
        with self._lock:
            self._purge_locked()
            return len(self._failures.get(client, [])) < self._max_login_attempts

    def register_failure(self, client: str) -> None:
        now = self._now()
        with self._lock:
            attempts = self._failures.setdefault(client, [])
            attempts.append(now)
            cutoff = now - LOGIN_WINDOW_SECONDS
            self._failures[client] = [
                timestamp for timestamp in attempts if timestamp > cutoff
            ]

    def reset_failures(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)

    def _purge_locked(self) -> None:
        now = self._now()
        self._sessions = {
            token: expires_at
            for token, expires_at in self._sessions.items()
            if expires_at > now
        }
        self._login_tokens = {
            token: expires_at
            for token, expires_at in self._login_tokens.items()
            if expires_at > now
        }
        for client, attempts in list(self._failures.items()):
            cutoff = now - LOGIN_WINDOW_SECONDS
            remaining = [
                timestamp for timestamp in attempts if timestamp > cutoff
            ]
            if remaining:
                self._failures[client] = remaining
            else:
                self._failures.pop(client, None)


def _read_password_file(path: str | Path) -> str:
    password_path = Path(path).expanduser()
    try:
        password = password_path.read_text(encoding="utf-8").rstrip("\r\n")
        mode = password_path.stat().st_mode
    except OSError as exc:
        raise ConfigError(
            f"Impossibile leggere il file della password: {exc}"
        ) from exc
    if not password:
        raise ConfigError("Il file della password è vuoto.")
    if os.name == "posix" and mode & 0o077:
        raise ConfigError(
            "Il file della password è accessibile ad altri utenti. "
            "Esegui `chmod 600` sul file."
        )
    return password


def resolve_auth(
    *,
    require_auth: bool,
    password_file: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[DashboardAuth | None, str | None]:
    """Determina la configurazione di autenticazione.

    Restituisce ``(auth, password_generata)``. La password generata è
    restituita una sola volta al chiamante per essere mostrata sul terminale.
    """

    environment = os.environ if environ is None else environ
    environment_password = environment.get("AGENT_MEMORY_DASHBOARD_PASSWORD")
    if environment_password is not None and password_file is not None:
        raise ConfigError(
            "Configura la password con una sola sorgente: variabile "
            "AGENT_MEMORY_DASHBOARD_PASSWORD oppure --password-file."
        )
    file_password = (
        _read_password_file(password_file) if password_file is not None else None
    )

    if environment_password is not None:
        if environment_password == "":
            raise ConfigError(
                "AGENT_MEMORY_DASHBOARD_PASSWORD è impostata ma vuota."
            )
        return DashboardAuth(environment_password), None
    if file_password is not None:
        return DashboardAuth(file_password), None
    if require_auth:
        generated = secrets.token_urlsafe(18)
        return DashboardAuth(generated), generated
    return None, None
