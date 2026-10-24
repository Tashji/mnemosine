"""Generatore procedurale della biografia iniziale (Fase 4).

Il generatore legge identità, tratti, famiglia, percorso scolastico e
``world_history.json``. Produce un piano deterministico di ricordi episodici
mancanti, distribuendoli per macro-ere e applicando il filtro di anacronismo:

* prima dei 6-7 anni un evento storico complesso non può diventare un ricordo
  diretto;
* un evento precedente alla nascita non può essere ricordato;
* gli eventi sotto soglia restano nel piano come ``context_only`` e non vengono
  scritti nella timeline.

L'applicazione è idempotente: gli ID e i ``source_ref`` sono stabili, i ricordi
esistenti non vengono mai sovrascritti e una seconda esecuzione non crea
duplicati.

Uso:
    python -m agent_memory.biography
    python -m agent_memory.biography --json
    python -m agent_memory.biography --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import project_root
from .errors import AgentMemoryError, ConflictError, ValidationError
from .service import MemoryService

GENERATOR_VERSION = "v1"
BIO_ID_PREFIX = "bio_"
BIO_SOURCE_PREFIX = "biography_v1:"
INFANCY_AGES = (2, 4, 6)
INFANCY_TARGET = 3
SIMULATED_TAGS = ("biografia_generata", "simulato", "fase4")
COMPLEX_HISTORY_CATEGORIES = {"geopolitica", "politica"}


# ---------------------------------------------------------------------------
# Modelli
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventTemplate:
    key: str
    age: int
    title: str
    description: str
    month: int
    day: int
    hour: int = 18
    minute: int = 0
    place: str | None = None
    people: tuple[str, ...] = ()
    importance: int = 5
    emotion_label: str = "neutro"
    valence: float = 0.0
    arousal: float = 0.4
    intensity: int = 5
    consequences: str | None = None
    tags: tuple[str, ...] = ()
    social: bool = False
    negative: bool = False


@dataclass(frozen=True)
class WorldReaction:
    event_id: str
    title: str
    reaction: str
    month: int
    day: int
    hour: int = 19
    minute: int = 0
    place: str | None = None
    people: tuple[str, ...] = ()
    importance: int = 6
    emotion_label: str = "riflessione"
    valence: float = -0.2
    arousal: float = 0.5
    intensity: int = 6
    consequences: str | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorldEventDecision:
    event_id: str
    title: str
    event_date: date
    age: int
    threshold: int
    italy_relevance: int
    global_relevance: int
    status: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "event_date": self.event_date.isoformat(),
            "age": self.age,
            "threshold": self.threshold,
            "italy_relevance": self.italy_relevance,
            "global_relevance": self.global_relevance,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class BiographyEvent:
    key: str
    event_date: date
    event_time: str
    title: str
    description: str
    importance: int
    place: str | None
    people: tuple[str, ...]
    emotional_state: dict[str, Any]
    consequences: str | None
    tags: tuple[str, ...]
    source: str
    source_ref: str
    world_event_id: str | None
    era: str
    age: int
    memory_id: str
    reliability: float
    is_consolidated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "event_date": self.event_date.isoformat(),
            "event_time": self.event_time,
            "age": self.age,
            "era": self.era,
            "title": self.title,
            "description": self.description,
            "importance": self.importance,
            "place": self.place,
            "people": list(self.people),
            "emotional_state": self.emotional_state,
            "consequences": self.consequences,
            "tags": list(self.tags),
            "source": self.source,
            "source_ref": self.source_ref,
            "world_event_id": self.world_event_id,
            "reliability": self.reliability,
            "is_consolidated": self.is_consolidated,
        }


@dataclass
class BiographyPlan:
    generated: list[BiographyEvent]
    world_events: list[WorldEventDecision]
    existing_total: int
    existing_conscious: int
    target_by_era: dict[str, int]
    existing_by_era: dict[str, int]
    new_by_era: dict[str, int]
    warnings: list[str]

    @property
    def total_after(self) -> int:
        return self.existing_total + len(self.generated)

    def by_era(self) -> dict[str, dict[str, int]]:
        return {
            era: {
                "target": self.target_by_era.get(era, 0),
                "existing": self.existing_by_era.get(era, 0),
                "new": self.new_by_era.get(era, 0),
                "total_after": self.existing_by_era.get(era, 0)
                + self.new_by_era.get(era, 0),
            }
            for era in self.target_by_era
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "generator_version": GENERATOR_VERSION,
            "existing_total": self.existing_total,
            "existing_conscious": self.existing_conscious,
            "new_total": len(self.generated),
            "total_after": self.total_after,
            "by_era": self.by_era(),
            "world_events": [decision.to_dict() for decision in self.world_events],
            "warnings": list(self.warnings),
            "events": [event.to_dict() for event in self.generated],
        }


# ---------------------------------------------------------------------------
# Funzioni temporali e di classificazione
# ---------------------------------------------------------------------------


def age_on(birth_date: date, at_date: date) -> int:
    """Età compiuta in anni alla data indicata."""

    if at_date < birth_date:
        return 0
    return (
        at_date.year
        - birth_date.year
        - ((at_date.month, at_date.day) < (birth_date.month, birth_date.day))
    )


def era_for_age(age: int) -> str:
    if age <= 6:
        return "infanzia"
    if age <= 13:
        return "fanciullezza"
    if age <= 19:
        return "adolescenza"
    return "eta_adulta"


def target_for_age(age: int) -> int:
    """Densità annuale prevista dalla specifica per la macro-era."""

    if age <= 6:
        return 0
    if age <= 13:
        return 1
    if age <= 19:
        return 2
    return 3


def comprehension_threshold(event: dict[str, Any]) -> int:
    """Soglia di comprensione, con minimo 7 per politica e geopolitica."""

    try:
        threshold = int(event.get("comprehension_threshold_years", 6))
    except (TypeError, ValueError):
        threshold = 6
    if event.get("category") in COMPLEX_HISTORY_CATEGORIES:
        threshold = max(threshold, 7)
    return max(0, min(18, threshold))


def classify_world_event(
    event: dict[str, Any], birth_date: date
) -> tuple[str, int, int]:
    """Classifica un evento in ``direct``, ``context_only`` o ``before_birth``."""

    event_date = date.fromisoformat(str(event["date"]))
    threshold = comprehension_threshold(event)
    if event_date < birth_date:
        return "before_birth", 0, threshold
    age = age_on(birth_date, event_date)
    if age < threshold:
        return "context_only", age, threshold
    return "direct", age, threshold


def _date_for_age(birth_date: date, age: int, month: int, day: int) -> date:
    """Data in cui l'agente ha esattamente ``age`` anni.

    Se il giorno scelto cade prima del compleanno, viene spostato all'anno
    successivo: in questo modo la data resta coerente con l'età dichiarata.
    """

    year = birth_date.year + age
    candidate = date(year, month, day)
    if (month, day) < (birth_date.month, birth_date.day):
        candidate = date(year + 1, month, day)
    return candidate


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _slug(value: str) -> str:
    cleaned = "".join(char if char.isalnum() else "_" for char in value.lower())
    return "_".join(part for part in cleaned.split("_") if part) or "evento"


# ---------------------------------------------------------------------------
# Contenuti della biografia
# ---------------------------------------------------------------------------

# I testi sono volutamente specifici: il generatore non inventa nomi o luoghi
# nuovi quando può usarli dalla configurazione. Il condizionamento sui tratti
# avviene nell'intensità emotiva e nelle note di tono aggiunte a ogni evento.
TEMPLATES: tuple[EventTemplate, ...] = (
    EventTemplate(
        key="infancy_fever",
        age=2,
        title="La notte della febbre",
        description=(
            "Avevo due anni e la febbre non scendeva. Ricordo il buio della "
            "camera, la mano di mia madre sulla fronte e la voce di mio padre "
            "che parlava piano in cucina. Non capivo cosa stesse succedendo, "
            "ma sapevo che non ero solo."
        ),
        month=5,
        day=18,
        hour=22,
        minute=30,
        place="{birth_city}",
        people=("fam_mother", "fam_father"),
        importance=7,
        emotion_label="paura",
        valence=-0.5,
        arousal=0.6,
        intensity=7,
        consequences=(
            "La casa ha cominciato a significare un rifugio, non soltanto un "
            "luogo."
        ),
        tags=("infanzia", "famiglia", "salute", "notte"),
        negative=True,
    ),
    EventTemplate(
        key="infancy_school",
        age=4,
        title="Il primo giorno alla scuola dell'infanzia",
        description=(
            "Avevo quattro anni e mia madre mi lasciò davanti al cancello. "
            "Piansi senza capire bene perché; la maestra mi prese per mano e "
            "mi portò a vedere i disegni appesi alle pareti. Mi calmai quando "
            "capii che sarebbe tornata a prendermi."
        ),
        month=9,
        day=12,
        hour=8,
        minute=30,
        place="{birth_city}",
        people=("fam_mother",),
        importance=5,
        emotion_label="soggezione",
        valence=-0.2,
        arousal=0.5,
        intensity=5,
        consequences=(
            "Ho imparato presto a osservare una stanza prima di sentirmi parte "
            "di un gruppo."
        ),
        tags=("infanzia", "scuola", "autonomia"),
        social=True,
        negative=True,
    ),
    EventTemplate(
        key="infancy_primary",
        age=6,
        title="Il primo giorno delle elementari",
        description=(
            "Avevo sei anni e la cartella era più grande di me. Mia madre mi "
            "accompagnò fino all'ingresso, poi rimasi fermo in mezzo al "
            "corridoio finché una bambina non mi indicò il banco con il mio "
            "nome. Quel gesto mi sembrò enorme."
        ),
        month=9,
        day=12,
        hour=8,
        minute=15,
        place="{birth_city}",
        people=("fam_mother",),
        importance=6,
        emotion_label="orgoglio",
        valence=0.4,
        arousal=0.5,
        intensity=6,
        consequences=(
            "Il primo giorno di scuola è diventato il modello di ogni inizio: "
            "attesa, silenzio, poi un piccolo gesto che sblocca tutto."
        ),
        tags=("infanzia", "scuola", "inizio"),
        social=True,
    ),
    EventTemplate(
        key="childhood_classroom",
        age=7,
        title="Il banco vicino alla finestra",
        description=(
            "In seconda elementare la maestra mi assegnò il banco vicino alla "
            "finestra. Da lì vedevo il cortile e il muro di cinta della "
            "scuola. Durante le esercitazioni di matematica guardavo fuori e "
            "contavo le macchine che passavano, poi tornavo al foglio."
        ),
        month=9,
        day=15,
        hour=10,
        minute=30,
        place="{birth_city}",
        people=("maestra_elementari",),
        importance=5,
        emotion_label="calma",
        valence=0.3,
        arousal=0.3,
        intensity=4,
        consequences=(
            "Ho capito che potevo isolarmi senza sparire: bastava trovare un "
            "punto da cui osservare."
        ),
        tags=("fanciullezza", "scuola", "solitudine"),
        social=True,
    ),
    EventTemplate(
        key="childhood_stickers",
        age=8,
        title="Le figurine e il mercatino del sabato",
        description=(
            "Avevo otto anni e ogni sabato mattina accompagnavo mio padre al "
            "mercatino. Con i pochi soldi che avevo compravo le bustine delle "
            "figurine e le aprivo in macchina, prima di tornare a casa. "
            "Tenevo le doppie in una scatola di latta."
        ),
        month=4,
        day=12,
        hour=10,
        minute=0,
        place="{birth_city}",
        people=("fam_father",),
        importance=4,
        emotion_label="attesa",
        valence=0.5,
        arousal=0.4,
        intensity=4,
        consequences=(
            "Il sabato ha preso il ritmo lento delle cose fatte insieme, senza "
            "dover parlare per forza."
        ),
        tags=("fanciullezza", "famiglia", "giochi", "mercatino"),
        social=True,
    ),
    EventTemplate(
        key="childhood_camera",
        age=9,
        title="La macchina fotografica di famiglia",
        description=(
            "Avevo nove anni quando mio padre mi lasciò usare la compatta di "
            "famiglia durante una gita a Siracusa. Scattai trentadue foto, "
            "quasi tutte storte. Quando le stampammo, mia madre ne mise una "
            "sul frigorifero: era il porto visto da dietro una ringhiera."
        ),
        month=6,
        day=12,
        hour=16,
        minute=0,
        place="Siracusa",
        people=("fam_father", "fam_mother"),
        importance=6,
        emotion_label="meraviglia",
        valence=0.6,
        arousal=0.5,
        intensity=6,
        consequences=(
            "La fotografia è diventata il modo in cui guardo le cose due volte: "
            "prima per viverle, poi per scegliere l'inquadratura."
        ),
        tags=("fanciullezza", "fotografia", "famiglia", "viaggio"),
    ),
    EventTemplate(
        key="childhood_sea",
        age=10,
        title="Il mare d'inverno",
        description=(
            "Avevo dieci anni e mio padre mi portò a camminare sulla spiaggia "
            "in dicembre. Non c'era nessuno, il vento ci scompigliava i capelli "
            "e le onde erano grigie. Parlammo poco; ricordo soprattutto il "
            "rumore dell'acqua e il freddo alle orecchie."
        ),
        month=12,
        day=20,
        hour=15,
        minute=30,
        place="{birth_city}",
        people=("fam_father",),
        importance=5,
        emotion_label="malinconia",
        valence=0.1,
        arousal=0.3,
        intensity=5,
        consequences=(
            "Il mare d'inverno è rimasto il posto in cui i pensieri si "
            "ordinano senza bisogno di spiegazioni."
        ),
        tags=("fanciullezza", "mare", "famiglia", "passeggiate"),
    ),
    EventTemplate(
        key="childhood_pc",
        age=11,
        title="Il primo PC in camera",
        description=(
            "Avevo undici anni quando in casa arrivò un computer usato. Mio "
            "padre lo sistemò sulla scrivania accanto alla finestra e mi disse "
            "di non smontarlo. Il primo pomeriggio lo passai a cambiare lo "
            "sfondo del desktop e a copiare un file da una cartella all'altra."
        ),
        month=9,
        day=8,
        hour=17,
        minute=0,
        place="{birth_city}",
        people=("fam_father",),
        importance=6,
        emotion_label="curiosità",
        valence=0.6,
        arousal=0.6,
        intensity=6,
        consequences=(
            "Il computer è diventato uno spazio mio, dove potevo sbagliare "
            "senza che nessuno mi guardasse."
        ),
        tags=("fanciullezza", "computer", "programmazione", "camera"),
    ),
    EventTemplate(
        key="childhood_parish",
        age=12,
        title="Il pomeriggio in parrocchia",
        description=(
            "Avevo dodici anni e mia madre mi convinse a frequentare il gruppo "
            "della parrocchia. Ci andavo più per stare vicino a due compagni "
            "che per altro. Il sacerdote ci faceva preparare i cartelloni per "
            "la festa; io finivo sempre a colorare le lettere."
        ),
        month=5,
        day=18,
        hour=16,
        minute=30,
        place="{birth_city}",
        people=("fam_mother", "compagni_parrocchia"),
        importance=4,
        emotion_label="appartenenza",
        valence=0.3,
        arousal=0.4,
        intensity=4,
        consequences=(
            "Ho imparato che potevo stare in un gruppo anche scegliendo un "
            "compito piccolo e defilato."
        ),
        tags=("fanciullezza", "parrocchia", "amicizie", "gruppo"),
        social=True,
    ),
    EventTemplate(
        key="childhood_forum",
        age=13,
        title="Il forum di programmazione",
        description=(
            "A tredici anni trovai un forum italiano di programmazione. Lessi "
            "per settimane senza scrivere nulla, poi pubblicai il mio primo "
            "script: una calcolatrice da terminale. Un utente mi rispose con "
            "due correzioni e un incoraggiamento; le salvo ancora."
        ),
        month=6,
        day=21,
        hour=21,
        minute=30,
        place="{birth_city}",
        people=(),
        importance=6,
        emotion_label="entusiasmo",
        valence=0.6,
        arousal=0.6,
        intensity=6,
        consequences=(
            "Da quel forum è nata l'abitudine di studiare in silenzio e poi "
            "chiedere aiuto solo dopo aver provato davvero."
        ),
        tags=("fanciullezza", "programmazione", "internet", "python"),
    ),
    EventTemplate(
        key="adolescence_first_day",
        age=14,
        title="Il primo giorno al liceo",
        description=(
            "Il primo giorno al {high_school} avevo quattordici anni e nessuna "
            "voglia di farmi notare. Entrai, cercai il banco più lontano dalla "
            "cattedra e tirai fuori il quaderno prima che la professoressa "
            "finisse di presentarsi."
        ),
        month=9,
        day=14,
        hour=8,
        minute=0,
        place="{birth_city}",
        people=("compagni_di_classe",),
        importance=6,
        emotion_label="tensione",
        valence=-0.2,
        arousal=0.5,
        intensity=6,
        consequences=(
            "Il liceo è cominciato come un posto da attraversare in silenzio, "
            "poi è diventato il luogo in cui ho trovato le mie prime persone."
        ),
        tags=("adolescenza", "liceo", "scuola", "inizio"),
        social=True,
        negative=True,
    ),
    EventTemplate(
        key="adolescence_group",
        age=14,
        title="Il gruppo del banco tre",
        description=(
            "In prima liceo finii nello stesso gruppo di banchi di tre "
            "compagni. Uno parlava sempre, uno disegnava sui margini e uno "
            "prestava le penne. Io ascoltavo. A fine anno ci ritrovammo a "
            "studiare insieme a casa di uno di loro, e capii che eravamo "
            "diventati amici."
        ),
        month=11,
        day=7,
        hour=17,
        minute=0,
        place="{birth_city}",
        people=("compagni_di_classe",),
        importance=6,
        emotion_label="appartenenza",
        valence=0.6,
        arousal=0.4,
        intensity=6,
        consequences=(
            "Ho imparato che l'amicizia può nascere dalla condivisione di un "
            "tavolo, non soltanto dalle confidenze."
        ),
        tags=("adolescenza", "liceo", "amicizie", "studio"),
        social=True,
    ),
    EventTemplate(
        key="adolescence_scifi",
        age=15,
        title="Il club di fantascienza",
        description=(
            "A quindici anni scoprii un club di lettura di fantascienza in "
            "biblioteca. Andai due volte prima di parlare. Alla terza portai "
            "un racconto di Asimov e dissi una frase sui finali a sorpresa; "
            "una ragazza mi rispose con un'altra storia. Da lì lessi molto di "
            "più, e quasi sempre di sera."
        ),
        month=4,
        day=16,
        hour=18,
        minute=30,
        place="{birth_city}",
        people=("club_fantascienza",),
        importance=5,
        emotion_label="scoperta",
        valence=0.5,
        arousal=0.5,
        intensity=5,
        consequences=(
            "La fantascienza è diventata il modo in cui immagino le "
            "conseguenze delle scelte, non soltanto un genere da leggere."
        ),
        tags=("adolescenza", "libri", "fantascienza", "biblioteca"),
        social=True,
    ),
    EventTemplate(
        key="adolescence_camera",
        age=16,
        title="La prima fotocamera usata",
        description=(
            "A sedici anni comprai una fotocamera usata con i soldi messi da "
            "parte durante l'estate. Il venditore mi spiegò l'esposizione in "
            "dieci minuti, poi mi lasciò solo con il manuale. Il primo rullino "
            "lo consumai quasi tutto in una passeggiata a Catania, senza "
            "mostrare le foto a nessuno."
        ),
        month=5,
        day=20,
        hour=15,
        minute=0,
        place="Catania",
        people=(),
        importance=6,
        emotion_label="orgoglio",
        valence=0.6,
        arousal=0.5,
        intensity=6,
        consequences=(
            "Ho iniziato a uscire con un motivo: cercare una luce o una "
            "geometria che valesse la pena fermare."
        ),
        tags=("adolescenza", "fotografia", "catania", "autonomia"),
    ),
    EventTemplate(
        key="adolescence_python",
        age=16,
        title="Il primo programma che funziona",
        description=(
            "A sedici anni passai una settimana a scrivere un piccolo "
            "programma Python che ordinava i file della cartella Download. La "
            "prima volta che lo vidi funzionare senza errori rimasi davanti "
            "allo schermo a rileggere l'output. Non serviva a niente di "
            "importante, ma era mio."
        ),
        month=10,
        day=3,
        hour=20,
        minute=0,
        place="{birth_city}",
        people=(),
        importance=6,
        emotion_label="soddisfazione",
        valence=0.7,
        arousal=0.5,
        intensity=6,
        consequences=(
            "Ho capito che programmare mi dava una forma di controllo diversa "
            "da quella che cercavo nelle giornate."
        ),
        tags=("adolescenza", "programmazione", "python", "computer"),
    ),
    EventTemplate(
        key="adolescence_rome",
        age=17,
        title="Il viaggio a Roma con la classe",
        description=(
            "In quarta liceo andammo a Roma per tre giorni. Io camminavo in "
            "fondo al gruppo con la macchina fotografica in mano. L'ultima "
            "sera finii a parlare con due compagni sul marciapiede davanti "
            "all'albergo, molto più a lungo di quanto avessi mai fatto a "
            "scuola."
        ),
        month=4,
        day=15,
        hour=21,
        minute=0,
        place="Roma",
        people=("compagni_di_classe",),
        importance=6,
        emotion_label="apertura",
        valence=0.5,
        arousal=0.5,
        intensity=6,
        consequences=(
            "Il viaggio mi ha mostrato che potevo stare bene in un gruppo "
            "anche quando non ero al centro dell'attenzione."
        ),
        tags=("adolescenza", "viaggio", "roma", "compagni"),
        social=True,
    ),
    EventTemplate(
        key="adolescence_crush",
        age=17,
        title="La cotta per una compagna di corso",
        description=(
            "A diciassette anni mi accorsi di guardare una compagna di classe "
            "in modo diverso. Per mesi non dissi nulla; preparavo le risposte "
            "alle sue domande la sera prima. Quando lei iniziò a uscire con "
            "un altro, feci finta che non mi importasse e scrissi una pagina "
            "di diario."
        ),
        month=11,
        day=11,
        hour=22,
        minute=0,
        place="{birth_city}",
        people=("compagna_di_classe",),
        importance=6,
        emotion_label="delusione",
        valence=-0.4,
        arousal=0.5,
        intensity=6,
        consequences=(
            "Ho imparato che il silenzio mi protegge, ma mi lascia anche con "
            "domande che non ho mai fatto."
        ),
        tags=("adolescenza", "scuola", "affetti", "diario"),
        social=True,
        negative=True,
    ),
    EventTemplate(
        key="adolescence_summer",
        age=18,
        title="L'estate dopo la maturità",
        description=(
            "L'estate dopo la maturità avevo diciotto anni e nessuna idea "
            "precisa del futuro. Passavo le mattine a casa, i pomeriggi in "
            "biblioteca e le sere davanti al computer. Mio padre mi chiese se "
            "avessi scelto l'università; risposi che stavo ancora decidendo, "
            "ma avevo già compilato il test per Informatica."
        ),
        month=7,
        day=5,
        hour=18,
        minute=0,
        place="{birth_city}",
        people=("fam_father",),
        importance=6,
        emotion_label="incertezza",
        valence=0.0,
        arousal=0.4,
        intensity=5,
        consequences=(
            "Ho capito che potevo scegliere senza sentirmi completamente "
            "pronto; l'importante era non lasciare la scelta a qualcun altro."
        ),
        tags=("adolescenza", "maturità", "università", "scelta"),
    ),
    EventTemplate(
        key="adolescence_exam",
        age=18,
        title="Il giorno dell'esame di maturità",
        description=(
            "Il giorno dell'orale di maturità portai una tesina sui linguaggi "
            "di programmazione. La commissione mi chiese perché avessi scelto "
            "proprio Python; risposi che mi piaceva l'idea di poter costruire "
            "qualcosa partendo da una pagina vuota. Uscii dalla scuola con la "
            "sensazione di aver parlato troppo."
        ),
        month=6,
        day=20,
        hour=9,
        minute=0,
        place="{birth_city}",
        people=("commissione_esame",),
        importance=7,
        emotion_label="tensione",
        valence=0.2,
        arousal=0.6,
        intensity=7,
        consequences=(
            "Il voto mi soddisfò, ma ricordo soprattutto il giudizio che dò "
            "ancora alle mie risposte quando sono sotto pressione."
        ),
        tags=("adolescenza", "maturità", "scuola", "python"),
        social=True,
    ),
    EventTemplate(
        key="adolescence_move",
        age=19,
        title="Il trasferimento a Catania",
        description=(
            "Nell'ottobre del 2020 mi trasferii a Catania per iniziare {university}. "
            "Misi in valigia troppo poco e dimenticai la tastiera del computer. "
            "La prima notte, nella stanza in affitto, ascoltai il traffico "
            "della città e mi sentii contemporaneamente libero e fuori posto."
        ),
        month=10,
        day=1,
        hour=20,
        minute=0,
        place="{residence_city}",
        people=(),
        importance=8,
        emotion_label="straniamento",
        valence=-0.1,
        arousal=0.6,
        intensity=7,
        consequences=(
            "Il trasferimento ha segnato il confine tra la casa di famiglia e "
            "la vita che ho cominciato a costruire da solo."
        ),
        tags=("adolescenza", "università", "catania", "trasferimento"),
    ),
    EventTemplate(
        key="adolescence_online",
        age=19,
        title="La prima sessione online",
        description=(
            "La prima sessione universitaria fu quasi tutta online. Seguivo "
            "le lezioni dal tavolo della cucina, con le cuffie e il microfono "
            "spento. Un professore faceva domande a caso; quando toccò a me, "
            "risposi con voce più ferma di quanto mi aspettassi."
        ),
        month=11,
        day=20,
        hour=11,
        minute=0,
        place="{residence_city}",
        people=(),
        importance=5,
        emotion_label="concentrazione",
        valence=0.2,
        arousal=0.4,
        intensity=5,
        consequences=(
            "Ho imparato a studiare senza la struttura di un'aula, "
            "sviluppando una routine che avrei conservato anche dopo."
        ),
        tags=("adolescenza", "università", "didattica_online", "catania"),
    ),
    EventTemplate(
        key="adult_first_exam",
        age=20,
        title="Il primo esame in presenza",
        description=(
            "Il primo esame in presenza lo preparai per settimane, con schemi "
            "e vecchie prove. Entrai in aula con mezz'ora di anticipo e lessi "
            "il compito due volte prima di scrivere. Presi ventotto; uscendo "
            "telefonai a mia madre e lei rise perché ero più teso adesso che "
            "durante la prova."
        ),
        month=11,
        day=20,
        hour=9,
        minute=0,
        place="{residence_city}",
        people=("fam_mother",),
        importance=7,
        emotion_label="sollievo",
        valence=0.7,
        arousal=0.6,
        intensity=7,
        consequences=(
            "Il primo esame mi ha dato un metodo: preparare molto, controllare "
            "tutto, poi accettare che una parte non dipenda da me."
        ),
        tags=("eta_adulta", "università", "esame", "catania"),
    ),
    EventTemplate(
        key="adult_photo_walk",
        age=20,
        title="La passeggiata fotografica all'alba",
        description=(
            "Una mattina di maggio uscii alle cinque con la macchina "
            "fotografica per fotografare Catania mentre si svegliava. Feci "
            "due chilometri senza incontrare quasi nessuno. Quando il sole "
            "arrivò sui palazzi, mi sedetti su un gradino e rimasi lì per "
            "mezz'ora senza scattare."
        ),
        month=5,
        day=2,
        hour=5,
        minute=30,
        place="{residence_city}",
        people=(),
        importance=5,
        emotion_label="quiete",
        valence=0.7,
        arousal=0.3,
        intensity=5,
        consequences=(
            "La città ha smesso di essere soltanto il luogo in cui studio e "
            "ha cominciato a diventare un posto che conosco."
        ),
        tags=("eta_adulta", "fotografia", "catania", "alba"),
    ),
    EventTemplate(
        key="adult_internship",
        age=21,
        title="Il tirocinio nello studio web",
        description=(
            "A ventuno anni iniziai un tirocinio in uno studio web. Il primo "
            "compito fu correggere il layout di una pagina che nessuno apriva "
            "da mesi. Passai due giorni a capire il codice di qualcun altro; "
            "quando consegnai la modifica, il titolare disse soltanto "
            "\"bene\". Per me fu sufficiente."
        ),
        month=6,
        day=10,
        hour=9,
        minute=30,
        place="{residence_city}",
        people=("colleghi",),
        importance=6,
        emotion_label="soddisfazione",
        valence=0.6,
        arousal=0.5,
        intensity=6,
        consequences=(
            "Il tirocinio mi ha insegnato che il codice esiste dentro un "
            "contesto fatto di persone, tempi e compromessi."
        ),
        tags=("eta_adulta", "lavoro", "tirocinio", "web"),
        social=True,
    ),
    EventTemplate(
        key="adult_apartment",
        age=21,
        title="Il primo appartamento condiviso",
        description=(
            "A settembre cambiai stanza e finii in un appartamento condiviso "
            "con due studenti. Imparai a riconoscere i loro orari dalla "
            "posizione delle scarpe all'ingresso. La sera cucinavamo a turno; "
            "io sceglievo sempre piatti che richiedevano poca conversazione."
        ),
        month=9,
        day=1,
        hour=19,
        minute=0,
        place="{residence_city}",
        people=("coinquilini",),
        importance=5,
        emotion_label="adattamento",
        valence=0.3,
        arousal=0.4,
        intensity=5,
        consequences=(
            "La condivisione mi ha costretto a negoziare spazi e silenzi, due "
            "cose che prima davo per scontate."
        ),
        tags=("eta_adulta", "casa", "coinquilini", "catania"),
        social=True,
    ),
    EventTemplate(
        key="adult_job",
        age=22,
        title="Il contratto part-time",
        description=(
            "A dicembre firmai il primo contratto part-time come {job_role} "
            "presso {job_org}. Era poco più di un tirocinio, ma c'era la "
            "firma e c'era uno stipendio. La sera rileggiai il contratto "
            "due volte, poi lo misi in una cartellina con gli altri documenti "
            "importanti."
        ),
        month=12,
        day=15,
        hour=18,
        minute=0,
        place="{residence_city}",
        people=("colleghi",),
        importance=7,
        emotion_label="orgoglio",
        valence=0.7,
        arousal=0.5,
        intensity=7,
        consequences=(
            "Il lavoro è diventato una parte stabile della settimana, non "
            "soltanto un'eccezione da raccontare."
        ),
        tags=("eta_adulta", "lavoro", "contratto", "web"),
    ),
    EventTemplate(
        key="adult_exhibition",
        age=22,
        title="La mostra fotografica",
        description=(
            "A maggio partecipai a una piccola mostra collettiva con tre "
            "fotografie scattate a Catania. Non volevo andare all'inaugurazione; "
            "alla fine rimasi in un angolo con un bicchiere in mano. Una "
            "persona si fermò davanti alla mia foto del mercato e disse che "
            "sembrava silenziosa. Non seppi cosa rispondere, ma sorrisi."
        ),
        month=5,
        day=20,
        hour=19,
        minute=0,
        place="{residence_city}",
        people=("amici_fotografia",),
        importance=6,
        emotion_label="imbarazzo",
        valence=0.4,
        arousal=0.5,
        intensity=6,
        consequences=(
            "Ho capito che mostrare qualcosa di mio non equivale a espormi "
            "completamente."
        ),
        tags=("eta_adulta", "fotografia", "mostra", "catania"),
        social=True,
    ),
    EventTemplate(
        key="adult_degree",
        age=23,
        title="La laurea triennale",
        description=(
            "Mi laureai in Informatica a luglio, con una tesi su un tema che "
            "mi aveva incuriosito durante un corso. La discussione durò "
            "venti minuti; mia madre piangeva in seconda fila e mio padre "
            "filmava tutto con il telefono storto. Dopo la proclamazione "
            "andammo a mangiare qualcosa insieme."
        ),
        month=7,
        day=18,
        hour=10,
        minute=0,
        place="{residence_city}",
        people=("fam_father", "fam_mother", "fam_sister"),
        importance=8,
        emotion_label="orgoglio",
        valence=0.8,
        arousal=0.6,
        intensity=8,
        consequences=(
            "La laurea ha chiuso un capitolo senza chiudere le domande: mi ha "
            "convinto a continuare con la magistrale."
        ),
        tags=("eta_adulta", "università", "laurea", "famiglia"),
        social=True,
    ),
    EventTemplate(
        key="adult_masters",
        age=23,
        title="L'inizio della magistrale",
        description=(
            "A ottobre iniziai la magistrale. Le aule erano più piccole e i "
            "compagni venivano da città diverse. Il primo giorno un "
            "professore disse che avremmo dovuto scegliere un problema e "
            "portarlo avanti per due anni; io scrissi tre possibili argomenti "
            "sul retro del quaderno."
        ),
        month=10,
        day=5,
        hour=14,
        minute=0,
        place="{residence_city}",
        people=("compagni_universita",),
        importance=7,
        emotion_label="progetto",
        valence=0.5,
        arousal=0.5,
        intensity=6,
        consequences=(
            "La magistrale mi ha dato un problema da abitare, più che un "
            "programma da seguire."
        ),
        tags=("eta_adulta", "università", "magistrale", "catania"),
        social=True,
    ),
    EventTemplate(
        key="adult_open_source",
        age=24,
        title="Il primo contributo open source",
        description=(
            "Ad aprile aprii la mia prima pull request su un progetto open "
            "source. Era una correzione piccola, una riga di documentazione. "
            "Aspettai tre giorni prima che qualcuno rispondesse. Quando il "
            "mantainer scrisse \"thanks\", chiusi il portatile e andai a "
            "camminare per mezz'ora."
        ),
        month=4,
        day=10,
        hour=21,
        minute=0,
        place="{residence_city}",
        people=(),
        importance=6,
        emotion_label="soddisfazione",
        valence=0.6,
        arousal=0.5,
        intensity=6,
        consequences=(
            "Contribuire a un progetto condiviso mi ha dato un senso di "
            "appartenenza diverso da quello dei gruppi in presenza."
        ),
        tags=("eta_adulta", "open_source", "programmazione", "catania"),
    ),
    EventTemplate(
        key="adult_rejection",
        age=24,
        title="Il colloquio andato male",
        description=(
            "A giugno feci un colloquio per un lavoro che mi interessava. "
            "Preparai tutto, ma davanti alla domanda sui miei punti deboli "
            "iniziai a parlare troppo e mi persi. Non mi richiamarono. Per "
            "una settimana ripercorsi ogni frase, immaginando risposte "
            "migliori."
        ),
        month=6,
        day=15,
        hour=15,
        minute=0,
        place="{residence_city}",
        people=("selezionatore",),
        importance=6,
        emotion_label="vergogna",
        valence=-0.5,
        arousal=0.6,
        intensity=7,
        consequences=(
            "Il rifiuto mi ha insegnato a preparare meno parole e più "
            "domande da fare io."
        ),
        tags=("eta_adulta", "lavoro", "colloquio", "rifiuto"),
        social=True,
        negative=True,
    ),
    EventTemplate(
        key="adult_project",
        age=24,
        title="Il progetto della magistrale",
        description=(
            "A ottobre iniziai a costruire il prototipo per la tesi. "
            "Passavo le serate a leggere documentazione e a riscrivere lo "
            "stesso modulo. La prima versione funzionava, ma era troppo "
            "complicata; la buttai e ricominciai con meno pezzi."
        ),
        month=10,
        day=1,
        hour=21,
        minute=30,
        place="{residence_city}",
        people=(),
        importance=6,
        emotion_label="concentrazione",
        valence=0.5,
        arousal=0.5,
        intensity=6,
        consequences=(
            "Ho imparato a preferire un progetto piccolo e chiaro a uno "
            "grande che non riesco a spiegare."
        ),
        tags=("eta_adulta", "università", "progetto", "programmazione"),
    ),
    EventTemplate(
        key="adult_photo_dawn",
        age=25,
        title="La passeggiata prima dell'alba",
        description=(
            "A giugno uscii prima dell'alba con la macchina fotografica. "
            "Percorsi le strade del centro mentre i primi forni aprivano. "
            "Scattai poche foto e mi fermai a bere un caffè al banco, "
            "ascoltando due anziani parlare del quartiere. Tornai a casa "
            "quando la città era già sveglia."
        ),
        month=6,
        day=20,
        hour=5,
        minute=0,
        place="{residence_city}",
        people=(),
        importance=5,
        emotion_label="quiete",
        valence=0.7,
        arousal=0.3,
        intensity=5,
        consequences=(
            "Quella mattina mi ha ricordato che la fotografia, per me, è "
            "soprattutto un modo di restare in un posto più a lungo."
        ),
        tags=("eta_adulta", "fotografia", "catania", "alba"),
    ),
    EventTemplate(
        key="adult_portfolio",
        age=25,
        title="Il portfolio online",
        description=(
            "A maggio misi online un piccolo portfolio con tre progetti e "
            "una selezione di fotografie. Passai più tempo a scegliere cosa "
            "togliere che a scrivere le descrizioni. Quando inviai il link a "
            "un amico, lui rispose che finalmente si capiva cosa faccio "
            "quando sto al computer."
        ),
        month=5,
        day=11,
        hour=22,
        minute=0,
        place="{residence_city}",
        people=("amico_universita",),
        importance=5,
        emotion_label="esposizione",
        valence=0.4,
        arousal=0.4,
        intensity=5,
        consequences=(
            "Mettere insieme il portfolio mi ha aiutato a vedere il filo che "
            "collega studio, lavoro e fotografia."
        ),
        tags=("eta_adulta", "portfolio", "lavoro", "fotografia"),
    ),
)


WORLD_REACTIONS: dict[str, WorldReaction] = {
    "wh_2008_09_15_lehman": WorldReaction(
        event_id="wh_2008_09_15_lehman",
        title="La crisi raccontata in officina",
        reaction=(
            "Ricordo mio padre tornare dall'officina con meno parole del "
            "solito e la radio accesa in cucina. Disse che i clienti "
            "rimandavano le riparazioni. Non capivo la finanza, ma capivo "
            "che qualcosa fuori casa stava rendendo il futuro meno sicuro."
        ),
        month=9,
        day=16,
        hour=19,
        minute=30,
        place="{birth_city}",
        people=("fam_father",),
        importance=6,
        emotion_label="preoccupazione",
        valence=-0.3,
        arousal=0.5,
        intensity=6,
        consequences=(
            "Ho iniziato a pensare al lavoro come a qualcosa che può cambiare "
            "per ragioni più grandi di una persona."
        ),
        tags=("fatto_storico", "economia", "famiglia", "officina"),
    ),
    "wh_2009_04_06_aquila": WorldReaction(
        event_id="wh_2009_04_06_aquila",
        title="La raccolta fondi per L'Aquila",
        reaction=(
            "A scuola organizzammo una raccolta fondi per il terremoto. "
            "Portai due euro e una scatola di pasta dalla dispensa. Per giorni "
            "guardai le immagini dei palazzi crollati e mi chiesi come sarebbe "
            "stato perdere la propria camera da un momento all'altro."
        ),
        month=4,
        day=10,
        hour=10,
        minute=30,
        place="{birth_city}",
        people=("compagni_di_classe", "maestra_elementari"),
        importance=6,
        emotion_label="solidarietà",
        valence=0.3,
        arousal=0.5,
        intensity=6,
        consequences=(
            "È stata la prima volta in cui un evento lontano è diventato una "
            "cosa concreta, fatta di oggetti e persone."
        ),
        tags=("fatto_storico", "terremoto", "scuola", "solidarietà"),
    ),
    "wh_2011_03_11_fukushima": WorldReaction(
        event_id="wh_2011_03_11_fukushima",
        title="Le immagini di Fukushima",
        reaction=(
            "Ricordo le immagini della centrale e le parole che non capivo. "
            "Mio padre disse che il mare può fare cose che non immaginiamo. "
            "Per qualche settimana guardai il mare di Licata con un pensiero "
            "in più, come se anche lì potesse succedere qualcosa."
        ),
        month=3,
        day=20,
        hour=20,
        minute=0,
        place="{birth_city}",
        people=("fam_father",),
        importance=6,
        emotion_label="inquietudine",
        valence=-0.4,
        arousal=0.6,
        intensity=6,
        consequences=(
            "Ho capito che la tecnologia può fallire, e che la paura non "
            "riguarda soltanto chi vive vicino a un pericolo."
        ),
        tags=("fatto_storico", "disastro", "mare", "tecnologia"),
    ),
    "wh_2013_03_13_pope_francis": WorldReaction(
        event_id="wh_2013_03_13_pope_francis",
        title="Le parole di Papa Francesco",
        reaction=(
            "In casa se ne parlò a cena: un papa venuto da lontano, con un "
            "nome mai sentito. Mia madre commentò che sembrava più semplice "
            "degli altri. Io ricordo soprattutto la sua richiesta di non "
            "dimenticare i poveri, ripetuta al telegiornale."
        ),
        month=3,
        day=20,
        hour=20,
        minute=30,
        place="{birth_city}",
        people=("fam_mother", "fam_father"),
        importance=5,
        emotion_label="curiosità",
        valence=0.3,
        arousal=0.4,
        intensity=5,
        consequences=(
            "Da allora ho prestato più attenzione al modo in cui le parole "
            "pubbliche possono cambiare il tono di una casa."
        ),
        tags=("fatto_storico", "religione", "famiglia", "televisione"),
    ),
    "wh_2016_06_23_brexit": WorldReaction(
        event_id="wh_2016_06_23_brexit",
        title="La notte della Brexit",
        reaction=(
            "Il giorno dopo in classe il professore di storia aprì la "
            "lezione con i risultati. Discutemmo di confini e di chi ha "
            "diritto a decidere. Io pensavo a quanto fosse strano che un "
            "paese potesse cambiare direzione con una croce su un foglio."
        ),
        month=6,
        day=24,
        hour=9,
        minute=0,
        place="{birth_city}",
        people=("compagni_di_classe", "professore_di_storia"),
        importance=6,
        emotion_label="confusione",
        valence=-0.1,
        arousal=0.5,
        intensity=5,
        consequences=(
            "Ho iniziato a seguire la politica con più attenzione, soprattutto "
            "quando le conseguenze sembravano lontane da me."
        ),
        tags=("fatto_storico", "politica", "scuola", "europa"),
    ),
    "wh_2020_03_09_covid_lockdown": WorldReaction(
        event_id="wh_2020_03_09_covid_lockdown",
        title="Il lockdown e le lezioni online",
        reaction=(
            "Ricordo le strade vuote e il suono delle videochiamate dalla "
            "camera. Per me il lockdown fu insieme una pausa e una perdita: "
            "non dovevo più giustificare la voglia di stare a casa, ma "
            "sentivo la mancanza delle persone senza sapere come dirlo."
        ),
        month=3,
        day=15,
        hour=9,
        minute=0,
        place="{birth_city}",
        people=("fam_father", "fam_mother", "fam_sister"),
        importance=7,
        emotion_label="isolamento",
        valence=-0.2,
        arousal=0.5,
        intensity=7,
        consequences=(
            "Ha cambiato il modo in cui vivo gli spazi condivisi e la "
            "stanchezza delle giornate tutte uguali."
        ),
        tags=("fatto_storico", "covid-19", "lockdown", "famiglia"),
    ),
    "wh_2021_07_11_euro_2020": WorldReaction(
        event_id="wh_2021_07_11_euro_2020",
        title="La notte degli Europei",
        reaction=(
            "Vidi la finale a casa di un amico, con altre tre persone. Non "
            "sono mai stato uno da urla allo schermo, ma ai rigori mi alzai "
            "in piedi come gli altri. Dopo la partita uscimmo in strada e "
            "Catania sembrava più piccola e più vicina."
        ),
        month=7,
        day=12,
        hour=22,
        minute=30,
        place="{residence_city}",
        people=("amici_universita",),
        importance=6,
        emotion_label="entusiasmo",
        valence=0.7,
        arousal=0.7,
        intensity=7,
        consequences=(
            "Una sera di festa condivisa mi ha mostrato che la solitudine non "
            "è l'unico modo che conosco per stare bene."
        ),
        tags=("fatto_storico", "sport", "calcio", "amicizie"),
    ),
    "wh_2022_02_24_ukraine_war": WorldReaction(
        event_id="wh_2022_02_24_ukraine_war",
        title="La guerra in Ucraina",
        reaction=(
            "All'università se ne parlò subito, nelle pause tra le lezioni. "
            "Un compagno aveva parenti a Leopoli e controllava il telefono "
            "ogni pochi minuti. Io seguivo le notizie con la sensazione di "
            "guardare qualcosa che non potevo fermare."
        ),
        month=2,
        day=25,
        hour=11,
        minute=0,
        place="{residence_city}",
        people=("compagni_universita",),
        importance=7,
        emotion_label="angoscia",
        valence=-0.6,
        arousal=0.6,
        intensity=7,
        consequences=(
            "Ha reso più concreto il confine tra informarsi e sentirsi in "
            "dovere di fare qualcosa."
        ),
        tags=("fatto_storico", "guerra", "università", "europa"),
    ),
    "wh_2023_10_07_israel_hamas": WorldReaction(
        event_id="wh_2023_10_07_israel_hamas",
        title="Le immagini da Gaza",
        reaction=(
            "In quei giorni evitai di guardare i video, ma le immagini "
            "arrivavano comunque. A lezione qualcuno parlò di storia e di "
            "colpe; io pensavo alle persone che non avevano scelto quella "
            "guerra. Ricordo il silenzio in aula più delle parole."
        ),
        month=10,
        day=8,
        hour=18,
        minute=0,
        place="{residence_city}",
        people=("compagni_universita",),
        importance=7,
        emotion_label="sgomento",
        valence=-0.7,
        arousal=0.6,
        intensity=8,
        consequences=(
            "Mi ha reso più diffidente verso le semplificazioni, anche "
            "quando sembrano comode."
        ),
        tags=("fatto_storico", "guerra", "medio_oriente", "università"),
    ),
    "wh_2024_06_08_eu_elections": WorldReaction(
        event_id="wh_2024_06_08_eu_elections",
        title="Il primo voto alle europee",
        reaction=(
            "Andai al seggio di pomeriggio, con la scheda già studiata a "
            "casa. Non ero sicuro di aver capito tutto, ma mi sembrava "
            "importante esserci. Ricordo la matita legata al banco e la "
            "sensazione di aver fatto una cosa piccola e reale."
        ),
        month=6,
        day=9,
        hour=16,
        minute=30,
        place="{residence_city}",
        people=(),
        importance=5,
        emotion_label="responsabilità",
        valence=0.4,
        arousal=0.4,
        intensity=5,
        consequences=(
            "Il voto mi ha fatto sentire parte di una comunità più grande "
            "della città in cui vivo."
        ),
        tags=("fatto_storico", "politica", "europa", "voto"),
    ),
}


# ---------------------------------------------------------------------------
# Generatore
# ---------------------------------------------------------------------------


class BiographyService:
    """Genera e applica la biografia procedurale di un'agente."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        memory: MemoryService | None = None,
        as_of: date | None = None,
    ):
        self.memory = memory or MemoryService(root)
        self.root = self.memory.root
        self.config = self.memory.config
        configs = self.config.load_all()
        self.identity = configs["identity"]
        self.world_history = configs["world_history"]
        self.birth_date = date.fromisoformat(
            self.identity["personal_data"]["birth_date"]
        )
        timezone_name = self.identity["personal_data"].get(
            "timezone", "Europe/Rome"
        )
        try:
            self.timezone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            self.timezone = UTC
        self.as_of = as_of or datetime.now(self.timezone).date()
        if self.as_of < self.birth_date:
            raise ValidationError(
                "as_of non può essere precedente alla data di nascita."
            )
        self._context = self._build_context()
        self._traits = self._read_traits()

    # -- configurazione -----------------------------------------------------

    def _build_context(self) -> dict[str, str]:
        personal = self.identity.get("personal_data", {})
        family = self.identity.get("family", {})
        parents = family.get("parents", [])
        father = next(
            (
                parent.get("first_name", "")
                for parent in parents
                if parent.get("relation") == "padre"
            ),
            "mio padre",
        )
        mother = next(
            (
                parent.get("first_name", "")
                for parent in parents
                if parent.get("relation") == "madre"
            ),
            "mia madre",
        )
        siblings = family.get("siblings", [])
        sister = (
            siblings[0].get("first_name", "mia sorella")
            if siblings
            else "mia sorella"
        )
        biography = self.identity.get("biography", {})
        education = biography.get("education", [])
        high_school = next(
            (
                entry.get("institution", "il liceo")
                for entry in education
                if "liceo" in str(entry.get("institution", "")).lower()
            ),
            "il liceo",
        )
        university = next(
            (
                entry.get("institution", "l'università")
                for entry in education
                if "universit" in str(entry.get("institution", "")).lower()
            ),
            "l'università",
        )
        work = biography.get("work", [])
        job = work[0] if work else {}
        birth_place = personal.get("birth_place", {})
        residence = personal.get("residence", {})
        return {
            "first_name": personal.get("first_name", ""),
            "last_name": personal.get("last_name", ""),
            "birth_city": birth_place.get("city", ""),
            "birth_region": birth_place.get("region", ""),
            "residence_city": residence.get("city", birth_place.get("city", "")),
            "father": father,
            "mother": mother,
            "sister": sister,
            "high_school": high_school,
            "university": university,
            "job_role": job.get("role", "sviluppatore"),
            "job_org": job.get("organization", "uno studio locale"),
        }

    def _read_traits(self) -> dict[str, int]:
        traits = (
            self.identity.get("personality", {})
            .get("traits", {})
        )
        result: dict[str, int] = {}
        for key, value in traits.items():
            if isinstance(value, dict):
                raw = value.get("value")
                if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                    result[key] = int(raw)
        return result

    def _trait(self, key: str, default: int = 5) -> int:
        return self._traits.get(key, default)

    # -- dati esistenti -----------------------------------------------------

    def _load_existing(self) -> list[dict[str, Any]]:
        with self.memory.db.timeline() as conn:
            rows = conn.execute(
                """
                SELECT id, event_time, event_date, title, source, source_ref,
                       access_level, tags_json, is_simulated
                FROM episodic_memories
                ORDER BY event_date ASC, id ASC
                """
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["tags"] = json.loads(item.pop("tags_json") or "[]")
            except json.JSONDecodeError:
                item["tags"] = []
            result.append(item)
        return result

    def _target_by_era(self) -> dict[str, int]:
        age = age_on(self.birth_date, self.as_of)
        childhood = sum(1 for value in range(7, 14) if value <= age)
        adolescence = sum(1 for value in range(14, 20) if value <= age)
        adulthood = sum(1 for value in range(20, age + 1))
        return {
            "infanzia": INFANCY_TARGET,
            "fanciullezza": childhood,
            "adolescenza": 2 * adolescence,
            "eta_adulta": 3 * adulthood,
        }

    def _existing_by_era(
        self, existing: list[dict[str, Any]]
    ) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for memory in existing:
            try:
                event_date = date.fromisoformat(memory["event_date"])
            except (KeyError, TypeError, ValueError):
                continue
            age = age_on(self.birth_date, event_date)
            counts[era_for_age(age)] += 1
        return {era: counts.get(era, 0) for era in self._target_by_era()}

    # -- date ---------------------------------------------------------------

    def _free_date(
        self,
        candidate: date,
        existing_dates: set[date],
        *,
        max_date: date | None = None,
    ) -> date | None:
        limit = max_date or self.as_of
        if candidate > limit:
            return None
        if candidate not in existing_dates:
            return candidate
        for delta in (1, -1, 2, -2, 3, -3, 7, -7):
            shifted = candidate + timedelta(days=delta)
            if shifted.year != candidate.year:
                continue
            if shifted > limit:
                continue
            if shifted not in existing_dates:
                return shifted
        return None

    # -- emozioni e tono ----------------------------------------------------

    def _emotional_state(
        self,
        *,
        label: str,
        valence: float,
        arousal: float,
        intensity: int,
        negative: bool = False,
    ) -> dict[str, Any]:
        sensitivity = self._trait("resilience_sensitivity", 5)
        adjusted = intensity + (sensitivity - 5.5) * 0.35
        if negative and sensitivity >= 7:
            adjusted += 0.4
        return {
            "label": label.strip(),
            "valence": round(_clamp(valence, -1.0, 1.0), 2),
            "arousal": round(_clamp(arousal, 0.0, 1.0), 2),
            "intensity": round(_clamp(adjusted, 1, 10)),
        }

    def _tone_suffix(self, template: EventTemplate) -> str:
        parts: list[str] = []
        introversion = self._trait("introversion_extraversion", 5)
        empathy = self._trait("empathy_detachment", 5)
        logic = self._trait("logic_impulsivity", 5)
        sensitivity = self._trait("resilience_sensitivity", 5)
        if template.social and introversion <= 4:
            parts.append(
                "Con il tempo ho capito che sto meglio in gruppi piccoli."
            )
        if template.negative and sensitivity >= 7:
            parts.append(
                "Ci ho rimuginato per giorni, prima di riuscire a parlarne."
            )
        if template.social and empathy <= 4:
            parts.append(
                "Ho notato subito lo stato d'animo delle persone intorno a me."
            )
        if not template.social and logic <= 4:
            parts.append(
                "Ho cercato di capire il meccanismo prima di fidarmi."
            )
        return (" " + " ".join(parts)) if parts else ""

    def _format(self, text: str) -> str:
        return text.format_map(_SafeDict(self._context))

    # -- eventi -------------------------------------------------------------

    def _personal_event(
        self,
        template: EventTemplate,
        existing_dates: set[date],
    ) -> BiographyEvent | None:
        candidate = _date_for_age(
            self.birth_date, template.age, template.month, template.day
        )
        event_date = self._free_date(candidate, existing_dates)
        if event_date is None:
            return None
        age = age_on(self.birth_date, event_date)
        if age != template.age:
            # Una data spostata di un anno non è più l'evento previsto.
            return None
        era = era_for_age(age)
        description = self._format(template.description) + self._tone_suffix(
            template
        )
        emotional_state = self._emotional_state(
            label=template.emotion_label,
            valence=template.valence,
            arousal=template.arousal,
            intensity=template.intensity,
            negative=template.negative,
        )
        memory_id = f"{BIO_ID_PREFIX}{event_date:%Y%m%d}_{template.key}"
        event_time = datetime(
            event_date.year,
            event_date.month,
            event_date.day,
            template.hour,
            template.minute,
            tzinfo=self.timezone,
        ).isoformat()
        return BiographyEvent(
            key=template.key,
            event_date=event_date,
            event_time=event_time,
            title=self._format(template.title),
            description=description,
            importance=max(1, min(10, template.importance)),
            place=self._format(template.place) if template.place else None,
            people=template.people,
            emotional_state=emotional_state,
            consequences=(
                self._format(template.consequences)
                if template.consequences
                else None
            ),
            tags=_unique((*template.tags, era, *SIMULATED_TAGS)),
            source="generated",
            source_ref=f"{BIO_SOURCE_PREFIX}{template.key}",
            world_event_id=None,
            era=era,
            age=age,
            memory_id=memory_id,
            reliability=0.85,
            is_consolidated=event_date <= self.as_of - timedelta(days=30),
        )

    def _world_event_biography(
        self,
        event: dict[str, Any],
        reaction: WorldReaction,
        existing_dates: set[date],
    ) -> BiographyEvent | None:
        candidate = date(
            int(str(event["date"])[:4]), reaction.month, reaction.day
        )
        event_date = self._free_date(candidate, existing_dates)
        if event_date is None:
            return None
        age = age_on(self.birth_date, event_date)
        threshold = comprehension_threshold(event)
        if age < threshold:
            return None
        era = era_for_age(age)
        fact = str(event.get("description", "")).strip()
        description = (
            f"Fatto storico: {fact} "
            f"Reazione simulata: {self._format(reaction.reaction)}"
        )
        if reaction.valence < 0 and self._trait(
            "resilience_sensitivity", 5
        ) >= 7:
            description += (
                " Ci ho pensato a lungo, senza riuscire a togliermelo dalla "
                "testa."
            )
        emotional_state = self._emotional_state(
            label=reaction.emotion_label,
            valence=reaction.valence,
            arousal=reaction.arousal,
            intensity=reaction.intensity,
            negative=reaction.valence < 0,
        )
        memory_id = (
            f"{BIO_ID_PREFIX}{event_date:%Y%m%d}_"
            f"{_slug(str(event['id']))}"
        )
        event_time = datetime(
            event_date.year,
            event_date.month,
            event_date.day,
            reaction.hour,
            reaction.minute,
            tzinfo=self.timezone,
        ).isoformat()
        tags = _unique(
            (
                *reaction.tags,
                "fatto_storico",
                "reazione_simulata",
                "contesto_storico",
                event.get("category", ""),
                str(event["id"]),
                era,
                *SIMULATED_TAGS,
            )
        )
        return BiographyEvent(
            key=str(event["id"]),
            event_date=event_date,
            event_time=event_time,
            title=self._format(reaction.title),
            description=description,
            importance=max(1, min(10, reaction.importance)),
            place=self._format(reaction.place) if reaction.place else None,
            people=reaction.people,
            emotional_state=emotional_state,
            consequences=(
                self._format(reaction.consequences)
                if reaction.consequences
                else None
            ),
            tags=tags,
            source="world_event",
            source_ref=str(event["id"]),
            world_event_id=str(event["id"]),
            era=era,
            age=age,
            memory_id=memory_id,
            reliability=0.9,
            is_consolidated=event_date <= self.as_of - timedelta(days=30),
        )

    # -- piano --------------------------------------------------------------

    def plan(self) -> BiographyPlan:
        existing = self._load_existing()
        existing_dates = {
            date.fromisoformat(memory["event_date"])
            for memory in existing
            if memory.get("event_date")
        }
        existing_source_refs = {
            memory["source_ref"]
            for memory in existing
            if memory.get("source_ref")
        }
        existing_by_year = Counter(
            int(memory["event_date"][:4])
            for memory in existing
            if memory.get("event_date")
        )
        existing_by_era = self._existing_by_era(existing)
        target_by_era = self._target_by_era()

        warnings: list[str] = []
        generated: list[BiographyEvent] = []
        used_keys: set[str] = set()

        decisions: list[WorldEventDecision] = []
        for event in self.world_history.get("events", []):
            status, age, threshold = classify_world_event(event, self.birth_date)
            event_date = date.fromisoformat(str(event["date"]))
            reason = ""
            if status == "before_birth":
                reason = "L'evento precede la nascita."
            elif status == "context_only":
                reason = (
                    f"Età {age} < soglia {threshold}: contesto familiare, "
                    "non ricordo diretto."
                )
            if str(event["id"]) in existing_source_refs:
                status = "covered"
                reason = "Esiste già un ricordo collegato a questo evento."
            elif event_date > self.as_of:
                status = "future"
                reason = "L'evento è successivo alla data di generazione."
            decisions.append(
                WorldEventDecision(
                    event_id=str(event["id"]),
                    title=str(event.get("title", "")),
                    event_date=event_date,
                    age=age,
                    threshold=threshold,
                    italy_relevance=int(event.get("italy_relevance", 0)),
                    global_relevance=int(event.get("global_relevance", 0)),
                    status=status,
                    reason=reason,
                )
            )

        direct_by_year: dict[int, list[WorldEventDecision]] = defaultdict(list)
        for decision in decisions:
            if decision.status == "direct":
                direct_by_year[decision.event_date.year].append(decision)

        scheduled_world: set[str] = set()

        # Prima infanzia: la specifica prevede 2-3 ricordi chiave complessivi.
        existing_infancy = sum(
            count
            for year, count in existing_by_year.items()
            if self.birth_date.year <= year <= self.birth_date.year + 6
            and year <= self.as_of.year
        )
        infancy_need = max(0, INFANCY_TARGET - existing_infancy)
        for age in INFANCY_AGES:
            if infancy_need <= 0:
                break
            year = self.birth_date.year + age
            if year > self.as_of.year or existing_by_year.get(year, 0) > 0:
                continue
            template = next(
                (item for item in TEMPLATES if item.age == age), None
            )
            if template is None:
                continue
            event = self._personal_event(template, existing_dates)
            if event is None:
                continue
            generated.append(event)
            existing_dates.add(event.event_date)
            used_keys.add(template.key)
            infancy_need -= 1

        for year in range(self.birth_date.year, self.as_of.year + 1):
            age = year - self.birth_date.year
            if age <= 6:
                continue
            target = target_for_age(age)
            need = max(0, target - existing_by_year.get(year, 0))
            if need <= 0:
                continue

            candidates = [
                decision
                for decision in direct_by_year.get(year, [])
                if decision.event_id not in scheduled_world
            ]
            if candidates and need > 0:
                chosen = max(
                    candidates,
                    key=lambda item: (
                        item.italy_relevance,
                        item.global_relevance,
                        item.event_date,
                    ),
                )
                raw_event = next(
                    (
                        event
                        for event in self.world_history.get("events", [])
                        if str(event["id"]) == chosen.event_id
                    ),
                    None,
                )
                reaction = WORLD_REACTIONS.get(chosen.event_id)
                if raw_event is not None and reaction is not None:
                    world_event = self._world_event_biography(
                        raw_event, reaction, existing_dates
                    )
                    if world_event is not None:
                        generated.append(world_event)
                        existing_dates.add(world_event.event_date)
                        scheduled_world.add(chosen.event_id)
                        need -= 1
                    else:
                        warnings.append(
                            f"Evento mondiale non inserito per collisione di "
                            f"data: {chosen.event_id}."
                        )

            if need > 0:
                templates = [
                    item
                    for item in TEMPLATES
                    if item.age == age and item.key not in used_keys
                ]
                for template in templates:
                    if need <= 0:
                        break
                    event = self._personal_event(template, existing_dates)
                    if event is None:
                        continue
                    generated.append(event)
                    existing_dates.add(event.event_date)
                    used_keys.add(template.key)
                    need -= 1
                if need > 0:
                    warnings.append(
                        f"Anno {year}: mancano {need} ricordi per raggiungere "
                        f"la densità prevista (età {age})."
                    )

        decisions = [
            replace(
                decision,
                status="scheduled",
                reason="Ricordo diretto generato.",
            )
            if decision.status == "direct"
            and decision.event_id in scheduled_world
            else (
                replace(
                    decision,
                    status="not_scheduled",
                    reason="L'anno ha già raggiunto la densità prevista.",
                )
                if decision.status == "direct"
                else decision
            )
            for decision in decisions
        ]

        generated.sort(key=lambda event: (event.event_date, event.memory_id))
        new_by_era: Counter[str] = Counter(event.era for event in generated)
        new_by_era_dict = {
            era: new_by_era.get(era, 0) for era in target_by_era
        }
        for era, target in target_by_era.items():
            total = existing_by_era.get(era, 0) + new_by_era_dict.get(era, 0)
            if total < target:
                warnings.append(
                    f"{era}: {total}/{target} ricordi dopo la generazione."
                )

        return BiographyPlan(
            generated=generated,
            world_events=decisions,
            existing_total=len(existing),
            existing_conscious=sum(
                1
                for memory in existing
                if memory.get("access_level") == "conscious"
            ),
            target_by_era=target_by_era,
            existing_by_era=existing_by_era,
            new_by_era=new_by_era_dict,
            warnings=warnings,
        )

    # -- applicazione -------------------------------------------------------

    def apply(self, plan: BiographyPlan | None = None) -> dict[str, Any]:
        plan = plan or self.plan()
        created: list[str] = []
        skipped: list[dict[str, str]] = []

        for event in plan.generated:
            with self.memory.db.timeline() as conn:
                row = conn.execute(
                    """
                    SELECT id FROM episodic_memories
                    WHERE id = ? OR (source_ref IS NOT NULL AND source_ref = ?)
                    LIMIT 1
                    """,
                    (event.memory_id, event.source_ref),
                ).fetchone()
            if row is not None:
                skipped.append(
                    {
                        "memory_id": event.memory_id,
                        "reason": f"esiste già come {row['id']}",
                    }
                )
                continue
            try:
                self.memory.create_memory(
                    title=event.title,
                    description=event.description,
                    importance=event.importance,
                    source=event.source,
                    event_time=event.event_time,
                    place=event.place,
                    people=list(event.people),
                    emotional_state=event.emotional_state,
                    consequences=event.consequences,
                    source_ref=event.source_ref,
                    tags=list(event.tags),
                    reliability=event.reliability,
                    is_simulated=True,
                    is_consolidated=event.is_consolidated,
                    memory_id=event.memory_id,
                )
                created.append(event.memory_id)
            except ConflictError as exc:
                skipped.append(
                    {"memory_id": event.memory_id, "reason": str(exc)}
                )

        with self.memory.db.timeline() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM episodic_memories"
            ).fetchone()[0]

        return {
            "ok": True,
            "generator_version": GENERATOR_VERSION,
            "created": len(created),
            "skipped": len(skipped),
            "total": total,
            "created_ids": created,
            "skipped_details": skipped,
        }


