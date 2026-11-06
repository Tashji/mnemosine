"""Risposte deterministiche sull'identità del personaggio.

L'identità vive nel profilo, ma non può dipendere dal modello: questo modulo è
la fonte unica usata dal server MCP e dai client che lo interrogano.

La decisione se la regola si applica non viene presa qui: il chiamante passa
``compromises`` e la guida mediata. Così questo modulo resta puro e non conosce
i tratti dell'ombra.
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


def direct_identity_reply(text: str) -> str | None:
    """Restituisce la risposta fissa per una domanda diretta sull'identità.

    Il modello può esitare o negare di essere una persona: qui l'identità non
    dipende dal campionamento né dal modello scelto.
    """

    return IDENTITY_REPLY if IDENTITY_QUESTION.search(text or "") else None


def disclosure_payload(
    text: str, *, compromises: bool = False, guidance: str = ""
) -> dict[str, Any]:
    """Risposta del tool: la regola fissa vale solo se il profilo non mente.

    ``compromises`` e ``guidance`` arrivano dalla politica dell'ombra; questo
    modulo non legge i tratti e non sa quale tratto li abbia prodotti.
    """

    if not IDENTITY_QUESTION.search(text or ""):
        return {
            "direct_identity_question": False,
            "reply": "",
            "applies": False,
            "guidance": "",
        }
    if compromises:
        return {
            "direct_identity_question": True,
            "reply": "",
            "applies": False,
            "guidance": guidance,
        }
    return {
        "direct_identity_question": True,
        "reply": IDENTITY_REPLY,
        "applies": True,
        "guidance": "",
    }
