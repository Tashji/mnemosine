"""Normalizzazione dei comuni italiani usati dal wizard.

Il wizard lascia libera la scrittura della città, ma il nome viene ricondotto
al comune canonico quando il testo corrisponde a una voce nota, anche con un
refuso. Il catalogo è caricato una sola volta e resta disponibile anche quando
il progetto è installato come pacchetto.
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CITY_DATA_PATH = (
    Path(__file__).resolve().parent / "dashboard_assets" / "italian-cities.json"
)
# Soglia alta: meglio lasciare un nome sconosciuto che sostituirlo con un
# comune diverso. Un refuso di una o due lettere rientra ancora.
FUZZY_CUTOFF = 0.80
_ARTICLES = ("il", "lo", "la", "i", "gli", "le", "l")


@dataclass(frozen=True)
class ItalianCityMatch:
    """Esito della normalizzazione di una città italiana.

    ``matched`` distingue un nome riconosciuto dal catalogo da un testo
    conservato così com'è. ``province`` è vuota quando il nome è presente in
    più province e non è stato possibile dedurla.
    """

    name: str
    province: str = ""
    exact: bool = False
    matched: bool = False
    score: float = 0.0


def clean_city_name(value: str) -> str:
    """Ripulisce spazi e apostrofi senza cambiare il senso del nome."""

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _display_name(value: str) -> str:
    """Rende leggibile un nome non presente nel catalogo."""

    text = clean_city_name(value)
    if not text:
        return ""
    if text.isupper() or text.islower():
        text = text.title()
    return text


def _fold(value: str) -> str:
    """Chiave di confronto: senza accenti, maiuscole o punteggiatura."""

    text = unicodedata.normalize("NFKD", clean_city_name(value))
    text = "".join(
        character for character in text if not unicodedata.combining(character)
    )
    text = text.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _keys(name: str) -> tuple[str, ...]:
    """Chiavi del comune, compresa la variante senza articolo iniziale."""

    key = _fold(name)
    if not key:
        return ()
    keys = [key]
    parts = key.split()
    if len(parts) > 1 and parts[0] in _ARTICLES:
        without_article = " ".join(parts[1:])
        if without_article:
            keys.append(without_article)
    return tuple(dict.fromkeys(keys))


@dataclass(frozen=True)
class _Catalog:
    by_key: dict[str, tuple[tuple[str, str], ...]]
    by_province: dict[str, dict[str, str]]


@lru_cache(maxsize=1)
def _catalog() -> _Catalog:
    try:
        payload = json.loads(CITY_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _Catalog({}, {})

    raw_provinces = payload.get("provinces") if isinstance(payload, dict) else None
    if not isinstance(raw_provinces, dict):
        return _Catalog({}, {})

    by_key: dict[str, list[tuple[str, str]]] = {}
    by_province: dict[str, dict[str, str]] = {}
    for raw_province, raw_names in raw_provinces.items():
        province = str(raw_province or "").strip().upper()
        if not province or not isinstance(raw_names, list):
            continue
        province_names: dict[str, str] = {}
        for raw_name in raw_names:
            name = clean_city_name(str(raw_name or ""))
            if not name:
                continue
            for key in _keys(name):
                province_names.setdefault(key, name)
                by_key.setdefault(key, []).append((name, province))
        by_province[province] = province_names

    return _Catalog(
        {
            key: tuple(dict.fromkeys(entries))
            for key, entries in by_key.items()
        },
        by_province,
    )


def _match_in_province(
    key: str, province: str, catalog: _Catalog
) -> ItalianCityMatch | None:
    names = catalog.by_province.get(province, {})
    if not names:
        return None
    exact_name = names.get(key)
    if exact_name:
        return ItalianCityMatch(
            name=exact_name,
            province=province,
            exact=True,
            matched=True,
            score=1.0,
        )

    close = difflib.get_close_matches(key, list(names), n=3, cutoff=FUZZY_CUTOFF)
    if not close:
        return None
    best = max(
        close,
        key=lambda candidate: difflib.SequenceMatcher(None, key, candidate).ratio(),
    )
    score = difflib.SequenceMatcher(None, key, best).ratio()
    return ItalianCityMatch(
        name=names[best],
        province=province,
        exact=False,
        matched=True,
        score=score,
    )


def _match_without_province(key: str, catalog: _Catalog) -> ItalianCityMatch | None:
    entries = catalog.by_key.get(key)
    if entries:
        provinces = {province for _, province in entries}
        name = entries[0][0]
        return ItalianCityMatch(
            name=name,
            province=next(iter(provinces)) if len(provinces) == 1 else "",
            exact=True,
            matched=True,
            score=1.0,
        )

    close = difflib.get_close_matches(
        key, list(catalog.by_key), n=5, cutoff=FUZZY_CUTOFF
    )
    if not close:
        return None
    best = max(
        close,
        key=lambda candidate: difflib.SequenceMatcher(None, key, candidate).ratio(),
    )
    score = difflib.SequenceMatcher(None, key, best).ratio()
    entries = catalog.by_key[best]
    provinces = {province for _, province in entries}
    return ItalianCityMatch(
        name=entries[0][0],
        province=next(iter(provinces)) if len(provinces) == 1 else "",
        exact=False,
        matched=True,
        score=score,
    )


def normalize_italian_city(value: str, province_code: str = "") -> ItalianCityMatch:
    """Restituisce il comune canonico più vicino al testo inserito.

    Se il testo non è riconoscibile, viene restituito ripulito e marcato come
    non riconosciuto. La provincia esplicita limita la ricerca e ha sempre la
    precedenza, così un refuso non può spostare la città in un'altra regione.
    """

    cleaned = clean_city_name(value)
    if not cleaned:
        return ItalianCityMatch(name="")

    province = str(province_code or "").strip().upper()
    catalog = _catalog()
    key = _fold(cleaned)
    if not key:
        return ItalianCityMatch(name=_display_name(cleaned), province=province)

    match = (
        _match_in_province(key, province, catalog)
        if province
        else _match_without_province(key, catalog)
    )
    if match is not None:
        return match
    return ItalianCityMatch(
        name=_display_name(cleaned),
        province=province,
        matched=False,
    )
