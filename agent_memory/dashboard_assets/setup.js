(function () {
  "use strict";

  const form = document.getElementById("setup-form");
  if (!form) {
    return;
  }

  const steps = Array.from(form.querySelectorAll(".wizard-step"));
  const progressButtons = Array.from(
    document.querySelectorAll(".wizard-progress-step")
  );
  const backButton = document.getElementById("wizard-back");
  const nextButton = document.getElementById("wizard-next");
  const submitButton = document.getElementById("wizard-submit");
  const errorBox = document.getElementById("setup-error");
  const reviewBox = document.getElementById("setup-review");
  const valuesNode = document.getElementById("setup-values");
  const formHidden = form.hidden;

  const rowTypes = {
    language: { container: "languages-rows", title: "Lingua" },
    parent: { container: "parents-rows", title: "Genitore o tutore" },
    sibling: { container: "siblings-rows", title: "Fratello o sorella" },
    person: { container: "people-rows", title: "Persona importante" },
    education: { container: "education-rows", title: "Formazione" },
    work: { container: "work-rows", title: "Lavoro" },
  };

  const defaults = {
    timezone: "Europe/Rome",
    birth_country: "Italia",
    citizenship: "Italia",
    gender: "non specificato",
    trait_introversion_extraversion: "5",
    trait_empathy_detachment: "5",
    trait_logic_impulsivity: "5",
    trait_resilience_sensitivity: "5",
  };

  const visualFields = [
    "body",
    "face",
    "hair",
    "eyes",
    "distinctive_marks",
    "style",
  ];

  // Ogni riga del creatore aggiunge un pezzo alla descrizione del campo:
  // la composizione avviene nell’ordine in cui le righe sono elencate.
  const visualRows = [
    {
      id: "build",
      field: "body",
      label: "Corporatura",
      options: [
        { label: "Esile", value: "corporatura esile, ossatura fine", build: "esile" },
        { label: "Snella", value: "corporatura snella", build: "snella" },
        { label: "Media", value: "corporatura media, proporzionata", build: "media" },
        { label: "Atletica", value: "corporatura atletica e tonica", build: "atletica" },
        { label: "Robusta", value: "corporatura robusta, spalle larghe", build: "robusta" },
        { label: "Morbida", value: "corporatura morbida e arrotondata", build: "morbida" },
        { label: "Formosa", value: "corporatura formosa, fianchi pronunciati", build: "formosa" },
      ],
    },
    {
      id: "height",
      field: "body",
      label: "Altezza in centimetri",
      range: { min: 150, max: 205, step: 1, value: 170 },
    },
    {
      id: "face_shape",
      field: "face",
      label: "Forma del viso",
      options: [
        { label: "Ovale", value: "viso ovale, lineamenti morbidi", shape: "ovale" },
        { label: "Rotondo", value: "viso rotondo, guance piene", shape: "rotondo" },
        { label: "Squadrato", value: "viso squadrato, mascella definita", shape: "squadrato" },
        { label: "Allungato", value: "viso allungato, zigomi alti", shape: "allungato" },
        { label: "A cuore", value: "viso a cuore, fronte ampia e mento appuntito", shape: "cuore" },
        { label: "Triangolare", value: "viso triangolare, fronte stretta e mascella ampia", shape: "triangolare" },
      ],
    },
    {
      id: "skin",
      field: "face",
      label: "Carnagione",
      options: [
        { label: "Chiarissima", value: "carnagione chiarissima", swatch: "#f7d9c4" },
        { label: "Chiara", value: "carnagione chiara", swatch: "#f0cbb0" },
        { label: "Olivastra", value: "carnagione olivastra", swatch: "#d8a97f" },
        { label: "Ambrata", value: "carnagione ambrata", swatch: "#bf8a5f" },
        { label: "Scura", value: "carnagione scura", swatch: "#8d5a3b" },
        { label: "Molto scura", value: "carnagione molto scura", swatch: "#6a4028" },
      ],
    },
    {
      id: "hair_length",
      field: "hair",
      label: "Lunghezza",
      options: [
        { label: "Rasati", value: "capelli rasati", hair: "rasati" },
        { label: "Corti", value: "capelli corti", hair: "corti" },
        { label: "Medi", value: "capelli medi", hair: "medi" },
        { label: "Lunghi", value: "capelli lunghi", hair: "lunghi" },
        { label: "Raccolti", value: "capelli raccolti in una coda", hair: "coda" },
      ],
    },
    {
      id: "hair_texture",
      field: "hair",
      label: "Texture",
      standalonePrefix: "capelli ",
      options: [
        { label: "Lisci", value: "lisci", texture: "lisci" },
        { label: "Mossi", value: "mossi", texture: "mossi" },
        { label: "Ricci", value: "ricci", texture: "ricci" },
      ],
    },
    {
      id: "hair_color",
      field: "hair",
      label: "Colore",
      standalonePrefix: "capelli ",
      options: [
        { label: "Neri", value: "neri", swatch: "#1f1a17" },
        { label: "Castano scuro", value: "castani scuri", swatch: "#4a3324" },
        { label: "Castani", value: "castani", swatch: "#6b4a33" },
        { label: "Castano chiaro", value: "castani chiari", swatch: "#8a5f3c" },
        { label: "Biondi", value: "biondi", swatch: "#c9a46a" },
        { label: "Biondo cenere", value: "biondi cenere", swatch: "#b7a98c" },
        { label: "Rossi", value: "rossi", swatch: "#a5482f" },
        { label: "Grigi", value: "grigi", swatch: "#9a9a9a" },
      ],
    },
    {
      id: "eyes_shape",
      field: "eyes",
      label: "Forma",
      options: [
        { label: "A mandorla", value: "occhi a mandorla", eyes: "mandorla" },
        { label: "Grandi", value: "occhi grandi", eyes: "grandi" },
        { label: "Allungati", value: "occhi allungati", eyes: "allungati" },
        { label: "Tondi", value: "occhi tondi", eyes: "tondi" },
      ],
    },
    {
      id: "eyes_color",
      field: "eyes",
      label: "Colore",
      standalonePrefix: "occhi ",
      options: [
        { label: "Castani", value: "castani", swatch: "#6b4a2f" },
        { label: "Nocciola", value: "nocciola", swatch: "#8a6a3f" },
        { label: "Verdi", value: "verdi", swatch: "#4e7a4a" },
        { label: "Azzurri", value: "azzurri", swatch: "#6f9fc4" },
        { label: "Grigi", value: "grigi", swatch: "#8a9096" },
        { label: "Neri", value: "neri", swatch: "#2a2320" },
        { label: "Ambra", value: "ambra", swatch: "#b58a3f" },
      ],
    },
    {
      id: "marks",
      field: "distinctive_marks",
      label: "Segni particolari",
      options: [
        { label: "Nessuno", value: "nessun segno evidente", mark: "nessuno" },
        { label: "Neo", value: "un neo vicino al labbro", mark: "neo" },
        { label: "Cicatrice", value: "una piccola cicatrice sul sopracciglio", mark: "cicatrice" },
        { label: "Lentiggini", value: "lentiggini sul naso", mark: "lentiggini" },
        { label: "Barba", value: "una barba corta e curata", mark: "barba" },
        { label: "Baffi", value: "dei baffi curati", mark: "baffi" },
        { label: "Tatuaggio", value: "un tatuaggio sul collo", mark: "tatuaggio" },
        { label: "Occhiali", value: "occhiali dalla montatura sottile", mark: "occhiali" },
      ],
    },
    {
      id: "style",
      field: "style",
      label: "Stile",
      options: [
        { label: "Casual", value: "casual, jeans e magliette scure", outfit: "#3a4a63" },
        { label: "Minimal", value: "minimal, colori neutri", outfit: "#5a5f66" },
        { label: "Sportivo", value: "sportivo, felpe e sneakers", outfit: "#2f5d4a" },
        { label: "Elegante", value: "elegante ma informale, camicia e giacca", outfit: "#4a3a52" },
        { label: "Alternativo", value: "alternativo, giacca di pelle e anelli", outfit: "#6b3f4a" },
        { label: "Formale", value: "formale, abito scuro", outfit: "#2f3a4a" },
        { label: "Streetwear", value: "streetwear, felpa ampia e sneakers", outfit: "#3d4d5c" },
        { label: "Bohemien", value: "bohemien, tessuti morbidi e accessori artigianali", outfit: "#6a5540" },
      ],
    },
  ];

  // Intestazioni dei gruppi del creatore: ogni campo del profilo visivo
  // raccoglie le righe che lo compongono.
  const visualGroupLabels = {
    body: "Corpo",
    face: "Viso",
    hair: "Capelli",
    eyes: "Occhi",
    distinctive_marks: "",
    style: "",
  };

  const lifeFieldNames = ["interests", "values", "habits"];

  const lifeIdeas = {
    interests: [
      { label: "Creatività", items: ["fotografia", "disegno", "scrittura", "musica"] },
      { label: "Cultura", items: ["lettura", "cinema", "storia"] },
      { label: "Tecnologia", items: ["programmazione", "videogiochi", "elettronica"] },
      { label: "Tempo libero", items: ["viaggi", "cucina", "escursioni"] },
    ],
    values: [
      { label: "Personali", items: ["onestà", "curiosità", "autonomia"] },
      { label: "Relazioni", items: ["lealtà", "rispetto", "empatia"] },
      { label: "Lavoro", items: ["competenza", "responsabilità", "precisione"] },
      { label: "Società", items: ["giustizia", "libertà", "solidarietà"] },
    ],
    habits: [
      { label: "Quotidiane", items: ["legge la sera", "si sveglia presto", "tiene un diario"] },
      { label: "Sociali", items: ["sente gli amici", "organizza cene", "telefona ai familiari"] },
      { label: "Creative", items: ["suona uno strumento", "scrive appunti", "ascolta podcast"] },
      { label: "Benessere", items: ["corre al mattino", "medita", "fa stretching"] },
    ],
  };

  const lifePresets = [
    {
      label: "Curioso e riflessivo",
      description: "Legge, osserva e coltiva interessi tranquilli.",
      interests: ["lettura", "fotografia", "storia", "podcast"],
      values: ["curiosità", "onestà", "autonomia", "rispetto"],
      habits: [
        "legge la sera",
        "tiene un diario",
        "cammina al mattino",
        "ascolta podcast",
      ],
    },
    {
      label: "Creativo e sociale",
      description: "Musica, immagini e serate con gli amici.",
      interests: ["musica", "cinema", "disegno", "concerti"],
      values: ["empatia", "lealtà", "libertà", "gentilezza"],
      habits: [
        "suona uno strumento",
        "organizza cene con gli amici",
        "frequenta mostre",
        "scrive appunti",
      ],
    },
    {
      label: "Concreto e metodico",
      description: "Routine ordinate, obiettivi chiari e mano pratica.",
      interests: ["programmazione", "giochi da tavolo", "cucina", "ciclismo"],
      values: ["competenza", "responsabilità", "precisione", "affidabilità"],
      habits: [
        "si sveglia presto",
        "pianifica la settimana",
        "corre al mattino",
        "cucina in casa",
      ],
    },
  ];

  const relationNoteIdeas = [
    {
      label: "Rapporto",
      items: [
        "Rapporto stretto e affettuoso",
        "Rapporto sereno",
        "Rapporto formale",
        "Rapporto conflittuale",
      ],
    },
    {
      label: "Carattere",
      items: ["Persona premurosa", "Persona riservata", "Persona ironica"],
    },
    {
      label: "Distanza e presenza",
      items: ["Vive vicino", "Vive lontano", "Si sentono raramente"],
    },
  ];

  const familyNoteIdeas = [
    {
      label: "Clima",
      items: ["Clima sereno", "Clima teso", "Clima formale", "Clima protettivo"],
    },
    {
      label: "Legami",
      items: ["Legami stretti", "Legami distanti", "Legami conflittuali"],
    },
    {
      label: "Distanza e presenza",
      items: ["Vivono vicini", "Vivono lontani", "Si sentono raramente"],
    },
  ];

  // Generatore di personaggio: riempie i campi facoltativi con dati coerenti.
  // Resta tutto modificabile e nulla viene inviato finché non si preme
  // "Crea profilo".
  const identityPools = {
    femminile: [
      "Giulia", "Sofia", "Martina", "Chiara", "Elena", "Francesca",
      "Alessia", "Sara", "Beatrice", "Ilaria", "Valentina", "Anna",
    ],
    maschile: [
      "Luca", "Marco", "Andrea", "Matteo", "Davide", "Simone",
      "Stefano", "Paolo", "Lorenzo", "Federico", "Riccardo", "Antonio",
    ],
    "non binario": ["Alex", "Ariel", "Nico", "Ely", "Sam", "Robin", "Lior"],
    "non specificato": [
      "Giulia", "Luca", "Sofia", "Marco", "Chiara", "Andrea",
      "Elena", "Davide", "Sara", "Matteo",
    ],
  };
  const identityLastNames = [
    "Rossi", "Russo", "Ferrari", "Esposito", "Bianchi", "Romano",
    "Colombo", "Ricci", "Marino", "Greco", "Bruno", "Gallo", "Conti",
    "Costa", "Giordano", "Mancini", "Rizzo", "Lombardi", "Moretti",
  ];
  const identityCities = [
    "Roma", "Milano", "Torino", "Bologna", "Firenze", "Napoli",
    "Genova", "Palermo", "Bari", "Verona", "Padova", "Parma",
    "Brescia", "Cagliari", "Lecce",
  ];

  function randomItem(list) {
    return list[Math.floor(Math.random() * list.length)];
  }

  function isoDate(year, month, day) {
    return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  }

  function setGeneratedValue(name, value) {
    const element = form.querySelector(`[name="${name}"]`);
    if (!element) {
      return null;
    }
    setFieldValue(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return element;
  }

  function generateIdentity() {
    const genderSelect = form.querySelector('[name="gender"]');
    const gender = genderSelect ? genderSelect.value : "non specificato";
    const pool = identityPools[gender] || identityPools["non specificato"];
    const firstName = randomItem(pool);
    const lastName = randomItem(identityLastNames);
    setGeneratedValue("first_name", firstName);
    setGeneratedValue("last_name", lastName);

    const today = new Date();
    const age = 20 + Math.floor(Math.random() * 36);
    const birthYear = today.getFullYear() - age;
    const birthMonth = 1 + Math.floor(Math.random() * 12);
    const birthDay = 1 + Math.floor(Math.random() * 28);
    setGeneratedValue("birth_date", isoDate(birthYear, birthMonth, birthDay));
    setGeneratedValue("birth_country", "Italia");
    setGeneratedValue("citizenship", "Italia");
    const birthCity = randomItem(identityCities);
    let residenceCity = randomItem(identityCities);
    while (residenceCity === birthCity) {
      residenceCity = randomItem(identityCities);
    }
    setGeneratedValue("birth_city", birthCity);
    setGeneratedValue("residence_city", residenceCity);
    setGeneratedValue("timezone", "Europe/Rome");

    const movedYear = Math.min(
      today.getFullYear(),
      Math.max(birthYear + 18, today.getFullYear() - Math.floor(Math.random() * 12))
    );
    setGeneratedValue(
      "residence_since",
      isoDate(movedYear, 1 + Math.floor(Math.random() * 12), 1)
    );

    let languageRow = document.querySelector("#languages-rows .setup-row");
    if (!languageRow) {
      addRow("language");
      languageRow = document.querySelector("#languages-rows .setup-row");
    }
    if (languageRow) {
      const code = languageRow.querySelector('[name$="_code"]');
      const level = languageRow.querySelector('[name$="_level"]');
      if (code) {
        setFieldValue(code, "it");
      }
      if (level) {
        setFieldValue(level, "madrelingua");
      }
    }

    setGeneratedValue(
      "biography_summary",
      `Origini a ${birthCity}, vita quotidiana a ${residenceCity}. ` +
        `${firstName} ha ${age} anni e sta costruendo la propria strada tra ` +
        "lavoro, affetti e interessi personali."
    );
    setGeneratedValue(
      "current_situation",
      `Vive a ${residenceCity}. La giornata ruota attorno agli impegni ` +
        "quotidiani, alle persone vicine e al tempo libero."
    );

    form
      .querySelectorAll('input[type="range"][data-trait]')
      .forEach((element) => {
        element.value = String(1 + Math.floor(Math.random() * 10));
        element.dispatchEvent(new Event("input", { bubbles: true }));
      });

    const visualRandom = document.querySelector("[data-randomize-visual]");
    if (visualRandom) {
      visualRandom.click();
    }
    const lifePresets = form.querySelectorAll(".setup-life-preset");
    if (lifePresets.length) {
      randomItem(Array.from(lifePresets)).click();
    }

    const parentRows = document.querySelectorAll("#parents-rows .setup-row");
    parentRows.forEach((row, index) => {
      const first = row.querySelector('input[name$="_first_name"]');
      const last = row.querySelector('input[name$="_last_name"]');
      const relation = row.querySelector('input[name$="_relation"]');
      if (first) {
        first.value = randomItem(
          index === 1 ? identityPools.femminile : identityPools.maschile
        );
      }
      if (last) {
        last.value = lastName;
      }
      if (relation) {
        relation.value = index === 1 ? "madre" : "padre";
      }
    });
    const siblingRow = document.querySelector("#siblings-rows .setup-row");
    if (siblingRow) {
      const first = siblingRow.querySelector('input[name$="_first_name"]');
      const last = siblingRow.querySelector('input[name$="_last_name"]');
      const relation = siblingRow.querySelector('input[name$="_relation"]');
      const male = Math.random() < 0.5;
      if (first) {
        first.value = randomItem(
          male ? identityPools.maschile : identityPools.femminile
        );
      }
      if (last) {
        last.value = lastName;
      }
      if (relation) {
        relation.value = male ? "fratello" : "sorella";
      }
    }
    const personRow = document.querySelector("#people-rows .setup-row");
    if (personRow) {
      const name = personRow.querySelector('input[name$="_display_name"]');
      const relation = personRow.querySelector('input[name$="_relation"]');
      const trust = personRow.querySelector('input[name$="_trust"]');
      const intimacy = personRow.querySelector('input[name$="_intimacy"]');
      if (name) {
        name.value = `${randomItem(identityPools.femminile)} ${lastName}`;
      }
      if (relation) {
        relation.value = "amica";
      }
      if (trust) {
        trust.value = String(45 + Math.floor(Math.random() * 45));
      }
      if (intimacy) {
        intimacy.value = String(30 + Math.floor(Math.random() * 45));
      }
    }
    return { firstName, lastName, birthCity, residenceCity, age };
  }

  function initProfileGenerator() {
    const button = document.querySelector("[data-generate-profile]");
    const status = document.getElementById("profile-generation-status");
    if (!button) {
      return;
    }
    button.addEventListener("click", () => {
      const generated = generateIdentity();
      if (status) {
        status.textContent =
          `Personaggio generato: ${generated.firstName} ${generated.lastName}, ` +
          `${generated.age} anni, da ${generated.birthCity} a ${generated.residenceCity}. ` +
          "Controlla i passaggi e modifica ciò che vuoi.";
      }
      clearError();
    });
  }

  let currentStep = 0;
  const rowCounters = {};

  function parseInitialValues() {
    if (!valuesNode) {
      return {};
    }
    try {
      const parsed = JSON.parse(valuesNode.dataset.values || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (_error) {
      return {};
    }
  }

  const initialValues = parseInitialValues();
  const hasInitialValues = Object.keys(initialValues).length > 0;
  const traitGuides = parseTraitGuides();
  const traitStates = new WeakMap();

  function parseTraitGuides() {
    const node = document.getElementById("trait-guides");
    if (!node) {
      return {};
    }
    try {
      const parsed = JSON.parse(node.textContent || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (_error) {
      return {};
    }
  }

  function traitGuideFor(traitId, value) {
    const bands = Array.isArray(traitGuides[traitId])
      ? traitGuides[traitId]
      : [];
    const numeric = Number(value);
    const band = bands.find(
      (item) => Number(item.min) <= numeric && numeric <= Number(item.max)
    );
    if (!band) {
      return null;
    }
    const levelDescription =
      band.levels && band.levels[String(numeric)];
    return levelDescription ? { ...band, description: levelDescription } : band;
  }

  function setFieldValue(element, value) {
    if (element.type === "range") {
      element.value = value;
      updateRangeOutput(element);
      return;
    }
    if (element.tagName === "SELECT") {
      const option = Array.from(element.options).find(
        (item) => item.value === value
      );
      if (option) {
        element.value = value;
        return;
      }
      // I menu geografici accettano solo le voci previste: un valore
      // sconosciuto non deve diventare una nuova opzione.
      if (element.hasAttribute("data-strict")) {
        return;
      }
      // Valor preservado da un profilo o da un modulo precedente: resta
      // visibile invece di essere sostituito con la prima voce del menu.
      if (value !== undefined && value !== null && String(value) !== "") {
        element.add(new Option(String(value), String(value)));
        element.value = String(value);
      }
      return;
    }
    element.value = value;
  }

  function updateRangeOutput(element) {
    const output = document.querySelector(`output[for="${element.id}"]`);
    if (output) {
      output.textContent = element.value;
    }
  }

  function updateTraitBand(element) {
    const traitId = element.dataset.trait;
    const bandNode = form.querySelector(`[data-trait-band="${traitId}"]`);
    const guide = traitGuideFor(traitId, element.value);
    if (bandNode && guide) {
      bandNode.textContent = guide.label;
    }
  }

  function updateTraitDescription(element, forceAuto) {
    const traitId = element.dataset.trait;
    const note = form.querySelector(`[name="trait_${traitId}_note"]`);
    const reset = form.querySelector(`[data-trait-reset="${traitId}"]`);
    const guide = traitGuideFor(traitId, element.value);
    if (!note || !guide) {
      return;
    }
    let state = traitStates.get(element);
    if (!state) {
      const current = note.value.trim();
      state = { auto: !current || current === guide.description };
      traitStates.set(element, state);
    }
    if (forceAuto) {
      state.auto = true;
    }
    if (state.auto) {
      note.value = guide.description;
    }
    if (reset) {
      reset.hidden = state.auto;
    }
  }

  function refreshTrait(element, forceAuto) {
    updateRangeOutput(element);
    updateTraitBand(element);
    updateTraitDescription(element, Boolean(forceAuto));
  }

  function initTraitGuides() {
    form.querySelectorAll('input[type="range"][data-trait]').forEach((element) => {
      refreshTrait(element, false);
      element.addEventListener("input", () => refreshTrait(element, false));
      const traitId = element.dataset.trait;
      const note = form.querySelector(`[name="trait_${traitId}_note"]`);
      const reset = form.querySelector(`[data-trait-reset="${traitId}"]`);
      if (note) {
        note.addEventListener("input", () => {
          const guide = traitGuideFor(traitId, element.value);
          const state = traitStates.get(element) || { auto: false };
          state.auto =
            !note.value.trim() ||
            Boolean(guide && note.value.trim() === guide.description);
          traitStates.set(element, state);
          if (reset) {
            reset.hidden = state.auto;
          }
        });
      }
      if (reset && note) {
        reset.addEventListener("click", () => {
          refreshTrait(element, true);
          note.focus();
        });
      }
    });
  }

  function visualField(key) {
    return form.querySelector(`[name="visual_${key}"]`);
  }

  function initVisualSuggestions() {
    const status = document.getElementById("visual-suggestion-status");
    const randomButton = document.querySelector("[data-randomize-visual]");
    const resetButton = form.querySelector("[data-reset-visual]");
    const avatar = form.querySelector("[data-visual-avatar]");
    const caption = form.querySelector("[data-avatar-caption]");
    const container = form.querySelector("[data-visual-options]");
    const selections = {};
    const buttonsByRow = new Map();
    const rangeInputs = new Map();
    const valueByRow = new Map();
    const progressLabel = form.querySelector("[data-visual-progress]");
    const progressBar = form.querySelector("[data-visual-progress-bar]");

    function heightSelection(cm) {
      return { label: `${cm} cm`, value: `altezza ${cm} cm`, cm };
    }

    function composeField(field) {
      const chosen = visualRows
        .filter((row) => row.field === field && selections[row.id])
        .map((row) => ({ row, option: selections[row.id] }));
      if (!chosen.length) {
        return "";
      }
      const text =
        chosen.length === 1 && chosen[0].row.standalonePrefix
          ? `${chosen[0].row.standalonePrefix}${chosen[0].option.value}`
          : chosen.map((entry) => entry.option.value).join(", ");
      return text.charAt(0).toUpperCase() + text.slice(1);
    }

    function refreshPressed(field) {
      visualRows.forEach((row) => {
        if (field && row.field !== field) {
          return;
        }
        const buttons = buttonsByRow.get(row.id);
        if (!buttons) {
          return;
        }
        buttons.forEach((button, option) => {
          button.setAttribute(
            "aria-pressed",
            selections[row.id] === option ? "true" : "false"
          );
        });
      });
    }

    function refreshRowValues() {
      visualRows.forEach((row) => {
        const node = valueByRow.get(row.id);
        if (!node) {
          return;
        }
        const chosen = selections[row.id];
        node.textContent = chosen ? chosen.label : "";
        node.classList.toggle("is-empty", !chosen);
      });
    }

    function updateProgress() {
      const chosen = visualRows.filter((row) => selections[row.id]).length;
      if (progressLabel) {
        progressLabel.textContent = `${chosen} di ${visualRows.length}`;
      }
      if (progressBar) {
        progressBar.style.width = `${Math.round(
          (chosen / visualRows.length) * 100
        )}%`;
        const track = progressBar.parentElement;
        if (track) {
          track.setAttribute("aria-valuenow", String(chosen));
          track.setAttribute("aria-valuemax", String(visualRows.length));
        }
      }
    }

    function updateAvatar() {
      if (avatar) {
        const skin = selections.skin;
        const hairColor = selections.hair_color;
        const eyesColor = selections.eyes_color;
        const style = selections.style;
        avatar.style.setProperty(
          "--avatar-skin",
          (skin && skin.swatch) || "#e6b795"
        );
        avatar.style.setProperty(
          "--avatar-hair",
          (hairColor && hairColor.swatch) || "#3b2a21"
        );
        avatar.style.setProperty(
          "--avatar-eyes",
          (eyesColor && eyesColor.swatch) || "#4e7a4a"
        );
        avatar.style.setProperty(
          "--avatar-outfit",
          (style && style.outfit) || "#3a4a63"
        );
        const height = selections.height;
        const scale =
          height && height.cm
            ? 0.9 + ((height.cm - 150) / 55) * 0.16
            : 1;
        avatar.style.setProperty("--avatar-scale", scale.toFixed(3));
        const length =
          selections.hair_length && selections.hair_length.hair;
        const texture =
          selections.hair_texture && selections.hair_texture.texture;
        let hair = length || "corti";
        if (
          (hair === "corti" || hair === "medi") &&
          texture === "ricci"
        ) {
          hair = "ricci";
        } else if (
          (hair === "corti" || hair === "medi") &&
          texture === "mossi"
        ) {
          hair = "mossi";
        }
        avatar.dataset.hair = hair;
        avatar.dataset.face =
          (selections.face_shape && selections.face_shape.shape) || "ovale";
        avatar.dataset.build =
          (selections.build && selections.build.build) || "media";
        avatar.dataset.eyes =
          (selections.eyes_shape && selections.eyes_shape.eyes) || "mandorla";
        avatar.dataset.marks =
          (selections.marks && selections.marks.mark) || "nessuno";
      }
      if (caption) {
        const parts = [
          "build",
          "height",
          "hair_length",
          "hair_color",
          "eyes_color",
        ]
          .map((rowId) => selections[rowId] && selections[rowId].label)
          .filter(Boolean);
        caption.textContent = parts.length
          ? parts.join(" · ")
          : "Personaggio da definire";
      }
    }

    function writeField(field) {
      const textarea = visualField(field);
      if (!textarea) {
        return;
      }
      textarea.value = composeField(field);
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function randomizeRow(row) {
      if (row.range) {
        const input = rangeInputs.get(row.id);
        if (!input) {
          return;
        }
        const steps = Math.round(
          (row.range.max - row.range.min) / row.range.step
        );
        const value =
          row.range.min +
          Math.floor(Math.random() * (steps + 1)) * row.range.step;
        input.value = String(value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
        return;
      }
      selections[row.id] =
        row.options[Math.floor(Math.random() * row.options.length)];
      writeField(row.field);
      refreshPressed(row.field);
      updateAvatar();
    }

    function buildRows() {
      if (!container) {
        return;
      }
      container.textContent = "";
      let groupBody = null;
      let groupLabel = "";
      visualRows.forEach((row) => {
        const groupName =
          visualGroupLabels[row.field] === undefined
            ? "Aspetto"
            : visualGroupLabels[row.field];
        if (!groupBody || groupName !== groupLabel) {
          groupLabel = groupName;
          const group = document.createElement("section");
          group.className = "setup-creator-group";
          if (groupName) {
            const title = document.createElement("h4");
            title.className = "setup-creator-group-title";
            title.textContent = groupName;
            group.appendChild(title);
          } else {
            group.classList.add("is-plain");
          }
          groupBody = document.createElement("div");
          groupBody.className = "setup-creator-group-body";
          group.appendChild(groupBody);
          container.appendChild(group);
        }
        const wrapper = document.createElement("div");
        wrapper.className = "setup-creator-row";
        const head = document.createElement("div");
        head.className = "setup-creator-head";
        const label = document.createElement("span");
        label.className = "setup-creator-label";
        label.textContent = row.label;
        const dice = document.createElement("button");
        dice.type = "button";
        dice.className = "setup-creator-dice";
        dice.textContent = "🎲";
        dice.title = `Sorteggia ${row.label.toLowerCase()}`;
        dice.setAttribute("aria-label", `Sorteggia ${row.label.toLowerCase()}`);
        dice.addEventListener("click", () => {
          randomizeRow(row);
          refreshRowValues();
          updateProgress();
          if (status) {
            status.textContent = "";
          }
        });
        const value = document.createElement("span");
        value.className = "setup-creator-value";
        if (!row.range) {
          valueByRow.set(row.id, value);
        }
        head.appendChild(label);
        head.appendChild(value);
        head.appendChild(dice);
        wrapper.appendChild(head);

        if (row.range) {
          const range = document.createElement("div");
          range.className = "setup-creator-range";
          const input = document.createElement("input");
          input.type = "range";
          input.min = String(row.range.min);
          input.max = String(row.range.max);
          input.step = String(row.range.step);
          input.value = String(row.range.value);
          input.setAttribute("aria-label", row.label);
          const output = document.createElement("output");
          output.textContent = `${row.range.value} cm`;
          range.appendChild(input);
          range.appendChild(output);
          const scale = document.createElement("div");
          scale.className = "setup-creator-range-scale";
          const minimum = document.createElement("span");
          minimum.textContent = `${row.range.min} cm`;
          const maximum = document.createElement("span");
          maximum.textContent = `${row.range.max} cm`;
          scale.appendChild(minimum);
          scale.appendChild(maximum);
          input.addEventListener("input", () => {
            const cm = Number(input.value);
            selections[row.id] = heightSelection(cm);
            output.textContent = `${cm} cm`;
            writeField(row.field);
            refreshRowValues();
            updateProgress();
            updateAvatar();
            if (status) {
              status.textContent = "";
            }
          });
          rangeInputs.set(row.id, input);
          wrapper.appendChild(range);
          wrapper.appendChild(scale);
          groupBody.appendChild(wrapper);
          return;
        }

        const choices = document.createElement("div");
        choices.className = "setup-creator-choices";
        const buttons = new Map();
        row.options.forEach((option) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "setup-choice";
          button.setAttribute("aria-pressed", "false");
          if (option.swatch) {
            const dot = document.createElement("span");
            dot.className = "setup-choice-swatch";
            dot.style.setProperty("--swatch", option.swatch);
            dot.setAttribute("aria-hidden", "true");
            button.appendChild(dot);
          }
          const text = document.createElement("span");
          text.textContent = option.label;
          button.appendChild(text);
          button.addEventListener("click", () => {
            if (selections[row.id] === option) {
              delete selections[row.id];
            } else {
              selections[row.id] = option;
            }
            writeField(row.field);
            refreshPressed(row.field);
            refreshRowValues();
            updateProgress();
            updateAvatar();
            if (status) {
              status.textContent = "";
            }
          });
          buttons.set(option, button);
          choices.appendChild(button);
        });
        buttonsByRow.set(row.id, buttons);
        wrapper.appendChild(choices);
        groupBody.appendChild(wrapper);
      });
    }

    function preselectFromText() {
      visualRows.forEach((row) => {
        const textarea = visualField(row.field);
        if (!textarea || !textarea.value.trim()) {
          return;
        }
        const value = textarea.value.toLowerCase();
        if (row.range) {
          const match = value.match(/altezza\s+(\d{2,3})\s*cm/i);
          if (!match) {
            return;
          }
          const cm = Number(match[1]);
          if (cm < row.range.min || cm > row.range.max) {
            return;
          }
          selections[row.id] = heightSelection(cm);
          const input = rangeInputs.get(row.id);
          if (input) {
            input.value = String(cm);
            const output = input.parentElement.querySelector("output");
            if (output) {
              output.textContent = `${cm} cm`;
            }
          }
          return;
        }
        const match = row.options.find((option) =>
          value.includes(option.value.toLowerCase())
        );
        if (match) {
          selections[row.id] = match;
        }
      });
    }

    buildRows();
    preselectFromText();
    refreshPressed();
    refreshRowValues();
    updateProgress();
    updateAvatar();

    // Se il testo viene riscritto a mano, le scelte che non compaiono più
    // nel campo vengono deselezionate per non mostrare uno stato falso.
    visualFields.forEach((field) => {
      const textarea = visualField(field);
      if (!textarea) {
        return;
      }
      textarea.addEventListener("input", () => {
        const value = textarea.value.toLowerCase();
        visualRows.forEach((row) => {
          if (row.field !== field) {
            return;
          }
          const chosen = selections[row.id];
          if (chosen && !value.includes(chosen.value.toLowerCase())) {
            delete selections[row.id];
          }
        });
        refreshPressed(field);
        refreshRowValues();
        updateProgress();
        updateAvatar();
      });
    });

    if (randomButton) {
      randomButton.addEventListener("click", () => {
        visualRows.forEach((row) => randomizeRow(row));
        visualFields.forEach(writeField);
        refreshPressed();
        refreshRowValues();
        updateProgress();
        updateAvatar();
        if (status) {
          status.textContent =
            "Aspetto generato. Cambia solo le voci che vuoi.";
        }
      });
    }

    if (resetButton) {
      resetButton.addEventListener("click", () => {
        visualRows.forEach((row) => {
          delete selections[row.id];
          if (row.range) {
            const input = rangeInputs.get(row.id);
            if (input) {
              input.value = String(row.range.value);
              const output = input.parentElement.querySelector("output");
              if (output) {
                output.textContent = `${row.range.value} cm`;
              }
            }
          }
        });
        visualFields.forEach((field) => {
          const textarea = visualField(field);
          if (textarea) {
            textarea.value = "";
            textarea.dispatchEvent(new Event("input", { bubbles: true }));
          }
        });
        refreshPressed();
        refreshRowValues();
        updateProgress();
        updateAvatar();
        if (status) {
          status.textContent =
            "Aspetto azzerato: ricomincia dalle scelte o scrivi a mano.";
        }
      });
    }
  }

  function initLifeSuggestions() {
    const status = document.getElementById("life-preset-status");
    const resetButton = form.querySelector("[data-reset-life]");
    const presetsBox = form.querySelector("[data-life-presets]");

    function lifeField(name) {
      return form.querySelector(`[name="${name}"]`);
    }

    function items(name) {
      const field = lifeField(name);
      if (!field) {
        return [];
      }
      return field.value
        .split(/[\n,;]+/)
        .map((item) => item.trim())
        .filter(Boolean);
    }

    function same(left, right) {
      return left.toLowerCase() === right.toLowerCase();
    }

    function write(name, list) {
      const field = lifeField(name);
      if (!field) {
        return;
      }
      field.value = list.join("\n");
      field.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function refresh(name) {
      const current = items(name);
      form
        .querySelectorAll(`[data-life-choice="${name}"]`)
        .forEach((button) => {
          const pressed = current.some((item) =>
            same(item, button.dataset.lifeValue || "")
          );
          button.setAttribute("aria-pressed", pressed ? "true" : "false");
        });
    }

    function refreshAll() {
      lifeFieldNames.forEach(refresh);
    }

    lifeFieldNames.forEach((name) => {
      const container = form.querySelector(`[data-life-ideas="${name}"]`);
      if (!container) {
        return;
      }
      container.textContent = "";
      (lifeIdeas[name] || []).forEach((group) => {
        const row = document.createElement("div");
        row.className = "setup-life-category";
        const label = document.createElement("span");
        label.className = "setup-life-category-label";
        label.textContent = group.label;
        row.appendChild(label);
        group.items.forEach((item) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "setup-choice";
          button.dataset.lifeChoice = name;
          button.dataset.lifeValue = item;
          button.textContent = item;
          button.setAttribute("aria-pressed", "false");
          button.addEventListener("click", () => {
            const list = items(name);
            const existing = list.findIndex((entry) => same(entry, item));
            if (existing === -1) {
              list.push(item);
            } else {
              list.splice(existing, 1);
            }
            write(name, list);
            refresh(name);
            if (status) {
              status.textContent = "";
            }
          });
          row.appendChild(button);
        });
        container.appendChild(row);
      });
    });

    if (presetsBox) {
      presetsBox.textContent = "";
      lifePresets.forEach((preset) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "setup-life-preset";
        const title = document.createElement("strong");
        title.textContent = preset.label;
        const description = document.createElement("span");
        description.textContent = preset.description;
        button.appendChild(title);
        button.appendChild(description);
        button.addEventListener("click", () => {
          lifeFieldNames.forEach((name) => write(name, preset[name] || []));
          refreshAll();
          if (status) {
            status.textContent = `Modello “${preset.label}” applicato: puoi togliere o aggiungere voci.`;
          }
        });
        presetsBox.appendChild(button);
      });
    }

    if (resetButton) {
      resetButton.addEventListener("click", () => {
        lifeFieldNames.forEach((name) => write(name, []));
        refreshAll();
        if (status) {
          status.textContent =
            "Campi azzerati: puoi ricominciare da un modello o scrivere a mano.";
        }
      });
    }

    lifeFieldNames.forEach((name) => {
      const field = lifeField(name);
      if (!field) {
        return;
      }
      field.addEventListener("input", () => {
        refresh(name);
        if (status) {
          status.textContent = "";
        }
      });
    });

    refreshAll();
  }

  function appendNotePhrase(text, phrase) {
    const trimmed = text.trim();
    if (!trimmed) {
      return `${phrase}.`;
    }
    if (/[.!?…]$/.test(trimmed) || /[,;]$/.test(trimmed)) {
      return `${trimmed} ${phrase}.`;
    }
    return `${trimmed}. ${phrase}.`;
  }

  function removeNotePhrase(text, phrase) {
    const index = text.toLowerCase().indexOf(phrase.toLowerCase());
    if (index === -1) {
      return text;
    }
    let end = index + phrase.length;
    if (text.charAt(end) === ".") {
      end += 1;
    }
    while (end < text.length && /\s/.test(text.charAt(end))) {
      end += 1;
    }
    let start = index;
    while (start > 0 && /\s/.test(text.charAt(start - 1))) {
      start -= 1;
    }
    const before = text.slice(0, start).replace(/\s+$/, "");
    const after = text.slice(end).replace(/^\s+/, "");
    if (before && after) {
      return `${before} ${after}`.replace(/[ \t]{2,}/g, " ");
    }
    return (before || after).replace(/[ \t]{2,}/g, " ");
  }

  function initNoteIdeas(textarea, container, groups) {
    if (!textarea || !container) {
      return;
    }
    container.textContent = "";
    const ideas = groups || relationNoteIdeas;

    function refresh() {
      const text = textarea.value.toLowerCase();
      container.querySelectorAll("[data-note-phrase]").forEach((button) => {
        const phrase = button.dataset.notePhrase || "";
        button.setAttribute(
          "aria-pressed",
          text.includes(phrase.toLowerCase()) ? "true" : "false"
        );
      });
    }

    ideas.forEach((group) => {
      const row = document.createElement("div");
      row.className = "setup-life-category";
      const label = document.createElement("span");
      label.className = "setup-life-category-label";
      label.textContent = group.label;
      row.appendChild(label);
      group.items.forEach((phrase) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "setup-choice";
        button.dataset.notePhrase = phrase;
        button.textContent = phrase;
        button.setAttribute("aria-pressed", "false");
        button.addEventListener("click", () => {
          const current = textarea.value;
          const present = current.toLowerCase().includes(phrase.toLowerCase());
          textarea.value = present
            ? removeNotePhrase(current, phrase)
            : appendNotePhrase(current, phrase);
          textarea.dispatchEvent(new Event("input", { bubbles: true }));
          refresh();
        });
        row.appendChild(button);
      });
      container.appendChild(row);
    });

    textarea.addEventListener("input", refresh);
    refresh();
  }

  function applyScalarValues(values, useDefaults) {
    form.querySelectorAll("input[name], select[name], textarea[name]").forEach(
      (element) => {
        const name = element.name;
        if (!name || name === "csrf") {
          return;
        }
        if (Object.prototype.hasOwnProperty.call(values, name)) {
          setFieldValue(element, values[name]);
        } else if (useDefaults && Object.prototype.hasOwnProperty.call(defaults, name)) {
          setFieldValue(element, defaults[name]);
        }
      }
    );
  }

  function rowIndexes(values, prefix) {
    const pattern = new RegExp(`^${prefix}_(\\d+)_`);
    const indexes = new Set();
    Object.keys(values).forEach((key) => {
      const match = key.match(pattern);
      if (match) {
        indexes.add(Number(match[1]));
      }
    });
    return Array.from(indexes).sort((left, right) => left - right);
  }

  function rowData(values, prefix, index) {
    const data = {};
    Object.keys(values).forEach((key) => {
      const match = key.match(new RegExp(`^${prefix}_${index}_(.+)$`));
      if (match) {
        data[match[1]] = values[key];
      }
    });
    return data;
  }

  function setRowField(row, name, value) {
    const element = row.querySelector(`[name="${name}"]`);
    if (element && value !== undefined && value !== null && value !== "") {
      setFieldValue(element, value);
    }
  }

  function addRow(type, data, forcedIndex) {
    const config = rowTypes[type];
    if (!config) {
      return null;
    }
    const template = document.getElementById(`${type}-row-template`);
    const container = document.getElementById(config.container);
    if (!template || !container) {
      return null;
    }
    const index = Number.isInteger(forcedIndex)
      ? forcedIndex
      : rowCounters[type] || 0;
    rowCounters[type] = Math.max(rowCounters[type] || 0, index + 1);

    const fragment = template.content.cloneNode(true);
    const row = fragment.querySelector(".setup-row");
    if (!row) {
      return null;
    }
    row.dataset.index = String(index);
    row.querySelectorAll("[name]").forEach((element) => {
      element.name = element.name.replaceAll("__INDEX__", String(index));
    });

    const rowTitle = row.querySelector(".setup-row-title");
    if (rowTitle) {
      rowTitle.textContent = config.title;
    }

    if (type === "language" && !data) {
      if (index === 0) {
        setRowField(row, `language_${index}_code`, "it");
        setRowField(row, `language_${index}_level`, "madrelingua");
      }
    } else if (type === "parent" && !data) {
      setRowField(
        row,
        `parent_${index}_relation`,
        index === 0 ? "padre" : index === 1 ? "madre" : "genitore"
      );
    } else if (type === "sibling" && !data) {
      setRowField(row, `sibling_${index}_relation`, "sorella");
    } else {
      row.querySelectorAll("[name]").forEach((element) => {
        const name = element.name;
        const key = name.replace(`${type}_${index}_`, "");
        if (data && Object.prototype.hasOwnProperty.call(data, key)) {
          setFieldValue(element, data[key]);
        }
      });
    }

    if (type === "parent" || type === "sibling" || type === "person") {
      const notesField = row.querySelector('textarea[name$="_notes"]');
      const notesLabel = notesField ? notesField.closest("label") : null;
      if (notesField && notesLabel) {
        const noteBox = document.createElement("div");
        noteBox.className = "setup-note-ideas setup-field-wide";
        notesLabel.insertAdjacentElement("afterend", noteBox);
        initNoteIdeas(notesField, noteBox);
      }
    }

    container.appendChild(fragment);
    return row;
  }

  function initializeRows(values) {
    Object.keys(rowTypes).forEach((type) => {
      const indexes = rowIndexes(values, type);
      if (indexes.length === 0) {
        if (type === "language") {
          addRow("language");
        } else if (type === "parent") {
          addRow("parent");
          addRow("parent");
        } else if (type === "sibling") {
          addRow("sibling");
        } else if (type === "person") {
          addRow("person");
        } else if (type === "education") {
          addRow("education");
        } else if (type === "work") {
          addRow("work");
        }
        return;
      }
      indexes.forEach((index) => {
        addRow(type, rowData(values, type, index), index);
      });
    });
  }

  function initGeoMenus() {
    form.querySelectorAll("[data-province-select]").forEach((province) => {
      const key = province.dataset.provinceSelect;
      const region = form.querySelector(`[data-region-select="${key}"]`);
      if (!region) {
        return;
      }

      function applyVisibility(resetOnEmptyRegion) {
        const selectedRegion = region.value;
        let provinceStillValid = true;
        province.querySelectorAll("optgroup").forEach((group) => {
          const visible = !selectedRegion || group.label === selectedRegion;
          group.hidden = !visible;
          group.disabled = !visible;
          group.querySelectorAll("option").forEach((option) => {
            option.hidden = !visible;
            option.disabled = !visible;
            if (option.selected && !visible) {
              provinceStillValid = false;
            }
          });
        });
        if (resetOnEmptyRegion && !selectedRegion) {
          province.value = "";
          return;
        }
        if (province.value && !provinceStillValid) {
          province.value = "";
        }
      }

      region.addEventListener("change", () => applyVisibility(true));
      province.addEventListener("change", () => {
        const option = province.selectedOptions[0];
        const provinceRegion = option ? option.dataset.region || "" : "";
        if (provinceRegion && region.value !== provinceRegion) {
          region.value = provinceRegion;
        }
        applyVisibility(false);
      });
      applyVisibility(false);
    });
  }

  function initForeignGeoFields(key) {
    const country = form.querySelector(`[data-country-select="${key}"]`);
    const birthCountry =
      key === "residence"
        ? form.querySelector('[name="birth_country"]')
        : null;
    if (!country) {
      return;
    }
    const italianFields = Array.from(
      form.querySelectorAll(`[data-italian-geo="${key}"]`)
    );
    const foreignFields = Array.from(
      form.querySelectorAll(`[data-foreign-geo="${key}"]`)
    );
    const regionList = document.getElementById(`${key}-region-options`);
    const provinceList = document.getElementById(
      `${key}-province-options`
    );
    const regionInput = form.querySelector(`[name="${key}_region_foreign"]`);
    const provinceInput = form.querySelector(
      `[name="${key}_province_foreign"]`
    );

    let geoData = null;
    let geoRequest = null;
    let geoToken = 0;
    let lastCode = null;

    function selectedCode(select) {
      const option = select ? select.selectedOptions[0] : null;
      return option ? option.dataset.code || "" : "";
    }

    function setEnabled(field, enabled) {
      field.hidden = !enabled;
      field.querySelectorAll("input, select, textarea").forEach((element) => {
        element.disabled = !enabled;
      });
    }

    function emptyList(list) {
      if (list) {
        list.replaceChildren();
      }
    }

    function clearForeignOptions() {
      geoToken += 1;
      emptyList(regionList);
      emptyList(provinceList);
    }

    function loadGeoData() {
      if (geoData) {
        return Promise.resolve(geoData);
      }
      if (!geoRequest) {
        geoRequest = fetch("/static/foreign-geo.json", { cache: "no-store" })
          .then((response) => (response.ok ? response.json() : {}))
          .then((data) => {
            geoData = data && typeof data === "object" ? data : {};
            return geoData;
          })
          .catch(() => {
            geoData = {};
            return geoData;
          });
      }
      return geoRequest;
    }

    function fillList(list, values) {
      if (!list) {
        return;
      }
      const fragment = document.createDocumentFragment();
      const items = Array.isArray(values) ? values : [];
      items.forEach((value) => {
        const option = document.createElement("option");
        option.value = String(value);
        fragment.appendChild(option);
      });
      list.replaceChildren(fragment);
    }

    function populateForeignOptions(code) {
      if (!code || code === "IT") {
        clearForeignOptions();
        return;
      }
      const token = ++geoToken;
      loadGeoData().then((data) => {
        if (token !== geoToken) {
          return;
        }
        const entry = data[code] || {};
        fillList(regionList, entry.regions);
        fillList(provinceList, entry.provinces);
      });
    }

    function apply() {
      const effectiveCountry =
        country.value ||
        (birthCountry ? birthCountry.value : "") ||
        "Italia";
      const isForeign = effectiveCountry !== "Italia";
      const code = country.value
        ? selectedCode(country)
        : selectedCode(birthCountry);
      const changed = lastCode !== null && code !== lastCode;
      lastCode = code;
      italianFields.forEach((field) => setEnabled(field, !isForeign));
      foreignFields.forEach((field) => setEnabled(field, isForeign));
      if (isForeign) {
        if (changed) {
          if (regionInput) {
            regionInput.value = "";
          }
          if (provinceInput) {
            provinceInput.value = "";
          }
        }
        populateForeignOptions(code);
      } else {
        if (changed) {
          if (regionInput) {
            regionInput.value = "";
          }
          if (provinceInput) {
            provinceInput.value = "";
          }
        }
        clearForeignOptions();
      }
    }

    country.addEventListener("change", apply);
    if (birthCountry) {
      birthCountry.addEventListener("change", apply);
    }
    apply();
  }

  const cityCatalog = { data: null, request: null };
  const foreignCityCatalog = { data: null, request: null };
  let italianCityIndex = null;

  function loadCityCatalog() {
    if (cityCatalog.data) {
      return Promise.resolve(cityCatalog.data);
    }
    if (!cityCatalog.request) {
      cityCatalog.request = fetch("/static/italian-cities.json", {
        cache: "no-store",
      })
        .then((response) => (response.ok ? response.json() : {}))
        .then((data) => {
          cityCatalog.data =
            data && typeof data === "object" && data.provinces
              ? data
              : { provinces: {} };
          return cityCatalog.data;
        })
        .catch(() => {
          cityCatalog.data = { provinces: {} };
          return cityCatalog.data;
        });
    }
    return cityCatalog.request;
  }

  function loadForeignCityCatalog() {
    if (foreignCityCatalog.data) {
      return Promise.resolve(foreignCityCatalog.data);
    }
    if (!foreignCityCatalog.request) {
      foreignCityCatalog.request = fetch("/static/foreign-cities.json", {
        cache: "no-store",
      })
        .then((response) => (response.ok ? response.json() : {}))
        .then((data) => {
          foreignCityCatalog.data =
            data && typeof data === "object" && data.countries
              ? data
              : { countries: {} };
          return foreignCityCatalog.data;
        })
        .catch(() => {
          foreignCityCatalog.data = { countries: {} };
          return foreignCityCatalog.data;
        });
    }
    return foreignCityCatalog.request;
  }

  function cityLookupKey(value) {
    return String(value || "")
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLocaleLowerCase("it")
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function italianCityLookup(data) {
    if (italianCityIndex !== null) {
      return italianCityIndex;
    }
    const index = new Map();
    Object.entries(data.provinces || {}).forEach(([province, names]) => {
      if (!Array.isArray(names)) {
        return;
      }
      names.forEach((name) => {
        const key = cityLookupKey(name);
        if (!key) {
          return;
        }
        const entries = index.get(key) || [];
        entries.push({ name, province });
        index.set(key, entries);
      });
    });
    italianCityIndex = index;
    return index;
  }

  function fillCityList(list, values) {
    const unique = [...new Set((Array.isArray(values) ? values : []).map(String))]
      .filter(Boolean)
      .sort((left, right) => left.localeCompare(right, "it"));
    const fragment = document.createDocumentFragment();
    unique.forEach((name) => {
      const option = document.createElement("option");
      option.value = name;
      fragment.appendChild(option);
    });
    list.replaceChildren(fragment);
  }

  function applyCityMatch(value, countryCode, data, refs) {
    const key = cityLookupKey(value);
    if (!key) {
      return;
    }

    if (countryCode === "IT") {
      const entries = italianCityLookup(data).get(key) || [];
      let match = null;
      if (refs.province && refs.province.value) {
        match = entries.find((entry) => entry.province === refs.province.value) || null;
      } else if (entries.length === 1) {
        match = entries[0];
      }
      if (!match || !refs.province) {
        return;
      }
      const option = Array.from(refs.province.options).find(
        (candidate) => candidate.value === match.province
      );
      if (!option) {
        return;
      }
      const optionRegion = option.dataset.region || "";
      const needsRegion =
        refs.region && optionRegion && refs.region.value !== optionRegion;
      const needsProvince = refs.province.value !== match.province;
      if (!needsRegion && !needsProvince) {
        return;
      }
      if (needsRegion) {
        refs.region.value = optionRegion;
        refs.region.dispatchEvent(new Event("change", { bubbles: true }));
      }
      refs.province.value = match.province;
      refs.province.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }

    const country = data.countries ? data.countries[countryCode] : null;
    if (!country) {
      return;
    }
    const cities = country.cities || {};
    let canonicalKey = key;
    if (!Object.prototype.hasOwnProperty.call(cities, canonicalKey)) {
      canonicalKey = (country.aliases || {})[key] || "";
    }
    const record = canonicalKey ? cities[canonicalKey] : null;
    if (!Array.isArray(record)) {
      return;
    }
    const regions = Array.isArray(country.regions) ? country.regions : [];
    const provinces = Array.isArray(country.provinces) ? country.provinces : [];
    const regionValue = record[1] >= 0 ? regions[record[1]] || "" : "";
    const provinceValue = record[2] >= 0 ? provinces[record[2]] || "" : "";
    if (refs.foreignRegion) {
      refs.foreignRegion.value = regionValue;
    }
    if (refs.foreignProvince) {
      refs.foreignProvince.value = provinceValue;
    }
  }

  function initCitySuggestions(prefix) {
    const input = form.querySelector(`[name="${prefix}_city"]`);
    const list = document.getElementById(`${prefix}-city-options`);
    const country = form.querySelector(`[data-country-select="${prefix}"]`);
    const birthCountry =
      prefix === "residence"
        ? form.querySelector('[name="birth_country"]')
        : null;
    const province = form.querySelector(`[data-province-select="${prefix}"]`);
    const region = form.querySelector(`[data-region-select="${prefix}"]`);
    const foreignRegion = form.querySelector(
      `[name="${prefix}_region_foreign"]`
    );
    const foreignProvince = form.querySelector(
      `[name="${prefix}_province_foreign"]`
    );
    if (!input || !list) {
      return;
    }

    const refs = { province, region, foreignRegion, foreignProvince };
    let requestToken = 0;
    let inputTimer = null;

    function selectedCountryCode() {
      const effectiveCountry =
        country.value ||
        (birthCountry ? birthCountry.value : "") ||
        "Italia";
      if (effectiveCountry === "Italia") {
        return "IT";
      }
      const select = country.value ? country : birthCountry;
      const option = select ? select.selectedOptions[0] : null;
      return option ? option.dataset.code || "" : "";
    }

    function loadForCountry(code) {
      return code === "IT" ? loadCityCatalog() : loadForeignCityCatalog();
    }

    function populate() {
      const token = ++requestToken;
      const code = selectedCountryCode();
      if (!code) {
        list.replaceChildren();
        return;
      }
      loadForCountry(code).then((data) => {
        if (token !== requestToken || selectedCountryCode() !== code) {
          return;
        }
        let names = [];
        if (code === "IT") {
          const provinces = data.provinces || {};
          const selectedProvince = province ? province.value : "";
          names = selectedProvince
            ? provinces[selectedProvince]
            : Object.values(provinces).flat();
        } else {
          const countryData = (data.countries || {})[code] || {};
          names = Object.values(countryData.cities || {}).map((record) =>
            Array.isArray(record) ? record[0] : ""
          );
          if (Array.isArray(countryData.alias_names)) {
            names = names.concat(countryData.alias_names);
          }
        }
        fillCityList(list, names);
      });
    }

    function applyCurrentCity() {
      const value = input.value.trim();
      const code = selectedCountryCode();
      if (!value || !code) {
        return;
      }
      loadForCountry(code).then((data) => {
        if (selectedCountryCode() !== code || input.value.trim() !== value) {
          return;
        }
        applyCityMatch(value, code, data, refs);
      });
    }

    function handleCityInput() {
      clearTimeout(inputTimer);
      inputTimer = setTimeout(applyCurrentCity, 120);
    }

    function resetGeoFields() {
      if (province) {
        province.value = "";
      }
      if (region) {
        region.value = "";
      }
      if (foreignRegion) {
        foreignRegion.value = "";
      }
      if (foreignProvince) {
        foreignProvince.value = "";
      }
    }

    function handleCountryChange() {
      resetGeoFields();
      populate();
      applyCurrentCity();
    }

    function handleBirthCountryChange() {
      if (prefix === "residence" && country.value) {
        return;
      }
      resetGeoFields();
      populate();
      applyCurrentCity();
    }

    country.addEventListener("change", handleCountryChange);
    if (birthCountry) {
      birthCountry.addEventListener("change", handleBirthCountryChange);
    }
    if (province) {
      province.addEventListener("change", populate);
    }
    if (region) {
      region.addEventListener("change", populate);
    }
    input.addEventListener("input", handleCityInput);
    input.addEventListener("change", applyCurrentCity);
    input.addEventListener("blur", applyCurrentCity);
    populate();
  }

  function clearError() {
    if (errorBox) {
      errorBox.hidden = true;
      errorBox.textContent = "";
    }
    form.querySelectorAll(".has-error").forEach((element) => {
      element.classList.remove("has-error");
    });
  }

  function showError(message, element) {
    if (errorBox) {
      errorBox.textContent = message;
      errorBox.hidden = false;
    }
    if (element) {
      element.classList.add("has-error");
      element.focus();
    }
  }

  function fieldLabel(element) {
    if (element.id) {
      const explicit = document.querySelector(`label[for="${element.id}"]`);
      if (explicit) {
        return explicit.textContent.trim();
      }
    }
    const label = element.closest("label");
    const span = label ? label.querySelector("span") : null;
    if (span) {
      return span.textContent.trim();
    }
    return element.name || "Campo";
  }

  function validateStep(index) {
    const step = steps[index];
    if (!step) {
      return true;
    }
    const required = Array.from(step.querySelectorAll("[data-required]"));
    for (const element of required) {
      const value = String(element.value || "").trim();
      if (!value) {
        showError(`Compila il campo “${fieldLabel(element)}”.`, element);
        return false;
      }
      if (!element.checkValidity()) {
        showError(
          `Il campo “${fieldLabel(element)}” non è valido. Controlla il formato.`,
          element
        );
        return false;
      }
    }

    const birthDate = step.querySelector('[name="birth_date"]');
    if (birthDate && birthDate.value) {
      const selected = new Date(`${birthDate.value}T00:00:00`);
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      if (Number.isNaN(selected.getTime()) || selected > today) {
        showError("La data di nascita non può essere nel futuro.", birthDate);
        return false;
      }
    }

    const numbers = Array.from(
      step.querySelectorAll('input[type="number"][min], input[type="range"][min]')
    );
    for (const element of numbers) {
      if (element.value !== "" && !element.checkValidity()) {
        showError(
          `Il campo “${fieldLabel(element)}” è fuori dall’intervallo consentito.`,
          element
        );
        return false;
      }
    }

    clearError();
    return true;
  }

  function showStep(index, preserveError) {
    currentStep = Math.max(0, Math.min(index, steps.length - 1));
    steps.forEach((step, position) => {
      const active = position === currentStep;
      step.hidden = !active;
      step.classList.toggle("is-active", active);
    });
    progressButtons.forEach((button, position) => {
      const active = position === currentStep;
      button.classList.toggle("is-active", active);
      if (active) {
        button.setAttribute("aria-current", "step");
      } else {
        button.removeAttribute("aria-current");
      }
    });
    if (backButton) {
      backButton.disabled = currentStep === 0;
    }
    if (nextButton) {
      nextButton.hidden = currentStep === steps.length - 1;
    }
    if (submitButton) {
      submitButton.hidden = currentStep !== steps.length - 1;
    }
    if (reviewBox) {
      const reviewStep = reviewBox.closest(".wizard-step");
      if (reviewStep && steps.indexOf(reviewStep) === currentStep) {
        renderReview();
      }
    }
    if (!preserveError) {
      clearError();
    }
  }

  function displayValue(element) {
    if (element.type === "range") {
      return `${element.value}/10`;
    }
    if (element.tagName === "SELECT") {
      if (!element.value) {
        return "";
      }
      const option = element.options[element.selectedIndex];
      return option ? option.textContent.trim() : element.value;
    }
    return String(element.value || "").trim();
  }

  function renderReview() {
    if (!reviewBox) {
      return;
    }
    reviewBox.textContent = "";
    const groups = [
      { index: 0, title: "Identità e contesto" },
      { index: 1, title: "Carattere e aspetto" },
      { index: 2, title: "Relazioni" },
      { index: 3, title: "Storia" },
    ];

    groups.forEach((group) => {
      const step = steps[group.index];
      if (!step) {
        return;
      }
      const section = document.createElement("section");
      section.className = "setup-review-group";
      const heading = document.createElement("h3");
      heading.textContent = group.title;
      section.appendChild(heading);

      const list = document.createElement("dl");
      list.className = "setup-review-list";
      let count = 0;
      step.querySelectorAll("input[name], select[name], textarea[name]").forEach(
        (element) => {
          if (
            element.type === "hidden" ||
            element.name === "csrf" ||
            element.disabled
          ) {
            return;
          }
          const value = displayValue(element);
          if (!value) {
            return;
          }
          const term = document.createElement("dt");
          term.textContent = fieldLabel(element);
          const description = document.createElement("dd");
          description.textContent = value;
          list.appendChild(term);
          list.appendChild(description);
          count += 1;
        }
      );

      if (count === 0) {
        const empty = document.createElement("p");
        empty.className = "setup-review-empty";
        empty.textContent = "Nessun dato inserito in questa sezione.";
        section.appendChild(empty);
      } else {
        section.appendChild(list);
      }
      reviewBox.appendChild(section);
    });
  }

  function nextStep() {
    if (validateStep(currentStep)) {
      showStep(currentStep + 1);
    }
  }

  function previousStep() {
    showStep(currentStep - 1);
  }

  function submitSetup(event) {
    for (let index = 0; index < steps.length; index += 1) {
      if (!validateStep(index)) {
        showStep(index, true);
        return;
      }
    }
    // The server is the source of truth; this only prevents obvious omissions.
    if (formHidden) {
      event.preventDefault();
    }
  }

  form.addEventListener("submit", submitSetup);
  if (nextButton) {
    nextButton.addEventListener("click", nextStep);
  }
  if (backButton) {
    backButton.addEventListener("click", previousStep);
  }

  progressButtons.forEach((button, index) => {
    button.addEventListener("click", () => {
      if (index > currentStep) {
        for (let position = currentStep; position < index; position += 1) {
          if (!validateStep(position)) {
            showStep(position, true);
            return;
          }
        }
      }
      showStep(index);
    });
  });

  document.querySelectorAll("[data-add-row]").forEach((button) => {
    button.addEventListener("click", () => {
      const type = button.dataset.addRow;
      addRow(type);
      const container = document.getElementById(rowTypes[type].container);
      const lastRow = container ? container.lastElementChild : null;
      const firstInput = lastRow ? lastRow.querySelector("input, select, textarea") : null;
      if (firstInput) {
        firstInput.focus();
      }
    });
  });

  form.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-row]");
    if (!button) {
      return;
    }
    const row = button.closest(".setup-row");
    if (row) {
      row.remove();
      clearError();
    }
  });

  applyScalarValues(initialValues, !hasInitialValues);
  initializeRows(initialValues);
  initTraitGuides();
  initVisualSuggestions();
  initLifeSuggestions();
  initProfileGenerator();
  initNoteIdeas(
    form.querySelector('[name="family_notes"]'),
    form.querySelector('[data-note-ideas="family"]'),
    familyNoteIdeas
  );
  initGeoMenus();
  initForeignGeoFields("birth");
  initForeignGeoFields("residence");
  initCitySuggestions("birth");
  initCitySuggestions("residence");
  showStep(0);
})();
