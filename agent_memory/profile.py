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
from collections.abc import Iterator, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, BinaryIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from .config import CONFIG_FILENAMES, ConfigStore
from .db import DATABASE_TABLES
from .errors import AgentMemoryError, ConfigError, ValidationError
from .migrations import (
    DATABASE_SCHEMAS,
    JSON_DOCUMENTS,
    ensure_database_schema,
    format_version,
)
from .setup_options import (
    country_code,
    italian_province,
    italian_region,
    language_name,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Gli schemi vivono dentro il pacchetto: così restano raggiungibili anche
# quando il progetto è installato con pip e non esiste una working directory.
PACKAGE_DIR = Path(__file__).resolve().parent
SCHEMA_DIR = PACKAGE_DIR / "schemas"
DATABASE_FILES = tuple(f"{database}.db" for database in DATABASE_SCHEMAS)
def _schema_version(name: str) -> str:
    """Versione del documento, dalla tabella unica in ``migrations.py``.

    I documenti non sono tutti alla stessa versione: ``current_state`` può
    essere più avanti degli altri, e un profilo nuovo deve nascere già
    allineato invece di migrare al primo avvio.
    """

    return format_version(JSON_DOCUMENTS[name])


PROFILE_STATE_MISSING = "missing"
PROFILE_STATE_INCOMPLETE = "incomplete"
PROFILE_STATE_READY = "ready"
TRAIT_LABELS = {
    "introversion_extraversion": ("introversione", "estroversione"),
    "empathy_detachment": ("empatia", "distacco"),
    "logic_impulsivity": ("logica", "impulsività"),
    "resilience_sensitivity": ("resilienza", "sensibilità"),
}
MAX_SETUP_ROWS = 20
MAX_TEXT_FIELD = 2000
LANGUAGE_CODE_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})?$")
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


def profile_state(root: str | Path) -> str:
    """Classifica una directory come profilo assente, incompleto o pronto."""

    target = Path(root).expanduser().resolve()
    config_paths = [
        target / "config" / name for name in CONFIG_FILENAMES.values()
    ]
    database_paths = [target / "data" / name for name in DATABASE_FILES]
    paths = config_paths + database_paths
    if not any(path.exists() for path in paths):
        return PROFILE_STATE_MISSING
    if all(path.is_file() for path in paths):
        return PROFILE_STATE_READY
    return PROFILE_STATE_INCOMPLETE


def _form_text(values: Mapping[str, Any], name: str, default: str = "") -> str:
    raw = values.get(name, default)
    if raw is None:
        return default
    if not isinstance(raw, str):
        raw = str(raw)
    return raw.strip()


def _form_limited(
    values: Mapping[str, Any],
    name: str,
    label: str,
    *,
    default: str = "",
    maximum: int = MAX_TEXT_FIELD,
) -> str:
    value = _form_text(values, name, default)
    if len(value) > maximum:
        raise ValidationError(
            f"{label}: massimo {maximum} caratteri."
        )
    return value


