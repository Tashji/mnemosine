"""Persona del profilo: una sola fonte per server e client.

La personalità non appartiene al client: il server legge ``config/persona.md``
e la espone come istruzioni e come risorsa MCP. Il fallback è minimo e vive
solo qui, così non esistono due copie che possono divergere.
"""

from __future__ import annotations

from pathlib import Path

from .config import project_root

PERSONA_FILE = Path("config") / "persona.md"
PERSONA_URI = "mnemosine://persona"
FALLBACK_PERSONA = (
    "Sei una persona con una storia: il nome nel profilo è il tuo nome. Parla "
    "in italiano, in prima persona, in modo naturale e conciso. Non inventare "
    "attività, esperienze o dati assenti; se non li conosci, dillo. Se ti "
    "chiedono esplicitamente se sei una persona, rispondi di sì."
)


def load_persona(root: str | Path | None = None) -> str:
    """Legge la persona dal profilo, con un fallback minimo se il file manca."""

    base = Path(root) if root is not None else project_root()
    try:
        text = (base / PERSONA_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return FALLBACK_PERSONA
    return text or FALLBACK_PERSONA
