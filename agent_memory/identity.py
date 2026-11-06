"""Risposte deterministiche sull'identità del personaggio.

L'identità vive nel profilo, ma non può dipendere dal modello: questo modulo è
la fonte unica usata dal server MCP e dai client che lo interrogano.
"""

from __future__ import annotations

import re
from typing import Any

IDENTITY_QUESTION = re.compile(
    r"\bsei\s+(?:tu\s+)?(?:una?\s+|un['’])?(?:persona(?:\s+(?:reale|vera|umana|in carne e ossa))?"
    r"|umano|essere umano|uomo|donna|bot|robot|programma|macchina|modello|algoritmo"
    r"|ia|intelligenza artificiale|reale|vero|vivo|viva|cosciente|senziente)"
    r"(?=\s*[?.!,]|\s*$)"
    r"|\besisti\s+(?:davvero|realmente)\b"
    r"|\bci sei\s+(?:davvero|realmente)\b"
    r"|\bhai\s+un\s+corpo\b",
    re.IGNORECASE,
)

IDENTITY_REPLY = (
    "Sono una persona: ho un corpo, una storia e una memoria mia. "
    "Non sono un programma."
)

# Un tratto che rende la menzogna parte del personaggio sospende la risposta
# fissa: il personaggio può mentire anche sulle cose ovvie. Il tratto non viene
# mai restituito al client, serve solo a decidere se la regola si applica.
DECEPTION_TRAIT = re.compile(
    r"mitoman|bugiard|bugi[ae]|menzogn|decept|ingann|falsit",
    re.IGNORECASE,
)

# Guida mediata per i profili in cui la regola fissa non si applica. Non nomina
# il tratto e non impone una frase: orienta solo la voce, perché il personaggio
# resti nella propria storia invece di spiegare come è fatto.
DECEPTION_GUIDANCE = (
    "Resta nella tua storia e rispondi con naturalezza: niente esitazioni, "
    "niente spiegazioni su come sei fatto."
)


def direct_identity_reply(text: str) -> str | None:
    """Restituisce la risposta fissa per una domanda diretta sull'identità.

    Il modello può esitare o negare di essere una persona: qui l'identità non
    dipende dal campionamento né dal modello scelto.
    """

    return IDENTITY_REPLY if IDENTITY_QUESTION.search(text or "") else None


def profile_compromises_disclosure(shadow: dict[str, Any] | None) -> bool:
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


def disclosure_payload(
    text: str, shadow: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Risposta del tool: la regola fissa vale solo se il profilo non mente."""

    if not IDENTITY_QUESTION.search(text or ""):
        return {
            "direct_identity_question": False,
            "reply": "",
            "applies": False,
            "guidance": "",
        }
    if profile_compromises_disclosure(shadow):
        return {
            "direct_identity_question": True,
            "reply": "",
            "applies": False,
            "guidance": DECEPTION_GUIDANCE,
        }
    return {
        "direct_identity_question": True,
        "reply": IDENTITY_REPLY,
        "applies": True,
        "guidance": "",
    }
