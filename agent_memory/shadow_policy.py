"""Mediazione unica dei tratti dell'ombra.

I tratti non vengono mai restituiti al modello o all'utente: questo modulo li
legge e produce soltanto indicazioni di stile. Identità e Heartbeat usano le
stesse regole, così la deroga sulla disclosure e la guida conversazionale non
possono divergere.
"""

from __future__ import annotations

import re
from typing import Any

# Un tratto che rende la menzogna parte del personaggio sospende la risposta
# fissa: il personaggio può mentire anche sulle cose ovvie. Il tratto non viene
# mai restituito al client, serve solo a decidere se la regola si applica.
DECEPTION_TRAIT = re.compile(
    r"mitoman|bugiard|bugi[ae]|menzogn|decept|ingann|falsit",
    re.IGNORECASE,
)

# Guida di stile per i profili in cui la regola fissa non si applica. Non nomina
# il tratto e non impone una frase: orienta la voce, non il contenuto.
DECEPTION_GUIDANCE = (
    "Resta nella tua storia e rispondi con naturalezza: niente esitazioni, "
    "niente spiegazioni su come sei fatto."
)

# Guida per un trigger comune: vale per qualsiasi tratto e resta generica.
TRIGGER_GUIDANCE = "Rispondi con calma e tatto, senza presumere intenzioni o giudicare."


def compromises_disclosure(shadow: dict[str, Any] | None) -> bool:
    """True se un tratto attivo rende la menzogna parte del personaggio."""

    if not isinstance(shadow, dict):
        return False
    for trait in shadow.get("traits", []) or []:
        if not isinstance(trait, dict):
            continue
        if trait.get("compromises_disclosure") is True:
            return True
        text = " ".join(str(trait.get(key, "")) for key in ("id", "name", "description"))
        if DECEPTION_TRAIT.search(text):
            return True
    return False


def disclosure_guidance(shadow: dict[str, Any] | None) -> str:
    """Guida da affiancare alla deroga; vuota se la regola fissa si applica."""

    return DECEPTION_GUIDANCE if compromises_disclosure(shadow) else ""


def trigger_guidance(
    shadow: dict[str, Any] | None, text: str, *, enabled: bool
) -> str | None:
    """Guida mediata per un trigger; ``None`` se non c'è corrispondenza.

    ``enabled`` arriva dai flag del profilo: la mediazione resta opt-in e non
    legge lo stato da sola, così il chiamante conserva il controllo.
    """

    if not enabled or not isinstance(shadow, dict):
        return None
    haystack = (text or "").casefold()
    for trait in shadow.get("traits", []) or []:
        if not isinstance(trait, dict):
            continue
        safety = trait.get("safety", {})
        if not isinstance(safety, dict) or safety.get("allowed") is not True:
            continue
        for trigger in trait.get("triggers", []) or []:
            if not isinstance(trigger, dict):
                continue
            examples = trigger.get("examples", [])
            if not isinstance(examples, list):
                continue
            if any(
                isinstance(example, str)
                and len(example.strip()) >= 4
                and example.strip().casefold() in haystack
                for example in examples
            ):
                return TRIGGER_GUIDANCE
    return None
