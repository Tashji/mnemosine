"""TLS locale per la dashboard amministrativa.

Il certificato è self-signed e viene generato con OpenSSL nella directory
indicata. Non viene distribuito con il progetto: la chiave privata resta in
locale con permessi ristretti e l'intera directory è ignorata da Git.
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError

DEFAULT_CERT_NAME = "cert.pem"
DEFAULT_KEY_NAME = "key.pem"
DEFAULT_DAYS = 825


@dataclass(frozen=True)
class DashboardTLS:
    """Contesto TLS pronto per il server e percorsi del certificato."""

    context: ssl.SSLContext
    certificate_path: Path
    key_path: Path
    alternative_names: tuple[str, ...]


def _local_names() -> list[str]:
    names = ["localhost", "127.0.0.1", "::1"]
    try:
        hostname = socket.gethostname().strip()
    except OSError:
        hostname = ""
    if hostname:
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            if _valid_dns_name(hostname):
                names.append(hostname)
        else:
            names.append(hostname)
        try:
            for info in socket.getaddrinfo(hostname, None):
                address = info[4][0]
                if address and "%" not in address:
                    names.append(address)
        except OSError:
            pass
    return names


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return tuple(result)


def _valid_dns_name(value: str) -> bool:
    if not value or len(value) > 253 or value.startswith("."):
        return False
    labels = value.rstrip(".").split(".")
    if not labels:
        return False
    for label in labels:
        if not 1 <= len(label) <= 63:
            return False
        if label.startswith("-") or label.endswith("-"):
            return False
        if not all(
            character.isascii()
            and (character.isalnum() or character == "-")
            for character in label
        ):
            return False
    return True


def _san_entries(values: Sequence[str]) -> tuple[tuple[str, str], ...]:
    entries: list[tuple[str, str]] = []
    for value in _dedupe(values):
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            if not _valid_dns_name(value):
                raise ConfigError(
                    f"Nome non valido per il certificato TLS: {value!r}."
                )
            entries.append(("DNS", value.rstrip(".")))
        else:
            entries.append(("IP", str(address)))
    return tuple(entries)


def _certificate_config(names: Sequence[str]) -> str:
    entries = _san_entries(names)
    if not entries:
        raise ConfigError("Nessun nome alternativo disponibile per il certificato.")
    lines = [
        "[req]",
        "distinguished_name = dn",
        "x509_extensions = v3_req",
        "prompt = no",
        "default_md = sha256",
        "",
        "[dn]",
        "CN = Mnemosine Dashboard",
        "",
        "[v3_req]",
        "basicConstraints = critical,CA:FALSE",
        "keyUsage = critical,digitalSignature,keyEncipherment",
        "extendedKeyUsage = serverAuth",
        "subjectKeyIdentifier = hash",
        "authorityKeyIdentifier = keyid,issuer",
        "subjectAltName = @alt_names",
        "",
        "[alt_names]",
    ]
    counters: dict[str, int] = {"DNS": 0, "IP": 0}
    for kind, value in entries:
        counters[kind] += 1
        lines.append(f"{kind}.{counters[kind]} = {value}")
    return "\n".join(lines) + "\n"


def _openssl_binary() -> str:
    binary = shutil.which("openssl")
    if not binary:
        raise ConfigError(
            "OpenSSL non trovato. Installalo per generare il certificato HTTPS."
        )
    return binary


def _certificate_is_current(
    openssl: str, certificate_path: Path
) -> bool:
    try:
        result = subprocess.run(
            [
                openssl,
                "x509",
                "-checkend",
                "0",
                "-noout",
                "-in",
                str(certificate_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConfigError(
            f"Impossibile verificare il certificato TLS: {exc}"
        ) from exc
    return result.returncode == 0


def _generate_certificate(
    directory: Path,
    openssl: str,
    *,
    days: int,
    names: Sequence[str],
) -> None:
    config = _certificate_config(names)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=directory,
        prefix=".openssl-",
        suffix=".cnf",
        delete=False,
    ) as handle:
        handle.write(config)
        config_path = Path(handle.name)
    os.chmod(config_path, 0o600)

    certificate_path = directory / DEFAULT_CERT_NAME
    key_path = directory / DEFAULT_KEY_NAME
    temporary_certificate = directory / ".cert.pem.tmp"
    temporary_key = directory / ".key.pem.tmp"
    for temporary in (temporary_certificate, temporary_key):
        temporary.unlink(missing_ok=True)

    command = [
        openssl,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-sha256",
        "-days",
        str(days),
        "-nodes",
        "-keyout",
        str(temporary_key),
        "-out",
        str(temporary_certificate),
        "-config",
        str(config_path),
        "-batch",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise ConfigError(
                "Generazione del certificato HTTPS fallita: "
                + (result.stderr.strip() or "errore OpenSSL.")
            )
        os.replace(temporary_certificate, certificate_path)
        os.replace(temporary_key, key_path)
        os.chmod(certificate_path, 0o644)
        os.chmod(key_path, 0o600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConfigError(
            f"Impossibile creare il certificato TLS: {exc}"
        ) from exc
    finally:
        config_path.unlink(missing_ok=True)
        temporary_certificate.unlink(missing_ok=True)
        temporary_key.unlink(missing_ok=True)


def ensure_dashboard_tls(
    directory: str | Path,
    *,
    extra_names: Sequence[str] = (),
    days: int = DEFAULT_DAYS,
) -> DashboardTLS:
    """Crea o carica il certificato dedicato della dashboard."""

    tls_dir = Path(directory).expanduser().resolve()
    try:
        tls_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(tls_dir, 0o700)
    except OSError as exc:
        raise ConfigError(
            f"Impossibile preparare la directory TLS '{tls_dir}': {exc}"
        ) from exc

    certificate_path = tls_dir / DEFAULT_CERT_NAME
    key_path = tls_dir / DEFAULT_KEY_NAME
    openssl = _openssl_binary()
    names = _local_names() + list(extra_names)

    if certificate_path.is_file() != key_path.is_file():
        raise ConfigError(
            "Certificato e chiave TLS non sono entrambi presenti. "
            "Rimuovi i file rimasti per rigenerarli."
        )
    if certificate_path.is_file() and not _certificate_is_current(
        openssl, certificate_path
    ):
        certificate_path.unlink(missing_ok=True)
        key_path.unlink(missing_ok=True)
    if not certificate_path.is_file():
        _generate_certificate(tls_dir, openssl, days=days, names=names)

    if os.name == "posix":
        try:
            os.chmod(certificate_path, 0o644)
            os.chmod(key_path, 0o600)
            os.chmod(tls_dir, 0o700)
        except OSError as exc:
            raise ConfigError(
                f"Impossibile proteggere i file TLS: {exc}"
            ) from exc

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_cert_chain(
            certfile=str(certificate_path),
            keyfile=str(key_path),
        )
    except (OSError, ssl.SSLError) as exc:
        raise ConfigError(
            "Certificato HTTPS non caricabile. Rimuovi "
            f"'{certificate_path.name}' e '{key_path.name}' per rigenerarli: {exc}"
        ) from exc

    return DashboardTLS(
        context=context,
        certificate_path=certificate_path,
        key_path=key_path,
        alternative_names=_dedupe(names),
    )
