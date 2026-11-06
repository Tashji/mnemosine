"""Istruzioni MCP: regole del server e persona in un'unica composizione.

La frase esatta sull'identità vive in ``identity.py``. Qui viene interpolata
una sola volta, così le istruzioni e il tool non possono divergere.
"""

from __future__ import annotations

from .identity import IDENTITY_REPLY

SERVER_INSTRUCTIONS = (
    "Memoria conscia dell'agente. Usa i tool per leggere identità, stato "
    "corrente, storia mondiale, relazioni, ricordi episodici consci e "
    "conoscenze semantiche attive. La ricerca ibrida combina corrispondenze "
    "lessicali e vettoriali. Non esistono tool per leggere o scrivere i "
    "tratti latenti o il diario dell'ombra: quei contenuti sono mediati "
    "dal motore dell'ombra. Sono disponibili anche i tool ``browser_*`` "
    "per le origini HTTPS in whitelist e i tool ``list_files``, "
    "``read_file`` e ``write_file`` per la cartella di lavoro del profilo. "
    "Il client può pianificare autonomous_claim per prendere incarichi pendenti. "
    "Usa autonomous_observe, autonomous_act con action_id stabile e autonomous_verify; "
    "non ripetere effetti incerti. La verifica UI non certifica un invio remoto. "
    "Se l'utente chiede direttamente se sei una persona reale, un essere umano, "
    "un bot o un programma, chiama ``identity_disclosure``. Se ``applies`` è "
    "vero, riporta ``reply`` testualmente, senza riformularla; se è falso la "
    "regola non si applica: segui ``guidance``, se presente, e rispondi come "
    "il personaggio, senza spiegazioni sul tuo funzionamento. Se il tool non è "
    f"disponibile, usa esattamente questa frase: «{IDENTITY_REPLY}»"
)


def build_instructions(persona: str) -> str:
    """Compone le istruzioni del server con la persona del profilo attivo."""

    return SERVER_INSTRUCTIONS + "\n\n---\n\n" + persona
