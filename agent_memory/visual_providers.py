"""Provider di generazione immagini configurabili dall'interfaccia.

Mnemosine non impone un servizio: il profilo può usare Replicate, un'API
compatibile OpenAI oppure un server locale con API A1111 (Forge, Forge Neo,
reForge, A1111). Il provider scelto e l'endpoint vivono in
``config/integrations.json``; le chiavi restano in ``data/secrets/``.

Nessun provider viene contattato durante la configurazione: la verifica è
un'azione esplicita e non genera immagini.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import secrets
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .errors import ConfigError, ValidationError
from .migrations import migrate_integrations, write_json_atomic

INTEGRATIONS_FILENAME = "integrations.json"
PROVIDERS = ("replicate", "openai", "local")
DEFAULT_REPLICATE_LABEL = "Replicate · FLUX.1 Kontext Pro"
DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-image-1"
DEFAULT_LOCAL_BASE = "http://127.0.0.1:7860"
MAX_GENERATED_BYTES = 32 * 1024 * 1024
MAX_PROMPT_CHARS = 1000
TOKEN_MIN_CHARS = 8
TOKEN_MAX_CHARS = 500
REQUEST_TIMEOUT = 180
CHECK_TIMEOUT = 20


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise ConfigError(
            "Il provider ha richiesto un redirect inatteso durante la richiesta."
        )


def _valid_secret(value: str) -> bool:
    return (
        isinstance(value, str)
        and TOKEN_MIN_CHARS <= len(value) <= TOKEN_MAX_CHARS
        and not any(
            char.isspace() or ord(char) < 33 or ord(char) == 127 for char in value
        )
    )


def api_key_path(root: str | Path) -> Path:
    return (
        Path(root).expanduser().resolve()
        / "data"
        / "secrets"
        / "visual_api_key"
    )


def _write_secret(path: Path, value: str) -> None:
    from .visual import _ensure_private_dir, _write_private

    _ensure_private_dir(path.parent)
    _write_private(path, value.encode("utf-8") + b"\n")


def resolve_api_key(root: str | Path) -> str:
    path = api_key_path(root)
    if path.is_symlink():
        raise ConfigError("Il file della chiave API non può essere un symlink.")
    if not path.is_file():
        return ""
    try:
        if path.stat().st_size > 4096:
            raise ConfigError("Il file della chiave API è troppo grande.")
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigError("Impossibile leggere la chiave API locale.") from exc
    if not value:
        return ""
    if not _valid_secret(value):
        raise ConfigError("La chiave API salvata non ha un formato valido.")
    return value


def _path(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / "config" / INTEGRATIONS_FILENAME


def _read_document(root: str | Path) -> dict[str, Any]:
    path = _path(root)
    if not path.is_file():
        return {"schema_version": 2, "browser": {"interaction_origins": []}}
    if path.is_symlink():
        raise ConfigError("config/integrations.json non può essere un symlink.")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError("config/integrations.json non è leggibile.") from exc
    if not isinstance(document, dict):
        raise ConfigError("config/integrations.json: atteso un oggetto JSON.")
    return document


def _normalize_base(value: str, *, local_only: bool) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Indica l'URL di base del provider immagini.")
    value = value.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValidationError(
            "URL del provider non valido: usa http:// o https:// con un host."
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValidationError(
            "L'URL del provider non può contenere credenziali, query o frammenti."
        )
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if local_only and not loopback:
        raise ValidationError(
            "Il provider locale deve ascoltare su questo computer "
            "(127.0.0.1, localhost o ::1)."
        )
    if not loopback and parsed.scheme != "https":
        raise ValidationError(
            "Un provider su un altro computer deve usare HTTPS."
        )
    return value


def load_provider(root: str | Path) -> dict[str, Any]:
    """Configurazione effettiva; senza sezione ``visual`` vale Replicate."""

    document = _read_document(root)
    raw = document.get("visual")
    if not isinstance(raw, dict):
        raw = {}
    provider = raw.get("provider")
    if provider not in PROVIDERS:
        provider = "replicate"
    base_url = raw.get("base_url")
    base_url = base_url.strip() if isinstance(base_url, str) else ""
    model = raw.get("model")
    model = model.strip() if isinstance(model, str) else ""
    if provider == "openai":
        base_url = base_url or DEFAULT_OPENAI_BASE
        model = model or DEFAULT_OPENAI_MODEL
    elif provider == "local":
        base_url = base_url or DEFAULT_LOCAL_BASE
    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "external": provider != "local",
    }


def provider_label(config: dict[str, Any]) -> str:
    provider = config.get("provider")
    if provider == "local":
        model = config.get("model") or "checkpoint attivo"
        return f"Locale · Forge/A1111 ({model})"
    if provider == "openai":
        return f"API compatibile OpenAI · {config.get('model') or DEFAULT_OPENAI_MODEL}"
    return DEFAULT_REPLICATE_LABEL


def configured_provider(root: str | Path, config: dict[str, Any] | None = None) -> bool:
    config = config or load_provider(root)
    provider = config["provider"]
    if provider == "replicate":
        from .visual import resolve_token

        try:
            resolve_token(root)
        except ConfigError:
            return False
        return True
    if provider == "openai":
        if not config.get("base_url") or not config.get("model"):
            return False
        try:
            return bool(resolve_api_key(root))
        except ConfigError:
            return False
    return bool(config.get("base_url"))


def provider_source(root: str | Path, config: dict[str, Any] | None = None) -> str | None:
    config = config or load_provider(root)
    if config["provider"] == "replicate":
        from .visual import resolve_token

        try:
            _token, source = resolve_token(root)
        except ConfigError:
            return None
        return source
    if config["provider"] == "openai":
        try:
            return "file" if resolve_api_key(root) else None
        except ConfigError:
            return None
    return "config" if config.get("base_url") else None


def config_fingerprint(root: str | Path, config: dict[str, Any] | None = None) -> str:
    config = config or load_provider(root)
    secret = ""
    if config["provider"] == "replicate":
        from .visual import resolve_token

        try:
            secret, _source = resolve_token(root)
        except ConfigError:
            secret = ""
    elif config["provider"] == "openai":
        try:
            secret = resolve_api_key(root)
        except ConfigError:
            secret = ""
    payload = json.dumps(
        {
            "provider": config["provider"],
            "base_url": config.get("base_url", ""),
            "model": config.get("model", ""),
        },
        sort_keys=True,
    )
    return hashlib.sha256((payload + "\0" + secret).encode("utf-8")).hexdigest()


def _clean_extra(value: Any, label: str, maximum: int = 200) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise ValidationError(f"{label}: massimo {maximum} caratteri.")
    return value.strip()


def validate_provider(
    root: str | Path,
    payload: dict[str, Any],
    *,
    allow_existing_secret: bool = True,
) -> dict[str, Any]:
    """Valida senza scrivere nulla; usato anche dal wizard prima del profilo."""

    if not isinstance(payload, dict):
        raise ValidationError("Configurazione del provider non valida.")
    provider = payload.get("provider")
    if provider not in PROVIDERS:
        raise ValidationError(
            "Provider non riconosciuto: scegli Replicate, API compatibile OpenAI o locale."
        )
    root = Path(root).expanduser().resolve()
    base_url = ""
    model = ""
    secret = payload.get("api_key", "")
    if secret is None:
        secret = ""
    if not isinstance(secret, str):
        raise ValidationError("Chiave API non valida.")
    secret = secret.strip()

    if provider == "local":
        base_url = _normalize_base(
            _clean_extra(payload.get("base_url"), "URL del provider locale", 500),
            local_only=True,
        )
        model = _clean_extra(payload.get("model"), "Modello locale", 200)
    elif provider == "openai":
        base_url = _normalize_base(
            _clean_extra(
                payload.get("base_url") or DEFAULT_OPENAI_BASE,
                "URL del provider",
                500,
            ),
            local_only=False,
        )
        model = _clean_extra(
            payload.get("model") or DEFAULT_OPENAI_MODEL,
            "Modello",
            200,
        )
        if not secret:
            try:
                existing = resolve_api_key(root)
            except ConfigError:
                existing = ""
            if not allow_existing_secret:
                raise ValidationError("Inserisci la chiave API del provider.")
            if not existing:
                raise ValidationError(
                    "Inserisci la chiave API: non ce n'è una salvata da riusare."
                )
        elif not _valid_secret(secret):
            raise ValidationError(
                "Chiave API non valida: da 8 a 500 caratteri senza spazi."
            )
    else:
        if not secret:
            if not allow_existing_secret:
                raise ValidationError("Inserisci il token Replicate.")
            from .visual import resolve_token

            try:
                resolve_token(root)
            except ConfigError:
                raise ValidationError("Inserisci il token Replicate.") from None
        elif not _valid_secret(secret):
            raise ValidationError(
                "Token Replicate non valido: da 8 a 500 caratteri senza spazi."
            )

    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key": secret,
    }


def save_provider(
    root: str | Path,
    payload: dict[str, Any],
    *,
    allow_existing_secret: bool = True,
) -> dict[str, Any]:
    """Salva provider, endpoint e chiave; la generazione resta disattivata."""

    cleaned = validate_provider(
        root, payload, allow_existing_secret=allow_existing_secret
    )
    root = Path(root).expanduser().resolve()
    provider = cleaned["provider"]
    base_url = cleaned["base_url"]
    model = cleaned["model"]
    secret = cleaned["api_key"]

    document = _read_document(root)
    document = migrate_integrations(document)
    visual: dict[str, Any] = {"provider": provider}
    if provider == "openai":
        visual.update({"base_url": base_url, "model": model})
    elif provider == "local":
        visual.update({"base_url": base_url, "model": model})
    document["visual"] = visual
    write_json_atomic(_path(root), document)

    if provider == "replicate" and secret:
        from .visual_token import store_token

        store_token(root, secret)
    elif provider == "openai" and secret:
        _write_secret(api_key_path(root), secret)
    return load_provider(root)


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "mnemosine-visual",
    }


def _read_json_response(response, label: str) -> dict[str, Any]:
    length = response.headers.get("Content-Length")
    if length is not None:
        try:
            if int(length) > MAX_GENERATED_BYTES:
                raise ConfigError(f"{label}: risposta troppo grande.")
        except ValueError:
            pass
    body = response.read(MAX_GENERATED_BYTES + 1)
    if len(body) > MAX_GENERATED_BYTES:
        raise ConfigError(f"{label}: risposta troppo grande.")
    try:
        payload = json.loads(body or b"{}")
    except ValueError as exc:
        raise ConfigError(f"{label}: risposta JSON non valida.") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{label}: risposta non valida.")
    return payload


def _multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]):
    boundary = "----mnemosine" + secrets.token_hex(16)
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    for name, (filename, data, content_type) in files.items():
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _same_origin(url: str, base_url: str) -> bool:
    parsed = urlparse(url)
    base = urlparse(base_url)
    return (
        parsed.scheme == base.scheme
        and parsed.hostname == base.hostname
        and (parsed.port or (443 if parsed.scheme == "https" else 80))
        == (base.port or (443 if base.scheme == "https" else 80))
        and parsed.username is None
        and parsed.password is None
    )


def _download_same_origin(url: str, base_url: str, label: str) -> bytes:
    if not _same_origin(url, base_url):
        raise ConfigError(f"{label}: URL immagine non autorizzato.")
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(url, timeout=REQUEST_TIMEOUT) as response:
            if response.status != 200:
                raise ConfigError(f"{label}: immagine non disponibile.")
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > MAX_GENERATED_BYTES:
                raise ConfigError(f"{label}: immagine troppo grande.")
            data = response.read(MAX_GENERATED_BYTES + 1)
    except ConfigError:
        raise
    except (OSError, URLError, ValueError) as exc:
        raise ConfigError(f"{label}: impossibile scaricare l'immagine.") from exc
    if len(data) > MAX_GENERATED_BYTES:
        raise ConfigError(f"{label}: immagine troppo grande.")
    return data


def _decode_image(payload: dict[str, Any], base_url: str, label: str) -> bytes:
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ConfigError(f"{label}: nessuna immagine restituita.")
    item = data[0]
    encoded = item.get("b64_json")
    if isinstance(encoded, str) and encoded:
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ConfigError(f"{label}: immagine base64 non valida.") from exc
        if len(raw) > MAX_GENERATED_BYTES:
            raise ConfigError(f"{label}: immagine troppo grande.")
        return raw
    url = item.get("url")
    if isinstance(url, str) and url:
        return _download_same_origin(url, base_url, label)
    raise ConfigError(f"{label}: risposta senza immagine.")


class OpenAIImagesBackend:
    """API ``/images/edits`` compatibile OpenAI, con la reference come input."""

    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def generate(
        self, prompt: str, image_path: Path, seed: int, *, token: str
    ) -> bytes:
        if not token:
            raise ConfigError("Chiave API mancante per il provider OpenAI-compatibile.")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise ConfigError("Prompt troppo lungo per il provider.")
        try:
            data = image_path.read_bytes()
        except OSError as exc:
            raise ConfigError("Impossibile leggere la reference locale.") from exc
        content_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        body, content_type_header = _multipart(
            {
                "model": self.model,
                "prompt": prompt,
                "size": "1024x1024",
                "n": "1",
            },
            {"image": (image_path.name, data, content_type)},
        )
        request = Request(
            f"{self.base_url}/images/edits",
            data=body,
            headers={
                **_auth_headers(token),
                "Content-Type": content_type_header,
            },
            method="POST",
        )
        opener = build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
                if response.status < 200 or response.status >= 300:
                    raise ConfigError(
                        f"Il provider ha risposto HTTP {response.status}."
                    )
                payload = _read_json_response(response, "Provider immagini")
        except HTTPError as exc:
            raise ConfigError(
                f"Il provider ha rifiutato la richiesta (HTTP {exc.code}). "
                "Controlla URL, chiave e nome del modello."
            ) from exc
        except ConfigError:
            raise
        except (OSError, URLError, ValueError) as exc:
            raise ConfigError(
                "Provider immagini non raggiungibile: controlla l'URL e la connessione."
            ) from exc
        return _decode_image(payload, self.base_url, "Provider immagini")


class LocalA1111Backend:
    """Server locale con API A1111: Forge, Forge Neo, reForge o A1111."""

    def __init__(self, base_url: str, model: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def generate(
        self, prompt: str, image_path: Path, seed: int, *, token: str
    ) -> bytes:
        try:
            reference = base64.b64encode(image_path.read_bytes()).decode("ascii")
        except OSError as exc:
            raise ConfigError("Impossibile leggere la reference locale.") from exc
        payload: dict[str, Any] = {
            "init_images": [reference],
            "prompt": prompt,
            "negative_prompt": "blurry, low quality, distorted face, extra limbs",
            "seed": seed,
            "steps": 28,
            "cfg_scale": 5.5,
            "width": 768,
            "height": 768,
            "denoising_strength": 0.55,
            "save_images": False,
        }
        if self.model:
            payload["override_settings"] = {"sd_model_checkpoint": self.model}
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}/sdapi/v1/img2img",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "mnemosine-visual",
            },
            method="POST",
        )
        opener = build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
                result = _read_json_response(response, "Server locale")
        except HTTPError as exc:
            raise ConfigError(
                f"Il server locale ha risposto HTTP {exc.code}. "
                "Avvialo con --api e controlla l'URL."
            ) from exc
        except ConfigError:
            raise
        except (OSError, URLError, ValueError) as exc:
            raise ConfigError(
                "Server locale non raggiungibile: avvia Forge/A1111 con --api "
                f"su {self.base_url} e riprova."
            ) from exc
        images = result.get("images")
        if not isinstance(images, list) or not images or not isinstance(images[0], str):
            raise ConfigError("Il server locale non ha restituito un'immagine.")
        encoded = images[0]
        if encoded.startswith("data:image/"):
            try:
                encoded = encoded.split(",", 1)[1]
            except IndexError as exc:
                raise ConfigError("Immagine locale non valida.") from exc
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ConfigError("Immagine locale non valida.") from exc
        if len(raw) > MAX_GENERATED_BYTES:
            raise ConfigError("Immagine locale troppo grande.")
        return raw


def build_backend(config: dict[str, Any]):
    provider = config["provider"]
    if provider == "openai":
        return OpenAIImagesBackend(config["base_url"], config["model"])
    if provider == "local":
        return LocalA1111Backend(config["base_url"], config.get("model", ""))
    from .visual import ReplicateBackend

    return ReplicateBackend()


def check_connection(config: dict[str, Any], token: str) -> dict[str, Any]:
    """Verifica endpoint e credenziali senza generare immagini."""

    provider = config["provider"]
    if provider == "openai":
        if not token:
            return {"valid": False, "detail": "Chiave API non configurata."}
        request = Request(
            f"{config['base_url']}/models",
            headers=_auth_headers(token),
        )
        try:
            with build_opener(_NoRedirect()).open(
                request, timeout=CHECK_TIMEOUT
            ) as response:
                return {
                    "valid": response.status == 200,
                    "detail": f"HTTP {response.status}",
                }
        except HTTPError as exc:
            return {"valid": False, "detail": f"HTTP {exc.code}"}
        except (OSError, URLError, ValueError) as exc:
            return {"valid": False, "detail": "Endpoint non raggiungibile."}
    if provider == "local":
        request = Request(
            f"{config['base_url']}/sdapi/v1/options",
            headers={"Accept": "application/json", "User-Agent": "mnemosine-visual"},
        )
        try:
            with build_opener(_NoRedirect()).open(
                request, timeout=CHECK_TIMEOUT
            ) as response:
                payload = _read_json_response(response, "Server locale")
        except HTTPError as exc:
            return {"valid": False, "detail": f"HTTP {exc.code}"}
        except ConfigError as exc:
            return {"valid": False, "detail": str(exc)}
        except (OSError, URLError, ValueError):
            return {"valid": False, "detail": "Server locale non raggiungibile."}
        model = payload.get("sd_model_checkpoint")
        return {
            "valid": True,
            "detail": model if isinstance(model, str) else "Server locale raggiungibile.",
        }
    return {"valid": False, "detail": "Usa la verifica Replicate dedicata."}


def provider_from_form(form: dict[str, str]) -> dict[str, Any] | None:
    """Estrae la configurazione opzionale dal modulo del wizard."""

    provider = form.get("image_provider", "none")
    if provider in {"", "none"}:
        return None
    return {
        "provider": provider,
        "base_url": form.get("image_base_url", ""),
        "model": form.get("image_model", ""),
        "api_key": form.get("image_api_key", ""),
    }