def _form_int(
    values: Mapping[str, Any],
    name: str,
    label: str,
    *,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    raw = _form_text(values, name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValidationError(f"{label}: atteso un numero intero.") from exc
    if minimum is not None and value < minimum:
        raise ValidationError(f"{label}: valore minimo {minimum}.")
    if maximum is not None and value > maximum:
        raise ValidationError(f"{label}: valore massimo {maximum}.")
    return value


def _form_date(
    values: Mapping[str, Any],
    name: str,
    label: str,
) -> date | None:
    raw = _form_text(values, name)
    if not raw:
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError(
            f"{label}: data non valida {raw!r}. Usa YYYY-MM-DD."
        ) from exc
    if parsed > date.today():
        raise ValidationError(f"{label}: la data non può essere nel futuro.")
    return parsed


def _form_entries(values: Mapping[str, Any], name: str) -> list[str]:
    raw = _form_text(values, name)
    if not raw:
        return []
    entries: list[str] = []
    for item in re.split(r"[,;\n]+", raw):
        cleaned = item.strip()
        if cleaned and cleaned not in entries:
            entries.append(cleaned)
    return entries


def _row_indexes(values: Mapping[str, Any], prefix: str) -> list[int]:
    pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)_")
    indexes = {
        int(match.group(1))
        for key in values
        if (match := pattern.match(str(key)))
    }
    if len(indexes) > MAX_SETUP_ROWS:
        raise ValidationError(
            f"Troppe righe in {prefix}: massimo {MAX_SETUP_ROWS}."
        )
    return sorted(indexes)


def _has_row_values(
    values: Mapping[str, Any], prefix: str, index: int, fields: tuple[str, ...]
) -> bool:
    return any(
        _form_text(values, f"{prefix}_{index}_{field}")
        for field in fields
    )


def _parse_languages(values: Mapping[str, Any]) -> list[dict[str, str]]:
    indexes = _row_indexes(values, "language")
    if not indexes:
        return [{"code": "it", "name": "italiano", "level": "madrelingua"}]
    languages: list[dict[str, str]] = []
    seen: set[str] = set()
    for index in indexes:
        if not _has_row_values(
            values, "language", index, ("code", "name", "level")
        ):
            continue
        code = _form_text(values, f"language_{index}_code").lower()
        name = _form_text(values, f"language_{index}_name")
        if code and not name:
            name = (
                language_name(code)
                or language_name(code.split("-", 1)[0])
                or ""
            )
        level = _form_text(values, f"language_{index}_level")
        if not code or not name:
            raise ValidationError(
                "Lingue: scegli una lingua dall'elenco per ogni riga inserita."
            )
        if not LANGUAGE_CODE_RE.fullmatch(code):
            raise ValidationError(
                f"Lingue: codice non valido {code!r}. Usa un codice ISO come it, en o pt-BR."
            )
        if code in seen:
            raise ValidationError(f"Lingue: codice duplicato {code!r}.")
        seen.add(code)
        languages.append(
            {
                "code": code,
                "name": name,
                "level": level or "non specificato",
            }
        )
    if not languages:
        raise ValidationError("Inserisci almeno una lingua.")
    return languages


def _parse_parents(values: Mapping[str, Any]) -> list[dict[str, str]] | None:
    indexes = _row_indexes(values, "parent")
    if not indexes:
        return None
    parents: list[dict[str, str]] = []
    for index in indexes:
        fields = ("first_name", "last_name", "relation", "birth_date", "occupation", "notes")
        if not _has_row_values(values, "parent", index, fields):
            continue
        first_name = _form_text(values, f"parent_{index}_first_name")
        if not first_name:
            raise ValidationError("Familiari: il nome del genitore è obbligatorio.")
        parent = {
            "first_name": first_name,
            "last_name": _form_text(values, f"parent_{index}_last_name"),
            "relation": _form_text(values, f"parent_{index}_relation") or "genitore",
            "occupation": _form_text(values, f"parent_{index}_occupation"),
            "notes": _form_limited(
                values, f"parent_{index}_notes", "Note familiare"
            ),
        }
        birth_date = _form_date(
            values, f"parent_{index}_birth_date", "Data di nascita del genitore"
        )
        if birth_date is not None:
            parent["birth_date"] = birth_date.isoformat()
        parents.append(parent)
    return parents


def _parse_siblings(values: Mapping[str, Any]) -> list[dict[str, str]] | None:
    indexes = _row_indexes(values, "sibling")
    if not indexes:
        return None
    siblings: list[dict[str, str]] = []
    for index in indexes:
        fields = ("first_name", "last_name", "relation", "birth_date", "occupation", "notes")
        if not _has_row_values(values, "sibling", index, fields):
            continue
        first_name = _form_text(values, f"sibling_{index}_first_name")
        if not first_name:
            raise ValidationError("Familiari: il nome del fratello o sorella è obbligatorio.")
        sibling = {
            "first_name": first_name,
            "last_name": _form_text(values, f"sibling_{index}_last_name"),
            "relation": _form_text(values, f"sibling_{index}_relation") or "sorella",
            "occupation": _form_text(values, f"sibling_{index}_occupation"),
            "notes": _form_limited(
                values, f"sibling_{index}_notes", "Note familiare"
            ),
        }
        birth_date = _form_date(
            values, f"sibling_{index}_birth_date", "Data di nascita del fratello o sorella"
        )
        if birth_date is not None:
            sibling["birth_date"] = birth_date.isoformat()
        siblings.append(sibling)
    return siblings


def _parse_people(values: Mapping[str, Any]) -> list[dict[str, Any]]:
    indexes = _row_indexes(values, "person")
    people: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index in indexes:
        fields = ("display_name", "relation", "trust", "intimacy", "notes")
        if not _has_row_values(values, "person", index, fields):
            continue
        display_name = _form_text(values, f"person_{index}_display_name")
        if not display_name:
            raise ValidationError("Persone importanti: il nome è obbligatorio.")
        user_id = _slug(display_name)
        if user_id in seen:
            raise ValidationError(
                f"Persone importanti: nome duplicato {display_name!r}."
            )
        seen.add(user_id)
        trust = _form_int(
            values,
            f"person_{index}_trust",
            "Fiducia",
            default=50,
            minimum=0,
            maximum=100,
        )
        intimacy = _form_int(
            values,
            f"person_{index}_intimacy",
            "Intimità",
            default=50,
            minimum=0,
            maximum=100,
        )
        people.append(
            {
                "user_id": f"user_{user_id}",
                "display_name": display_name,
                "full_name": display_name,
                "relation": _form_text(values, f"person_{index}_relation") or "persona importante",
                "trust": trust,
                "intimacy": intimacy,
                "notes": _form_limited(
                    values, f"person_{index}_notes", "Note sulla persona"
                ),
            }
        )
    return people


def _parse_education(values: Mapping[str, Any]) -> list[dict[str, Any]]:
    indexes = _row_indexes(values, "education")
    education: list[dict[str, Any]] = []
    for index in indexes:
        fields = ("institution", "qualification", "start_year", "end_year", "location")
        if not _has_row_values(values, "education", index, fields):
            continue
        institution = _form_text(values, f"education_{index}_institution")
        qualification = _form_text(values, f"education_{index}_qualification")
        if not institution or not qualification:
            raise ValidationError(
                "Formazione: istituto e titolo sono obbligatori per ogni riga."
            )
        start_year = _form_int(
            values,
            f"education_{index}_start_year",
            "Anno di inizio formazione",
            minimum=1900,
            maximum=date.today().year + 1,
        )
        end_year = _form_int(
            values,
            f"education_{index}_end_year",
            "Anno di fine formazione",
            minimum=1900,
            maximum=date.today().year + 5,
        )
        if start_year is None:
            raise ValidationError(
                "Formazione: l'anno di inizio è obbligatorio."
            )
        if end_year is not None and end_year < start_year:
            raise ValidationError(
                "Formazione: l'anno di fine non può precedere quello di inizio."
            )
        education.append(
            {
                "institution": institution,
                "qualification": qualification,
                "start_year": start_year,
                "end_year": end_year,
                "location": _form_text(values, f"education_{index}_location"),
            }
        )
    return education


def _parse_work(values: Mapping[str, Any]) -> list[dict[str, Any]]:
    indexes = _row_indexes(values, "work")
    work: list[dict[str, Any]] = []
    for index in indexes:
        fields = ("role", "organization", "start_year", "end_year", "location")
        if not _has_row_values(values, "work", index, fields):
            continue
        role = _form_text(values, f"work_{index}_role")
        organization = _form_text(values, f"work_{index}_organization")
        if not role or not organization:
            raise ValidationError(
                "Lavoro: ruolo e organizzazione sono obbligatori per ogni riga."
            )
        start_year = _form_int(
            values,
            f"work_{index}_start_year",
            "Anno di inizio lavoro",
            minimum=1900,
            maximum=date.today().year + 1,
        )
        end_year = _form_int(
            values,
            f"work_{index}_end_year",
            "Anno di fine lavoro",
            minimum=1900,
            maximum=date.today().year + 5,
        )
        if start_year is None:
            raise ValidationError(
                "Lavoro: l'anno di inizio è obbligatorio."
            )
        if end_year is not None and end_year < start_year:
            raise ValidationError(
                "Lavoro: l'anno di fine non può precedere quello di inizio."
            )
        work.append(
            {
                "role": role,
                "organization": organization,
                "start_year": start_year,
                "end_year": end_year,
                "location": _form_text(values, f"work_{index}_location"),
            }
        )
    return work


def _geo_fields(
    values: Mapping[str, Any],
    *,
    prefix: str,
    country_code: str,
    province_label: str,
    region_label: str,
) -> tuple[str, str]:
    """Valida provincia e regione italiane e ne garantisce la coerenza."""

    raw_province = _form_limited(
        values, f"{prefix}_province", province_label, maximum=80
    )
    raw_region = _form_limited(
        values, f"{prefix}_region", region_label, maximum=80
    )
    province = raw_province.strip().upper()
    if province:
        entry = italian_province(province)
        if entry is None:
            raise ValidationError(
                f"{province_label}: scegli una provincia dall'elenco."
            )
        name, province_region = entry
        if country_code != "IT":
            raise ValidationError(
                f"{province_label}: la provincia è italiana, ma il paese "
                "selezionato non è l'Italia. Scegli l'Italia o lascia vuoto "
                "il campo."
            )
        if raw_region:
            region = italian_region(raw_region)
            if region is None:
                raise ValidationError(
                    f"{region_label}: scegli una regione dall'elenco."
                )
            if region != province_region:
                raise ValidationError(
                    f"{province_label}: {name} ({province}) non appartiene "
                    f"alla regione {region}. Scegli la regione corretta o "
                    "lascia vuoto il campo."
                )
        return province, province_region

    if raw_region:
        region = italian_region(raw_region)
        if region is None:
            raise ValidationError(
                f"{region_label}: scegli una regione dall'elenco."
            )
        if country_code != "IT":
            raise ValidationError(
                f"{region_label}: la regione è italiana, ma il paese "
                "selezionato non è l'Italia. Scegli l'Italia o lascia vuoto "
                "il campo."
            )
        return "", region

    return "", ""


def _foreign_geo_fields(
    values: Mapping[str, Any],
    *,
    prefix: str,
    province_label: str,
    region_label: str,
) -> tuple[str, str]:
    """Accetta regione e provincia locali per una località estera.

    I due campi dedicati hanno la precedenza; i vecchi campi restano letti
    per compatibilità con i moduli inviati prima dell'introduzione dei campi
    esteri. I valori italiani restano riservati all'Italia.
    """

    foreign_province = _form_limited(
        values, f"{prefix}_province_foreign", province_label, maximum=120
    )
    foreign_region = _form_limited(
        values, f"{prefix}_region_foreign", region_label, maximum=120
    )
    if foreign_province or foreign_region:
        raw_province, raw_region = foreign_province, foreign_region
    else:
        raw_province = _form_limited(
            values, f"{prefix}_province", province_label, maximum=120
        )
        raw_region = _form_limited(
            values, f"{prefix}_region", region_label, maximum=120
        )
    province = raw_province.strip()
    region = raw_region.strip()
    if province and italian_province(province) is not None:
        raise ValidationError(
            f"{province_label}: per un paese estero indica la provincia "
            "locale, non una sigla italiana."
        )
    if region and italian_region(region) is not None:
        raise ValidationError(
            f"{region_label}: per un paese estero indica la regione locale, "
            "non una regione italiana."
        )
    return province, region


def build_profile_args(values: Mapping[str, Any]) -> argparse.Namespace:
    """Costruisce e valida gli argomenti di ``profile init`` da un modulo web."""

    first_name = _form_limited(values, "first_name", "Nome", maximum=80)
    last_name = _form_limited(values, "last_name", "Cognome", maximum=80)
    birth_city = _form_limited(values, "birth_city", "Città di nascita", maximum=120)
    birth_date_text = _form_text(values, "birth_date")
    required = [
        label
        for value, label in (
            (first_name, "nome"),
            (last_name, "cognome"),
            (birth_date_text, "data di nascita"),
            (birth_city, "città di nascita"),
        )
        if not value
    ]
    if required:
        raise ValidationError(
            "Compila i campi obbligatori: " + ", ".join(required) + "."
        )

    try:
        birth_date = _birth_date(birth_date_text)
    except argparse.ArgumentTypeError as exc:
        raise ValidationError(str(exc)) from exc
    timezone_name = _form_text(values, "timezone", "Europe/Rome") or "Europe/Rome"
    try:
        timezone_name = _timezone(timezone_name)
    except argparse.ArgumentTypeError as exc:
        raise ValidationError(str(exc)) from exc

    birth_country = (
        _form_limited(values, "birth_country", "Paese di nascita", maximum=80)
        or "Italia"
    )
    birth_country_code = country_code(birth_country)
    if birth_country_code is None:
        raise ValidationError(
            "Paese di nascita: scegli un paese dall'elenco."
        )
    residence_country = (
        _form_limited(values, "residence_country", "Paese di residenza", maximum=80)
        or birth_country
    )
    residence_country_code = country_code(residence_country)
    if residence_country_code is None:
        raise ValidationError(
            "Paese di residenza: scegli un paese dall'elenco."
        )
    if birth_country_code == "IT":
        birth_province, birth_region = _geo_fields(
            values,
            prefix="birth",
            country_code=birth_country_code,
            province_label="Provincia di nascita",
            region_label="Regione di nascita",
        )
    else:
        birth_province, birth_region = _foreign_geo_fields(
            values,
            prefix="birth",
            province_label="Provincia di nascita",
            region_label="Regione di nascita",
        )
    if residence_country_code == "IT":
        residence_province, residence_region = _geo_fields(
            values,
            prefix="residence",
            country_code=residence_country_code,
            province_label="Provincia di residenza",
            region_label="Regione di residenza",
        )
    else:
        residence_province, residence_region = _foreign_geo_fields(
            values,
            prefix="residence",
            province_label="Provincia di residenza",
            region_label="Regione di residenza",
        )
    residence_since = _form_date(
        values, "residence_since", "Data di inizio residenza"
    )
    if residence_since is not None and residence_since < birth_date:
        raise ValidationError(
            "Residenza: la data di inizio non può precedere la nascita."
        )

    parents = _parse_parents(values)
    siblings = _parse_siblings(values)
    if parents is not None:
        for parent in parents:
            born = parent.get("birth_date")
            if born and date.fromisoformat(str(born)) >= birth_date:
                raise ValidationError(
                    "Familiari: la data di nascita del genitore deve precedere quella del profilo."
                )
    education = _parse_education(values)
    work = _parse_work(values)
    for entry in education:
        if int(entry["start_year"]) < birth_date.year:
            raise ValidationError(
                "Formazione: l'anno di inizio non può precedere la nascita."
            )
    for entry in work:
        if int(entry["start_year"]) < birth_date.year:
            raise ValidationError(
                "Lavoro: l'anno di inizio non può precedere la nascita."
            )

    visual_profile: dict[str, Any] = {}
    for field in ("body", "face", "hair", "eyes", "distinctive_marks", "style"):
        visual_profile[field] = _form_limited(
            values, f"visual_{field}", f"Aspetto: {field}", maximum=2000
        )
    raw_seed = _form_text(values, "visual_fixed_seed")
    if raw_seed:
        try:
            fixed_seed = int(raw_seed)
        except ValueError as exc:
            raise ValidationError("Seed visivo: inserisci un numero intero.") from exc
        if not 0 <= fixed_seed <= 2**31 - 1:
            raise ValidationError("Seed visivo: usa un intero tra 0 e 2147483647.")
    else:
        fixed_seed = None
    visual_profile["fixed_seed"] = fixed_seed

    traits: dict[str, dict[str, Any]] = {}
    for trait_id, (left, right) in TRAIT_LABELS.items():
        value = _form_int(
            values,
            f"trait_{trait_id}",
            f"Tratto {left}/{right}",
            default=5,
            minimum=1,
            maximum=10,
        )
        note = _form_limited(
            values, f"trait_{trait_id}_note", f"Descrizione del tratto {left}/{right}", maximum=500
        )
        traits[trait_id] = {
            "value": value,
            "left": left,
            "right": right,
            "description": note
            or (
                f"Equilibrato tra {left} e {right}."
                if value == 5
                else f"Più orientato verso {left if value < 5 else right}."
            ),
        }

    return argparse.Namespace(
        first_name=first_name,
        last_name=last_name,
        birth_date=birth_date,
        birth_city=birth_city,
        birth_province=birth_province,
        birth_region=birth_region,
        birth_country=birth_country,
        birth_country_code=birth_country_code,
        citizenship=_form_limited(
            values, "citizenship", "Cittadinanza", maximum=80
        )
        or "italiana",
        residence_city=_form_limited(
            values, "residence_city", "Città di residenza", maximum=120
        )
        or None,
        residence_province=residence_province or None,
        residence_region=residence_region or None,
        residence_country=residence_country,
        residence_since=residence_since,
        timezone=timezone_name,
        gender=_form_limited(values, "gender", "Genere", maximum=60)
        or "non specificato",
        pronouns=_form_limited(values, "pronouns", "Pronomi", maximum=60),
        identity_id=None,
        father_name=_form_text(values, "father_name") or None,
        mother_name=_form_text(values, "mother_name") or None,
        sibling_name=_form_text(values, "sibling_name") or None,
        sibling_relation=_form_text(values, "sibling_relation", "sorella")
        or "sorella",
        parents=parents,
        siblings=siblings,
        languages=_parse_languages(values),
        traits=traits,
        interests=_form_entries(values, "interests"),
        values=_form_entries(values, "values"),
        habits=_form_entries(values, "habits"),
        family_notes=_form_limited(
            values, "family_notes", "Note sulla famiglia", maximum=1000
        ),
        biography_summary=_form_limited(
            values, "biography_summary", "Sintesi biografica", maximum=MAX_TEXT_FIELD
        ),
        current_situation=_form_limited(
            values, "current_situation", "Situazione attuale", maximum=MAX_TEXT_FIELD
        ),
        education=education,
        work=work,
        social_people=_parse_people(values),
        visual_profile=visual_profile,
    )


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
    birth_province = args.birth_province.strip()
    birth_region = args.birth_region.strip()
    if not birth_region and birth_province:
        entry = italian_province(birth_province)
        if entry is not None:
            birth_region = entry[1]
    birth_place = {
        "city": args.birth_city.strip(),
        "province": birth_province,
        "region": birth_region,
        "country": args.birth_country.strip(),
        "country_code": args.birth_country_code.strip().upper(),
    }
    residence_country = (
        getattr(args, "residence_country", None) or args.birth_country.strip()
    )
    # Eredita il luogo di nascita solo quando la residenza non è specificata
    # e resta nello stesso paese: una città diversa non deve ereditare la
    # provincia di nascita.
    inherit_birth_place = (
        not (args.residence_city or "").strip()
        and residence_country.casefold() == args.birth_country.strip().casefold()
    )
    residence_province = (args.residence_province or "").strip()
    if not residence_province and inherit_birth_place:
        residence_province = birth_province
    residence_region = (args.residence_region or "").strip()
    if not residence_region and inherit_birth_place:
        residence_region = birth_region
    if not residence_region and residence_province:
        entry = italian_province(residence_province)
        if entry is not None:
            residence_region = entry[1]
    residence = {
        "city": (args.residence_city or args.birth_city).strip(),
        "province": residence_province,
        "region": residence_region,
        "country": residence_country,
    }
    residence_since = getattr(args, "residence_since", None)
    if residence_since is None and not hasattr(args, "parents"):
        residence_since = today
    if residence_since is not None:
        residence["since"] = (
            residence_since.isoformat()
            if isinstance(residence_since, date)
            else str(residence_since)
        )

    rich_parents = getattr(args, "parents", None)
    if rich_parents is None:
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
    else:
        parents = []
        for index, parent in enumerate(rich_parents, start=1):
            item: dict[str, Any] = {
                "id": f"fam_parent_{index}",
                "first_name": str(parent.get("first_name", "")).strip(),
                "last_name": str(parent.get("last_name", "")).strip(),
                "relation": str(parent.get("relation", "genitore")).strip()
                or "genitore",
            }
            for key in ("birth_date", "occupation", "notes"):
                value = str(parent.get(key, "")).strip()
                if value:
                    item[key] = value
            parents.append(item)

    rich_siblings = getattr(args, "siblings", None)
    if rich_siblings is None:
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
    else:
        siblings = []
        for index, sibling in enumerate(rich_siblings, start=1):
            item = {
                "id": f"fam_sibling_{index}",
                "first_name": str(sibling.get("first_name", "")).strip(),
                "last_name": str(sibling.get("last_name", "")).strip(),
                "relation": str(sibling.get("relation", "sorella")).strip()
                or "sorella",
            }
            for key in ("birth_date", "occupation", "notes"):
                value = str(sibling.get(key, "")).strip()
                if value:
                    item[key] = value
            siblings.append(item)

    rich_traits = getattr(args, "traits", None)
    if rich_traits is None:
        traits = {
            trait_id: {
                "value": 5,
                "left": left,
                "right": right,
                "description": "Valore iniziale neutro; modificabile nel profilo.",
            }
            for trait_id, (left, right) in TRAIT_LABELS.items()
        }
    else:
        traits = rich_traits

    languages = getattr(args, "languages", None) or [
        {
            "code": "it",
            "name": "italiano",
            "level": "madrelingua",
        }
    ]
    family_notes = getattr(args, "family_notes", None)
    if family_notes is None:
        family_notes = "Profilo iniziale: completa i familiari in identity.json."

    identity: dict[str, Any] = {
        "schema_version": _schema_version("identity"),
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
            "languages": languages,
            "residence": residence,
            "timezone": args.timezone,
        },
        "family": {
            "parents": parents,
            "siblings": siblings,
            "family_notes": family_notes,
        },
        "visual_profile": {
            "schema_version": 1,
            **getattr(args, "visual_profile", {}),
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
            "summary": getattr(args, "biography_summary", ""),
            "current_situation": getattr(args, "current_situation", ""),
            "education": getattr(args, "education", []),
            "work": getattr(args, "work", []),
            "notes": "Profilo creato dalla prima configurazione; la biografia è generata proceduralmente.",
        },
    }
    for key in ("interests", "values", "habits"):
        entries = getattr(args, key, None)
        if entries:
            identity[key] = entries
    return identity


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
            "schema_version": _schema_version("shadow_traits"),
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
            "schema_version": _schema_version("current_state"),
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
            # Gli stessi nomi di ``heartbeat.HEARTBEAT_DEFAULTS``: un profilo
            # nuovo nasce già pronto per il ciclo autonomo, senza migrazione
            # al primo battito.
            "heartbeat": {
                "last_run_at": None,
                "next_run_at": None,
                "last_decision": "profilo_creato",
                "spontaneous_message_pending": False,
                "interval_minutes": 30,
                "cooldown_minutes": 180,
                "max_impulses_per_day": 3,
                "pending_impulse": None,
                "last_trigger": None,
                "last_impulse_at": None,
                "last_interaction_at": None,
                "impulses_today": {"date": None, "count": 0},
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
            "schema_version": _schema_version("world_history"),
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
            "schema_version": _schema_version("social_graph"),
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
            "people": getattr(args, "social_people", None) or [],
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


def _create_database(path: Path, database: str) -> None:
    """Crea il database dallo schema del pacchetto e lo marcia all'ultima versione.

    Un profilo nuovo parte già migrato, non alla versione degli schemi: così non
    deve recuperare i passi intermedi al primo avvio.
    """

    schema_name = DATABASE_SCHEMAS[database][0]
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(path)
        try:
            conn.executescript(_schema_path(schema_name).read_text(encoding="utf-8"))
            ensure_database_schema(conn, database, path, DATABASE_TABLES[database])
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error, AgentMemoryError) as exc:
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
        for database in DATABASE_SCHEMAS:
            _create_database(staging / "data" / f"{database}.db", database)
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
