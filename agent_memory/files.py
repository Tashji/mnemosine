"""File locali dell'agente, confinati in una sandbox per profilo.

Il server MCP può essere usato da un agente che vive altrove: come un agente
normale, deve poter leggere e scrivere file. Qui l'accesso è volutamente
minimo e confinato a ``<root>/files``:

- nessun percorso assoluto, nessun ``..``, nessun ``~``;
- nessun symlink, né nei file né nelle cartelle intermedie;
- lettura solo di testo UTF-8 sotto 1 MiB, scrittura sotto 256 KiB;
- ``config/``, ``data/`` e le altre cartelle del profilo non sono raggiungibili,
  perché la sandbox è una radice separata.

Le scritture sono atomiche (file temporaneo e ``os.replace``) e non
sovrascrivono un file esistente senza ``overwrite`` esplicito.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import project_root
from .errors import (
    ConfigError,
    ConflictError,
    NotFoundError,
    ValidationError,
)

SANDBOX_DIRNAME = "files"
MAX_READ_BYTES = 1_048_576
MAX_WRITE_CHARS = 262_144
MAX_PATH_CHARS = 512
MAX_ENTRIES = 200
DEFAULT_MAX_CHARS = 20_000

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_RELATIVE_SEPARATORS = re.compile(r"[\\/]+")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).replace(microsecond=0).isoformat()


def _check_component(part: str) -> None:
    if part in (".", ".."):
        raise ValidationError("Il percorso non può contenere '.' o '..'.")
    if ":" in part:
        raise ValidationError("Il percorso non può contenere ':'.")
    if _CONTROL_CHARS.search(part):
        raise ValidationError("Il percorso contiene caratteri di controllo.")


class FileSandbox:
    """Accesso in lettura/scrittura a una cartella di lavoro isolata."""

    def __init__(self, root: str | Path | None = None, *, directory: str = SANDBOX_DIRNAME):
        base = Path(root).expanduser().resolve() if root else project_root()
        self.root = base / directory

    # ------------------------------------------------------------------
    # Percorsi
    # ------------------------------------------------------------------

    @property
    def base(self) -> Path:
        return self.root

    @property
    def root_label(self) -> str:
        return SANDBOX_DIRNAME

    def ensure(self) -> Path:
        """Crea la sandbox se assente e restituisce la radice risolta."""

        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(f"Impossibile creare la sandbox dei file: {exc}") from exc
        if self.root.is_symlink():
            raise ConfigError("La sandbox dei file non può essere un symlink.")
        return self.root.resolve()

    def _parts(self, relative: str | Path) -> list[str]:
        if isinstance(relative, Path):
            relative = str(relative)
        if not isinstance(relative, str):
            raise ValidationError("Il percorso deve essere una stringa.")
        if len(relative) > MAX_PATH_CHARS:
            raise ValidationError(f"Il percorso supera {MAX_PATH_CHARS} caratteri.")
        raw = relative.strip()
        if raw.startswith(("~", "/", "\\")):
            raise ValidationError("Il percorso deve essere relativo alla sandbox.")
        parts = [part for part in _RELATIVE_SEPARATORS.split(raw) if part and part != "."]
        for part in parts:
            _check_component(part)
        return parts

    def resolve(self, relative: str | Path = "") -> Path:
        """Risolve un percorso relativo dentro la sandbox.

        Symlink e percorsi che escono dalla sandbox vengono rifiutati prima di
        qualsiasi operazione di I/O.
        """

        base = self.ensure()
        parts = self._parts(relative)
        candidate = base.joinpath(*parts)
        current = base
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise ValidationError(
                    "Il percorso attraversa un symlink, non consentito nella sandbox."
                )
        resolved = candidate.resolve()
        if resolved != base and base not in resolved.parents:
            raise ValidationError("Il percorso esce dalla sandbox dei file.")
        return resolved

    @staticmethod
    def display(base: Path, path: Path) -> str:
        relative = path.relative_to(base).as_posix()
        return "" if relative == "." else relative

    # ------------------------------------------------------------------
    # Operazioni
    # ------------------------------------------------------------------

    def list_files(self, relative: str = "", *, limit: int = MAX_ENTRIES) -> dict[str, Any]:
        """Elenca il contenuto di una cartella della sandbox."""

        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValidationError("limit deve essere un intero.")
        if not 1 <= limit <= MAX_ENTRIES:
            raise ValidationError(f"limit deve essere tra 1 e {MAX_ENTRIES}.")
        base = self.ensure()
        target = self.resolve(relative)
        if target.exists() and not target.is_dir():
            raise ValidationError("Il percorso indicato non è una cartella.")
        entries: list[dict[str, Any]] = []
        truncated = False
        if target.is_dir():
            children = sorted(target.iterdir(), key=lambda child: child.name.lower())
            for index, child in enumerate(children):
                if index >= limit:
                    truncated = True
                    break
                if child.is_symlink():
                    kind = "symlink"
                elif child.is_dir():
                    kind = "directory"
                elif child.is_file():
                    kind = "file"
                else:
                    kind = "other"
                entry: dict[str, Any] = {
                    "name": child.name,
                    "path": self.display(base, child),
                    "type": kind,
                }
                try:
                    stat = child.lstat()
                except OSError:
                    stat = None
                if stat is not None:
                    entry["modified_at"] = _iso(stat.st_mtime)
                    if kind == "file":
                        entry["size"] = stat.st_size
                entries.append(entry)
        return {
            "path": self.display(base, target),
            "count": len(entries),
            "truncated": truncated,
            "entries": entries,
        }

    def read_file(self, relative: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> dict[str, Any]:
        """Legge un file di testo UTF-8 della sandbox."""

        if isinstance(max_chars, bool) or not isinstance(max_chars, int):
            raise ValidationError("max_chars deve essere un intero.")
        if not 1 <= max_chars <= MAX_READ_BYTES:
            raise ValidationError(f"max_chars deve essere tra 1 e {MAX_READ_BYTES}.")
        base = self.ensure()
        target = self.resolve(relative)
        if not target.exists():
            raise NotFoundError("File non trovato nella sandbox dei file.")
        if target.is_dir():
            raise ValidationError("Il percorso indicato è una cartella, non un file.")
        try:
            size = target.stat().st_size
        except OSError as exc:
            raise NotFoundError(f"Impossibile leggere il file: {exc}") from exc
        if size > MAX_READ_BYTES:
            raise ValidationError(
                f"Il file supera {MAX_READ_BYTES} byte: non viene letto per intero."
            )
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise NotFoundError(f"Impossibile leggere il file: {exc}") from exc
        if b"\x00" in data:
            raise ValidationError("Il file sembra binario: la sandbox legge solo testo.")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("Il file non è codificato in UTF-8.") from exc
        return {
            "path": self.display(base, target),
            "size": len(data),
            "text": text[:max_chars],
            "truncated": len(text) > max_chars,
        }

    def write_file(
        self, relative: str, content: str, *, overwrite: bool = False
    ) -> dict[str, Any]:
        """Scrive un file di testo nella sandbox, senza uscire dalla cartella."""

        if not isinstance(content, str):
            raise ValidationError("Il contenuto deve essere una stringa.")
        if len(content) > MAX_WRITE_CHARS:
            raise ValidationError(
                f"Il contenuto supera {MAX_WRITE_CHARS} caratteri."
            )
        base = self.ensure()
        target = self.resolve(relative)
        if target == base:
            raise ValidationError("Indica un nome di file, non la radice della sandbox.")
        if target.exists() and target.is_dir():
            raise ValidationError("Esiste già una cartella con questo nome.")
        existed = target.exists()
        if existed and not overwrite:
            raise ConflictError(
                "Il file esiste già: passa overwrite=True per sostituirlo."
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValidationError(f"Impossibile creare la cartella: {exc}") from exc
        # Ricontrolla dopo la creazione: una cartella intermedia potrebbe essere
        # diventata un symlink nel frattempo.
        self.resolve(relative)
        tmp = target.with_name(f".{target.name}.tmp")
        try:
            with tmp.open("w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except OSError as exc:
            raise ValidationError(f"Impossibile scrivere il file: {exc}") from exc
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return {
            "path": self.display(base, target),
            "size": len(content.encode("utf-8")),
            "created": not existed,
            "overwritten": existed,
        }

    def status(self) -> dict[str, Any]:
        """Descrive la sandbox senza esporre percorsi assoluti."""

        return {
            "root": self.root_label,
            "exists": self.root.is_dir(),
            "max_read_bytes": MAX_READ_BYTES,
            "max_write_chars": MAX_WRITE_CHARS,
            "max_entries": MAX_ENTRIES,
        }