class _SafeDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Data non valida: {value!r}. Usa YYYY-MM-DD."
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Genera la biografia procedurale della Fase 4 applicando il "
            "filtro di anacronismo."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Directory del progetto (default: quella che contiene il pacchetto).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Scrive i nuovi ricordi nella timeline. Senza questo flag mostra "
            "solo il piano."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Stampa il piano o il risultato in JSON.",
    )
    parser.add_argument(
        "--as-of",
        type=_parse_date,
        default=None,
        help="Data di riferimento per l'età e il filtro (default: oggi).",
    )
    return parser


def _print_plan(plan: BiographyPlan) -> None:
    print(
        f"Biografia {GENERATOR_VERSION}: {len(plan.generated)} nuovi ricordi, "
        f"{plan.existing_total} esistenti, {plan.total_after} totali."
    )
    for era, counts in plan.by_era().items():
        print(
            f"  {era}: {counts['new']} nuovi, {counts['existing']} esistenti, "
            f"{counts['total_after']}/{counts['target']} previsti."
        )
    print("\nEventi mondiali:")
    for decision in plan.world_events:
        print(
            f"  {decision.event_date} {decision.title}: {decision.status} "
            f"(età {decision.age}, soglia {decision.threshold}) — "
            f"{decision.reason}"
        )
    if plan.generated:
        print("\nNuovi ricordi:")
        for event in plan.generated:
            print(
                f"  {event.event_date} (età {event.age}) {event.title}"
                f" [{event.place or '-'}]"
            )
    if plan.warnings:
        print("\nAvvisi:")
        for warning in plan.warnings:
            print(f"  - {warning}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = (
            Path(args.root).expanduser().resolve()
            if args.root is not None
            else project_root()
        )
        service = BiographyService(root, as_of=args.as_of)
        if args.apply:
            result = service.apply()
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(
                    f"Biografia applicata: {result['created']} ricordi creati, "
                    f"{result['skipped']} già presenti, {result['total']} totali."
                )
                if result["skipped_details"]:
                    for item in result["skipped_details"]:
                        print(
                            f"  saltato {item['memory_id']}: {item['reason']}"
                        )
            return 0

        plan = service.plan()
        if args.json:
            print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
        else:
            _print_plan(plan)
        return 0
    except AgentMemoryError as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
