"""Profilo dell'aspetto e generazione esplicita tramite Replicate.

Descrizioni e reference restano locali. Un'immagine viene inviata al provider
solo dopo conferma esplicita dell'amministratore nella dashboard.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, build_opener

from .config import ConfigStore
from .errors import ConfigError, ValidationError
from .profile import _profile_lock

MAX_REFERENCE_BYTES = 8 * 1024 * 1024
MAX_GENERATED_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_PROMPT_CHARS = 1000
MAX_SEED = 2**31 - 1
MAX_GENERATED_IMAGES = 50
MODEL = "black-forest-labs/flux-kontext-pro"
TOKEN_ENV = "REPLICATE_API_TOKEN"
TOKEN_FILE_ENV = "REPLICATE_API_TOKEN_FILE"
TOKEN_MAX_BYTES = 4096
TOKEN_MIN_CHARS = 8
TOKEN_MAX_CHARS = 500
PROFILE_FIELDS = ("body", "face", "hair", "eyes", "distinctive_marks", "style")
_GENERATION_LOCKS: dict[Path, threading.Lock] = {}
_GENERATION_LOCKS_GUARD = threading.Lock()


def _generation_lock_for(root: Path) -> threading.Lock:
    """Un solo job visivo per profilo nel processo della dashboard."""
    with _GENERATION_LOCKS_GUARD:
        return _GENERATION_LOCKS.setdefault(root, threading.Lock())


class ImageBackend(Protocol):
    def generate(self, prompt: str, image_path: Path, seed: int, *, token: str) -> bytes: ...


class ReplicateBackend:
    """Client Replicate con token esplicito: nessuna variabile d'ambiente implicita."""

    def generate(self, prompt: str, image_path: Path, seed: int, *, token: str) -> bytes:
        try:
            import replicate
        except ImportError as exc:
            raise ConfigError("Provider visivo non installato: esegui pip install 'mnemosine[visual]'.") from exc

        try:
            client = replicate.Client(api_token=token)
            with image_path.open("rb") as reference:
                output = client.run(
                    MODEL,
                    input={"prompt": prompt, "input_image": reference, "seed": seed},
                )
            if isinstance(output, (list, tuple)):
                output = output[0] if output else None
            if output is None:
                raise ConfigError("Replicate non ha restituito un'immagine.")
            if isinstance(output, bytes):
                data = output
            elif isinstance(output, str):
                data = self._read_output_url(output)
            elif hasattr(output, "url"):
                data = self._read_output_url(str(output.url))
            else:
                raise ConfigError("Replicate non ha restituito un URL immagine valido.")
            if not data:
                raise ConfigError("Replicate non ha restituito un'immagine valida.")
            if len(data) > MAX_GENERATED_BYTES:
                raise ConfigError("L'immagine restituita dal provider supera 32 MiB.")
            return data
        except ConfigError:
            raise
        except Exception as exc:
            # Non riportare dettagli HTTP grezzi: potrebbero contenere input o token.
            raise ConfigError(
                "Replicate non ha completato la richiesta. Verifica connessione, "
                "saldo e configurazione; se l'esito è incerto, controlla la cronologia "
                "Replicate prima di riprovare."
            ) from exc

    @staticmethod
    def _read_output_url(url: str) -> bytes:
        if url.startswith("data:image/"):
            try:
                header, encoded = url.split(",", 1)
                if ";base64" not in header:
                    raise ValueError("formato data URI non supportato")
                if len(encoded) > (MAX_GENERATED_BYTES * 4 // 3) + 8:
                    raise ConfigError("L'immagine restituita dal provider supera 32 MiB.")
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, base64.binascii.Error) as exc:
                raise ConfigError("Replicate ha restituito un'immagine non valida.") from exc
        else:
            parsed = urlparse(url)
            if not _is_replicate_delivery(parsed):
                raise ConfigError("Replicate ha restituito un URL immagine non autorizzato.")

            class SameProviderRedirects(HTTPRedirectHandler):
                def redirect_request(self, request, response, code, message, headers, new_url):
                    if not _is_replicate_delivery(urlparse(new_url)):
                        raise ConfigError("Replicate ha restituito un redirect non autorizzato.")
                    return super().redirect_request(request, response, code, message, headers, new_url)

            opener = build_opener(SameProviderRedirects())
            try:
                with opener.open(url, timeout=30) as response:
                    if response.status != 200:
                        raise ConfigError("Il file generato non è disponibile.")
                    length = response.headers.get("Content-Length")
                    if length is not None and int(length) > MAX_GENERATED_BYTES:
                        raise ConfigError("L'immagine restituita dal provider supera 32 MiB.")
                    chunks: list[bytes] = []
                    total = 0
                    while chunk := response.read(64 * 1024):
                        total += len(chunk)
                        if total > MAX_GENERATED_BYTES:
                            raise ConfigError("L'immagine restituita dal provider supera 32 MiB.")
                        chunks.append(chunk)
                    data = b"".join(chunks)
            except (OSError, URLError, ValueError) as exc:
                raise ConfigError("Impossibile scaricare il file generato da Replicate.") from exc
        if len(data) > MAX_GENERATED_BYTES:
            raise ConfigError("L'immagine restituita dal provider supera 32 MiB.")
        return data


