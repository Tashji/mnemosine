"""Backup e ripristino locale del profilo Mnemosine.

Un backup è una directory ``backups/backup-AAAAMMGG-HHMMSS`` con:

- i database SQLite copiati con l'API di backup di SQLite, quindi coerenti
  anche se il server è in esecuzione;
- i documenti di ``config/``;
- facoltativamente ``files/`` e ``data/outbox/``;
- un ``manifest.json`` con checksum, dimensioni, versioni di schema e motivi
  delle esclusioni.

Non entrano mai nel backup ``data/secrets``, ``data/models``,
``data/dashboard_tls``, ``data/browser``, ``data/indexes`` e ``data/logs``.
Sono dati locali, credenziali o artefatti rigenerabili; copiarli renderebbe un
backup condivisibile molto più pericoloso. Il backup resta comunque un file
locale con dati personali: non va pubblicato.

Il ripristino richiede la conferma letterale ``RIPRISTINA`` e crea prima un
backup di sicurezza ``pre-restore``. Non cancella i backup precedenti.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .audit import AuditLog
from .config import project_root
from .errors import ConfigError
from .migrations import DATABASE_SCHEMAS

BACKUP_FORMAT = 1
BACKUP_PREFIX = "backup-"
MANIFEST_NAME = "manifest.json"
DEFAULT_KEEP = 7
EXCLUDED_DATA = (
    "browser",
    "dashboard_tls",
    "indexes",
    "logs",
    "models",
    "secrets",
)
RESTORE_CONFIRMATION = "RIPRISTINA"


def _now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _reason(value: str) -> str:
    text = " ".join(str(value).replace("\x00", " ").split())
    if not text:
        text = "manuale"
    return text[:120]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry(path: Path, relative: str) -> dict[str, Any]:
    return {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _safe_relative(value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("Il manifest contiene un percorso vuoto o non valido.")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ConfigError(f"Percorso non sicuro nel manifest: {value!r}.")
    return path


def _json_version(value: Any) -> int | None:
    try:
        return int(str(value).split(".", 1)[0])
    except (TypeError, ValueError):
        return None


def _sqlite_integrity(path: Path) -> tuple[bool, str | None]:
    try:
        conn = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10
        )
    except sqlite3.Error as exc:
        return False, f"apertura fallita: {exc}"
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        return False, f"integrity_check fallito: {exc}"
    finally:
        conn.close()
    if row is None or str(row[0]).casefold() != "ok":
        return False, "integrity_check non ha restituito ok"
    return True, None


def _schema_version(path: Path) -> str | None:
    try:
        conn = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10
        )
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return None if row is None else str(row[0])


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.restore-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    )
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ConfigError(
            f"Impossibile ripristinare {destination.name}: {exc}"
        ) from exc


class BackupService:
    """Gestisce snapshot, verifica, ripristino e retention del profilo."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        backups_dir: str | Path | None = None,
        audit: AuditLog | None = None,
    ):
        self.root = Path(root).expanduser().resolve() if root else project_root()
        self.backups_dir = (
            Path(backups_dir).expanduser().resolve()
            if backups_dir is not None
            else self.root / "backups"
        )
        self.audit = audit or AuditLog(self.root)

    # -- creazione ---------------------------------------------------------

    def _new_name(self) -> str:
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        for suffix in range(0, 100):
            name = f"{BACKUP_PREFIX}{stamp}"
            if suffix:
                name = f"{name}-{suffix}"
            if not (self.backups_dir / name).exists():
                return name
        raise ConfigError("Troppi backup nello stesso secondo; riprova tra poco.")

    def _copy_tree(
        self,
        source: Path,
        destination: Path,
        prefix: str,
        entries: list[dict[str, Any]],
    ) -> bool:
        if not source.exists():
            return False
        if source.is_symlink() or not source.is_dir():
            raise ConfigError(
                f"La directory da salvare non è sicura: {source.name}."
            )
        destination.mkdir(parents=True, exist_ok=True)
        source_root = source.resolve()
        for current, directories, filenames in os.walk(
            source, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            for directory in list(directories):
                candidate = current_path / directory
                if candidate.is_symlink():
                    raise ConfigError(
                        "Il profilo contiene un symlink in una cartella da "
                        f"salvare: {candidate.relative_to(source_root)}."
                    )
            for filename in sorted(filenames):
                # Il lock e i file temporanei del trasporto outbox sono stato
                # operativo del processo, non dati da ripristinare; su Windows
                # copiare un file bloccato fallirebbe l'intero backup.
                if prefix == "data/outbox" and (
                    filename == ".transport.lock"
                    or (
                        filename.startswith(".status.json.")
                        and filename.endswith(".tmp")
                    )
                ):
                    continue
                candidate = current_path / filename
                if candidate.is_symlink():
                    raise ConfigError(
                        "Il profilo contiene un symlink in un file da salvare: "
                        f"{candidate.relative_to(source_root)}."
                    )
                if not candidate.is_file():
                    continue
                relative = candidate.relative_to(source)
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(candidate, target)
                except OSError as exc:
                    raise ConfigError(
                        f"Impossibile copiare {relative.as_posix()}: {exc}"
                    ) from exc
                entries.append(
                    _entry(target, f"{prefix}/{relative.as_posix()}")
                )
        return True

    def _snapshot_database(self, source: Path, destination: Path) -> None:
        if source.is_symlink() or not source.is_file():
            raise ConfigError(
                f"Il database da salvare non è un file regolare: {source.name}."
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            origin = sqlite3.connect(
                f"{source.resolve().as_uri()}?mode=ro", uri=True, timeout=15
            )
        except sqlite3.Error as exc:
            raise ConfigError(
                f"Impossibile aprire {source.name} per il backup: {exc}"
            ) from exc
        try:
            target = sqlite3.connect(str(destination))
        except sqlite3.Error as exc:
            origin.close()
            raise ConfigError(
                f"Impossibile creare {destination.name}: {exc}"
            ) from exc
        try:
            origin.backup(target)
            target.execute("PRAGMA journal_mode = DELETE")
            target.commit()
            check = target.execute("PRAGMA integrity_check").fetchone()
            if check is None or str(check[0]).casefold() != "ok":
                raise ConfigError(
                    f"Il backup di {source.name} non ha superato integrity_check."
                )
        except sqlite3.Error as exc:
            raise ConfigError(
                f"Backup SQLite fallito per {source.name}: {exc}"
            ) from exc
        finally:
            target.close()
            origin.close()

    def _identity_id(self) -> str | None:
        path = self.root / "config" / "identity.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(document, dict):
            return None
        value = document.get("identity_id")
        return value if isinstance(value, str) and value else None

    def _excluded_paths(self) -> list[str]:
        result: list[str] = []
        for name in EXCLUDED_DATA:
            if (self.root / "data" / name).exists():
                result.append(f"data/{name}")
        return result

    def create(
        self,
        *,
        reason: str = "manuale",
        include_files: bool = True,
        keep: int | None = DEFAULT_KEEP,
    ) -> dict[str, Any]:
        """Crea uno snapshot completo o parziale del profilo."""

        if not self.root.is_dir():
            raise ConfigError(f"Profilo non trovato: {self.root}.")
        safe_reason = _reason(reason)
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        name = self._new_name()
        temporary = self.backups_dir / f".{name}.tmp"
        entries: list[dict[str, Any]] = []
        missing: list[str] = []
        databases: dict[str, str | None] = {}
        included = {"config": False, "databases": [], "files": False, "outbox": False}
        try:
            temporary.mkdir()
            included["config"] = self._copy_tree(
                self.root / "config",
                temporary / "config",
                "config",
                entries,
            )
            if not included["config"]:
                missing.append("config")

            for database in DATABASE_SCHEMAS:
                source = self.root / "data" / f"{database}.db"
                if not source.exists():
                    missing.append(f"data/{database}.db")
                    continue
                destination = temporary / "data" / f"{database}.db"
                self._snapshot_database(source, destination)
                entries.append(
                    _entry(destination, f"data/{database}.db")
                )
                included["databases"].append(database)
                databases[database] = _schema_version(destination)

            included["outbox"] = self._copy_tree(
                self.root / "data" / "outbox",
                temporary / "data" / "outbox",
                "data/outbox",
                entries,
            )
            if include_files:
                included["files"] = self._copy_tree(
                    self.root / "files",
                    temporary / "files",
                    "files",
                    entries,
                )

            manifest = {
                "format": BACKUP_FORMAT,
                "name": name,
                "created_at": _now(),
                "reason": safe_reason,
                "app_version": __version__,
                "identity_id": self._identity_id(),
                "included": included,
                "missing": missing,
                "excluded": self._excluded_paths(),
                "schema_versions": databases,
                "entries": sorted(entries, key=lambda item: item["path"]),
            }
            (temporary / MANIFEST_NAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            destination = self.backups_dir / name
            os.replace(temporary, destination)
        except BaseException as exc:
            shutil.rmtree(temporary, ignore_errors=True)
            self.audit.record(
                "backup.create",
                reason=safe_reason,
                outcome="error",
                error_type=type(exc).__name__,
            )
            if isinstance(exc, ConfigError):
                raise
            if isinstance(exc, OSError):
                raise ConfigError(f"Backup non riuscito: {exc}") from exc
            raise

        summary = self._summary(destination)
        self.audit.record(
            "backup.create",
            name=name,
            reason=safe_reason,
            outcome="ok",
            files=summary.get("files"),
            databases=len(included["databases"]),
            bytes=summary.get("bytes"),
        )
        if keep is not None:
            self.prune(keep)
        return summary

    # -- lettura -----------------------------------------------------------

    @staticmethod
    def _read_manifest(directory: Path) -> dict[str, Any]:
        path = directory / MANIFEST_NAME
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigError(
                f"Manifest del backup non leggibile: {directory.name}: {exc}"
            ) from exc
        except ValueError as exc:
            raise ConfigError(
                f"Manifest del backup non valido: {directory.name}: {exc}"
            ) from exc
        if not isinstance(manifest, dict):
            raise ConfigError(f"Manifest non valido: {directory.name}.")
        if manifest.get("format") != BACKUP_FORMAT:
            raise ConfigError(
                f"Formato del backup non supportato: {directory.name}."
            )
        if not isinstance(manifest.get("entries"), list):
            raise ConfigError(f"Elenco file mancante: {directory.name}.")
        return manifest

    def _summary(self, directory: Path) -> dict[str, Any]:
        manifest = self._read_manifest(directory)
        entries = manifest.get("entries", [])
        size = sum(
            int(item.get("bytes", 0))
            for item in entries
            if isinstance(item, dict)
        )
        return {
            "name": directory.name,
            "path": str(directory),
            "created_at": manifest.get("created_at"),
            "reason": manifest.get("reason"),
            "files": len(entries),
            "bytes": size,
            "databases": len(manifest.get("schema_versions", {})),
            "missing": list(manifest.get("missing", [])),
        }

    def _locate(self, name: str) -> Path:
        candidate = Path(str(name))
        if (
            candidate.name != str(name)
            or not str(name).startswith(BACKUP_PREFIX)
            or str(name) in {".", ".."}
        ):
            raise ConfigError(f"Nome del backup non valido: {name!r}.")
        directory = (self.backups_dir / str(name)).resolve()
        try:
            directory.relative_to(self.backups_dir)
        except ValueError as exc:
            raise ConfigError(f"Nome del backup non valido: {name!r}.") from exc
        if directory.is_symlink() or not directory.is_dir():
            raise ConfigError(f"Backup non trovato: {name}.")
        return directory

    def list(self) -> list[dict[str, Any]]:
        if not self.backups_dir.is_dir():
            return []
        result: list[dict[str, Any]] = []
        for directory in self.backups_dir.iterdir():
            if (
                not directory.is_dir()
                or directory.is_symlink()
                or not directory.name.startswith(BACKUP_PREFIX)
            ):
                continue
            try:
                result.append(self._summary(directory))
            except ConfigError:
                result.append(
                    {
                        "name": directory.name,
                        "path": str(directory),
                        "created_at": None,
                        "reason": None,
                        "files": 0,
                        "bytes": 0,
                        "databases": 0,
                        "missing": [],
                        "ok": False,
                        "error": "manifest non valido",
                    }
                )
        result.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return result

    def verify(self, name: str) -> dict[str, Any]:
        """Verifica checksum, struttura e integrità SQLite di un backup."""

        directory = self._locate(name)
        try:
            manifest = self._read_manifest(directory)
        except ConfigError as exc:
            return {
                "ok": False,
                "name": name,
                "path": str(directory),
                "errors": [str(exc)],
                "warnings": [],
                "checked": 0,
                "databases": 0,
            }

        errors: list[str] = []
        warnings: list[str] = []
        entries = manifest.get("entries", [])
        checked = 0
        databases = 0
        created_at = manifest.get("created_at")
        try:
            if not isinstance(created_at, str) or not datetime.fromisoformat(
                created_at
            ):
                raise ValueError
        except (TypeError, ValueError):
            errors.append("created_at non è una data ISO-8601 valida.")

        for item in entries:
            if not isinstance(item, dict):
                errors.append("Una voce del manifest non è un oggetto.")
                continue
            try:
                relative = _safe_relative(item.get("path"))
            except ConfigError as exc:
                errors.append(str(exc))
                continue
            target = (directory / relative).resolve()
            try:
                target.relative_to(directory)
            except ValueError:
                errors.append(
                    f"Percorso fuori dal backup: {relative.as_posix()}."
                )
                continue
            if target.is_symlink():
                errors.append(f"Symlink non ammesso: {relative.as_posix()}.")
                continue
            if not target.is_file():
                errors.append(f"File mancante: {relative.as_posix()}.")
                continue
            checked += 1
            expected_size = item.get("bytes")
            if isinstance(expected_size, int) and target.stat().st_size != expected_size:
                errors.append(
                    f"Dimensione diversa: {relative.as_posix()}."
                )
            expected_hash = item.get("sha256")
            if not isinstance(expected_hash, str) or len(expected_hash) != 64:
                errors.append(
                    f"Checksum non valido nel manifest: {relative.as_posix()}."
                )
            else:
                try:
                    actual_hash = _sha256(target)
                except OSError as exc:
                    errors.append(
                        f"Checksum non calcolabile per {relative.as_posix()}: {exc}."
                    )
                else:
                    if actual_hash != expected_hash:
                        errors.append(
                            f"Checksum non corrispondente: {relative.as_posix()}."
                        )
            if relative.suffix == ".db":
                databases += 1
                ok, detail = _sqlite_integrity(target)
                if not ok:
                    errors.append(
                        f"Database non integro ({relative.as_posix()}): {detail}."
                    )

        for path in directory.rglob("*"):
            if path.is_symlink():
                errors.append(
                    f"Symlink non ammesso nel backup: {path.relative_to(directory).as_posix()}."
                )
                break

        recorded_versions = manifest.get("schema_versions", {})
        if isinstance(recorded_versions, dict):
            for database, recorded in recorded_versions.items():
                relative = Path("data") / f"{database}.db"
                target = directory / relative
                if not target.is_file():
                    continue
                actual = _schema_version(target)
                if actual != recorded:
                    errors.append(
                        f"Versione di schema diversa per {relative.as_posix()}."
                    )
        if manifest.get("missing"):
            warnings.append(
                "Il backup è parziale: mancano "
                + ", ".join(str(item) for item in manifest["missing"])
                + "."
            )
        result = {
            "ok": not errors,
            "name": name,
            "path": str(directory),
            "created_at": created_at,
            "reason": manifest.get("reason"),
            "files": checked,
            "databases": databases,
            "bytes": sum(
                int(item.get("bytes", 0))
                for item in entries
                if isinstance(item, dict)
            ),
            "errors": errors,
            "warnings": warnings,
            "missing": list(manifest.get("missing", [])),
        }
        self.audit.record(
            "backup.verify",
            name=name,
            outcome="ok" if result["ok"] else "error",
            files=checked,
            databases=databases,
        )
        return result

    # -- ripristino --------------------------------------------------------

    @staticmethod
    def _replace_tree(source: Path, destination: Path) -> None:
        if destination.is_symlink():
            raise ConfigError(
                f"La destinazione del ripristino è un symlink: {destination.name}."
            )
        if destination.exists():
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        if source.exists():
            if source.is_symlink() or not source.is_dir():
                raise ConfigError(f"Directory di backup non valida: {source.name}.")
            shutil.copytree(source, destination, symlinks=False)
        else:
            destination.mkdir(parents=True, exist_ok=True)

    def _restore_database(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("-wal", "-shm"):
            try:
                (destination.parent / f"{destination.name}{suffix}").unlink(
                    missing_ok=True
                )
            except OSError as exc:
                raise ConfigError(
                    f"Impossibile rimuovere il file transitorio di {destination.name}: {exc}"
                ) from exc
        _atomic_copy(source, destination)
        ok, detail = _sqlite_integrity(destination)
        if not ok:
            raise ConfigError(
                f"Il database ripristinato {destination.name} non è integro: {detail}."
            )

    def restore(self, name: str, *, confirmation: str | None) -> dict[str, Any]:
        """Ripristina un backup dopo conferma esplicita e backup di sicurezza."""

        if confirmation != RESTORE_CONFIRMATION:
            raise ConfigError(
                f"Ripristino non confermato. Usa la conferma esatta "
                f"{RESTORE_CONFIRMATION!r}."
            )
        report = self.verify(name)
        if not report["ok"]:
            raise ConfigError(
                "Il backup non è integro e non può essere ripristinato: "
                + " ".join(report["errors"][:3])
            )
        directory = self._locate(name)
        manifest = self._read_manifest(directory)
        from .profile import _profile_lock

        with _profile_lock(self.root, timeout=30):
            # Il backup di sicurezza viene creato prima di toccare il profilo.
            safety = self.create(
                reason="pre-restore", include_files=True, keep=None
            )
            restored: list[str] = []
            restored_databases: list[str] = []
            try:
                for item in manifest["entries"]:
                    if not isinstance(item, dict):
                        continue
                    relative = _safe_relative(item.get("path"))
                    if relative.parts[0] == "files":
                        continue
                    if relative.parts[:2] == ("data", "outbox"):
                        continue
                    source = directory / relative
                    destination = self.root / relative
                    if relative.parts[0] == "config":
                        _atomic_copy(source, destination)
                        restored.append(relative.as_posix())
                    elif relative.parent == Path("data") and relative.suffix == ".db":
                        self._restore_database(source, destination)
                        restored.append(relative.as_posix())
                        restored_databases.append(relative.stem)
                included = manifest.get("included", {})
                if isinstance(included, dict) and included.get("files"):
                    self._replace_tree(directory / "files", self.root / "files")
                if isinstance(included, dict) and included.get("outbox"):
                    self._replace_tree(
                        directory / "data" / "outbox",
                        self.root / "data" / "outbox",
                    )
            except BaseException as exc:
                self.audit.record(
                    "backup.restore",
                    name=name,
                    safety=safety.get("name"),
                    outcome="error",
                    error_type=type(exc).__name__,
                )
                raise
        result = {
            "ok": True,
            "name": name,
            "restored": restored,
            "databases": restored_databases,
            "safety_backup": safety.get("name"),
            "safety_path": safety.get("path"),
        }
        self.audit.record(
            "backup.restore",
            name=name,
            safety=safety.get("name"),
            outcome="ok",
            files=len(restored),
            databases=len(restored_databases),
        )
        return result

    # -- retention e hook di migrazione -----------------------------------

    def prune(self, keep: int = DEFAULT_KEEP) -> dict[str, Any]:
        if not isinstance(keep, int) or isinstance(keep, bool):
            raise ConfigError("La retention deve essere un numero intero.")
        if keep < 1 or keep > 100:
            raise ConfigError("La retention deve essere compresa tra 1 e 100.")
        backups = [item for item in self.list() if item.get("ok", True)]
        backups.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        removed: list[str] = []
        for item in backups[keep:]:
            directory = self._locate(item["name"])
            try:
                shutil.rmtree(directory)
            except OSError as exc:
                raise ConfigError(
                    f"Impossibile rimuovere il backup {item['name']}: {exc}"
                ) from exc
            removed.append(item["name"])
        result = {
            "ok": True,
            "removed": removed,
            "kept": [item["name"] for item in backups[:keep]],
        }
        self.audit.record(
            "backup.prune", outcome="ok", removed=len(removed), keep=keep
        )
        return result

    def ensure_pre_migration(self, database: str) -> dict[str, Any]:
        """Crea al massimo un backup pre-migrazione ogni cinque minuti."""

        if not self.backups_dir.is_dir():
            return self.create(
                reason=f"pre-migration:{database}",
                include_files=False,
                keep=10,
            )
        now = datetime.now().timestamp()
        for item in self.list():
            if not str(item.get("reason", "")).startswith("pre-migration"):
                continue
            path = Path(str(item.get("path", "")))
            try:
                if now - path.stat().st_mtime <= 300:
                    return item
            except OSError:
                continue
        return self.create(
            reason=f"pre-migration:{database}",
            include_files=False,
            keep=10,
        )


def _human_bytes(value: Any) -> str:
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--root",
        type=Path,
        default=argparse.SUPPRESS,
        help="Directory del profilo (default: progetto corrente).",
    )
    parser = argparse.ArgumentParser(
        description="Backup e ripristino locale del profilo Mnemosine."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory del profilo (default: progetto corrente).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser(
        "create", parents=[common], help="Crea un backup del profilo."
    )
    create_parser.add_argument("--reason", default="manuale")
    create_parser.add_argument(
        "--no-files", action="store_true", help="Esclude la sandbox files/."
    )
    create_parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    create_parser.add_argument("--json", action="store_true")

    list_parser = subparsers.add_parser(
        "list", parents=[common], help="Elenca i backup disponibili."
    )
    list_parser.add_argument("--json", action="store_true")

    verify_parser = subparsers.add_parser(
        "verify", parents=[common], help="Verifica checksum e database di un backup."
    )
    verify_parser.add_argument("name")
    verify_parser.add_argument("--json", action="store_true")

    restore_parser = subparsers.add_parser(
        "restore", parents=[common], help="Ripristina un backup verificato."
    )
    restore_parser.add_argument("name")
    restore_parser.add_argument(
        "--confirm",
        default=None,
        help=f"Conferma esplicita: {RESTORE_CONFIRMATION}.",
    )
    restore_parser.add_argument("--json", action="store_true")

    prune_parser = subparsers.add_parser(
        "prune", parents=[common], help="Conserva solo i backup più recenti."
    )
    prune_parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    prune_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        service = BackupService(args.root)
        if args.command == "create":
            result = service.create(
                reason=args.reason,
                include_files=not args.no_files,
                keep=args.keep,
            )
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(
                    f"Backup creato: {result['path']} "
                    f"({result['files']} file, {_human_bytes(result['bytes'])})."
                )
            return 0
        if args.command == "list":
            result = service.list()
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif not result:
                print("Nessun backup disponibile.")
            else:
                for item in result:
                    print(
                        f"{item['name']}  {item.get('created_at') or '?'}  "
                        f"{item.get('reason') or '?'}  "
                        f"{item.get('files', 0)} file  "
                        f"{_human_bytes(item.get('bytes'))}"
                    )
            return 0
        if args.command == "verify":
            result = service.verify(args.name)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(
                    f"Backup {args.name}: "
                    f"{'OK' if result['ok'] else 'NON VALIDO'} "
                    f"({result['files']} file, {result['databases']} database)."
                )
                for warning in result.get("warnings", []):
                    print(f"  avviso: {warning}")
                for error in result.get("errors", []):
                    print(f"  errore: {error}")
            return 0 if result["ok"] else 1
        if args.command == "restore":
            result = service.restore(args.name, confirmation=args.confirm)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(
                    f"Ripristino completato da {args.name}; backup di sicurezza: "
                    f"{result['safety_backup']}."
                )
            return 0
        if args.command == "prune":
            result = service.prune(args.keep)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(
                    f"Rimossi {len(result['removed'])} backup; "
                    f"conservati {len(result['kept'])}."
                )
            return 0
    except ConfigError as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1
    parser.error("Comando non riconosciuto.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BACKUP_FORMAT",
    "BackupService",
    "DEFAULT_KEEP",
    "EXCLUDED_DATA",
    "RESTORE_CONFIRMATION",
    "main",
]
