"""Dashboard amministrativa locale, di sola lettura.

Il modulo espone una piccola applicazione HTTP su loopback che legge i
database e i file di configurazione già usati dai servizi. Non modifica
ricordi, conoscenze, tratti o storico: le uniche operazioni sono letture.

Uso:
    python -m agent_memory.dashboard
    python -m agent_memory.dashboard --port 8765 --open

La dashboard contiene anche i tratti dell'ombra, quindi per impostazione
predefinita ascolta solo su 127.0.0.1 e non deve essere pubblicata su Internet.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import traceback
import webbrowser
from collections.abc import Iterable
from datetime import date, datetime
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .dashboard_auth import SESSION_COOKIE, DashboardAuth, resolve_auth
from .dashboard_tls import DashboardTLS, ensure_dashboard_tls
from .db import decode_memory
from .errors import AgentMemoryError, NotFoundError, ValidationError
from .evolution import EvolutionService
from .semantic import KNOWLEDGE_TYPES, SEMANTIC_STATUSES, SemanticService
from .service import MEMORY_SOURCES, MemoryService, ShadowService

ASSETS_DIR = Path(__file__).resolve().parent / "dashboard_assets"
ACCESS_LEVELS = {"conscious", "latent", "repressed"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
MAX_TIMELINE_ROWS = 500
MAX_SEMANTIC_ROWS = 100
MAX_FORM_BYTES = 4096
LOGIN_PATH = "/login"
LOGOUT_PATH = "/logout"
SESSION_PATH = "/api/session"


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def _origin_key(value: str) -> tuple[str, str, int]:
    """Normalizza un'origine in (schema, host, porta)."""

    try:
        parsed = urlparse(value.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "attesa un'origine nella forma http(s)://host[:porta]"
        )
    return parsed.scheme, host.lower(), port or _default_port(parsed.scheme)


def _origin_matches_authority(origin: Any, authority: str | None) -> bool:
    if not authority:
        return False
    try:
        candidate = urlparse(f"//{authority.strip()}")
        origin_host = origin.hostname
        candidate_host = candidate.hostname
        origin_port = origin.port
        candidate_port = candidate.port
    except ValueError:
        return False
    if not candidate_host or not origin_host:
        return False
    if origin_host.lower() != candidate_host.lower():
        return False
    expected_port = origin_port or _default_port(origin.scheme)
    actual_port = candidate_port or _default_port(origin.scheme)
    return expected_port == actual_port