class VisualService:
    def __init__(self, root: str | Path | None = None, *, backend: ImageBackend | None = None):
        self.config = ConfigStore(root)
        self.root = self.config.root
        self.visual_dir = self.root / "data" / "visual"
        self.reference_path = self.visual_dir / "reference.jpg"
        self.output_dir = self.root / "files" / "images"
        self.backend = backend or ReplicateBackend()
        self._generation_lock = _generation_lock_for(self.root)

    @staticmethod
    def _profile_document(identity: dict[str, Any]) -> dict[str, Any]:
        raw = identity.get("visual_profile", {})
        return raw if isinstance(raw, dict) else {}

    def status(self) -> dict[str, Any]:
        try:
            _token, source = resolve_token(self.root)
        except ConfigError:
            _token, configured, source = None, False, None
        else:
            configured = True
        checked = self._read_token_status()
        if (
            not configured
            or checked.get("source") != source
            or not hmac.compare_digest(
                checked.get("token_fingerprint", "").encode("utf-8"),
                hashlib.sha256(_token.encode("utf-8")).hexdigest().encode("ascii") if _token else b"",
            )
        ):
            checked = {}
        identity = self.config.get("identity")
        state = self.config.get("current_state")
        files: list[dict[str, Any]] = []
        if self.output_dir.is_dir() and not self.output_dir.is_symlink():
            metadata_paths = sorted(
                (
                    path
                    for path in self.output_dir.glob("*.json")
                    if path.is_file() and not path.is_symlink()
                ),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
            for metadata_path in metadata_paths:
                try:
                    item = json.loads(metadata_path.read_text(encoding="utf-8"))
                    name = metadata_path.stem
                    image = self.output_dir / f"{name}.png"
                    if (
                        not image.is_file()
                        or image.is_symlink()
                        or not isinstance(item, dict)
                        or not _valid_id(name)
                    ):
                        continue
                    if (
                        not isinstance(item.get("scene"), str)
                        or isinstance(item.get("seed"), bool)
                        or not isinstance(item.get("seed"), int)
                        or not isinstance(item.get("created_at"), str)
                    ):
                        continue
                    files.append(
                        {
                            "id": name,
                            "path": f"images/{name}.png",
                            "prompt": item["scene"],
                            "seed": item["seed"],
                            "created_at": item["created_at"],
                        }
                    )
                    if len(files) >= MAX_GENERATED_IMAGES:
                        break
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
        profile = self._profile_document(identity)
        flags = state.get("flags", {})
        reference_available = self.reference_path.is_file() and not self.reference_path.is_symlink()
        return {
            "profile": {
                key: profile.get(key, "") for key in PROFILE_FIELDS
            } | {"fixed_seed": profile.get("fixed_seed")},
            "enabled": bool(flags.get("visual_generation_enabled", False)),
            "provider": "Replicate · FLUX.1 Kontext Pro",
            "price_per_image_usd": 0.04,
            "provider_configured": configured,
            "provider_source": source,
            "provider_checked_at": checked.get("checked_at"),
            "provider_valid": checked.get("valid"),
            "provider_username": checked.get("username"),
            "reference_available": reference_available,
            "reference_path": "reference.jpg" if reference_available else None,
            "generated_images": files,
        }

    def configure(
        self, profile: dict[str, Any], *, enabled: bool, confirmation: str = ""
    ) -> dict[str, Any]:
        if not isinstance(profile, dict):
            raise ValidationError("Profilo aspetto non valido.")
        if not isinstance(enabled, bool):
            raise ValidationError("enabled: atteso true o false.")
        cleaned: dict[str, Any] = {}
        for key in PROFILE_FIELDS:
            value = profile.get(key, "")
            if not isinstance(value, str) or len(value) > 2000 or "\x00" in value:
                raise ValidationError(f"{key}: massimo 2000 caratteri.")
            cleaned[key] = value.strip()
        seed = profile.get("fixed_seed")
        if seed in (None, ""):
            cleaned["fixed_seed"] = None
        elif isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= MAX_SEED:
            raise ValidationError(f"fixed_seed: usa un intero tra 0 e {MAX_SEED}.")
        else:
            cleaned["fixed_seed"] = seed
        current_flags = self.config.get("current_state").get("flags", {})
        old_enabled = bool(current_flags.get("visual_generation_enabled"))
        if enabled and not old_enabled and (not isinstance(confirmation, str) or confirmation.strip() != "GENERA"):
            raise ValidationError("Scrivi GENERA per abilitare le richieste a pagamento al provider esterno.")
        with _profile_lock(self.root, timeout=30):
            identity = self.config.get("identity")
            identity["visual_profile"] = {"schema_version": 1, **cleaned}
            self.config.update_identity(identity)
            if enabled != old_enabled:
                self.config.update_current_state(
                    {"flags": {"visual_generation_enabled": enabled}},
                    allow_visual_generation=enabled,
                )
        return self.status()

    def save_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        """Aggiorna il profilo visivo conservando il flag gestito dalla dashboard."""
        return self.configure(
            profile,
            enabled=bool(
                self.config.get("current_state").get("flags", {}).get("visual_generation_enabled")
            ),
        )

    def save_reference(self, data: bytes, content_type: str) -> dict[str, Any]:
        if not isinstance(data, bytes) or not data or len(data) > MAX_REFERENCE_BYTES:
            raise ValidationError(f"L'immagine deve pesare al massimo {MAX_REFERENCE_BYTES // (1024 * 1024)} MiB.")
        allowed = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
        if content_type not in allowed:
            raise ValidationError("Formato immagine non supportato: usa PNG, JPEG o WebP.")
        try:
            from PIL import Image, ImageOps, UnidentifiedImageError
        except ImportError as exc:
            raise ConfigError("Supporto immagini non installato: esegui pip install 'mnemosine[visual]'.") from exc
        from io import BytesIO
        try:
            with Image.open(BytesIO(data)) as source:
                if source.format != allowed[content_type]:
                    raise ValidationError("Il formato effettivo dell'immagine non corrisponde al Content-Type dichiarato.")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise ValidationError("L'immagine supera il limite di 20 megapixel.")
                source.load()
                cleaned = ImageOps.exif_transpose(source).convert("RGB")
                output = BytesIO()
                cleaned.save(output, format="JPEG", quality=92, optimize=True)
                normalized = output.getvalue()
        except ValidationError:
            raise
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            raise ValidationError("Immagine non valida o danneggiata.") from exc
        if len(normalized) > MAX_REFERENCE_BYTES:
            raise ValidationError("L'immagine ottimizzata supera il limite consentito.")
        with _profile_lock(self.root, timeout=30):
            self._ensure_private_dir(self.visual_dir)
            _write_private(self.reference_path, normalized)
        return self.status()

    def remove_reference(self) -> dict[str, Any]:
        with _profile_lock(self.root, timeout=30):
            if self.reference_path.is_symlink():
                raise ConfigError("Reference non valida: il percorso non può essere un symlink.")
            try:
                self.reference_path.unlink(missing_ok=True)
            except OSError as exc:
                raise ConfigError("Impossibile rimuovere la reference visiva.") from exc
        return {"removed": True, **self.status()}

    def delete_image(self, image_id: str) -> dict[str, Any]:
        """Rimuove un'immagine generata e i suoi metadati dal profilo locale."""

        if not isinstance(image_id, str) or not _valid_id(image_id):
            raise ValidationError("ID immagine non valido.")
        image_path = self.output_dir / f"{image_id}.png"
        metadata_path = self.output_dir / f"{image_id}.json"
        with _profile_lock(self.root, timeout=30):
            for path in (image_path, metadata_path):
                if path.is_symlink():
                    raise ConfigError("Immagine non valida: il percorso non può essere un symlink.")
            if not image_path.is_file():
                raise ConfigError("Immagine non disponibile: potrebbe essere già stata eliminata.")
            try:
                image_path.unlink()
                metadata_path.unlink(missing_ok=True)
            except OSError as exc:
                raise ConfigError("Impossibile eliminare l'immagine.") from exc
        return {"removed": True, "id": image_id, **self.status()}

    def check_provider(self) -> dict[str, Any]:
        """Verifica il token sull'account Replicate senza avviare generazioni."""

        token, source = resolve_token(self.root)
        from .visual_token import check_token

        result = check_token(token)
        username = result.get("username")
        record = {
            "checked_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "valid": bool(result.get("valid")),
            "username": username if isinstance(username, str) else None,
            "source": source,
            "token_fingerprint": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        }
        with _profile_lock(self.root, timeout=30):
            self._ensure_private_dir(self.visual_dir)
            _write_private(
                self.token_status_path(),
                json.dumps(record, ensure_ascii=False).encode("utf-8"),
            )
        return self.status()

    def token_status_path(self) -> Path:
        return self.visual_dir / "token_status.json"

    def _read_token_status(self) -> dict[str, Any]:
        path = self.token_status_path()
        if path.is_symlink() or not path.is_file():
            return {}
        try:
            if path.stat().st_size > 4096:
                return {}
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(record, dict):
            return {}
        checked_at = record.get("checked_at")
        valid = record.get("valid")
        username = record.get("username")
        return {
            "source": record.get("source") if isinstance(record.get("source"), str) else None,
            "token_fingerprint": record.get("token_fingerprint") if isinstance(record.get("token_fingerprint"), str) else "",
            "checked_at": checked_at if isinstance(checked_at, str) else None,
            "valid": valid if isinstance(valid, bool) else None,
            "username": username if isinstance(username, str) else None,
        }

    def generate_image(self, prompt: str) -> dict[str, Any]:
        """Il tool MCP non dispone di un consenso per l'invio a servizi esterni."""
        raise ValidationError(
            "La generazione remota richiede conferma manuale nella sezione Aspetto della dashboard."
        )

    def generate(self, scene: str, *, consent_to_external_processing: bool) -> dict[str, Any]:
        if not isinstance(consent_to_external_processing, bool):
            raise ValidationError("Consenso esterno: atteso true o false.")
        flags = self.config.get("current_state").get("flags", {})
        if not flags.get("visual_generation_enabled") or flags.get("maintenance_mode"):
            raise ValidationError("Generazione disabilitata o modalità manutenzione attiva.")
        if not consent_to_external_processing:
            raise ValidationError("Conferma esplicita dell'invio immagine al provider esterno richiesta.")
        token, _source = resolve_token(self.root)
        if not isinstance(scene, str) or not scene.strip() or len(scene) > MAX_PROMPT_CHARS or "\x00" in scene:
            raise ValidationError(f"La scena deve contenere da 1 a {MAX_PROMPT_CHARS} caratteri.")
        if not self.reference_path.is_file() or self.reference_path.is_symlink():
            raise ValidationError("Carica prima una foto di riferimento nella sezione Aspetto.")
        try:
            if self.reference_path.stat().st_size > MAX_REFERENCE_BYTES:
                raise ConfigError("La reference locale supera il limite di 8 MiB.")
        except OSError as exc:
            raise ConfigError("Impossibile verificare la reference locale.") from exc
        profile = self._profile_document(self.config.get("identity"))
        traits = [
            f"{name}: {profile[name].strip()}"
            for name in PROFILE_FIELDS
            if isinstance(profile.get(name), str) and profile[name].strip()
        ]
        if not traits:
            raise ValidationError("Inserisci almeno una caratteristica fisica nel profilo Aspetto.")
        seed = profile.get("fixed_seed")
        if seed is None:
            seed = secrets.randbelow(MAX_SEED + 1)
        prompt = (
            "Use the person in the reference image as the same character. Preserve identity, "
            "face and body traits; change only scene, pose or clothing as requested. "
            "Character traits: " + "; ".join(traits) + ". Scene: " + scene.strip()
        )
        if not self._generation_lock.acquire(blocking=False):
            raise ValidationError("È già in corso una generazione; attendi il risultato prima di riprovare.")
        try:
            raw = self.backend.generate(prompt, self.reference_path, seed, token=token)
            return self._store_output(raw, scene.strip(), seed)
        finally:
            self._generation_lock.release()

    def _store_output(self, raw: bytes, scene: str, seed: int) -> dict[str, Any]:
        try:
            from PIL import Image, UnidentifiedImageError
        except ImportError as exc:
            raise ConfigError("Supporto immagini non installato: esegui pip install 'mnemosine[visual]'.") from exc
        from io import BytesIO
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_GENERATED_BYTES:
            raise ConfigError("Immagine generata assente o superiore a 32 MiB.")
        try:
            with Image.open(BytesIO(raw)) as image:
                if image.format != "PNG" or image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValidationError("L'immagine restituita dal provider non è un PNG valido o supera 20 megapixel.")
                image.load()
                clean = image.convert("RGB")
                output = BytesIO()
                clean.save(output, format="PNG", optimize=True)
                sanitized = output.getvalue()
        except ValidationError:
            raise
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            raise ConfigError("Il provider non ha restituito un PNG valido.") from exc
        image_id = uuid.uuid4().hex
        created_at = datetime.now(UTC).replace(microsecond=0).isoformat()
        metadata = {
            "id": image_id,
            "scene": scene,
            "seed": seed,
            "model": MODEL,
            "created_at": created_at,
            "reference_used": True,
        }
        with _profile_lock(self.root, timeout=30):
            self._ensure_private_dir(self.output_dir)
            image_path = self.output_dir / f"{image_id}.png"
            metadata_path = self.output_dir / f"{image_id}.json"
            _write_private(image_path, sanitized)
            try:
                _write_private(metadata_path, json.dumps(metadata, ensure_ascii=False).encode("utf-8"))
            except BaseException:
                image_path.unlink(missing_ok=True)
                raise
        return {
            "id": image_id,
            "path": f"images/{image_id}.png",
            "scene": scene,
            "seed": seed,
            "created_at": created_at,
            "note": "Generazione completata. Il riferimento aiuta la coerenza ma non garantisce identità perfetta.",
        }

    def image_bytes(self, image_id: str, *, reference: bool = False) -> tuple[bytes, str]:
        if reference:
            path = self.reference_path
            content_type = "image/jpeg"
        else:
            if not _valid_id(image_id):
                raise ValidationError("ID immagine non valido.")
            path = self.output_dir / f"{image_id}.png"
            content_type = "image/png"
        if path.is_symlink() or not path.is_file():
            raise ConfigError("Immagine non disponibile.")
        maximum = MAX_REFERENCE_BYTES if reference else MAX_GENERATED_BYTES
        try:
            if path.stat().st_size > maximum:
                raise ConfigError("Immagine locale oltre il limite consentito.")
            data = path.read_bytes()
        except OSError as exc:
            raise ConfigError("Impossibile leggere l'immagine.") from exc
        if len(data) > maximum:
            raise ConfigError("Immagine locale oltre il limite consentito.")
        return data, content_type

    def _ensure_private_dir(self, path: Path) -> None:
        _ensure_private_dir(path)


def token_path(root: str | Path) -> Path:
    """Percorso del token locale: override esplicito o ``data/secrets/``."""

    override = os.environ.get(TOKEN_FILE_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(root).expanduser().resolve() / "data" / "secrets" / "replicate_token"


def _valid_token(value: str) -> bool:
    return (
        isinstance(value, str)
        and TOKEN_MIN_CHARS <= len(value) <= TOKEN_MAX_CHARS
        and not any(char.isspace() or ord(char) < 33 or ord(char) == 127 for char in value)
    )


def resolve_token(root: str | Path) -> tuple[str, str]:
    """Restituisce il token e la sua origine, senza mai registrarlo.

    Ordine: ``REPLICATE_API_TOKEN`` nell'ambiente, poi il file locale. Un file
    leggibile da altri utenti viene rifiutato invece di essere usato.
    """

    env_token = os.environ.get(TOKEN_ENV, "").strip()
    if env_token:
        if not _valid_token(env_token):
            raise ConfigError("REPLICATE_API_TOKEN non ha un formato valido.")
        return env_token, "environment"
    path = token_path(root)
    if path.is_symlink():
        raise ConfigError("Il file del token Replicate non può essere un symlink.")
    if not path.is_file():
        raise ConfigError(
            "Provider non configurato: esegui `mnemosine-visual-token --root <profilo>` "
            "per salvare il token Replicate in un file locale protetto, oppure imposta "
            f"{TOKEN_ENV} nell'ambiente del server."
        )
    try:
        info = path.stat()
    except OSError as exc:
        raise ConfigError("Impossibile leggere il file del token Replicate.") from exc
    if os.name == "posix" and info.st_mode & 0o077:
        raise ConfigError(
            "Il file del token Replicate è leggibile da altri utenti: esegui "
            f"`chmod 600 {path}` prima di usarlo."
        )
    if info.st_size > TOKEN_MAX_BYTES:
        raise ConfigError("Il file del token Replicate è troppo grande per essere un token.")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError("Impossibile leggere il file del token Replicate.") from exc
    if not value:
        raise ConfigError("Il file del token Replicate è vuoto.")
    if not _valid_token(value):
        raise ConfigError("Il file del token Replicate non contiene un token valido.")
    return value, "file"


def _ensure_private_dir(path: Path) -> None:
    if path.is_symlink():
        raise ConfigError("La cartella dei dati privati non può essere un symlink.")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise ConfigError("Cartella dei dati privati non valida.")
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise ConfigError("Impossibile proteggere la cartella dei dati privati.") from exc


def _is_replicate_delivery(url) -> bool:
    return (
        url.scheme == "https"
        and url.hostname is not None
        and (url.hostname == "replicate.delivery" or url.hostname.endswith(".replicate.delivery"))
        and url.username is None
        and url.password is None
    )


def _valid_id(value: str) -> bool:
    return len(value) == 32 and all(char in "0123456789abcdef" for char in value)


def _write_private(path: Path, data: bytes) -> None:
    if path.is_symlink():
        raise ConfigError("Il percorso immagine non può essere un symlink.")
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = None
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        path.chmod(0o600)
    except OSError as exc:
        raise ConfigError("Impossibile salvare il file immagine in modo sicuro.") from exc
    finally:
        if fd is not None:
            os.close(fd)
        temp.unlink(missing_ok=True)
