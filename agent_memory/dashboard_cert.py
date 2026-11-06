"""Esportazione, installazione e rimozione del certificato della dashboard.

Comandi:
    python -m agent_memory.dashboard_cert export --output ~/mnemosine.crt
    python -m agent_memory.dashboard_cert fingerprint
    python -m agent_memory.dashboard_cert install --dry-run
    sudo python -m agent_memory.dashboard_cert install --yes
    python -m agent_memory.dashboard_cert uninstall --dry-run
    sudo python -m agent_memory.dashboard_cert uninstall --yes

L'installazione e la rimozione dal trust store modificano lo stato del
sistema e richiedono privilegi amministrativi. Senza ``--yes`` il comando
mostra solo il piano.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shlex
import shutil
import ssl
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import project_root
from .dashboard_tls import DEFAULT_CERT_NAME, ensure_dashboard_tls
from .errors import AgentMemoryError, ConfigError


@dataclass(frozen=True)
class TrustStorePlan:
    """Operazioni necessarie per installare il certificato nel sistema."""

    system: str
    copy_to: Path | None
    commands: tuple[tuple[str, ...], ...]
    note: str


@dataclass(frozen=True)
class UninstallPlan:
    """Operazioni necessarie per rimuovere il certificato dal sistema."""

    system: str
    remove_from: Path | None
    commands: tuple[tuple[str, ...], ...]
    note: str


def build_trust_plan(
    certificate_path: str | Path,
    *,
    system_name: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> TrustStorePlan:
    """Costruisce il piano di installazione senza eseguirlo."""

    certificate = Path(certificate_path).expanduser().resolve()
    system = (system_name or platform.system()).lower()

    if system == "linux":
        updater = which("update-ca-certificates")
        if updater:
            return TrustStorePlan(
                system="linux",
                copy_to=Path(
                    "/usr/local/share/ca-certificates/mnemosine-dashboard.crt"
                ),
                commands=((updater,),),
                note="Aggiorna il trust store CA del sistema.",
            )
        updater = which("update-ca-trust")
        if updater:
            return TrustStorePlan(
                system="linux",
                copy_to=Path(
                    "/etc/pki/ca-trust/source/anchors/mnemosine-dashboard.crt"
                ),
                commands=((updater,),),
                note="Aggiorna il trust store CA di Fedora/RHEL.",
            )
        updater = which("trust")
        if updater:
            return TrustStorePlan(
                system="linux",
                copy_to=Path(
                    "/etc/ca-certificates/trust-source/anchors/"
                    "mnemosine-dashboard.crt"
                ),
                commands=((updater, "extract-compat"),),
                note="Aggiorna il trust store CA di Arch Linux.",
            )
        raise ConfigError(
            "Nessun gestore del trust store Linux trovato. Esporta il "
            "certificato e importalo manualmente."
        )

    if system == "darwin":
        security = which("security")
        if not security:
            raise ConfigError("Comando 'security' non trovato.")
        return TrustStorePlan(
            system="darwin",
            copy_to=None,
            commands=(
                (
                    security,
                    "add-trusted-cert",
                    "-d",
                    "-r",
                    "trustRoot",
                    "-k",
                    "/Library/Keychains/System.keychain",
                    str(certificate),
                ),
            ),
            note="Aggiunge il certificato al Keychain di sistema macOS.",
        )

    if system == "windows":
        certutil = which("certutil")
        if not certutil:
            raise ConfigError("Comando 'certutil' non trovato.")
        return TrustStorePlan(
            system="windows",
            copy_to=None,
            commands=(
                (certutil, "-addstore", "-f", "Root", str(certificate)),
            ),
            note="Aggiunge il certificato all'archivio Root di Windows.",
        )

    raise ConfigError(
        f"Installazione automatica non supportata su '{system}'. "
        "Usa 'export' e importa il certificato manualmente."
    )


def build_uninstall_plan(
    certificate_path: str | Path,
    *,
    system_name: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> UninstallPlan:
    """Costruisce il piano di rimozione senza eseguirlo."""

    certificate = Path(certificate_path).expanduser().resolve()
    if not certificate.is_file():
        raise ConfigError(f"Certificato non trovato: {certificate}")
    system = (system_name or platform.system()).lower()

    if system == "linux":
        updater = which("update-ca-certificates")
        if updater:
            return UninstallPlan(
                system="linux",
                remove_from=Path(
                    "/usr/local/share/ca-certificates/mnemosine-dashboard.crt"
                ),
                commands=((updater, "--fresh"),),
                note="Rigenera il trust store CA del sistema dopo la rimozione.",
            )
        updater = which("update-ca-trust")
        if updater:
            return UninstallPlan(
                system="linux",
                remove_from=Path(
                    "/etc/pki/ca-trust/source/anchors/mnemosine-dashboard.crt"
                ),
                commands=((updater,),),
                note="Rigenera il trust store CA di Fedora/RHEL.",
            )
        updater = which("trust")
        if updater:
            return UninstallPlan(
                system="linux",
                remove_from=Path(
                    "/etc/ca-certificates/trust-source/anchors/"
                    "mnemosine-dashboard.crt"
                ),
                commands=((updater, "extract-compat"),),
                note="Rigenera il trust store CA di Arch Linux.",
            )
        raise ConfigError(
            "Nessun gestore del trust store Linux trovato. Rimuovi il "
            "certificato manualmente."
        )

    if system == "darwin":
        security = which("security")
        if not security:
            raise ConfigError("Comando 'security' non trovato.")
        return UninstallPlan(
            system="darwin",
            remove_from=None,
            commands=(
                (
                    security,
                    "delete-certificate",
                    "-Z",
                    _certificate_sha1(certificate),
                    "/Library/Keychains/System.keychain",
                ),
            ),
            note=(
                "Rimuove il certificato dal Keychain di sistema macOS. "
                "Se l'hai importato manualmente, rimuovilo anche da "
                "Accesso Portachiavi."
            ),
        )

    if system == "windows":
        certutil = which("certutil")
        if not certutil:
            raise ConfigError("Comando 'certutil' non trovato.")
        return UninstallPlan(
            system="windows",
            remove_from=None,
            commands=(
                (certutil, "-delstore", "Root", _certificate_sha1(certificate)),
            ),
            note="Rimuove il certificato dall'archivio Root di Windows.",
        )

    raise ConfigError(
        f"Rimozione automatica non supportata su '{system}'. "
        "Rimuovi il certificato manualmente."
    )


def is_elevated() -> bool:
    """Verifica i privilegi amministrativi correnti."""

    if os.name == "nt":
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001 - modulo opzionale del sistema
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def _certificate_sha1(certificate_path: str | Path) -> str:
    """Restituisce lo SHA-1 usato dai trust store come identificatore.

    Non è un controllo di sicurezza: macOS e Windows usano lo SHA-1 per
    individuare il certificato da eliminare.
    """

    path = Path(certificate_path).expanduser()
    try:
        pem = path.read_text(encoding="ascii")
    except OSError as exc:
        raise ConfigError(f"Impossibile leggere il certificato: {exc}") from exc
    try:
        der = ssl.PEM_cert_to_DER_cert(pem)
    except ValueError as exc:
        raise ConfigError("Il file non contiene un certificato PEM valido.") from exc
    return hashlib.sha1(der).hexdigest().upper()


def certificate_fingerprint(certificate_path: str | Path) -> str:
    """Restituisce il fingerprint SHA-256 in formato leggibile."""

    path = Path(certificate_path).expanduser()
    try:
        pem = path.read_text(encoding="ascii")
    except OSError as exc:
        raise ConfigError(f"Impossibile leggere il certificato: {exc}") from exc
    try:
        der = ssl.PEM_cert_to_DER_cert(pem)
    except ValueError as exc:
        raise ConfigError("Il file non contiene un certificato PEM valido.") from exc
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[index : index + 2] for index in range(0, len(digest), 2))


def export_certificate(
    certificate_path: str | Path,
    output_path: str | Path,
    *,
    force: bool = False,
) -> Path:
    """Copia il certificato in un file PEM pubblico."""

    source = Path(certificate_path).expanduser().resolve()
    destination = Path(output_path).expanduser().resolve()
    if not source.is_file():
        raise ConfigError(f"Certificato non trovato: {source}")
    if source == destination:
        return destination
    if destination.exists() and not force:
        raise ConfigError(
            f"Il file di destinazione esiste già: {destination}. Usa --force."
        )
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        os.chmod(destination, 0o644)
    except OSError as exc:
        raise ConfigError(f"Esportazione del certificato fallita: {exc}") from exc
    return destination


def install_certificate(
    certificate_path: str | Path,
    plan: TrustStorePlan,
    *,
    dry_run: bool = False,
) -> int:
    """Esegue o mostra il piano di installazione."""

    certificate = Path(certificate_path).expanduser().resolve()
    if not certificate.is_file():
        raise ConfigError(f"Certificato non trovato: {certificate}")
    print(f"Piattaforma: {plan.system}")
    if plan.copy_to is not None:
        print(f"Copia: {certificate} -> {plan.copy_to}")
    for command in plan.commands:
        print(f"Esegui: {shlex.join(command)}")
    print(plan.note)
    if dry_run:
        print("Dry-run: nessuna modifica eseguita.")
        return 0
    if not is_elevated():
        raise ConfigError(
            "Servono privilegi amministrativi. Su Linux/macOS usa sudo; "
            "su Windows apri un terminale come amministratore."
        )

    if plan.copy_to is not None:
        try:
            plan.copy_to.parent.mkdir(parents=True, exist_ok=True)
            if certificate != plan.copy_to.resolve():
                shutil.copyfile(certificate, plan.copy_to)
                os.chmod(plan.copy_to, 0o644)
        except OSError as exc:
            raise ConfigError(
                f"Impossibile copiare il certificato nel trust store: {exc}"
            ) from exc

    for command in plan.commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ConfigError(
                f"Impossibile eseguire {command[0]}: {exc}"
            ) from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise ConfigError(
                f"Il comando {shlex.join(command)} è fallito: "
                f"{detail or 'errore sconosciuto'}"
            )
    print("Certificato installato nel trust store di sistema.")
    return 0


def uninstall_certificate(
    certificate_path: str | Path,
    plan: UninstallPlan,
    *,
    dry_run: bool = False,
) -> int:
    """Esegue o mostra il piano di rimozione dal trust store."""

    certificate = Path(certificate_path).expanduser().resolve()
    if not certificate.is_file():
        raise ConfigError(f"Certificato non trovato: {certificate}")
    print(f"Piattaforma: {plan.system}")
    if plan.remove_from is not None:
        print(f"Rimuovi: {plan.remove_from}")
    for command in plan.commands:
        print(f"Esegui: {shlex.join(command)}")
    print(plan.note)
    if dry_run:
        print("Dry-run: nessuna modifica eseguita.")
        return 0
    if not is_elevated():
        raise ConfigError(
            "Servono privilegi amministrativi. Su Linux/macOS usa sudo; "
            "su Windows apri un terminale come amministratore."
        )

    if plan.remove_from is not None:
        try:
            if plan.remove_from.exists():
                plan.remove_from.unlink()
            else:
                print(f"File non presente, proseguo: {plan.remove_from}")
        except OSError as exc:
            raise ConfigError(
                f"Impossibile rimuovere il certificato dal trust store: {exc}"
            ) from exc

    for command in plan.commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ConfigError(
                f"Impossibile eseguire {command[0]}: {exc}"
            ) from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise ConfigError(
                f"Il comando {shlex.join(command)} è fallito: "
                f"{detail or 'errore sconosciuto'}"
            )
    print("Certificato rimosso dal trust store di sistema.")
    return 0


def _tls_directory(args: argparse.Namespace) -> Path:
    if args.tls_dir is not None:
        return Path(args.tls_dir).expanduser().resolve()
    root = Path(args.root).expanduser().resolve() if args.root else project_root()
    return root / "data" / "dashboard_tls"


def _ensure_certificate(args: argparse.Namespace) -> Path:
    tls_dir = _tls_directory(args)
    tls = ensure_dashboard_tls(tls_dir, extra_names=tuple(args.tls_san))
    return tls.certificate_path


def _certificate_for_uninstall(args: argparse.Namespace) -> Path:
    """Trova il certificato esistente senza generarne uno nuovo."""

    if args.cert is not None:
        certificate = Path(args.cert).expanduser().resolve()
    else:
        certificate = _tls_directory(args) / DEFAULT_CERT_NAME
    if not certificate.is_file():
        raise ConfigError(
            f"Certificato non trovato: {certificate}. "
            "Usa --cert per indicare il file installato."
        )
    return certificate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Esporta, installa o rimuove dal trust store il certificato "
            "della dashboard amministrativa."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory del progetto (default: directory corrente).",
    )
    parser.add_argument(
        "--tls-dir",
        type=Path,
        default=None,
        help="Directory del certificato (default: data/dashboard_tls).",
    )
    parser.add_argument(
        "--tls-san",
        action="append",
        default=[],
        metavar="HOST_OR_IP",
        help="Nome o IP da aggiungere al certificato se va generato.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser(
        "export", help="Copia il certificato in un file PEM."
    )
    export_parser.add_argument(
        "--output",
        type=Path,
        default=Path.home() / "mnemosine-dashboard.crt",
        help="File di destinazione (default: ~/mnemosine-dashboard.crt).",
    )
    export_parser.add_argument(
        "--force",
        action="store_true",
        help="Sovrascrive il file di destinazione se esiste.",
    )

    subparsers.add_parser(
        "fingerprint", help="Stampa il fingerprint SHA-256 del certificato."
    )

    install_parser = subparsers.add_parser(
        "install", help="Installa il certificato nel trust store di sistema."
    )
    install_parser.add_argument(
        "--cert",
        type=Path,
        default=None,
        help="Usa un certificato già esportato invece di quello della dashboard.",
    )
    install_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra le operazioni senza modificare il sistema.",
    )
    install_parser.add_argument(
        "--yes",
        action="store_true",
        help="Conferma l'installazione nel trust store.",
    )

    uninstall_parser = subparsers.add_parser(
        "uninstall", help="Rimuove il certificato dal trust store di sistema."
    )
    uninstall_parser.add_argument(
        "--cert",
        type=Path,
        default=None,
        help="Usa un certificato già esportato invece di quello della dashboard.",
    )
    uninstall_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra le operazioni senza modificare il sistema.",
    )
    uninstall_parser.add_argument(
        "--yes",
        action="store_true",
        help="Conferma la rimozione dal trust store.",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            certificate = _ensure_certificate(args)
            destination = export_certificate(
                certificate, args.output, force=args.force
            )
            print(f"Certificato esportato: {destination}")
            print(f"SHA-256: {certificate_fingerprint(destination)}")
            return 0
        if args.command == "fingerprint":
            certificate = _ensure_certificate(args)
            print(f"Certificato: {certificate}")
            print(f"SHA-256: {certificate_fingerprint(certificate)}")
            return 0
        if args.command == "install":
            certificate = (
                Path(args.cert).expanduser().resolve()
                if args.cert is not None
                else _ensure_certificate(args)
            )
            plan = build_trust_plan(certificate)
            if args.dry_run:
                return install_certificate(certificate, plan, dry_run=True)
            if not args.yes:
                install_certificate(certificate, plan, dry_run=True)
                print(
                    "Nessuna modifica eseguita. Aggiungi --yes per installare."
                )
                return 2
            return install_certificate(certificate, plan, dry_run=False)
        if args.command == "uninstall":
            certificate = _certificate_for_uninstall(args)
            plan = build_uninstall_plan(certificate)
            if args.dry_run:
                return uninstall_certificate(certificate, plan, dry_run=True)
            if not args.yes:
                uninstall_certificate(certificate, plan, dry_run=True)
                print(
                    "Nessuna modifica eseguita. Aggiungi --yes per rimuovere."
                )
                return 2
            return uninstall_certificate(certificate, plan, dry_run=False)
    except AgentMemoryError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