def _same_origin(
    origin: str | None,
    host: str | None,
    forwarded_host: str | None = None,
    trusted_origins: Iterable[str] = (),
    allow_null_origin: bool = False,
) -> bool:
    """Verifica l'Origin anche dietro un reverse proxy che riscrive Host."""

    if not origin:
        return True
    if origin.strip().lower() == "null":
        return allow_null_origin
    try:
        parsed = urlparse(origin)
        origin_host = parsed.hostname
        origin_key = _origin_key(origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not origin_host:
        return False
    authorities: list[str] = []
    if forwarded_host:
        authorities.extend(
            item.strip() for item in forwarded_host.split(",") if item.strip()
        )
    if host:
        authorities.append(host)
    if any(_origin_matches_authority(parsed, item) for item in authorities):
        return True
    for trusted in trusted_origins:
        try:
            if _origin_key(trusted) == origin_key:
                return True
        except ValueError:
            continue
    return False


def _like_pattern(value: str) -> str:
    """Crea un pattern LIKE letterale, con ESCAPE '\\'."""

    escaped = (
        value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


def _as_int(value: str | None, default: int, field: str) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field}: atteso un numero intero.") from exc


def _age_on(birth_date: str, today: date) -> int:
    born = date.fromisoformat(birth_date)
    return today.year - born.year - (
        (today.month, today.day) < (born.month, born.day)
    )


def _loads_snapshot(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        data = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


class DashboardData:
    """Letture aggregate per l'interfaccia amministrativa."""

    def __init__(self, root: str | Path | None = None):
        self.memory = MemoryService(root)
        self.semantic = SemanticService(root)
        self.shadow = ShadowService(root)
        self.evolution_service = EvolutionService(root)
        self.root = self.memory.root

    # -- panoramica ---------------------------------------------------------

    def overview(self) -> dict[str, Any]:
        identity = self.memory.get_identity()
        personal = identity.get("personal_data", {})
        personality = identity.get("personality", {})
        state = self.memory.get_current_state()

        with self.memory.db.timeline() as conn:
            access_rows = conn.execute(
                "SELECT access_level, COUNT(*) AS total "
                "FROM episodic_memories GROUP BY access_level"
            ).fetchall()
            timeline_total = conn.execute(
                "SELECT COUNT(*) FROM episodic_memories"
            ).fetchone()[0]
            last_updated = conn.execute(
                "SELECT MAX(updated_at) FROM episodic_memories"
            ).fetchone()[0]
            memory_cascades = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT cascade_id FROM memory_revisions "
                    "WHERE cascade_id IS NOT NULL"
                ).fetchall()
            }

        with self.semantic.db.semantic() as conn:
            status_rows = conn.execute(
                "SELECT status, COUNT(*) AS total "
                "FROM semantic_items GROUP BY status"
            ).fetchall()
            semantic_total = conn.execute(
                "SELECT COUNT(*) FROM semantic_items"
            ).fetchone()[0]
            semantic_cascades = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT cascade_id FROM semantic_revisions "
                    "WHERE cascade_id IS NOT NULL"
                ).fetchall()
            }

        with self.shadow.db.shadow() as conn:
            shadow_entries = conn.execute(
                "SELECT COUNT(*) FROM shadow_entries"
            ).fetchone()[0]

        access_counts = {
            row["access_level"]: int(row["total"]) for row in access_rows
        }
        status_counts = {
            row["status"]: int(row["total"]) for row in status_rows
        }
        history = self.evolution_service.history().get("changes", [])
        recent_memories = self.timeline(limit=5)["memories"]

        return {
            "generated_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "identity": {
                "identity_id": identity.get("identity_id"),
                "full_name": personal.get("full_name"),
                "first_name": personal.get("first_name"),
                "age": _age_on(personal["birth_date"], date.today())
                if personal.get("birth_date")
                else None,
                "birth_date": personal.get("birth_date"),
                "birth_place": personal.get("birth_place", {}),
                "residence": personal.get("residence", {}),
                "timezone": personal.get("timezone"),
            },
            "state": {
                "operational_status": state.get("operational_status"),
                "clock": state.get("clock", {}),
                "activity": state.get("activity", {}),
                "emotion": state.get("emotion", {}),
                "mental_load": state.get("mental_load"),
                "mental_load_thresholds": state.get(
                    "mental_load_thresholds", {}
                ),
                "routine": state.get("routine", {}),
            },
            "counts": {
                "memories": int(timeline_total),
                "conscious": access_counts.get("conscious", 0),
                "latent": access_counts.get("latent", 0),
                "repressed": access_counts.get("repressed", 0),
                "semantic_items": int(semantic_total),
                "active_semantic": status_counts.get("active", 0),
                "shadow_traits": len(self.shadow.list_traits()),
                "shadow_entries": int(shadow_entries),
                "identity_traits": len(personality.get("traits", {})),
                "cascades": len(memory_cascades | semantic_cascades),
                "history_changes": len(history),
            },
            "last_memory_update": last_updated,
            "recent_memories": recent_memories,
            "recent_history": sorted(
                history,
                key=lambda change: str(change.get("changed_at", "")),
                reverse=True,
            )[:5],
        }

    # -- timeline -----------------------------------------------------------

    def timeline(
        self,
        *,
        query: str | None = None,
        source: str | None = None,
        access: str | None = None,
        include_hidden: bool = False,
        limit: int = 200,
    ) -> dict[str, Any]:
        if source is not None and source not in MEMORY_SOURCES:
            raise ValidationError(f"source: valore non valido '{source}'.")
        if access is not None and access not in ACCESS_LEVELS:
            raise ValidationError(f"access: valore non valido '{access}'.")
        limit = max(1, min(int(limit), MAX_TIMELINE_ROWS))

        clauses: list[str] = []
        params: list[Any] = []
        if access is not None:
            clauses.append("m.access_level = ?")
            params.append(access)
        elif not include_hidden:
            clauses.append("m.access_level = 'conscious'")
        if source is not None:
            clauses.append("m.source = ?")
            params.append(source)
        if query:
            pattern = _like_pattern(query.strip())
            clauses.append(
                "(m.title LIKE ? ESCAPE '\\' OR m.description LIKE ? ESCAPE '\\' "
                "OR m.place LIKE ? ESCAPE '\\' OR m.tags_json LIKE ? ESCAPE '\\' "
                "OR m.people_json LIKE ? ESCAPE '\\' OR m.source_ref LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern] * 6)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT m.*, "
            "(SELECT COUNT(*) FROM memory_revisions r "
            " WHERE r.memory_id = m.id) AS revision_count, "
            "(SELECT MAX(r.changed_at) FROM memory_revisions r "
            " WHERE r.memory_id = m.id) AS last_revision_at "
            f"FROM episodic_memories m {where} "
            "ORDER BY m.event_time DESC, m.id DESC LIMIT ?"
        )
        params.append(limit)
        with self.memory.db.timeline() as conn:
            rows = conn.execute(sql, params).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) FROM episodic_memories"
            ).fetchone()[0]
            source_rows = conn.execute(
                "SELECT DISTINCT source FROM episodic_memories ORDER BY source"
            ).fetchall()
            access_rows = conn.execute(
                "SELECT access_level, COUNT(*) AS total "
                "FROM episodic_memories GROUP BY access_level"
            ).fetchall()

        memories = [decode_memory(row) for row in rows]
        return {
            "memories": memories,
            "count": len(memories),
            "total": int(total),
            "include_hidden": include_hidden,
            "sources": [row["source"] for row in source_rows],
            "access_counts": {
                row["access_level"]: int(row["total"])
                for row in access_rows
            },
        }

    def memory_detail(self, memory_id: str) -> dict[str, Any]:
        if not memory_id:
            raise ValidationError("memory_id mancante.")
        with self.memory.db.timeline() as conn:
            row = conn.execute(
                "SELECT m.*, "
                "(SELECT COUNT(*) FROM memory_revisions r "
                " WHERE r.memory_id = m.id) AS revision_count "
                "FROM episodic_memories m WHERE m.id = ?",
                (memory_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Ricordo non trovato: {memory_id}.")
            revision_rows = conn.execute(
                "SELECT * FROM memory_revisions WHERE memory_id = ? "
                "ORDER BY revision_number DESC, id DESC",
                (memory_id,),
            ).fetchall()
            link_rows = conn.execute(
                "SELECT *, CASE WHEN from_memory_id = ? THEN 'out' "
                "ELSE 'in' END AS direction, "
                "CASE WHEN from_memory_id = ? THEN to_memory_id "
                "ELSE from_memory_id END AS other_id "
                "FROM memory_links WHERE from_memory_id = ? OR to_memory_id = ? "
                "ORDER BY strength DESC, id DESC",
                (memory_id, memory_id, memory_id, memory_id),
            ).fetchall()

        revisions = []
        for revision in revision_rows:
            item = dict(revision)
            item["previous_snapshot"] = _loads_snapshot(
                item.pop("previous_snapshot_json", None)
            )
            item["new_snapshot"] = _loads_snapshot(
                item.pop("new_snapshot_json", None)
            )
            revisions.append(item)

        with self.semantic.db.semantic() as conn:
            semantic_rows = conn.execute(
                "SELECT l.*, s.title, s.knowledge_type, s.status "
                "FROM semantic_episodic_links l "
                "JOIN semantic_items s ON s.id = l.semantic_id "
                "WHERE l.memory_id = ? ORDER BY l.strength DESC, l.id DESC",
                (memory_id,),
            ).fetchall()

        return {
            "memory": decode_memory(row),
            "revisions": revisions,
            "links": [dict(link) for link in link_rows],
            "semantic_links": [dict(link) for link in semantic_rows],
        }

    # -- tratti -------------------------------------------------------------

    def traits(self) -> dict[str, Any]:
        identity = self.memory.get_identity()
        personality = identity.get("personality", {})
        scale = personality.get("scale", {})
        scale_min = int(scale.get("min", 1))
        scale_max = int(scale.get("max", 10))
        span = max(1, scale_max - scale_min)

        identity_traits = []
        for trait_id, trait in personality.get("traits", {}).items():
            value = float(trait.get("value", scale_min))
            identity_traits.append(
                {
                    "id": trait_id,
                    "value": value,
                    "left": trait.get("left"),
                    "right": trait.get("right"),
                    "description": trait.get("description"),
                    "position": round((value - scale_min) / span * 100, 1),
                }
            )

        shadow_doc = self.shadow.get_shadow_traits()
        shadow_traits = []
        for trait in shadow_doc.get("traits", []):
            shadow_traits.append(
                {
                    "id": trait.get("id"),
                    "name": trait.get("name"),
                    "description": trait.get("description"),
                    "origin": trait.get("origin"),
                    "intensity": trait.get("intensity"),
                    "conscious_awareness": trait.get("conscious_awareness"),
                    "triggers": trait.get("triggers", []),
                    "defense_mechanisms": trait.get("defense_mechanisms", []),
                    "behavioral_rules": trait.get("behavioral_rules", []),
                    "linked_memory_ids": trait.get("linked_memory_ids", []),
                    "safety": trait.get("safety", {}),
                }
            )

        history = self.evolution_service.history().get("changes", [])
        return {
            "identity": {
                "identity_id": identity.get("identity_id"),
                "full_name": identity.get("personal_data", {}).get("full_name"),
            },
            "model": personality.get("model"),
            "scale": {
                "min": scale_min,
                "max": scale_max,
                "midpoint": scale.get("midpoint"),
                "semantics": scale.get("semantics"),
            },
            "traits": identity_traits,
            "shadow": {
                "access_policy": shadow_doc.get("access_policy", {}),
                "traits": shadow_traits,
                "internal_conflicts": shadow_doc.get("internal_conflicts", []),
            },
            "history_count": len(history),
        }

    # -- conoscenze ---------------------------------------------------------

    def semantic_items(
        self,
        *,
        query: str | None = None,
        knowledge_type: str | None = None,
        status: str | None = "active",
    ) -> dict[str, Any]:
        if knowledge_type is not None and knowledge_type not in KNOWLEDGE_TYPES:
            raise ValidationError(
                f"knowledge_type: valore non valido '{knowledge_type}'."
            )
        if status in {"all", ""}:
            status = None
        if status is not None and status not in SEMANTIC_STATUSES:
            raise ValidationError(f"status: valore non valido '{status}'.")

        items = self.semantic.search_items(
            query=query or None,
            knowledge_type=knowledge_type,
            status=status,
            limit=MAX_SEMANTIC_ROWS,
        )
        with self.semantic.db.semantic() as conn:
            status_rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM semantic_items "
                "GROUP BY status"
            ).fetchall()
            type_rows = conn.execute(
                "SELECT knowledge_type, COUNT(*) AS total FROM semantic_items "
                "GROUP BY knowledge_type"
            ).fetchall()
            category_rows = conn.execute(
                "SELECT DISTINCT category FROM semantic_items ORDER BY category"
            ).fetchall()

        return {
            "items": items,
            "count": len(items),
            "status_counts": {
                row["status"]: int(row["total"]) for row in status_rows
            },
            "type_counts": {
                row["knowledge_type"]: int(row["total"])
                for row in type_rows
            },
            "categories": [row["category"] for row in category_rows],
        }

    # -- evoluzioni ---------------------------------------------------------

    def evolution(self) -> dict[str, Any]:
        history = self.evolution_service.history()
        changes = sorted(
            history.get("changes", []),
            key=lambda change: str(change.get("changed_at", "")),
            reverse=True,
        )

        with self.memory.db.timeline() as conn:
            seed_rows = conn.execute(
                "SELECT * FROM episodic_memories WHERE source = 'admin_seed' "
                "ORDER BY event_time DESC, id DESC"
            ).fetchall()
            memory_cascade_rows = conn.execute(
                "SELECT cascade_id, COUNT(*) AS revisions, "
                "COUNT(DISTINCT memory_id) AS memories, "
                "MIN(changed_at) AS first_at, MAX(changed_at) AS last_at "
                "FROM memory_revisions WHERE cascade_id IS NOT NULL "
                "GROUP BY cascade_id ORDER BY last_at DESC"
            ).fetchall()
            revision_rows = conn.execute(
                "SELECT r.id, r.memory_id, m.title, m.event_date, "
                "r.revision_number, r.changed_at, r.changed_by, "
                "r.change_reason, r.cascade_id "
                "FROM memory_revisions r "
                "JOIN episodic_memories m ON m.id = r.memory_id "
                "WHERE r.cascade_id IS NOT NULL "
                "ORDER BY r.changed_at DESC, r.id DESC LIMIT 50"
            ).fetchall()

        with self.semantic.db.semantic() as conn:
            semantic_cascade_rows = conn.execute(
                "SELECT cascade_id, COUNT(*) AS revisions, "
                "COUNT(DISTINCT item_id) AS items, "
                "MIN(changed_at) AS first_at, MAX(changed_at) AS last_at "
                "FROM semantic_revisions WHERE cascade_id IS NOT NULL "
                "GROUP BY cascade_id ORDER BY last_at DESC"
            ).fetchall()

        cascades: dict[str, dict[str, Any]] = {}
        for row in memory_cascade_rows:
            cascade_id = str(row["cascade_id"])
            cascades[cascade_id] = {
                "cascade_id": cascade_id,
                "memory_revisions": int(row["revisions"]),
                "memories": int(row["memories"]),
                "semantic_revisions": 0,
                "semantic_items": 0,
                "first_at": row["first_at"],
                "last_at": row["last_at"],
            }
        for row in semantic_cascade_rows:
            cascade_id = str(row["cascade_id"])
            entry = cascades.setdefault(
                cascade_id,
                {
                    "cascade_id": cascade_id,
                    "memory_revisions": 0,
                    "memories": 0,
                    "semantic_revisions": 0,
                    "semantic_items": 0,
                    "first_at": row["first_at"],
                    "last_at": row["last_at"],
                },
            )
            entry["semantic_revisions"] = int(row["revisions"])
            entry["semantic_items"] = int(row["items"])
            starts = [
                value
                for value in (entry["first_at"], row["first_at"])
                if value
            ]
            ends = [
                value
                for value in (entry["last_at"], row["last_at"])
                if value
            ]
            if starts:
                entry["first_at"] = min(starts)
            if ends:
                entry["last_at"] = max(ends)

        ordered_cascades = sorted(
            cascades.values(),
            key=lambda item: str(item.get("last_at") or ""),
            reverse=True,
        )

        return {
            "history": {
                "schema_version": history.get("schema_version"),
                "identity_id": history.get("identity_id"),
                "created_at": history.get("created_at"),
                "updated_at": history.get("updated_at"),
                "changes": changes,
                "count": len(changes),
            },
            "seeds": [decode_memory(row) for row in seed_rows],
            "cascades": ordered_cascades,
            "recent_revisions": [dict(row) for row in revision_rows],
        }


class DashboardHTTPServer(ThreadingHTTPServer):
    """Server HTTP con i dati della dashboard e gli asset statici."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        data: DashboardData,
        *,
        assets_dir: Path = ASSETS_DIR,
        quiet: bool = False,
        auth: DashboardAuth | None = None,
        tls: DashboardTLS | None = None,
        trusted_origins: tuple[str, ...] = (),
        allow_null_origin: bool = False,
    ):
        self.data = data
        self.assets_dir = assets_dir.resolve()
        self.quiet = quiet
        self.auth = auth
        self.tls = tls
        self.trusted_origins = trusted_origins
        self.allow_null_origin = allow_null_origin
        super().__init__(server_address, DashboardRequestHandler)
        if tls is not None:
            self.socket = tls.context.wrap_socket(
                self.socket, server_side=True
            )


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Router minimale per la dashboard."""

    server: DashboardHTTPServer
    server_version = "AgentMemoryDashboard/1.0"
    protocol_version = "HTTP/1.1"

    # -- HTTP ---------------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query, keep_blank_values=False)
        try:
            if path.startswith("/static/"):
                self._serve_file(unquote(path[len("/static/") :]))
                return
            if path == LOGIN_PATH:
                self._handle_login_page(params)
                return
            if path == LOGOUT_PATH:
                self._redirect(LOGIN_PATH if self.server.auth else "/")
                return
            if path == SESSION_PATH:
                self._send_json(self._session_payload())
                return
            if not self._is_authenticated():
                if path.startswith("/api/"):
                    self._send_json(
                        {"error": "Autenticazione richiesta."},
                        status=HTTPStatus.UNAUTHORIZED,
                    )
                    return
                next_path = path
                if parsed.query:
                    next_path = f"{path}?{parsed.query}"
                self._redirect(
                    f"{LOGIN_PATH}?next={quote(self._safe_next(next_path), safe='')}"
                )
                return

            if path == "/":
                self._serve_file("index.html")
            elif path == "/api/overview":
                self._send_json(self.server.data.overview())
            elif path == "/api/timeline":
                self._send_json(
                    self.server.data.timeline(
                        query=self._first(params, "q"),
                        source=self._first(params, "source"),
                        access=self._first(params, "access"),
                        include_hidden=self._flag(params, "include_hidden"),
                        limit=_as_int(
                            self._first(params, "limit"),
                            200,
                            "limit",
                        ),
                    )
                )
            elif path.startswith("/api/memory/"):
                memory_id = unquote(path[len("/api/memory/") :])
                self._send_json(self.server.data.memory_detail(memory_id))
            elif path == "/api/traits":
                self._send_json(self.server.data.traits())
            elif path == "/api/semantic":
                self._send_json(
                    self.server.data.semantic_items(
                        query=self._first(params, "q"),
                        knowledge_type=self._first(params, "type"),
                        status=self._first(params, "status", "active"),
                    )
                )
            elif path == "/api/evolution":
                self._send_json(self.server.data.evolution())
            elif path == "/api/health":
                self._send_json({"ok": True})
            else:
                self._send_json(
                    {"error": "Risorsa non trovata."},
                    status=HTTPStatus.NOT_FOUND,
                )
        except NotFoundError as exc:
            self._send_json(
                {"error": str(exc)}, status=HTTPStatus.NOT_FOUND
            )
        except ValidationError as exc:
            self._send_json(
                {"error": str(exc)}, status=HTTPStatus.BAD_REQUEST
            )
        except AgentMemoryError as exc:
            self._send_json(
                {"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR
            )
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:  # noqa: BLE001 - confine del server: risposta JSON, mai stack trace al client
            traceback.print_exc(file=sys.stderr)
            self._send_json(
                {"error": "Errore interno della dashboard."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if not self._is_same_origin():
                if not self.server.quiet:
                    sys.stderr.write(
                        "Origine rifiutata: "
                        f"Origin={self.headers.get('Origin')!r} "
                        f"Host={self.headers.get('Host')!r} "
                        "X-Forwarded-Host="
                        f"{self.headers.get('X-Forwarded-Host')!r}\n"
                    )
                    sys.stderr.flush()
                self._discard_body()
                self._send_json(
                    {"error": "Origine della richiesta non consentita."},
                    status=HTTPStatus.FORBIDDEN,
                )
                return
            if path == LOGIN_PATH:
                self._handle_login_post()
            elif path == LOGOUT_PATH:
                self._handle_logout()
            else:
                self._send_json(
                    {"error": "Metodo non consentito."},
                    status=HTTPStatus.METHOD_NOT_ALLOWED,
                )
        except ValidationError as exc:
            self._send_json(
                {"error": str(exc)}, status=HTTPStatus.BAD_REQUEST
            )
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:  # noqa: BLE001 - confine del server: risposta JSON, mai stack trace al client
            traceback.print_exc(file=sys.stderr)
            self._send_json(
                {"error": "Errore interno della dashboard."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def log_message(self, format: str, *args: Any) -> None:
        if self.server.quiet:
            return
        sys.stderr.write(f"{self.address_string()} - {format % args}\n")

    # -- autenticazione -----------------------------------------------------

    def _session_token(self) -> str | None:
        raw_cookie = self.headers.get("Cookie")
        if not raw_cookie:
            return None
        cookies = SimpleCookie()
        try:
            cookies.load(raw_cookie)
        except CookieError:
            return None
        morsel = cookies.get(SESSION_COOKIE)
        return morsel.value if morsel is not None else None

    def _is_authenticated(self) -> bool:
        auth = self.server.auth
        if auth is None:
            return True
        return auth.validate_session(self._session_token())

    def _session_payload(self) -> dict[str, Any]:
        auth = self.server.auth
        return {
            "auth_enabled": auth is not None,
            "authenticated": True if auth is None else self._is_authenticated(),
        }

    def _handle_login_page(self, params: dict[str, list[str]]) -> None:
        auth = self.server.auth
        if auth is None:
            self._redirect("/")
            return
        next_path = self._safe_next(self._first(params, "next", "/"))
        if self._is_authenticated():
            self._redirect(next_path)
            return
        self._render_login_page(auth, next_path)

    def _handle_login_post(self) -> None:
        auth = self.server.auth
        if auth is None:
            self._redirect("/")
            return
        if self._is_authenticated():
            self._redirect("/")
            return

        client = self.client_address[0]
        if not auth.allow_login(client):
            self._render_login_page(
                auth,
                "/",
                "Troppi tentativi di accesso. Attendi qualche minuto.",
                status=HTTPStatus.TOO_MANY_REQUESTS,
            )
            return

        form = self._read_form()
        next_path = self._safe_next(form.get("next", "/"))
        if not auth.consume_login_token(form.get("csrf", "")):
            self._render_login_page(
                auth,
                next_path,
                "Modulo scaduto o non valido. Riprova.",
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        if not auth.verify_password(form.get("password", "")):
            auth.register_failure(client)
            self._render_login_page(
                auth,
                next_path,
                "Password non corretta.",
                status=HTTPStatus.UNAUTHORIZED,
            )
            return

        auth.reset_failures(client)
        token = auth.issue_session()
        self._redirect(next_path, cookie=self._session_cookie(token))

    def _handle_logout(self) -> None:
        auth = self.server.auth
        if auth is None:
            self._redirect("/")
            return
        auth.revoke_session(self._session_token())
        self._redirect(LOGIN_PATH, cookie=self._expired_cookie())

    def _render_login_page(
        self,
        auth: DashboardAuth,
        next_path: str,
        error: str = "",
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        try:
            template = (self.server.assets_dir / "login.html").read_text(
                encoding="utf-8"
            )
        except OSError:
            self._send_json(
                {"error": "Pagina di accesso non disponibile."},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        page = (
            template.replace("{{csrf}}", html.escape(auth.create_login_token()))
            .replace("{{next}}", html.escape(self._safe_next(next_path)))
            .replace("{{error}}", html.escape(error))
        )
        self._send_html(page, status=status)

    def _read_request_body(self) -> bytes:
        transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
        if transfer_encoding:
            if "chunked" not in transfer_encoding:
                raise ValidationError("Transfer-Encoding non supportato.")
            return self._read_chunked_body()

        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length or "0")
        except ValueError as exc:
            raise ValidationError("Content-Length non valido.") from exc
        if length < 0 or length > MAX_FORM_BYTES:
            raise ValidationError("Richiesta troppo grande.")
        if length == 0:
            return b""
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValidationError("Corpo della richiesta incompleto.")
        return body

    def _read_chunked_body(self) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            line = self.rfile.readline(256)
            if not line:
                raise ValidationError("Corpo chunked incompleto.")
            size_text = line.split(b";", 1)[0].strip()
            try:
                size = int(size_text, 16)
            except ValueError as exc:
                raise ValidationError("Dimensione del chunk non valida.") from exc
            if size < 0:
                raise ValidationError("Dimensione del chunk non valida.")
            if size == 0:
                while True:
                    trailer = self.rfile.readline(8192)
                    if trailer in {b"\r\n", b"\n", b""}:
                        break
                break
            total += size
            if total > MAX_FORM_BYTES:
                raise ValidationError("Richiesta troppo grande.")
            chunk = self.rfile.read(size)
            if len(chunk) != size:
                raise ValidationError("Corpo chunked incompleto.")
            chunks.append(chunk)
            if self.rfile.read(2) not in {b"\r\n", b"\n"}:
                raise ValidationError("Chunk non terminato correttamente.")
        return b"".join(chunks)

    def _discard_body(self) -> None:
        """Consuma il corpo di una richiesta rifiutata prima della risposta.

        Senza questo, una connessione keep-alive può interpretare il corpo
        del POST come una nuova richiesta HTTP. Gestisce anche il corpo
        chunked usato da alcuni reverse proxy.
        """

        try:
            self._read_request_body()
        except ValidationError:
            self.close_connection = True

    def _read_form(self) -> dict[str, str]:
        if self.headers.get_content_type() != "application/x-www-form-urlencoded":
            raise ValidationError(
                "Il modulo deve usare application/x-www-form-urlencoded."
            )
        body = self._read_request_body()
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("Modulo non codificato in UTF-8.") from exc
        values = parse_qs(text, keep_blank_values=True)
        return {
            key: (items[0] if items else "") for key, items in values.items()
        }

    def _is_same_origin(self) -> bool:
        return _same_origin(
            self.headers.get("Origin"),
            self.headers.get("Host"),
            self.headers.get("X-Forwarded-Host"),
            self.server.trusted_origins,
            self.server.allow_null_origin,
        )

    def _session_cookie(self, token: str) -> str:
        auth = self.server.auth
        ttl = auth.session_ttl if auth is not None else 0
        secure = "; Secure" if self.server.tls is not None else ""
        return (
            f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; "
            f"Path=/; Max-Age={ttl}{secure}"
        )

    def _expired_cookie(self) -> str:
        secure = "; Secure" if self.server.tls is not None else ""
        return (
            f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; "
            f"Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT{secure}"
        )

    @staticmethod
    def _safe_next(value: str | None) -> str:
        if not value:
            return "/"
        value = value.strip()
        if len(value) > 2048:
            return "/"
        if "\\" in value or any(ord(character) < 32 for character in value):
            return "/"
        if not value.startswith("/") or value.startswith("//"):
            return "/"
        decoded = unquote(value)
        if decoded.startswith("//") or "\\" in decoded:
            return "/"
        parsed = urlparse(value)
        if parsed.scheme or parsed.netloc:
            return "/"
        return value

    def _redirect(self, location: str, *, cookie: str | None = None) -> None:
        if not location.startswith("/"):
            location = "/"
        self.close_connection = True
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Connection", "close")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

    # -- utilità ------------------------------------------------------------

    @staticmethod
    def _first(
        params: dict[str, list[str]], name: str, default: str | None = None
    ) -> str | None:
        values = params.get(name)
        if not values:
            return default
        value = values[0].strip()
        return value or default

    @staticmethod
    def _flag(params: dict[str, list[str]], name: str) -> bool:
        values = params.get(name)
        if not values:
            return False
        return values[0].strip().lower() in {"1", "true", "yes", "on"}

    def _send_json(
        self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(
            body, "application/json; charset=utf-8", status=status
        )

    def _send_html(
        self, page: str, *, status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        self._send_bytes(
            page.encode("utf-8"), "text/html; charset=utf-8", status=status
        )

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, name: str) -> None:
        candidate = (self.server.assets_dir / name).resolve()
        if name == "login.html" or candidate.name == "login.html":
            self._send_json(
                {"error": "File non trovato."},
                status=HTTPStatus.NOT_FOUND,
            )
            return
        try:
            candidate.relative_to(self.server.assets_dir)
        except ValueError:
            self._send_json(
                {"error": "File non trovato."},
                status=HTTPStatus.NOT_FOUND,
            )
            return
        if not candidate.is_file():
            self._send_json(
                {"error": "File non trovato."},
                status=HTTPStatus.NOT_FOUND,
            )
            return

        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }
        content_type = content_types.get(
            candidate.suffix.lower(), "application/octet-stream"
        )
        self._send_bytes(candidate.read_bytes(), content_type)


def create_server(
    root: str | Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    assets_dir: Path = ASSETS_DIR,
    quiet: bool = False,
    auth: DashboardAuth | None = None,
    https: bool = False,
    tls_dir: str | Path | None = None,
    tls_sans: tuple[str, ...] = (),
    trusted_origins: tuple[str, ...] = (),
    allow_null_origin: bool = False,
) -> DashboardHTTPServer:
    data = DashboardData(root)
    for origin in trusted_origins:
        try:
            _origin_key(origin)
        except ValueError as exc:
            raise ValidationError(f"--trusted-origin non valida: {exc}") from exc
    tls = None
    if https:
        directory = (
            Path(tls_dir)
            if tls_dir is not None
            else data.root / "data" / "dashboard_tls"
        )
        tls = ensure_dashboard_tls(directory, extra_names=tls_sans)
    return DashboardHTTPServer(
        (host, port),
        data,
        assets_dir=assets_dir,
        quiet=quiet,
        auth=auth,
        tls=tls,
        trusted_origins=trusted_origins,
        allow_null_origin=allow_null_origin,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dashboard amministrativa locale e di sola lettura per "
            "timeline, tratti, conoscenze ed evoluzioni."
        ),
        epilog=(
            "Su un host non loopback l'autenticazione è sempre attiva: se non "
            "fornisci una password ne viene generata una temporanea all'avvio. "
            "Usa --https per cifrare il traffico; senza TLS la password "
            "viaggia in chiaro sulla rete."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory del progetto (default: directory corrente).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Indirizzo di ascolto (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port", type=int, default=8765, help="Porta HTTP (default: 8765)."
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Apre il browser sulla dashboard dopo l'avvio.",
    )
    parser.add_argument(
        "--trusted-origin",
        action="append",
        default=[],
        metavar="ORIGIN",
        help=(
            "Origine esatta (https://host[:porta]) da accettare per i POST "
            "quando un reverse proxy riscrive l'header Host. Ripetibile."
        ),
    )
    parser.add_argument(
        "--allow-null-origin",
        action="store_true",
        help=(
            "Accetta Origin: null, usato dagli iframe sandbox. Il token CSRF "
            "resta obbligatorio; usalo solo per una preview locale fidata."
        ),
    )
    parser.add_argument(
        "--auth",
        action="store_true",
        help="Richiede la password anche quando ascolta su loopback.",
    )
    parser.add_argument(
        "--password-file",
        type=Path,
        default=None,
        help=(
            "File con la password della dashboard (consigliato chmod 600). "
            "In alternativa usa AGENT_MEMORY_DASHBOARD_PASSWORD."
        ),
    )
    parser.add_argument(
        "--https",
        action="store_true",
        help="Abilita HTTPS con un certificato self-signed dedicato.",
    )
    parser.add_argument(
        "--tls-dir",
        type=Path,
        default=None,
        help=(
            "Directory di certificato e chiave TLS "
            "(default: data/dashboard_tls)."
        ),
    )
    parser.add_argument(
        "--tls-san",
        action="append",
        default=[],
        metavar="HOST_OR_IP",
        help=(
            "Aggiunge un nome o indirizzo IP al certificato. "
            "Ripetibile; utile per l'IP LAN usato dagli altri dispositivi."
        ),
    )
    args = parser.parse_args(argv)

    if (args.tls_dir is not None or args.tls_san) and not args.https:
        print(
            "--tls-dir e --tls-san richiedono --https.",
            file=sys.stderr,
        )
        return 2

    try:
        auth, generated_password = resolve_auth(
            require_auth=args.auth or args.host not in LOOPBACK_HOSTS,
            password_file=args.password_file,
        )
    except AgentMemoryError as exc:
        print(f"Errore di autenticazione: {exc}", file=sys.stderr)
        return 2

    if args.host not in LOOPBACK_HOSTS:
        print(
            "Dashboard esposta sulla rete: autenticazione obbligatoria.",
            file=sys.stderr,
        )
    if args.host not in LOOPBACK_HOSTS and not args.https:
        print(
            "ATTENZIONE: senza --https la password di accesso viaggia in "
            "chiaro. Abilita --https o usa una rete fidata.",
            file=sys.stderr,
        )

    try:
        server = create_server(
            args.root,
            host=args.host,
            port=args.port,
            auth=auth,
            https=args.https,
            tls_dir=args.tls_dir,
            tls_sans=tuple(args.tls_san),
            trusted_origins=tuple(args.trusted_origin),
            allow_null_origin=args.allow_null_origin,
        )
    except AgentMemoryError as exc:
        print(f"Errore TLS: {exc}", file=sys.stderr)
        return 2
    display_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    if ":" in display_host and not display_host.startswith("["):
        display_host = f"[{display_host}]"
    scheme = "https" if args.https else "http"
    url = f"{scheme}://{display_host}:{server.server_address[1]}/"
    print(f"Dashboard disponibile su {url}", flush=True)
    if server.tls is not None:
        print(
            f"Certificato TLS: {server.tls.certificate_path}",
            flush=True,
        )
        print(
            "Certificato self-signed: il browser mostrerà un avviso finché "
            "non lo consideri attendibile.",
            flush=True,
        )
        print(
            "Per esportarlo: python -m agent_memory.dashboard_cert export",
            flush=True,
        )
    if args.host in {"0.0.0.0", "::"}:
        print(
            "Da un altro dispositivo usa l'indirizzo IP della macchina.",
            flush=True,
        )
    if generated_password is not None:
        print(
            "Autenticazione attiva. Password temporanea per questo avvio:",
            flush=True,
        )
        print(f"  {generated_password}", flush=True)
    elif auth is not None:
        print("Autenticazione attiva: password configurata.", flush=True)
    else:
        print(
            "Autenticazione disattivata: accesso consentito solo da loopback.",
            flush=True,
        )
    print("Sola lettura. Premi CTRL+C per terminare.", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard arrestata.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
