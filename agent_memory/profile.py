"""Creazione di un profilo agente vuoto.

Il comando crea i cinque documenti di configurazione e i quattro database
SQLite applicando gli schemi del progetto, senza inserire il dataset demo di
``scripts/init_db.py``. Il profilo è pronto per la biografia procedurale e per
l'indice vettoriale.

Uso:
    python -m agent_memory.profile init \
        --root ./profiles/marco \
        --first-name Marco --last-name Rossi \
        --birth-date 2003-05-12 --birth-city Roma
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import getpass
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any, BinaryIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from .config import CONFIG_FILENAMES, ConfigStore
from .errors import AgentMemoryError, ConfigError

PROFILE_VERSION = "1.0"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = PROJECT_ROOT / "schemas"
TIMELINE_SCHEMA = "timeline_episodic.sql"
SHADOW_SCHEMA = "shadow_diary.sql"
SEMANTIC_SCHEMA = "semantic_memory.sql"
VECTOR_SCHEMA = "vector_index.sql"
DATABASE_FILES = (
    "timeline_episodic.db",
    "shadow_diary.db",
    "semantic_memory.db",
    "vector_index.db",
)
TRAIT_LABELS = {
    "introversion_extraversion": ("introversione", "estroversione"),
    "empathy_detachment": ("empatia", "distacco"),
    "logic_impulsivity": ("logica", "impulsività"),
    "resilience_sensitivity": ("resilienza", "sensibilità"),
}
_LOCK_BUSY_ERRNOS = (errno.EACCES, errno.EAGAIN, errno.EDEADLK)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    return cleaned.strip("_") or "profilo"


def _age_on(birth_date: date, today: date) -> int:
    return today.year - birth_date.year - (
        (today.month, today.day) < (birth_date.month, birth_date.day)
    )


def _birth_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Data non valida: {value!r}. Usa YYYY-MM-DD."
        ) from exc
    if parsed > date.today():
        raise argparse.ArgumentTypeError(
            "La data di nascita non può essere nel futuro."
        )
    return parsed


def _timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            f"Timezone IANA non valida: {value!r}."
        ) from exc
    return value


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Timeout non valido: {value!r}. Usa un numero di secondi."
        ) from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError(
            "Il timeout deve essere un numero positivo di secondi."
        )
    return seconds


def _timestamp(timezone_name: str) -> str:
    return datetime.now(ZoneInfo(timezone_name)).replace(microsecond=0).isoformat()


def _identity_document(args: argparse.Namespace, birth_date: date) -> dict[str, Any]:
    today = date.today()
    full_name = f"{args.first_name.strip()} {args.last_name.strip()}"
    identity_id = args.identity_id or (
        f"character_{_slug(full_name)}_{birth_date.year}"
    )
    timestamp = _timestamp(args.timezone)
    birth_place = {
        "city": args.birth_city.strip(),
        "province": args.birth_province.strip(),
        "region": args.birth_region.strip(),
        "country": args.birth_country.strip(),
        "country_code": args.birth_country_code.strip().upper(),
    }
    residence = {
        "city": (args.residence_city or args.birth_city).strip(),
        "province": (args.residence_province or args.birth_province).strip(),
        "region": (args.residence_region or args.birth_region).strip(),
        "country": args.birth_country.strip(),
        "since": today.isoformat(),
    }
    parents: list[dict[str, Any]] = []
    if args.father_name:
        parents.append(
            {
                "id": "fam_father",
                "first_name": args.father_name.strip(),
                "last_name": args.last_name.strip(),
                "relation": "padre",
                "birth_place": args.birth_city.strip(),
                "notes": "Dato inserito durante la creazione del profilo.",
            }
        )
    if args.mother_name:
        parents.append(
            {
                "id": "fam_mother",
                "first_name": args.mother_name.strip(),
                "last_name": args.last_name.strip(),
                "relation": "madre",
                "birth_place": args.birth_city.strip(),
                "notes": "Dato inserito durante la creazione del profilo.",
            }
        )
    siblings: list[dict[str, Any]] = []
    if args.sibling_name:
        siblings.append(
            {
                "id": "fam_sibling_1",
                "first_name": args.sibling_name.strip(),
                "last_name": args.last_name.strip(),
                "relation": args.sibling_relation.strip() or "sorella",
                "birth_place": args.birth_city.strip(),
                "notes": "Dato inserito durante la creazione del profilo.",
            }
        )

    traits = {
        trait_id: {
            "value": 5,
            "left": left,
            "right": right,
            "description": "Valore iniziale neutro; modificabile nel profilo.",
        }
        for trait_id, (left, right) in TRAIT_LABELS.items()
    }
    return {
        "schema_version": PROFILE_VERSION,
        "identity_id": identity_id,
        "profile_status": "active",
        "created_at": timestamp,
        "updated_at": timestamp,
        "personal_data": {
            "first_name": args.first_name.strip(),
            "last_name": args.last_name.strip(),
            "full_name": full_name,
            "gender": args.gender.strip() or "non specificato",
            "pronouns": args.pronouns.strip(),
            "birth_date": birth_date.isoformat(),
            "age_at_profile_creation": _age_on(birth_date, today),
            "birth_place": birth_place,
            "citizenship": args.citizenship.strip() or "italiana",
            "languages": [
                {
                    "code": "it",
                    "name": "italiano",
                    "level": "madrelingua",
                }
            ],
            "residence": residence,
            "timezone": args.timezone,
        },
        "family": {
            "parents": parents,
            "siblings": siblings,
            "family_notes": (
                "Profilo iniziale: completa i familiari in identity.json."
            ),
        },
        "personality": {
            "model": "bipolar_sliders_v1",
            "scale": {
                "min": 1,
                "max": 10,
                "midpoint": 5.5,
                "semantics": "1 = primo polo indicato, 10 = secondo polo indicato",
            },
            "traits": traits,
        },
        "biography": {
            "education": [],
            "work": [],
            "notes": "Profilo creato da profile init; la biografia è generata proceduralmente.",
        },
    }


def _profile_id(identity: dict[str, Any]) -> str:
    return identity["identity_id"]


def _config_documents(
    args: argparse.Namespace, birth_date: date
) -> dict[str, dict[str, Any]]:
    identity = _identity_document(args, birth_date)
    identity_id = _profile_id(identity)
    timestamp = _timestamp(args.timezone)
    return {
        "identity": identity,
        "shadow_traits": {
            "schema_version": PROFILE_VERSION,
            "identity_id": identity_id,
            "created_at": timestamp,
            "updated_at": timestamp,
            "access_policy": {
                "conscious_direct_read": False,
                "rag_exposure": "never_by_default",
                "allowed_readers": ["shadow_engine", "admin_panel"],
                "notes": (
                    "I contenuti dell'ombra non sono leggibili direttamente "
                    "dal modello o dall'utente."
                ),
            },
            "traits": [],
            "internal_conflicts": [],
            "traumas": [],
        },
        "current_state": {
            "schema_version": PROFILE_VERSION,
            "state_id": f"state_{_slug(identity_id)}_{timestamp}",
            "identity_id": identity_id,
            "created_at": timestamp,
            "updated_at": timestamp,
            "operational_status": "active",
            "clock": {
                "timezone": args.timezone,
                "local_time": timestamp,
                "is_sleep_window": False,
            },
            "activity": {
                "id": "act_profile_init",
                "label": "Profilo appena creato",
                "category": "setup",
                "location": "sistema",
                "started_at": timestamp,
                "details": "Nessuna attività simulata: il profilo è vuoto.",
            },
            "emotion": {
                "label": "neutro",
                "valence": 0.0,
                "arousal": 0.3,
                "intensity": 3,
                "notes": "Stato iniziale del profilo.",
            },
            "mental_load": 0,
            "mental_load_thresholds": {"soft": 60, "overload": 80},
            "routine": {
                "mode": "setup",
                "day_label": "",
                "next_transition_at": None,
                "planned_activities": [],
            },
            "sleep": {
                "is_sleeping": False,
                "window": {"start": "00:00", "end": "07:00"},
                "last_consolidation_at": None,
            },
            "availability": {
                "accepting_messages": True,
                "response_style": "normale",
                "notes": "Nessun carico cognitivo attivo.",
            },
            "heartbeat": {
                "last_run_at": None,
                "next_run_at": None,
                "last_decision": "profilo_creato",
                "spontaneous_message_pending": False,
            },
            "flags": {
                "whatsapp_sending_enabled": False,
                "visual_generation_enabled": False,
                "shadow_direct_read_enabled": False,
                "shadow_engine_enabled": False,
                "maintenance_mode": False,
            },
            "notes": "Stato iniziale creato da profile init.",
        },
        "world_history": {
            "schema_version": PROFILE_VERSION,
            "dataset_id": f"world_{_slug(identity_id)}",
            "identity_id": identity_id,
            "birth_date": birth_date.isoformat(),
            "events": [],
            "notes": (
                "Aggiungi eventi storici reali in world_history.json per "
                "integrare la biografia con il contesto dell'epoca."
            ),
        },
        "social_graph": {
            "schema_version": PROFILE_VERSION,
            "identity_id": identity_id,
            "created_at": timestamp,
            "updated_at": timestamp,
            "scales": {
                "trust": {
                    "min": 0,
                    "max": 100,
                    "notes": "0 = nessuna fiducia, 100 = fiducia piena.",
                },
                "intimacy": {
                    "min": 0,
                    "max": 100,
                    "notes": "0 = relazione formale, 100 = condivisione profonda.",
                },
            },
            "people": [],
        },
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        raise ConfigError(f"Impossibile scrivere {path}: {exc}") from exc
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _schema_path(name: str) -> Path:
    path = SCHEMA_DIR / name
    if not path.is_file():
        raise ConfigError(f"Schema non trovato: {path}")
    return path


def _create_database(path: Path, schema_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(path)
        try:
            conn.executescript(_schema_path(schema_name).read_text(encoding="utf-8"))
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:
        raise ConfigError(f"Impossibile creare {path}: {exc}") from exc


def _existing_profile_files(root: Path) -> list[Path]:
    paths = [root / "config" / name for name in CONFIG_FILENAMES.values()]
    paths.extend(root / "data" / name for name in DATABASE_FILES)
    index_dir = root / "data" / "indexes"
    if index_dir.is_dir():
        paths.extend(path for path in index_dir.iterdir() if path.is_file())
    return [path for path in paths if path.exists()]


def _lock_owner() -> str:
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        return str(getuid())
    try:
        return _slug(getpass.getuser() or "utente")
    except OSError:
        return "utente"


def _lock_directory() -> Path:
    directory = (
        Path(tempfile.gettempdir()) / f"agent-memory-{_lock_owner()}-locks"
    )
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(
            f"Impossibile creare la directory dei lock {directory}: {exc}"
        ) from exc
    return directory


def _lock_path(target: Path) -> Path:
    key = os.path.normcase(str(target))
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return _lock_directory() / f"{digest}.lock"


def _acquire_lock(handle: BinaryIO) -> None:
    if os.name == "nt":
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_lock(handle: BinaryIO) -> None:
    if os.name == "nt":
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def _profile_lock(
    target: Path, *, wait: bool = False, timeout: float | None = None
) -> Iterator[None]:
    """Impedisce due creazioni simultanee sulla stessa root."""

    if timeout is not None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ConfigError(
                "Il timeout del lock deve essere un numero positivo di secondi."
            )
        wait = True
    target = target.expanduser().resolve()
    path = _lock_path(target)
    try:
        handle = path.open("a+b")
    except OSError as exc:
        raise ConfigError(f"Impossibile aprire il lock {path}: {exc}") from exc
    acquired = False
    waiting = False
    deadline = None if timeout is None else time.monotonic() + timeout
    timeout_label = f"{timeout:g}" if timeout is not None else ""
    timeout_error = (
        f"Timeout di {timeout_label} secondi in attesa del lock per {target}: "
        "un'altra creazione è in corso. Riprova più tardi o aumenta "
        "--wait-timeout."
    )
    try:
        while not acquired:
            try:
                _acquire_lock(handle)
                acquired = True
            except OSError as exc:
                if exc.errno not in _LOCK_BUSY_ERRNOS:
                    raise ConfigError(
                        f"Impossibile bloccare {target}: {exc}"
                    ) from exc
                if not wait:
                    raise ConfigError(
                        f"Un'altra creazione del profilo è già in corso per "
                        f"{target}. Attendi che termini e riprova."
                    ) from exc
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    raise ConfigError(timeout_error) from exc
                if not waiting:
                    suffix = (
                        f" (timeout {timeout_label}s)"
                        if timeout is not None
                        else ""
                    )
                    print(
                        f"In attesa del lock per {target}{suffix}...",
                        file=sys.stderr,
                        flush=True,
                    )
                    waiting = True
                delay = 0.1
                if deadline is not None:
                    delay = min(delay, max(0.0, deadline - now))
                if delay <= 0:
                    raise ConfigError(timeout_error) from exc
                time.sleep(delay)
        yield
    finally:
        if acquired:
            try:
                _release_lock(handle)
            except OSError:
                pass
        handle.close()


def _temporary_sibling(target: Path, label: str) -> Path:
    """Crea una directory temporanea sullo stesso filesystem del profilo."""

    for _ in range(10):
        candidate = (
            target.parent
            / f".{target.name}.{label}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        except OSError as exc:
            raise ConfigError(f"Impossibile creare {candidate}: {exc}") from exc
        return candidate
    raise ConfigError(
        f"Impossibile creare una directory temporanea accanto a {target}."
    )


def _ensure_directory(path: Path, created: list[Path]) -> None:
    if path.exists():
        if not path.is_dir():
            raise ConfigError(f"Impossibile usare {path}: non è una directory.")
        return
    try:
        path.mkdir(parents=True)
    except OSError as exc:
        raise ConfigError(f"Impossibile creare {path}: {exc}") from exc
    created.append(path)


def _install_file(source: Path, destination: Path) -> None:
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
    except OSError as exc:
        raise ConfigError(f"Impossibile creare {destination}: {exc}") from exc


def _rename_directory(source: Path, destination: Path) -> None:
    try:
        os.replace(source, destination)
    except OSError as exc:
        raise ConfigError(f"Impossibile creare {destination}: {exc}") from exc


def _rollback_install(
    target: Path,
    backup: Path,
    moved: list[Path],
    installed: list[Path],
    existing_relatives: set[Path],
    created_dirs: list[Path],
) -> bool:
    """Ripristina i file precedenti; False se il ripristino è incompleto."""

    restored = True
    for relative in reversed(installed):
        path = target / relative
        if relative in existing_relatives and not (backup / relative).exists():
            # Il vecchio file non è stato spostato: non cancellarlo.
            continue
        try:
            path.unlink(missing_ok=True)
        except (OSError, KeyboardInterrupt):
            # Il rollback deve proseguire anche con un secondo Ctrl+C.
            restored = False
    for relative in reversed(moved):
        source = backup / relative
        if not source.exists():
            continue
        destination = target / relative
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
        except (OSError, KeyboardInterrupt):
            restored = False
    for directory in reversed(created_dirs):
        try:
            directory.rmdir()
        except OSError:
            pass
    return restored


def _install_staged_profile(
    staging: Path, target: Path, existing: list[Path]
) -> None:
    """Installa il profilo preparato, con backup e rollback dei file sostituiti."""

    backup = _temporary_sibling(target, "backup")
    existing_relatives = {path.relative_to(target) for path in existing}
    moved = sorted(existing_relatives)
    installed = [
        Path("config") / name for name in CONFIG_FILENAMES.values()
    ] + [Path("data") / name for name in DATABASE_FILES]
    created_dirs: list[Path] = []
    keep_backup = False
    try:
        for relative in moved:
            source = target / relative
            if not source.exists():
                continue
            destination = backup / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
        _ensure_directory(target / "config", created_dirs)
        _ensure_directory(target / "data", created_dirs)
        for relative in installed:
            _install_file(staging / relative, target / relative)
        _ensure_directory(target / "data" / "indexes", created_dirs)
        ConfigStore(target).load_all()
    except BaseException:
        if not _rollback_install(
            target,
            backup,
            moved,
            installed,
            existing_relatives,
            created_dirs,
        ):
            keep_backup = True
            print(
                "ATTENZIONE: ripristino incompleto; copia di sicurezza in "
                f"{backup}.",
                file=sys.stderr,
            )
        raise
    finally:
        if not keep_backup:
            shutil.rmtree(backup, ignore_errors=True)


def _create_profile_locked(
    target: Path,
    args: argparse.Namespace,
    *,
    force: bool,
    dry_run: bool,
) -> dict[str, Any]:
    existing = _existing_profile_files(target)
    if existing and not force:
        names = ", ".join(str(path) for path in existing)
        raise ConfigError(
            f"Il profilo esiste già ({names}). Usa --force per sovrascriverlo."
        )
    birth_date = args.birth_date
    configs = _config_documents(args, birth_date)
    plan = {
        "root": str(target),
        "config_files": [
            str(target / "config" / name) for name in CONFIG_FILENAMES.values()
        ],
        "databases": [str(target / "data" / name) for name in DATABASE_FILES],
        "dry_run": dry_run,
        "force": force,
    }
    if dry_run:
        return plan

    if target.exists() and not target.is_dir():
        raise ConfigError(f"Impossibile creare {target}: non è una directory.")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"Impossibile creare {target.parent}: {exc}") from exc

    staging = _temporary_sibling(target, "init")
    try:
        for name, data in configs.items():
            _write_json(staging / "config" / CONFIG_FILENAMES[name], data)
        ConfigStore(staging).load_all()
        for filename, schema in zip(
            DATABASE_FILES,
            (TIMELINE_SCHEMA, SHADOW_SCHEMA, SEMANTIC_SCHEMA, VECTOR_SCHEMA),
            strict=True,
        ):
            _create_database(staging / "data" / filename, schema)
        (staging / "data" / "indexes").mkdir(parents=True, exist_ok=True)

        if target.exists():
            _install_staged_profile(
                staging, target, _existing_profile_files(target)
            )
        else:
            _rename_directory(staging, target)
        plan["identity_id"] = configs["identity"]["identity_id"]
        plan["created"] = True
        return plan
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def create_profile(
    root: str | Path,
    args: argparse.Namespace,
    *,
    force: bool = False,
    dry_run: bool = False,
    wait: bool = False,
    wait_timeout: float | None = None,
) -> dict[str, Any]:
    """Crea configurazione e database vuoti nella root indicata."""

    target = Path(root).expanduser().resolve()
    if target == PROJECT_ROOT:
        raise ConfigError(
            "Usa una directory separata per il profilo: il progetto contiene "
            "già il dataset demo e la configurazione corrente."
        )
    with _profile_lock(target, wait=wait, timeout=wait_timeout):
        return _create_profile_locked(
            target, args, force=force, dry_run=dry_run
        )


def _print_result(result: dict[str, Any]) -> None:
    print(f"Profilo creato: {result['root']}")
    print(f"Identità: {result.get('identity_id', '-')}")
    print("Configurazione: 5 file validati")
    print("Database: timeline=0, shadow=0, semantic=0, vector=0")
    print("\nProssimi passi:")
    print(
        f"  python -m agent_memory.biography --root {result['root']} --apply"
    )
    print(
        f"  python -m agent_memory.index --root {result['root']} --rebuild"
    )
    print(
        f"  python -m agent_memory.dashboard --root {result['root']} --auth"
    )
    print(
        "\nLa biografia e l'indice non vengono creati automaticamente: "
        "richiedono rispettivamente la generazione dei ricordi e il modello "
        "di embedding."
    )


def _ask(
    prompt: str,
    default: str | None = None,
    *,
    required: bool = False,
) -> str:
    while True:
        label = f"{prompt} [{default}]" if default else prompt
        raw = input(f"{label}: ").strip()
        value = raw or (default or "")
        if value or not required:
            return value
        print("  Campo obbligatorio.")


def _ask_date(prompt: str, default: date | None) -> date:
    default_text = default.isoformat() if default is not None else None
    while True:
        raw = _ask(prompt, default_text, required=True)
        try:
            return _birth_date(raw)
        except argparse.ArgumentTypeError as exc:
            print(f"  {exc}")


def _ask_timezone(prompt: str, default: str) -> str:
    while True:
        raw = _ask(prompt, default, required=True)
        try:
            return _timezone(raw)
        except argparse.ArgumentTypeError as exc:
            print(f"  {exc}")


def _interactive_args(args: argparse.Namespace) -> argparse.Namespace:
    print("Creazione guidata del profilo. Invio accetta il valore tra parentesi.")
    args.first_name = _ask("Nome", args.first_name, required=True)
    args.last_name = _ask("Cognome", args.last_name, required=True)
    args.birth_date = _ask_date("Data di nascita (YYYY-MM-DD)", args.birth_date)
    args.birth_city = _ask("Città di nascita", args.birth_city, required=True)
    args.birth_province = _ask("Provincia di nascita", args.birth_province)
    args.birth_region = _ask("Regione di nascita", args.birth_region)
    args.birth_country = _ask("Paese di nascita", args.birth_country)
    args.birth_country_code = _ask(
        "Codice paese (ISO 3166-1 alpha-2)", args.birth_country_code
    )
    args.citizenship = _ask("Cittadinanza", args.citizenship)
    args.gender = _ask("Genere", args.gender)
    args.pronouns = _ask("Pronomi (facoltativo)", args.pronouns)
    args.residence_city = _ask(
        "Città di residenza", args.residence_city or args.birth_city
    )
    args.residence_province = _ask(
        "Provincia di residenza", args.residence_province or args.birth_province
    )
    args.residence_region = _ask(
        "Regione di residenza", args.residence_region or args.birth_region
    )
    args.timezone = _ask_timezone("Timezone IANA", args.timezone)
    args.father_name = (
        _ask("Nome del padre (Invio per saltare)", args.father_name) or None
    )
    args.mother_name = (
        _ask("Nome della madre (Invio per saltare)", args.mother_name) or None
    )
    args.sibling_name = (
        _ask("Nome di un fratello o sorella (Invio per saltare)", args.sibling_name)
        or None
    )
    if args.sibling_name:
        args.sibling_relation = _ask(
            "Relazione (sorella/fratello)", args.sibling_relation
        )
    if args.root is None:
        default_root = Path("profiles") / _slug(
            f"{args.first_name} {args.last_name}"
        )
        args.root = Path(
            _ask("Directory del profilo", str(default_root), required=True)
        ).expanduser()
    return args


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Crea un profilo agente vuoto con configurazione valida e "
            "database senza dati demo."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser(
        "init", help="Crea un nuovo profilo vuoto."
    )
    init_parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=(
            "Directory del nuovo profilo. Obbligatoria senza --interactive; "
            "in modalità interattiva viene richiesta se omessa."
        ),
    )
    init_parser.add_argument("--first-name", default=None)
    init_parser.add_argument("--last-name", default=None)
    init_parser.add_argument(
        "--birth-date", type=_birth_date, default=None, help="YYYY-MM-DD"
    )
    init_parser.add_argument("--birth-city", default=None)
    init_parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Chiede interattivamente i dati anagrafici e familiari.",
    )
    init_parser.add_argument("--birth-province", default="")
    init_parser.add_argument("--birth-region", default="")
    init_parser.add_argument("--birth-country", default="Italia")
    init_parser.add_argument("--birth-country-code", default="IT")
    init_parser.add_argument("--citizenship", default="italiana")
    init_parser.add_argument("--residence-city", default=None)
    init_parser.add_argument("--residence-province", default=None)
    init_parser.add_argument("--residence-region", default=None)
    init_parser.add_argument(
        "--timezone", type=_timezone, default="Europe/Rome"
    )
    init_parser.add_argument("--gender", default="non specificato")
    init_parser.add_argument("--pronouns", default="")
    init_parser.add_argument(
        "--identity-id",
        default=None,
        help="ID profilo (default: character_<nome>_<anno>).",
    )
    init_parser.add_argument("--father-name", default=None)
    init_parser.add_argument("--mother-name", default=None)
    init_parser.add_argument("--sibling-name", default=None)
    init_parser.add_argument("--sibling-relation", default="sorella")
    init_parser.add_argument(
        "--wait",
        action="store_true",
        help=(
            "Attende il rilascio del lock senza limite invece di terminare "
            "subito se un'altra creazione è in corso."
        ),
    )
    init_parser.add_argument(
        "--wait-timeout",
        type=_positive_seconds,
        default=None,
        metavar="SECONDI",
        help=(
            "Attende il lock al massimo per SECONDI secondi; implica --wait "
            "e allo scadere termina con errore."
        ),
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Sovrascrive configurazione e database già presenti.",
    )
    init_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra il piano senza scrivere nulla.",
    )
    init_parser.add_argument(
        "--json",
        action="store_true",
        help="Stampa il risultato in JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command != "init":
            parser.error("Comando non supportato.")
        if args.interactive:
            try:
                args = _interactive_args(args)
            except (EOFError, KeyboardInterrupt):
                print("\nCreazione annullata.", file=sys.stderr)
                return 130
        else:
            missing = [
                label
                for value, label in (
                    (args.root, "--root"),
                    (args.first_name, "--first-name"),
                    (args.last_name, "--last-name"),
                    (args.birth_date, "--birth-date"),
                    (args.birth_city, "--birth-city"),
                )
                if not value
            ]
            if missing:
                parser.error(
                    "In modalità non interattiva servono: "
                    + ", ".join(missing)
                )
        result = create_profile(
            args.root,
            args,
            force=args.force,
            dry_run=args.dry_run,
            wait=args.wait,
            wait_timeout=args.wait_timeout,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.dry_run:
            print(f"Dry-run: profilo pianificato in {result['root']}")
            for path in result["config_files"] + result["databases"]:
                print(f"  {path}")
            print("Nessuna modifica eseguita.")
        else:
            _print_result(result)
        return 0
    except AgentMemoryError as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCreazione annullata.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
