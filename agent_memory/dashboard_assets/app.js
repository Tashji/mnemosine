"use strict";

const VIEW_META = {
  overview: {
    title: "Panoramica",
    subtitle: "Stato, memoria e identità in un'unica vista.",
  },
  life: {
    title: "Vita",
    subtitle: "Bisogni, attività, loop e ultimo episodio deliberativo.",
  },
  relations: {
    title: "Relazioni",
    subtitle: "Contatti, fiducia e storico completo delle interazioni.",
  },
  acquaintances: {
    title: "Incontrati",
    subtitle: "Profili progressivi di persone conosciute per gradi.",
  },
  timeline: {
    title: "Timeline",
    subtitle: "Ricordi episodici, fonti e livelli di accesso.",
  },
  traits: {
    title: "Tratti",
    subtitle: "Personalità conscia e configurazione amministrativa dell'ombra.",
  },
  semantic: {
    title: "Conoscenze",
    subtitle: "Memoria semantica con provenienza, affidabilità e stato.",
  },
  evolution: {
    title: "Evoluzioni",
    subtitle: "Seed, variazioni dei tratti e riconsolidamenti.",
  },
  settings: {
    title: "Impostazioni",
    subtitle: "Integrazioni, comportamento e ciclo di vita del profilo.",
  },
  visual: {
    title: "Aspetto",
    subtitle: "Seed visivo, caratteristiche fisiche e generazione manuale di immagini.",
  },
  maintenance: {
    title: "Manutenzione",
    subtitle: "Backup verificati, controllo di coerenza e stato dell'indice vettoriale.",
  },
};

const SOURCE_LABELS = {
  real_interaction: "Interazione reale",
  generated: "Generato",
  admin_seed: "Seed admin",
  world_event: "Evento mondiale",
  routine: "Routine",
};

const ACCESS_LABELS = {
  conscious: "Conscio",
  latent: "Latente",
  repressed: "Represso",
};

const KNOWLEDGE_LABELS = {
  verified_fact: "Fatto verificato",
  user_provided: "Fornito dall'utente",
  opinion: "Opinione",
  hypothesis: "Ipotesi",
  uncertain: "Incerto",
};

const STATUS_LABELS = {
  active: "Attiva",
  superseded: "Superata",
  retracted: "Ritirata",
};

const RELATION_LABELS = {
  associated: "Associato",
  caused_by: "Causato da",
  follows: "Segue",
  contradicts: "Contraddice",
  same_person: "Stessa persona",
  same_place: "Stesso luogo",
  thematic: "Tematico",
  related_to: "Collegata",
  explains: "Spiega",
  illustrates: "Illustra",
  derived_from: "Derivata da",
};

const PROFILE_STATUS_LABELS = {
  ready: "Pronto",
  missing: "Assente",
  incomplete: "Incompleto",
};

const ACQUAINTANCE_STATUS_LABELS = {
  unknown: "Sconosciuto",
  acquaintance: "Conoscente",
  known: "Conosciuto",
};

const ACQUAINTANCE_FACT_STATUS_LABELS = {
  candidate: "Candidato",
  confirmed: "Confermato",
  rejected: "Scartato",
};

const ACQUAINTANCE_ORIGIN_LABELS = {
  manual: "Aggiunto a mano",
  extracted: "Estratto da un messaggio",
  imported: "Importato",
};

// Proposte rapide per la vista Aspetto: le stesse voci del creatore del
// wizard, ridotte a descrizioni pronte da inserire nel profilo visivo.
const VISUAL_SUGGESTIONS = {
  body: [
    "corporatura esile, ossatura fine, altezza 163 cm",
    "corporatura snella, altezza 170 cm",
    "corporatura media e proporzionata, altezza 175 cm",
    "corporatura atletica e tonica, altezza 180 cm",
    "corporatura robusta, spalle larghe, altezza 186 cm",
    "corporatura morbida e arrotondata, altezza 168 cm",
  ],
  face: [
    "viso ovale, lineamenti morbidi, carnagione chiara",
    "viso rotondo, guance piene, carnagione olivastra",
    "viso squadrato, mascella definita, carnagione ambrata",
    "viso allungato, zigomi alti, carnagione scura",
    "viso a cuore, fronte ampia e mento appuntito",
    "viso triangolare, fronte stretta e mascella ampia",
  ],
  hair: [
    "capelli corti castani",
    "capelli medi mossi castano scuro",
    "capelli lunghi lisci neri",
    "capelli ricci biondi",
    "capelli raccolti in una coda, castani chiari",
    "capelli rasati, grigi",
  ],
  eyes: [
    "occhi a mandorla castani",
    "occhi grandi verdi",
    "occhi allungati azzurri",
    "occhi tondi nocciola",
    "occhi grigi",
    "occhi ambra",
  ],
  distinctive_marks: [
    "nessun segno evidente",
    "un neo vicino al labbro",
    "una piccola cicatrice sul sopracciglio",
    "lentiggini sul naso",
    "una barba corta e curata",
    "occhiali dalla montatura sottile",
  ],
  style: [
    "casual, jeans e magliette scure",
    "minimal, colori neutri",
    "sportivo, felpe e sneakers",
    "elegante ma informale, camicia e giacca",
    "alternativo, giacca di pelle e anelli",
    "formale, abito scuro",
  ],
};

const DEFENSE_LABELS = {
  projection: "Proiezione",
  rationalization: "Razionalizzazione",
  avoidance: "Evitamento",
  denial: "Negazione",
  displacement: "Spostamento",
  sublimation: "Sublimazione",
  none: "Nessuna",
};

const state = {
  view: "overview",
  loaded: {},
  sequence: { timeline: 0, semantic: 0 },
  relationId: null,
  relationOffset: 0,
  lastFocus: null,
};

const $ = (selector) => document.querySelector(selector);

function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = String(value);
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "style") node.setAttribute("style", value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function clear(node, ...children) {
  node.replaceChildren();
  append(node, children);
  return node;
}

function text(value, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function fmtDate(value, { time = false } = {}) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const options = time
    ? { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }
    : { day: "2-digit", month: "short", year: "numeric" };
  return new Intl.DateTimeFormat("it-IT", options).format(date);
}

function fmtTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("it-IT", { hour: "2-digit", minute: "2-digit" }).format(date);
}

function fmtPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${Math.round(number * 100)}%`;
}

function label(mapping, value) {
  return mapping[value] || value || "—";
}

function badge(labelText, className) {
  return h("span", { class: `badge ${className}`, text: labelText });
}

function sourceBadge(source) {
  return badge(label(SOURCE_LABELS, source), "badge-source");
}

function accessBadge(access) {
  return badge(label(ACCESS_LABELS, access), `badge-access-${access || "conscious"}`);
}

function knowledgeBadge(type) {
  return badge(label(KNOWLEDGE_LABELS, type), "badge-type");
}

function statusBadge(status) {
  return badge(label(STATUS_LABELS, status), `badge-status-${status || "active"}`);
}

function importanceMeter(value) {
  const number = Math.max(0, Math.min(10, Number(value) || 0));
  const wrapper = h("span", { class: "importance", title: `Importanza ${number}/10`, "aria-label": `Importanza ${number} su 10` });
  for (let index = 1; index <= 10; index += 1) {
    wrapper.append(h("span", { class: index <= number ? "is-on" : "" }));
  }
  return wrapper;
}

function emptyState(titleText, detailText) {
  return h(
    "div",
    { class: "empty-state" },
    h("strong", { text: titleText }),
    h("span", { text: detailText || "" })
  );
}

function loadingBlock(message) {
  return h(
    "div",
    { class: "loading-block" },
    h("span", { class: "spinner", "aria-hidden": "true" }),
    message
  );
}

function setLoading(container, message) {
  clear(container, loadingBlock(message));
}

function setNotice(message, kind = "error") {
  const notice = $("#notice");
  if (!message) {
    notice.hidden = true;
    notice.textContent = "";
    notice.classList.remove("is-success");
    return;
  }
  notice.hidden = false;
  notice.textContent = message;
  notice.classList.toggle("is-success", kind === "success");
}

async function api(path) {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  let body = null;
  try {
    body = await response.json();
  } catch (_error) {
    body = null;
  }
  if (response.status === 401 && path !== "/api/session") {
    const next = window.location.pathname + window.location.search;
    window.location.assign(`/login?next=${encodeURIComponent(next)}`);
    throw new Error("Sessione scaduta.");
  }
  if (!response.ok) {
    throw new Error((body && body.error) || `Errore HTTP ${response.status}`);
  }
  return body;
}


async function postJson(path, payload) {
  const session = await api("/api/session");
  if (!session.csrf) {
    throw new Error("Sessione non valida: ricarica la pagina.");
  }
  const response = await fetch(path, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    cache: "no-store",
    body: JSON.stringify({ ...payload, csrf: session.csrf }),
  });
  let body = null;
  try {
    body = await response.json();
  } catch (_error) {
    body = null;
  }
  if (response.status === 401) {
    const next = window.location.pathname + window.location.search;
    window.location.assign(`/login?next=${encodeURIComponent(next)}`);
    throw new Error("Sessione scaduta.");
  }
  if (!response.ok) {
    throw new Error((body && body.error) || `Errore HTTP ${response.status}`);
  }
  return body;
}

async function loadSession() {
  try {
    const session = await api("/api/session");
    if (session.auth_enabled) {
      $("#logout-form").hidden = false;
    }
  } catch (_error) {
    // La pagina principale è già protetta dal server; il modulo resta nascosto.
  }
}

/* -- panoramica ---------------------------------------------------------- */

function autonomyRunsPanel(autonomy) {
  const labels = {
    ready: "Pronto", running: "In corso", awaiting_verification: "Da verificare",
    uncertain: "Esito incerto", blocked: "Interrotto", completed: "Completato",
  };
  const runs = Array.isArray(autonomy.runs) ? autonomy.runs : [];
  const cards = runs.map((run) => {
    const action = run.last_action;
    const needsReview = run.review_required;
    let reviewForm = null;
    if (needsReview) {
      const reasonText = {
        uncertain: "Un'azione potrebbe essere ancora in corso oppure già riuscita.",
        expired: "L'incarico è scaduto; le azioni precedenti potrebbero già aver prodotto effetti.",
        blocked: "L'incarico è interrotto; le azioni precedenti potrebbero già aver prodotto effetti.",
      }[run.review_reason] || "L'incarico richiede una revisione.";
      reviewForm = settingsForm("Registra revisione e chiudi", [
        h("p", { class: "settings-warning", text: `${reasonText} Controlla prima il sito esterno. La chiusura non ripete azioni e non certifica una consegna; rimuove l'impulso originale solo se è ancora quello attuale.` }),
        settingsField("Cosa hai verificato", h("textarea", { name: "note", rows: "2", maxlength: "1000", required: true }), "Non inserire password o dati sensibili."),
        settingsField("Conferma esplicita", settingsInput("confirmation", "", "text", { required: true, placeholder: "Scrivi HO VERIFICATO" })),
      ], async (formData) => {
        const result = await postJson("/api/autonomy/review", {
          run_id: run.id, note: formData.get("note"), confirmation: formData.get("confirmation"),
        });
        await loadOverview(true);
        setNotice(result.impulse_cleared
          ? "Revisione registrata. Impulso originale rimosso; nessuna consegna dichiarata."
          : "Revisione registrata. L'impulso attuale è stato conservato; nessuna consegna dichiarata.", "success");
      }, { danger: true });
    }
    return h("article", { class: "autonomy-run" },
      h("div", { class: "autonomy-run-head" },
        h("strong", { text: needsReview ? "Revisione richiesta" : text(labels[run.state], run.state) }),
        h("span", { text: fmtDate(run.created_at, { time: true }) })
      ),
      h("p", { text: `${text(run.origin)} · ${run.steps} / 20 passi · ${text(run.trigger, "impulso")}` }),
      action ? h("p", { text: `Ultima azione: ${text(action.action)}${action.uncertain ? " · esito incerto" : ""}` }) : null,
      run.review ? h("p", { text: `Revisionato ${fmtDate(run.review.reviewed_at, { time: true })}: ${run.review.note}` }) : null,
      reviewForm
    );
  });
  return h("section", { class: "panel autonomy-runs-panel" },
    h("div", { class: "panel-header" }, h("h3", { text: "Incarichi browser" }),
      h("span", { class: "count-chip", text: `Ultimi ${runs.length}` })),
    h("div", { class: "panel-body" },
      h("p", { class: "autonomy-run-intro", text: "La verifica di una pagina non certifica una consegna. Rivedi gli esiti incerti prima di procedere." }),
      cards.length ? cards : emptyState("Nessun incarico", "Gli incarichi dell'agente compariranno qui.")
    )
  );
}

function renderOverview(data) {
  const identity = data.identity || {};
  const current = data.state || {};
  const counts = data.counts || {};
  const activity = current.activity || {};
  const emotion = current.emotion || {};
  const routine = current.routine || {};
  const heartbeat = current.heartbeat || {};
  const pendingImpulse =
    heartbeat.pending_impulse && typeof heartbeat.pending_impulse === "object"
      ? heartbeat.pending_impulse
      : null;
  const heartbeatSummary = pendingImpulse
    ? `Impulso in attesa (${text(pendingImpulse.trigger)})`
    : text(heartbeat.last_decision, "inattivo");
  const place = identity.residence || {};
  const birth = identity.birth_place || {};

  const identityCard = h(
    "article",
    { class: "identity-card" },
    h("p", { class: "eyebrow", text: "Identità attiva" }),
    h("div", { class: "identity-name", text: text(identity.full_name) }),
    h(
      "div",
      { class: "identity-meta" },
      h("span", { text: identity.age ? `${identity.age} anni` : "Età non disponibile" }),
      h("span", { text: [place.city, place.region].filter(Boolean).join(", ") || "Residenza non disponibile" }),
      h("span", { text: `Nato a ${[birth.city, birth.region].filter(Boolean).join(", ") || "—"}` }),
      h("span", { text: identity.timezone || "—" })
    ),
    h("p", {
      class: "identity-note",
      text: `Profilo ${text(identity.identity_id)} · ${counts.identity_traits || 0} tratti consci · ${counts.shadow_traits || 0} tratti dell'ombra · ${counts.history_changes || 0} variazioni registrate.`,
    })
  );

  const load = Math.max(0, Math.min(100, Number(current.mental_load) || 0));
  const stateCard = h(
    "article",
    { class: "state-card" },
    h(
      "div",
      { class: "state-top" },
      h("div", { class: "state-status", text: text(current.operational_status, "stato sconosciuto") }),
      h("span", { class: "mono", text: fmtTime((current.clock || {}).local_time) })
    ),
    h("div", { class: "state-activity", text: text(activity.label, "Nessuna attività") }),
    h("div", { class: "state-detail", text: text(activity.details, "") }),
    h(
      "dl",
      { class: "state-lines" },
      h("div", { class: "state-line" }, h("dt", { text: "Emozione" }), h("dd", { text: text(emotion.label) })),
      h(
        "div",
        { class: "state-line" },
        h("dt", { text: "Carico" }),
        h(
          "dd",
          {},
          `${load}/100`,
          h("div", { class: "load-track" }, h("div", { class: "load-fill", style: `width:${load}%` }))
        )
      ),
      h("div", { class: "state-line" }, h("dt", { text: "Routine" }), h("dd", { text: text(routine.day_label || routine.mode) })),
      h("div", { class: "state-line" }, h("dt", { text: "Luogo" }), h("dd", { text: text(activity.location) })),
      h("div", { class: "state-line" }, h("dt", { text: "Battito" }), h("dd", { text: heartbeatSummary }))
    )
  );

  const metric = (labelText, value, note) =>
    h(
      "div",
      { class: "metric-card" },
      h("div", { class: "metric-label", text: labelText }),
      h("div", { class: "metric-value", text: value }),
      h("div", { class: "metric-note", text: note })
    );

  const metrics = h(
    "div",
    { class: "metric-grid" },
    metric("Ricordi", counts.memories ?? 0, `${counts.conscious || 0} consci · ${(counts.latent || 0) + (counts.repressed || 0)} non consci`),
    metric("Conoscenze", counts.semantic_items ?? 0, `${counts.active_semantic || 0} attive`),
    metric("Tratti", (counts.identity_traits || 0) + (counts.shadow_traits || 0), `${counts.identity_traits || 0} consci · ${counts.shadow_traits || 0} ombra`),
    metric("Cascate", counts.cascades ?? 0, `${counts.history_changes || 0} variazioni di personalità`)
  );

  const recentPanel = h(
    "section",
    { class: "panel" },
    h(
      "div",
      { class: "panel-header" },
      h("h3", { text: "Ricordi recenti" }),
      h("button", { class: "panel-link", type: "button", onClick: () => navigate("timeline"), text: "Apri timeline" })
    ),
    h(
      "div",
      { class: "panel-body" },
      (data.recent_memories || []).length
        ? data.recent_memories.map((memory) =>
            h(
              "div",
              { class: "recent-item" },
              h("div", { class: "recent-date", text: fmtDate(memory.event_date) }),
              h(
                "div",
                {},
                h("div", { class: "recent-title", text: text(memory.title) }),
                h("div", { class: "recent-desc", text: text(memory.description, "") })
              )
            )
          )
        : emptyState("Nessun ricordo", "La timeline è vuota.")
    )
  );

  const historyPanel = h(
    "section",
    { class: "panel" },
    h(
      "div",
      { class: "panel-header" },
      h("h3", { text: "Ultime evoluzioni" }),
      h("button", { class: "panel-link", type: "button", onClick: () => navigate("evolution"), text: "Vedi storico" })
    ),
    h(
      "div",
      { class: "panel-body" },
      (data.recent_history || []).length
        ? data.recent_history.map(historyEntry)
        : emptyState("Nessuna evoluzione", "Non sono ancora state applicate variazioni.")
    )
  );

  const contacts = Array.isArray(data.contacts) ? data.contacts : [];
  const contactsPanel = h(
    "section",
    { class: "panel contacts-panel" },
    h(
      "div",
      { class: "panel-header" },
      h("h3", { text: "Relazioni" }),
      h("span", { class: "count-chip", text: `${contacts.length}` })
    ),
    contacts.length
      ? h(
          "div",
          { class: "contact-grid" },
          contacts.map((contact) =>
            h(
              "article",
              {
                class: "contact-card contact-card-action",
                role: "button",
                tabindex: "0",
                "aria-label": `Apri la relazione con ${text(contact.display_name, contact.user_id)}`,
                onClick: () => openRelation(contact.user_id),
                onKeydown: (event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    openRelation(contact.user_id);
                  }
                },
              },
              h(
                "div",
                { class: "contact-top" },
                h(
                  "div",
                  {},
                  h("div", { class: "contact-name", text: text(contact.display_name, contact.user_id) }),
                  h("div", { class: "contact-relation", text: text(contact.relation) })
                ),
                h(
                  "div",
                  { class: "contact-count", title: "Interazioni registrate" },
                  h("strong", { text: String(contact.interaction_count ?? 0) }),
                  h("span", { text: "interazioni" })
                )
              ),
              h(
                "div",
                { class: "contact-last" },
                h("span", { class: "contact-label", text: "Ultima interazione" }),
                h("strong", {
                  text: contact.last_interaction_at
                    ? fmtDate(contact.last_interaction_at, { time: true })
                    : "Nessuna registrata",
                }),
                contact.last_interaction_summary
                  ? h("p", { class: "contact-summary", text: contact.last_interaction_summary })
                  : null
              ),
              h(
                "div",
                { class: "contact-meta" },
                h("span", { text: `Fiducia ${text(contact.trust)}/100` }),
                h("span", { text: `Intimità ${text(contact.intimacy)}/100` })
              ),
              h("span", { class: "contact-open", text: "Apri scheda →", "aria-hidden": "true" })
            )
          )
        )
      : h(
          "div",
          { class: "panel-body" },
          emptyState("Nessuna relazione", "Il grafo delle relazioni è vuoto.")
        )
  );

  const outbox = data.outbox || {};
  const outboxRecent = Array.isArray(outbox.recent) ? outbox.recent : [];
  const impulsesToday = (heartbeat.impulses_today || {}).count ?? 0;
  const maxImpulses = heartbeat.max_impulses_per_day ?? "—";
  const pendingTarget = pendingImpulse && pendingImpulse.target ? pendingImpulse.target : {};
  const outboxPanel = h(
    "section",
    { class: "panel" },
    h(
      "div",
      { class: "panel-header" },
      h("h3", { text: "Ciclo autonomo e outbox" }),
      h("span", { class: "count-chip", text: `${outbox.count || 0} consegne` })
    ),
    h(
      "div",
      { class: "panel-body" },
      h(
        "div",
        { class: "autonomy-summary" },
        h(
          "div",
          { class: "autonomy-stat" },
          h("span", { text: "Ultimo battito" }),
          h("strong", { text: fmtDate(heartbeat.last_run_at, { time: true }) })
        ),
        h(
          "div",
          { class: "autonomy-stat" },
          h("span", { text: "Prossimo battito" }),
          h("strong", { text: fmtDate(heartbeat.next_run_at, { time: true }) })
        ),
        h(
          "div",
          { class: "autonomy-stat" },
          h("span", { text: "Impulsi oggi" }),
          h("strong", { text: `${impulsesToday} / ${maxImpulses}` })
        )
      ),
      pendingImpulse
        ? h(
            "div",
            { class: "pending-impulse-box" },
            h(
              "strong",
              {
                text: `Impulso in attesa · ${text(pendingImpulse.trigger)}${
                  pendingTarget.display_name || pendingTarget.user_id
                    ? ` → ${pendingTarget.display_name || pendingTarget.user_id}`
                    : ""
                }`,
              }
            ),
            h("span", { text: text(pendingImpulse.reason, "") })
          )
        : null,
      outboxRecent.length
        ? outboxRecent.map((msg) =>
            h(
              "div",
              { class: "recent-item" },
              h("div", { class: "recent-date", text: fmtDate(msg.delivered_at, { time: true }) }),
              h(
                "div",
                {},
                h("div", {
                  class: "recent-title",
                  text: `${text(msg.target_name, "Destinatario")} · ${text(msg.channel, "whatsapp")}`,
                }),
                h("div", { class: "recent-desc", text: text(msg.text, "") })
              )
            )
          )
        : emptyState("Outbox vuota", "Nessun messaggio spontaneo consegnato.")
    )
  );

  const files = data.files || {};
  const fileEntries = Array.isArray(files.entries) ? files.entries : [];
  const filesPanel = h(
    "section",
    { class: "panel" },
    h(
      "div",
      { class: "panel-header" },
      h("h3", { text: "Sandbox file (files/)" }),
      h("span", { class: "count-chip", text: `${files.count || 0}` })
    ),
    h(
      "div",
      { class: "panel-body" },
      fileEntries.length
        ? fileEntries.map((entry) =>
            h(
              "div",
              { class: "recent-item" },
              h("div", { class: "recent-date", text: fmtDate(entry.modified_at) }),
              h(
                "div",
                {},
                h("div", { class: "recent-title mono", text: text(entry.path || entry.name) }),
                h("div", {
                  class: "recent-desc",
                  text:
                    entry.type === "directory"
                      ? "Cartella"
                      : `${entry.size ?? 0} byte · ${text(entry.type, "file")}`,
                })
              )
            )
          )
        : emptyState("Sandbox vuota", "Nessun file presente in files/.")
    )
  );

  clear(
    $("#overview-content"),
    h("div", { class: "hero-grid" }, identityCard, stateCard),
    metrics,
    h("div", { class: "overview-columns" }, recentPanel, historyPanel),
    contactsPanel,
    autonomyRunsPanel(data.autonomy || {}),
    h("div", { class: "overview-columns overview-autonomy-columns" }, outboxPanel, filesPanel)
  );

  $("#footer-generated").textContent = `Aggiornata ${fmtDate(data.generated_at, { time: true })}`;
}

function historyEntry(entry) {
  const changes = entry.trait_changes || [];
  const detail = changes.length
    ? changes.map((change) => `${change.trait_id} ${change.old_value}→${change.new_value}`).join(" · ")
    : "Nessuna variazione di tratto";
  return h(
    "div",
    { class: "history-item" },
    h("span", { class: "history-dot", "aria-hidden": "true" }),
    h(
      "div",
      {},
      h("div", { class: "history-title", text: text(entry.reason, entry.seed_id || "Evoluzione") }),
      h("div", { class: "history-meta", text: `${fmtDate(entry.changed_at, { time: true })} · ${detail}` })
    )
  );
}

async function loadOverview(force = false) {
  if (state.loaded.overview && !force) return;
  const content = $("#overview-content");
  if (!content.querySelector(".hero-grid")) setLoading(content, "Caricamento della panoramica…");
  try {
    const data = await api("/api/overview");
    state.loaded.overview = data;
    renderOverview(data);
    setNotice("");
  } catch (error) {
    setNotice(`Impossibile caricare la panoramica: ${error.message}`);
    if (!content.querySelector(".hero-grid")) {
      clear(content, emptyState("Dati non disponibili", error.message));
    }
  }
}

/* -- relazioni ----------------------------------------------------------- */

function openRelation(userId) {
  state.relationId = userId;
  state.relationOffset = 0;
  delete state.loaded.relations;
  navigate("relations");
}

function relationStat(labelText, valueText) {
  return h(
    "div",
    { class: "relation-stat" },
    h("span", { text: labelText }),
    h("strong", { text: valueText })
  );
}

function scoreValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? `${number}/100` : "—";
}

function interactionCount(value) {
  const number = Math.max(0, Number(value) || 0);
  return `${number} ${number === 1 ? "interazione" : "interazioni"}`;
}

function relationEvent(entry) {
  const metaNodes = [];
  if (entry.sentiment) {
    metaNodes.push(h("span", { text: `Tono ${text(entry.sentiment)}` }));
  }
  if (entry.memory_id) {
    metaNodes.push(
      h("button", {
        class: "relation-memory-link",
        type: "button",
        title: `Apri il dettaglio del ricordo ${entry.memory_id}`,
        onClick: () => openMemory(entry.memory_id),
        text: `Ricordo ${entry.memory_id} →`,
      })
    );
  }
  return h(
    "li",
    { class: "relation-event" },
    h(
      "div",
      { class: "relation-event-when" },
      h("span", { class: "relation-event-date", text: fmtDate(entry.timestamp, { time: true }) }),
      h("span", { class: "relation-event-channel", text: text(entry.channel, "chat") })
    ),
    h(
      "div",
      { class: "relation-event-body" },
      h("p", {
        class: "relation-event-summary",
        text: text(entry.summary, "Nessun riassunto registrato."),
      }),
      metaNodes.length
        ? h("div", { class: "relation-event-meta" }, metaNodes)
        : null
    )
  );
}

function relationAbout(contact) {
  const preferences = Array.isArray(contact.preferences) ? contact.preferences : [];
  const facts = Array.isArray(contact.confirmed_facts) ? contact.confirmed_facts : [];
  const boundaries = Array.isArray(contact.boundaries) ? contact.boundaries : [];
  if (!preferences.length && !facts.length && !boundaries.length && !contact.notes) return null;
  return h(
    "div",
    { class: "relation-about" },
    preferences.length
      ? h(
          "div",
          { class: "relation-about-block" },
          h("span", { class: "relation-about-label", text: "Preferenze" }),
          h(
            "div",
            { class: "tag-list" },
            preferences.map((item) => h("span", { class: "tag", text: item }))
          )
        )
      : null,
    facts.length
      ? h(
          "div",
          { class: "relation-about-block" },
          h("span", { class: "relation-about-label", text: "Fatti confermati" }),
          h(
            "ul",
            { class: "relation-facts" },
            facts.map((item) => h("li", { text: item }))
          )
        )
      : null,
    boundaries.length
      ? h(
          "div",
          { class: "relation-about-block relation-about-wide" },
          h("span", { class: "relation-about-label", text: "Confini relazionali" }),
          h(
            "ul",
            { class: "relation-facts" },
            boundaries.map((item) => h("li", { text: item }))
          )
        )
      : null,
    contact.notes
      ? h("p", { class: "relation-notes", text: contact.notes })
      : null
  );
}

function contactEditor(contact = null) {
  const editing = Boolean(contact);
  const value = contact || {};
  const fields = [
    settingsField("Identificativo univoco", settingsInput("user_id", value.user_id || "", "text", {
      required: true, maxlength: "100", readonly: editing,
    }), "L'identificativo non cambia dopo la creazione."),
    ...[["display_name", "Nome visualizzato"], ["full_name", "Nome completo"], ["relation", "Relazione"]].map(
      ([name, title]) => settingsField(title, settingsInput(name, value[name] || "", "text", {
        required: name === "display_name", maxlength: "2000",
      }))
    ),
    ...[["trust", "Fiducia"], ["intimacy", "Intimità"]].map(
      ([name, title]) => settingsField(title, settingsInput(name, value[name] ?? 0, "number", {
        required: true, min: "0", max: "100", step: "1",
      }))
    ),
    ...[["preferences", "Preferenze"], ["confirmed_facts", "Fatti confermati"], ["boundaries", "Confini relazionali"]].map(
      ([name, title]) => settingsField(title, h("textarea", { name, rows: "3" }, (value[name] || []).join("\n")), "Una voce per riga.")
    ),
    settingsField("Note", h("textarea", { name: "notes", rows: "3", maxlength: "2000" }, value.notes || "")),
  ];
  const form = settingsForm(editing ? "Salva contatto" : "Crea contatto", fields, async (formData) => {
    const payload = { action: editing ? "update" : "create" };
    for (const name of ["user_id", "display_name", "full_name", "relation", "notes"]) {
      payload[name] = String(formData.get(name) || "");
    }
    for (const name of ["trust", "intimacy"]) payload[name] = Number(formData.get(name));
    for (const name of ["preferences", "confirmed_facts", "boundaries"]) {
      payload[name] = String(formData.get(name) || "").split("\n").map((item) => item.trim()).filter(Boolean);
    }
    await postJson("/api/relations", payload);
    state.relationId = payload.user_id.trim();
    delete state.loaded.overview;
    delete state.loaded.settings;
    await loadRelations(true);
    setNotice("Contatto salvato. Le autorizzazioni WhatsApp restano separate.", "success");
  });
  return h("details", { class: "settings-card" },
    h("summary", { text: editing ? "Modifica contatto" : "Aggiungi contatto" }), form);
}

async function removeContact(contact) {
  const confirmation = window.prompt(
    `Rimuovere ${contact.display_name}? Verrà eliminato anche il suo storico relazionale, non i ricordi episodici né la whitelist WhatsApp. Scrivi RIMUOVI per confermare.`
  );
  if (confirmation !== "RIMUOVI") return;
  try {
    await postJson("/api/relations", { action: "remove", user_id: contact.user_id, confirmation });
    state.relationId = null;
    delete state.loaded.overview;
    delete state.loaded.settings;
    await loadRelations(true);
    setNotice("Contatto rimosso. La whitelist WhatsApp non è stata modificata.", "success");
  } catch (error) {
    setNotice(error.message);
  }
}

function relationDetail(contact) {
  const interactions = Array.isArray(contact.interactions) ? contact.interactions : [];
  const identity = [contact.full_name, contact.user_id].filter(Boolean).join(" · ");
  return h(
    "div",
    { class: "relation-detail-inner" },
    h(
      "div",
      { class: "relation-detail-head" },
      h(
        "div",
        {},
        h("p", { class: "eyebrow", text: text(contact.relation, "Contatto") }),
        h("h2", { class: "relation-detail-name", text: text(contact.display_name, contact.user_id) }),
        h("p", { class: "relation-detail-sub", text: identity || "—" })
      ),
      h("span", { class: "count-chip", text: interactionCount(contact.interaction_count) })
    ),
    h(
      "div",
      { class: "relation-stats" },
      relationStat("Fiducia", scoreValue(contact.trust)),
      relationStat("Intimità", scoreValue(contact.intimacy)),
      relationStat("Prima interazione", fmtDate(contact.first_interaction_at)),
      relationStat("Ultima interazione", fmtDate(contact.last_interaction_at, { time: true }))
    ),
    relationAbout(contact),
    contactEditor(contact),
    h("button", {
      type: "button", class: "button button-danger", text: "Rimuovi contatto",
      onClick: () => removeContact(contact),
    }),
    h(
      "div",
      { class: "relation-history" },
      h(
        "div",
        { class: "relation-history-head" },
        h("h3", { text: "Storico interazioni" }),
        h("span", { class: "count-chip", text: `${contact.history_total ?? interactions.length} registrate` })
      ),
      interactions.length
        ? h("ol", { class: "relation-events" }, interactions.map(relationEvent))
        : emptyState("Storico vuoto", "Nessuna interazione in questa pagina."),
      h("div", { class: "settings-actions" },
        h("button", { type: "button", class: "button", text: "Più recenti", disabled: !contact.history_offset,
          onClick: () => { state.relationOffset = Math.max(0, contact.history_offset - contact.history_limit); loadRelations(true); } }),
        h("button", { type: "button", class: "button", text: "Più vecchie", disabled: contact.history_offset + interactions.length >= contact.history_total,
          onClick: () => { state.relationOffset = contact.history_offset + contact.history_limit; loadRelations(true); } })
      )
    )
  );
}

function renderRelations(data) {
  const contacts = Array.isArray(data.contacts) ? data.contacts : [];
  const total = Number(data.total_interactions) || 0;
  $("#relations-count").textContent = `${contacts.length} contatti · ${total} interazioni`;

  if (!contacts.length) {
    clear(
      $("#relations-content"),
      contactEditor(),
      emptyState("Nessuna relazione", "Il grafo delle relazioni è vuoto. Aggiungi il primo contatto.")
    );
    return;
  }

  const initial = contacts.find((contact) => contact.user_id === state.relationId) || contacts[0];
  const buttons = [];
  const detailHost = h("div", { class: "relation-detail-host" });

  function select(userId) {
    const selected = contacts.find((contact) => contact.user_id === userId);
    if (!selected) return;
    if (state.relationId !== selected.user_id && (state.relationOffset || selected.history_total > selected.interactions.length)) {
      state.relationId = selected.user_id;
      state.relationOffset = 0;
      loadRelations(true);
      return;
    }
    state.relationId = selected.user_id;
    for (const button of buttons) {
      const active = button.dataset.userId === selected.user_id;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    }
    clear(detailHost, relationDetail(selected));
  }

  for (const contact of contacts) {
    const button = h(
      "button",
      {
        class: "relation-select",
        type: "button",
        dataset: { userId: contact.user_id },
        "aria-pressed": "false",
      },
      h(
        "span",
        { class: "relation-select-top" },
        h("span", { class: "relation-select-name", text: text(contact.display_name, contact.user_id) }),
        h("span", { class: "relation-select-count", text: String(contact.interaction_count ?? 0) })
      ),
      h("span", { class: "relation-select-relation", text: text(contact.relation, "contatto") }),
      h("span", {
        class: "relation-select-date",
        text: contact.last_interaction_at
          ? `Ultima ${fmtDate(contact.last_interaction_at)}`
          : "Nessuna interazione",
      })
    );
    button.addEventListener("click", () => select(contact.user_id));
    buttons.push(button);
  }

  select(initial.user_id);

  clear(
    $("#relations-content"),
    contactEditor(),
    h(
      "div",
      { class: "relations-layout" },
      h(
        "aside",
        { class: "panel relations-index" },
        h(
          "div",
          { class: "panel-header" },
          h("h3", { text: "Contatti" }),
          h("span", { class: "count-chip", text: `${contacts.length}` })
        ),
        h("div", { class: "relation-list" }, buttons)
      ),
      h("section", { class: "panel relations-detail" }, detailHost)
    )
  );
}

async function loadRelations(force = false) {
  if (state.loaded.relations && !force) {
    renderRelations(state.loaded.relations);
    return;
  }
  const content = $("#relations-content");
  setLoading(content, "Caricamento delle relazioni…");
  try {
    const data = await api(`/api/relations?offset=${state.relationOffset}&limit=50${state.relationId ? `&user_id=${encodeURIComponent(state.relationId)}` : ""}`);
    state.loaded.relations = data;
    renderRelations(data);
    setNotice("");
  } catch (error) {
    setNotice(`Impossibile caricare le relazioni: ${error.message}`);
    clear(content, emptyState("Relazioni non disponibili", error.message));
  }
}

/* -- persone incontrate -------------------------------------------------- */

async function acquaintanceRequest(payload, message) {
  try {
    await postJson("/api/acquaintances", payload);
    state.loaded.acquaintances = null;
    await loadAcquaintances(true);
    setNotice(message, "success");
  } catch (error) {
    setNotice(error.message);
  }
}

function acquaintanceCreateForm() {
  const form = settingsForm(
    "Aggiungi persona",
    [
      settingsField(
        "Nickname",
        settingsInput("display_name", "", "text", {
          required: true,
          maxlength: "80",
          placeholder: "Quello che si conosce all'inizio",
        })
      ),
      settingsField(
        "Canale",
        settingsInput("channel", "", "text", {
          maxlength: "40",
          placeholder: "forum, chat, whatsapp…",
        })
      ),
      settingsField(
        "Handle o nome utente",
        settingsInput("handle", "", "text", { maxlength: "120" })
      ),
    ],
    async (data) => {
      await postJson("/api/acquaintances", {
        action: "create",
        display_name: String(data.get("display_name") || ""),
        channel: String(data.get("channel") || ""),
        handle: String(data.get("handle") || ""),
      });
      state.loaded.acquaintances = null;
      await loadAcquaintances(true);
      setNotice("Persona aggiunta al registro.", "success");
    }
  );
  return h(
    "details",
    { class: "settings-card acquaintance-create" },
    h("summary", { text: "Aggiungi persona incontrata" }),
    h("p", {
      class: "acquaintance-intro",
      text: "Basta il nickname: gli altri dati si aggiungono man mano che emergono, senza trasformarli subito in certezze.",
    }),
    form
  );
}

function acquaintanceFactActions(person, fact) {
  const actions = [];
  if (fact.status !== "confirmed") {
    actions.push(
      h("button", {
        type: "button",
        class: "button button-quiet",
        text: "Conferma",
        onClick: () =>
          acquaintanceRequest(
            {
              action: "fact_status",
              person_id: person.person_id,
              fact_id: fact.id,
              status: "confirmed",
            },
            "Fatto confermato."
          ),
      })
    );
  }
  if (fact.status !== "rejected") {
    actions.push(
      h("button", {
        type: "button",
        class: "button button-quiet",
        text: "Scarta",
        onClick: () =>
          acquaintanceRequest(
            {
              action: "fact_status",
              person_id: person.person_id,
              fact_id: fact.id,
              status: "rejected",
            },
            "Fatto scartato."
          ),
      })
    );
  }
  actions.push(
    h("button", {
      type: "button",
      class: "acquaintance-fact-remove",
      text: "Rimuovi",
      onClick: () =>
        acquaintanceRequest(
          {
            action: "fact_remove",
            person_id: person.person_id,
            fact_id: fact.id,
          },
          "Fatto rimosso."
        ),
    })
  );
  return actions;
}

function acquaintanceFacts(person) {
  const facts = Array.isArray(person.facts) ? person.facts : [];
  if (!facts.length) {
    return h("p", {
      class: "relation-notes",
      text: "Nessun fatto registrato: per ora si conosce solo il nickname.",
    });
  }
  const groups = new Map();
  for (const fact of facts) {
    if (!groups.has(fact.category)) groups.set(fact.category, []);
    groups.get(fact.category).push(fact);
  }
  return h(
    "div",
    { class: "acquaintance-facts" },
    [...groups.entries()].map(([category, items]) =>
      h(
        "div",
        { class: "acquaintance-fact-group" },
        h("span", {
          class: "acquaintance-fact-label",
          text: items[0].category_label || category,
        }),
        h(
          "div",
          { class: "acquaintance-fact-list" },
          items.map((fact) =>
            h(
              "div",
              { class: "acquaintance-fact" },
              h(
                "div",
                { class: "acquaintance-fact-main" },
                h("span", {
                  class: "acquaintance-fact-value",
                  text: fact.value,
                }),
                h("span", {
                  class: `tag acquaintance-fact-status is-${fact.status}`,
                  text:
                    ACQUAINTANCE_FACT_STATUS_LABELS[fact.status] ||
                    fact.status,
                }),
                fact.sensitive
                  ? h("span", {
                      class: "tag acquaintance-fact-sensitive",
                      text: "Sensibile",
                    })
                  : null
              ),
              h("p", {
                class: "acquaintance-fact-source",
                text: [
                  ACQUAINTANCE_ORIGIN_LABELS[fact.origin] ||
                    ACQUAINTANCE_ORIGIN_LABELS.manual,
                  fact.source,
                  fact.observed_at
                    ? fmtDate(fact.observed_at, { time: true })
                    : "",
                  typeof fact.confidence === "number"
                    ? `confidenza ${Math.round(fact.confidence * 100)}%`
                    : "",
                ]
                  .filter(Boolean)
                  .join(" · "),
              }),
              h(
                "div",
                { class: "acquaintance-fact-actions" },
                acquaintanceFactActions(person, fact)
              )
            )
          )
        )
      )
    )
  );
}

function acquaintanceAddFact(person, categories) {
  const options = categories
    .filter((category) => category.id !== "nickname")
    .map((category) =>
      h("option", {
        value: category.id,
        text: category.sensitive
          ? `${category.label} · sensibile`
          : category.label,
      })
    );
  const form = settingsForm(
    "Registra fatto",
    [
      settingsField("Categoria", h("select", { name: "category" }, options)),
      settingsField(
        "Valore",
        settingsInput("value", "", "text", {
          required: true,
          maxlength: "500",
        })
      ),
      settingsField(
        "Fonte",
        settingsInput("source", "", "text", {
          maxlength: "500",
          placeholder: "thread, messaggio, profilo pubblico…",
        })
      ),
      settingsField(
        "Confidenza (0–1)",
        settingsInput("confidence", "0.4", "number", {
          min: "0",
          max: "1",
          step: "0.1",
        })
      ),
    ],
    async (data) => {
      await postJson("/api/acquaintances", {
        action: "observe",
        person_id: person.person_id,
        category: String(data.get("category") || ""),
        value: String(data.get("value") || ""),
        source: String(data.get("source") || ""),
        confidence: Number(data.get("confidence") || 0.4),
      });
      state.loaded.acquaintances = null;
      await loadAcquaintances(true);
      setNotice("Fatto registrato come candidato.", "success");
    }
  );
  return h(
    "details",
    { class: "acquaintance-add" },
    h("summary", { text: "Aggiungi un fatto" }),
    form
  );
}

function acquaintanceReviewSection(data) {
  const review = Array.isArray(data.review) ? data.review : [];
  if (!review.length) {
    return null;
  }
  return settingsCard(
    "Da rivedere",
    `${review.length} fatti candidati aspettano una conferma o uno scarto. La coda unisce tutte le persone.`,
    h(
      "div",
      { class: "acquaintance-review" },
      review.map((item) =>
        h(
          "article",
          {
            class: `acquaintance-review-item${item.sensitive ? " is-sensitive" : ""}`,
          },
          h(
            "div",
            { class: "acquaintance-review-head" },
            h("strong", { text: item.display_name }),
            h("span", {
              class: "acquaintance-fact-label",
              text: item.category_label || item.category,
            }),
            item.sensitive
              ? h("span", {
                  class: "tag acquaintance-fact-sensitive",
                  text: "Sensibile",
                })
              : null
          ),
          h("p", { class: "acquaintance-review-value", text: item.value }),
          h("p", {
            class: "acquaintance-fact-source",
            text: [
              ACQUAINTANCE_ORIGIN_LABELS[item.origin] ||
                ACQUAINTANCE_ORIGIN_LABELS.manual,
              item.source,
              item.observed_at ? fmtDate(item.observed_at, { time: true }) : "",
              typeof item.confidence === "number"
                ? `confidenza ${Math.round(item.confidence * 100)}%`
                : "",
            ]
              .filter(Boolean)
              .join(" · "),
          }),
          h(
            "div",
            { class: "acquaintance-review-actions" },
            h("button", {
              type: "button",
              class: "button button-quiet",
              text: "Conferma",
              onClick: () =>
                acquaintanceRequest(
                  {
                    action: "fact_status",
                    person_id: item.person_id,
                    fact_id: item.fact_id,
                    status: "confirmed",
                  },
                  "Fatto confermato."
                ),
            }),
            h("button", {
              type: "button",
              class: "button button-quiet",
              text: "Scarta",
              onClick: () =>
                acquaintanceRequest(
                  {
                    action: "fact_status",
                    person_id: item.person_id,
                    fact_id: item.fact_id,
                    status: "rejected",
                  },
                  "Fatto scartato."
                ),
            })
          )
        )
      )
    )
  );
}

function acquaintancePolicyCard(data) {
  const policy = data.policy || {};
  const stats = data.stats || {};
  const form = settingsForm(
    "Salva policy",
    [
      settingsField(
        "L'agente può leggere",
        h(
          "select",
          { name: "agent_fact_visibility" },
          h("option", {
            value: "confirmed",
            text: "Solo i fatti confermati",
            selected: policy.agent_fact_visibility !== "all",
          }),
          h("option", {
            value: "all",
            text: "Anche i candidati",
            selected: policy.agent_fact_visibility === "all",
          })
        ),
        "I fatti scartati non sono mai visibili ai tool MCP."
      ),
      settingsCheck(
        "agent_sensitive_access",
        "Consenti i dati sensibili confermati",
        Boolean(policy.agent_sensitive_access),
        "Salute, religione, politica, orientamento, indirizzo e contatti restano nel registro amministrativo salvo questa scelta."
      ),
      settingsField(
        "Conservazione (giorni)",
        settingsInput("retention_days", policy.retention_days ?? 365, "number", {
          min: "1",
          max: "3650",
          step: "1",
        }),
        "Soglia usata dal pulsante Applica retention: non tocca le persone promosse."
      ),
    ],
    async (formData) => {
      await postJson("/api/acquaintances", {
        action: "policy",
        policy: {
          agent_fact_visibility: String(
            formData.get("agent_fact_visibility") || "confirmed"
          ),
          agent_sensitive_access: formData.has("agent_sensitive_access"),
          retention_days: Number(formData.get("retention_days") || 365),
        },
      });
      state.loaded.acquaintances = null;
      await loadAcquaintances(true);
      setNotice("Policy del registro aggiornata.", "success");
    }
  );
  const retentionButton = h("button", {
    type: "button",
    class: "button button-danger",
    text: "Applica retention",
    onClick: async () => {
      const days = window.prompt(
        "Elimina i fatti scartati e i profili inattivi più vecchi di quanti giorni?",
        String(policy.retention_days || 365)
      );
      if (days === null) return;
      const number = Number(days);
      if (!Number.isInteger(number) || number < 1 || number > 3650) {
        setNotice("La retention richiede un intero tra 1 e 3650 giorni.");
        return;
      }
      const confirmation = window.prompt(
        `Scrivi APPLICA per confermare la retention di ${number} giorni.`
      );
      if (confirmation === null) return;
      await acquaintanceRequest(
        { action: "retention", days: number, confirmation },
        "Retention applicata."
      );
    },
  });
  const exportLink = h("a", {
    class: "button button-quiet",
    href: "/api/acquaintances/export",
    download: "incontrati.json",
    text: "Esporta registro",
  });
  return settingsCard(
    "Policy e conservazione",
    `${stats.promoted ?? 0} relazioni promosse · ${stats.sensitive_confirmed ?? 0} dati sensibili confermati`,
    h(
      "div",
      { class: "acquaintance-policy" },
      form,
      h("div", { class: "settings-actions" }, exportLink, retentionButton)
    )
  );
}

function acquaintanceExtract(person) {
  const form = settingsForm(
    "Estrai candidati",
    [
      settingsField(
        "Messaggio",
        h("textarea", {
          name: "text",
          rows: "4",
          maxlength: "4000",
          required: true,
          placeholder:
            "Incolla il messaggio: «mi chiamo…», «ho 30 anni», «abito a…»",
        }),
        "L'estrazione è conservativa e crea solo candidati; la conferma resta manuale."
      ),
      settingsField(
        "Fonte",
        settingsInput("source", "", "text", {
          maxlength: "500",
          placeholder: "thread, chat, profilo pubblico…",
        })
      ),
    ],
    async (formData) => {
      const result = await postJson("/api/acquaintances", {
        action: "extract",
        person_id: person.person_id,
        text: String(formData.get("text") || ""),
        source: String(formData.get("source") || ""),
      });
      state.loaded.acquaintances = null;
      await loadAcquaintances(true);
      setNotice(
        `${result.count} fatti candidati estratti: controllali nella coda.`,
        "success"
      );
    }
  );
  return h(
    "details",
    { class: "acquaintance-add" },
    h("summary", { text: "Estrai da un messaggio" }),
    form
  );
}

function acquaintancePromote(person) {
  if (person.promoted) {
    return h("p", {
      class: "settings-hint",
      text: `Già in Relazioni come ${person.promoted_user_id}.`,
    });
  }
  const form = settingsForm(
    "Promuovi a relazione",
    [
      settingsField(
        "Relazione",
        settingsInput("relation", "", "text", {
          maxlength: "80",
          placeholder: "Amico, collega, conoscente…",
        })
      ),
      settingsField(
        "Affidabilità (0–100)",
        settingsInput("trust", "30", "number", { min: "0", max: "100" })
      ),
      settingsField(
        "Intimità (0–100)",
        settingsInput("intimacy", "10", "number", { min: "0", max: "100" })
      ),
      settingsField(
        "Conferma",
        settingsInput("confirmation", "", "text", {
          placeholder: "Scrivi PROMUOVI",
          autocomplete: "off",
        }),
        "Crea un contatto in Relazioni con i soli fatti confermati non sensibili."
      ),
    ],
    async (formData) => {
      await postJson("/api/acquaintances", {
        action: "promote",
        person_id: person.person_id,
        relation: String(formData.get("relation") || ""),
        trust: Number(formData.get("trust") || 30),
        intimacy: Number(formData.get("intimacy") || 10),
        confirmation: String(formData.get("confirmation") || ""),
      });
      state.loaded.acquaintances = null;
      state.loaded.relations = null;
      await loadAcquaintances(true);
      setNotice(
        `${person.display_name} è ora una relazione fidata. I dati sensibili restano nel registro.`,
        "success"
      );
    }
  );
  return h(
    "details",
    { class: "acquaintance-edit" },
    h("summary", { text: "Promuovi a relazione" }),
    form
  );
}

function acquaintanceEdit(person) {
  const form = settingsForm(
    "Salva profilo",
    [
      settingsField(
        "Stato",
        h(
          "select",
          { name: "status" },
          Object.entries(ACQUAINTANCE_STATUS_LABELS).map(([id, label]) =>
            h("option", {
              value: id,
              text: label,
              selected: person.status === id,
            })
          )
        )
      ),
      settingsField(
        "Note",
        h("textarea", { name: "notes", rows: "3", maxlength: "2000" },
          person.notes || "")
      ),
    ],
    async (data) => {
      await postJson("/api/acquaintances", {
        action: "update",
        person_id: person.person_id,
        status: String(data.get("status") || "unknown"),
        notes: String(data.get("notes") || ""),
      });
      state.loaded.acquaintances = null;
      await loadAcquaintances(true);
      setNotice("Profilo aggiornato.", "success");
    }
  );
  return h(
    "details",
    { class: "acquaintance-edit" },
    h("summary", { text: "Stato e note" }),
    form,
    h("button", {
      type: "button",
      class: "button button-danger",
      text: "Rimuovi persona",
      onClick: async () => {
        const confirmation = window.prompt(
          `Rimuovere ${person.display_name}? Scrivi RIMUOVI per confermare.`
        );
        if (confirmation !== "RIMUOVI") return;
        await acquaintanceRequest(
          {
            action: "remove",
            person_id: person.person_id,
            confirmation,
          },
          "Persona rimossa dal registro."
        );
      },
    })
  );
}

function acquaintanceCard(person, categories) {
  const counts = person.fact_counts || {
    candidate: 0,
    confirmed: 0,
    rejected: 0,
  };
  const channels = Array.isArray(person.channels) ? person.channels : [];
  const meta = [
    `${counts.confirmed} confermati`,
    `${counts.candidate} candidati`,
    `${person.observation_count ?? 0} osservazioni`,
  ];
  if (person.last_seen_at) {
    meta.push(`ultimo ${fmtDate(person.last_seen_at, { time: true })}`);
  }
  if (person.promoted) {
    meta.push("in Relazioni");
  }
  return h(
    "article",
    { class: "panel acquaintance-card" },
    h(
      "div",
      { class: "acquaintance-head" },
      h(
        "div",
        { class: "acquaintance-title" },
        h("h3", { text: person.display_name }),
        h("p", {
          class: "acquaintance-id",
          text: `${person.person_id}${channels.length ? ` · ${channels.join(", ")}` : ""}`,
        })
      ),
      h("span", {
        class: `tag acquaintance-status is-${person.status}`,
        text: ACQUAINTANCE_STATUS_LABELS[person.status] || person.status,
      })
    ),
    h(
      "div",
      {
        class: "acquaintance-meter",
        title: `${person.completeness ?? 0}% dei dati di base confermati`,
      },
      h("span", { style: `width:${person.completeness ?? 0}%` })
    ),
    h("p", { class: "acquaintance-meta", text: meta.join(" · ") }),
    acquaintanceFacts(person),
    h(
      "div",
      { class: "acquaintance-tools" },
      acquaintanceAddFact(person, categories),
      acquaintanceExtract(person),
      acquaintancePromote(person),
      acquaintanceEdit(person)
    )
  );
}

function renderAcquaintances(data) {
  const people = Array.isArray(data.people) ? data.people : [];
  const categories = Array.isArray(data.categories) ? data.categories : [];
  const stats = data.stats || {};
  $("#acquaintances-count").textContent = `${people.length} profili · ${stats.candidates ?? 0} da rivedere`;
  const content = $("#acquaintances-content");
  const sections = [];
  const review = acquaintanceReviewSection(data);
  if (review) {
    sections.push(review);
  }
  sections.push(acquaintancePolicyCard(data), acquaintanceCreateForm());
  if (people.length) {
    sections.push(
      h(
        "div",
        { class: "acquaintance-grid" },
        people.map((person) => acquaintanceCard(person, categories))
      )
    );
  } else {
    sections.push(
      emptyState(
        "Nessuna persona incontrata",
        "Aggiungi un nickname oppure registra la prima osservazione con il tool MCP record_acquaintance_observation."
      )
    );
  }
  clear(content, ...sections);
}

async function loadAcquaintances(force = false) {
  if (state.loaded.acquaintances && !force) {
    renderAcquaintances(state.loaded.acquaintances);
    return;
  }
  const content = $("#acquaintances-content");
  setLoading(content, "Caricamento delle persone incontrate…");
  try {
    const data = await api("/api/acquaintances");
    state.loaded.acquaintances = data;
    renderAcquaintances(data);
    setNotice("");
  } catch (error) {
    setNotice(`Impossibile caricare le persone incontrate: ${error.message}`);
    clear(content, emptyState("Registro non disponibile", error.message));
  }
}

/* -- timeline ------------------------------------------------------------ */

function populateSourceSelect(sources) {
  const select = $("#timeline-source");
  const current = select.value;
  clear(select, h("option", { value: "", text: "Tutte" }));
  for (const source of sources || []) {
    select.append(h("option", { value: source, text: label(SOURCE_LABELS, source) }));
  }
  if ([...select.options].some((option) => option.value === current)) select.value = current;
}

function renderTimeline(data) {
  populateSourceSelect(data.sources);
  const count = data.count || 0;
  const total = data.total || 0;
  $("#timeline-count").textContent = `${count} / ${total} ricordi`;

  if (!(data.memories || []).length) {
    clear(
      $("#timeline-content"),
      emptyState("Nessun ricordo trovato", "Modifica i filtri o includi i ricordi non consci.")
    );
    return;
  }

  const table = h(
    "table",
    { class: "data-table" },
    h(
      "thead",
      {},
      h(
        "tr",
        {},
        h("th", { class: "col-date", text: "Data" }),
        h("th", { text: "Ricordo" }),
        h("th", { class: "col-source", text: "Fonte" }),
        h("th", { class: "col-access", text: "Accesso" }),
        h("th", { class: "col-importance", text: "Importanza" }),
        h("th", { class: "col-version", text: "Versione" })
      )
    )
  );

  const body = h("tbody");
  for (const memory of data.memories) {
    const row = h(
      "tr",
      {
        class: "memory-row",
        tabindex: "0",
        role: "button",
        "aria-label": `Apri il ricordo ${memory.title}`,
        onClick: () => openMemory(memory.id),
        onKeydown: (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openMemory(memory.id);
          }
        },
      },
      h(
        "td",
        {},
        h("div", { text: fmtDate(memory.event_date) }),
        h("div", { class: "cell-sub", text: fmtTime(memory.event_time) })
      ),
      h(
        "td",
        {},
        h("div", { class: "cell-title", text: text(memory.title) }),
        h("div", { class: "cell-sub", text: text(memory.description, "") })
      ),
      h("td", {}, sourceBadge(memory.source)),
      h("td", {}, accessBadge(memory.access_level)),
      h("td", {}, importanceMeter(memory.importance)),
      h(
        "td",
        {},
        h("div", { class: "mono", text: `v${memory.version}` }),
        h("div", { class: "cell-sub", text: `${memory.revision_count || 0} rev.` })
      )
    );
    body.append(row);
  }
  table.append(body);
  clear($("#timeline-content"), h("div", { class: "table-wrap" }, table));
}

async function loadTimeline(force = false) {
  if (state.loaded.timeline && !force) return;
  const sequence = ++state.sequence.timeline;
  const content = $("#timeline-content");
  setLoading(content, "Caricamento dei ricordi…");
  const params = new URLSearchParams();
  const query = $("#timeline-query").value.trim();
  const source = $("#timeline-source").value;
  const access = $("#timeline-access").value;
  const includeHidden = $("#timeline-hidden").checked;
  if (query) params.set("q", query);
  if (source) params.set("source", source);
  if (access) params.set("access", access);
  if (includeHidden) params.set("include_hidden", "1");
  try {
    const data = await api(`/api/timeline?${params.toString()}`);
    if (sequence !== state.sequence.timeline) return;
    state.loaded.timeline = data;
    renderTimeline(data);
    setNotice("");
  } catch (error) {
    if (sequence !== state.sequence.timeline) return;
    setNotice(`Impossibile caricare la timeline: ${error.message}`);
    clear(content, emptyState("Dati non disponibili", error.message));
  }
}

async function openMemory(memoryId) {
  state.lastFocus = document.activeElement;
  const modal = $("#memory-modal");
  modal.hidden = false;
  document.body.classList.add("modal-open");
  $("#modal-title").textContent = "Caricamento…";
  setLoading($("#modal-body"), "Caricamento del dettaglio…");
  try {
    const data = await api(`/api/memory/${encodeURIComponent(memoryId)}`);
    renderMemoryDetail(data);
  } catch (error) {
    $("#modal-title").textContent = "Errore";
    clear($("#modal-body"), emptyState("Dettaglio non disponibile", error.message));
  }
  $("#modal-close").focus();
}

function closeModal() {
  const modal = $("#memory-modal");
  if (modal.hidden) return;
  modal.hidden = true;
  document.body.classList.remove("modal-open");
  if (state.lastFocus && typeof state.lastFocus.focus === "function") state.lastFocus.focus();
  state.lastFocus = null;
}

function renderMemoryDetail(data) {
  const memory = data.memory || {};
  $("#modal-title").textContent = text(memory.title);

  const detailField = (labelText, value) =>
    h(
      "div",
      { class: "detail-field" },
      h("span", { text: labelText }),
      h("strong", { text: text(value) })
    );

  const revisions = (data.revisions || []).map((revision) =>
    h(
      "div",
      { class: "revision-entry" },
      h(
        "div",
        { class: "revision-entry-top" },
        h("strong", { text: `Revisione ${revision.revision_number}` }),
        h("span", { text: fmtDate(revision.changed_at, { time: true }) })
      ),
      h("p", { text: text(revision.change_reason, "Nessun motivo registrato") }),
      h("p", {
        text: `${revision.changed_by || "—"}${revision.cascade_id ? ` · ${revision.cascade_id}` : ""}`,
      })
    )
  );

  const links = (data.links || []).map((link) =>
    h(
      "div",
      { class: "revision-entry" },
      h(
        "div",
        { class: "revision-entry-top" },
        h("strong", { text: link.other_id || "—" }),
        h("span", { text: label(RELATION_LABELS, link.relation_type) })
      ),
      h("p", { text: `Forza ${Math.round((Number(link.strength) || 0) * 100)}%${link.direction === "in" ? " · collegamento entrante" : ""}` })
    )
  );

  const semanticLinks = (data.semantic_links || []).map((link) =>
    h(
      "div",
      { class: "revision-entry" },
      h(
        "div",
        { class: "revision-entry-top" },
        h("strong", { text: text(link.title) }),
        h("span", { text: label(RELATION_LABELS, link.relation_type) })
      ),
      h("p", { text: `${label(KNOWLEDGE_LABELS, link.knowledge_type)} · ${STATUS_LABELS[link.status] || link.status || "—"}` })
    )
  );

  clear(
    $("#modal-body"),
    h("div", { class: "tag-list" }, sourceBadge(memory.source), accessBadge(memory.access_level)),
    h("p", { class: "detail-description", text: text(memory.description, "") }),
    h(
      "div",
      { class: "detail-grid" },
      detailField("Data", fmtDate(memory.event_time, { time: true })),
      detailField("Luogo", memory.place),
      detailField("Versione", `v${memory.version} · ${memory.revision_count || 0} revisioni`),
      detailField("Affidabilità", `${Math.round((Number(memory.reliability) || 0) * 100)}%`),
      detailField("Riferimento fonte", memory.source_ref),
      detailField("Persone", (memory.people || []).join(", ") || "—")
    ),
    memory.access_reason
      ? h(
          "div",
          { class: "detail-section" },
          h("h3", { text: "Motivo dell'accesso limitato" }),
          h("p", { text: memory.access_reason })
        )
      : null,
    (memory.tags || []).length
      ? h("div", { class: "tag-list" }, memory.tags.map((tag) => h("span", { class: "tag", text: tag })))
      : null,
    memory.consequences
      ? h("div", { class: "detail-section" }, h("h3", { text: "Conseguenze" }), h("p", { text: memory.consequences }))
      : null,
    (memory.emotional_state && Object.keys(memory.emotional_state).length)
      ? h(
          "div",
          { class: "detail-section" },
          h("h3", { text: "Stato emotivo" }),
          h("div", { class: "detail-grid" },
            detailField("Emozione", memory.emotional_state.label),
            detailField("Valenza", memory.emotional_state.valence),
            detailField("Attivazione", memory.emotional_state.arousal),
            detailField("Intensità", memory.emotional_state.intensity)
          )
        )
      : null,
    h(
      "div",
      { class: "detail-section" },
      h("h3", { text: "Revisioni" }),
      revisions.length ? h("div", { class: "revision-list" }, revisions) : h("p", { text: "Nessuna revisione registrata." })
    ),
    h(
      "div",
      { class: "detail-section" },
      h("h3", { text: "Collegamenti episodici" }),
      links.length ? h("div", { class: "revision-list" }, links) : h("p", { text: "Nessun collegamento episodico." })
    ),
    h(
      "div",
      { class: "detail-section" },
      h("h3", { text: "Conoscenze collegate" }),
      semanticLinks.length ? h("div", { class: "revision-list" }, semanticLinks) : h("p", { text: "Nessuna conoscenza semantica collegata." })
    )
  );
}

/* -- tratti -------------------------------------------------------------- */

function shadowTraitForm(trait = null) {
  const editing = Boolean(trait);
  const form = settingsForm(editing ? "Salva tratto" : "Aggiungi tratto", [
    settingsField("ID univoco", settingsInput("id", trait?.id || "", "text", {
      required: true, maxlength: "80", pattern: "[A-Za-z0-9][A-Za-z0-9_-]{0,79}", readonly: editing,
    }), editing ? "L'ID non può essere cambiato: resta collegato ai dati esistenti." : "Lettere, numeri, trattino o underscore."),
    settingsField("Nome", settingsInput("name", trait?.name || "", "text", { required: true, maxlength: "2000" })),
    settingsField("Descrizione", h("textarea", { name: "description", rows: "3", maxlength: "2000" }, trait?.description || "")),
    settingsField("Origine", h("textarea", { name: "origin", rows: "2", maxlength: "2000" }, trait?.origin || "")),
    settingsField("Intensità", settingsInput("intensity", trait?.intensity ?? 5, "number", { min: "1", max: "10", step: "1", required: true })),
    settingsField("Consapevolezza (0–1)", settingsInput("conscious_awareness", trait?.conscious_awareness ?? 0, "number", { min: "0", max: "1", step: "0.01", required: true })),
    settingsField("Esempi di trigger", h("textarea", { name: "trigger_examples", rows: "3", maxlength: "4000" }, (trait?.triggers?.find((trigger) => trigger.id === "dashboard_examples")?.examples || []).join("\n")), "Una frase per riga (4–200 caratteri): il confronto con il riepilogo è letterale, non semantico."),
    h("p", { class: "settings-hint", text: "Gli altri trigger, le difese, le regole e i ricordi collegati restano invariati. L'agente non può leggere direttamente l'ombra." }),
  ], async (data) => {
    await postJson("/api/traits/shadow", {
      action: editing ? "update" : "create", id: data.get("id"), name: data.get("name"),
      description: data.get("description"), origin: data.get("origin"),
      intensity: Number(data.get("intensity")), conscious_awareness: Number(data.get("conscious_awareness")),
      trigger_examples: String(data.get("trigger_examples") || "").split("\n").map((item) => item.trim()).filter(Boolean),
    });
    delete state.loaded.overview;
    delete state.loaded.traits;
    await loadTraits(true);
    setNotice("Tratto dell'ombra salvato.", "success");
  });
  return h("details", { class: "settings-card" },
    h("summary", { text: editing ? "Modifica tratto" : "Aggiungi tratto dell'ombra" }), form);
}

function renderTraits(data) {
  const scale = data.scale || {};
  const traits = (data.traits || []).map((trait) => {
    const marker = h("span", { class: "trait-marker", style: `left:${trait.position}%` });
    return h(
      "article",
      { class: "trait-card" },
      h(
        "div",
        { class: "trait-top" },
        h(
          "div",
          {},
          h("div", { class: "trait-name", text: text(trait.left) }),
          h("div", { class: "trait-id", text: trait.id })
        ),
        h("div", { class: "trait-value", text: trait.value })
      ),
      h("p", { class: "trait-description", text: text(trait.description, "") }),
      h("div", { class: "trait-scale" }, marker),
      h(
        "div",
        { class: "trait-poles" },
        h("span", { text: `${trait.left} · ${scale.min}` }),
        h("span", { text: `${trait.right} · ${scale.max}` })
      )
    );
  });

  const shadow = data.shadow || {};
  const policy = shadow.access_policy || {};
  const shadowCards = (shadow.traits || []).map((trait) => {
    const triggers = (trait.triggers || []).slice(0, 4).map((trigger) =>
      h("li", { text: text(trigger.description || trigger.id) })
    );
    const defenses = (trait.defense_mechanisms || []).map((mechanism) =>
      h("span", { class: "tag", text: label(DEFENSE_LABELS, mechanism) })
    );
    const linked = (trait.linked_memory_ids || []).map((memoryId) =>
      h("button", {
        class: "tag",
        type: "button",
        onClick: () => openMemory(memoryId),
        text: memoryId,
      })
    );
    return h(
      "article",
      { class: "shadow-card" },
      h(
        "div",
        { class: "trait-top" },
        h(
          "div",
          {},
          h("div", { class: "trait-name", text: text(trait.name) }),
          h("div", { class: "trait-id", text: trait.id })
        ),
        h("div", { class: "trait-value", text: trait.intensity })
      ),
      h("p", { class: "trait-description", text: text(trait.description, "") }),
      h(
        "div",
        { class: "shadow-metrics" },
        h("div", { class: "shadow-metric" }, h("span", { text: "Intensità" }), h("strong", { text: `${trait.intensity}/10` })),
        h("div", { class: "shadow-metric" }, h("span", { text: "Consapevolezza" }), h("strong", { text: fmtPercent(trait.conscious_awareness) }))
      ),
      h(
        "div",
        { class: "shadow-block" },
        h("h4", { text: "Origine" }),
        h("p", { text: text(trait.origin, "Origine non registrata.") })
      ),
      triggers.length
        ? h("div", { class: "shadow-block" }, h("h4", { text: "Trigger" }), h("ul", {}, triggers))
        : null,
      defenses.length
        ? h("div", { class: "shadow-block" }, h("h4", { text: "Difese" }), h("div", { class: "tag-list" }, defenses))
        : null,
      linked.length
        ? h("div", { class: "shadow-block" }, h("h4", { text: "Ricordi collegati" }), h("div", { class: "tag-list" }, linked))
        : null,
      trait.safety && trait.safety.limits
        ? h(
            "div",
            { class: "shadow-block" },
            h("h4", { text: "Limiti di sicurezza" }),
            h("p", { text: trait.safety.limits })
          )
        : null,
      shadowTraitForm(trait)
    );
  });

  const conflicts = (shadow.internal_conflicts || []).map((conflict) =>
    h(
      "div",
      { class: "revision-entry" },
      h("strong", { text: text(conflict.name || conflict.id) }),
      h("p", { text: text(conflict.description, "") })
    )
  );

  clear(
    $("#traits-content"),
    h(
      "div",
      { class: "traits-grid" },
      traits.length ? traits : emptyState("Nessun tratto", "La configurazione non contiene tratti.")
    ),
    h(
      "section",
      { class: "shadow-section" },
      h("p", { class: "eyebrow", text: "Configurazione amministrativa · non esposta al modello" }),
      h("h2", { text: "Tratti dell'ombra" }),
      h(
        "div",
        { class: "shadow-warning" },
        h("span", { "aria-hidden": "true", text: "!" }),
        h(
          "div",
          {},
          h("strong", { text: "Dati riservati" }),
          text(
            policy.notes ||
              "Questi tratti non sono leggibili direttamente dal modello conscio e restano confinati all'area amministrativa."
          )
        )
      ),
      shadowCards.length ? h("div", { class: "shadow-grid" }, shadowCards) : emptyState("Nessun tratto dell'ombra", "Questo profilo parte senza tratti dell'ombra. Puoi aggiungerne uno qui sotto."),
      shadowTraitForm(),
      conflicts.length
        ? h(
            "div",
            { class: "detail-section" },
            h("h3", { text: "Conflitti interni" }),
            h("div", { class: "revision-list" }, conflicts)
          )
        : null
    )
  );
}

async function loadTraits(force = false) {
  if (state.loaded.traits && !force) return;
  setLoading($("#traits-content"), "Caricamento dei tratti…");
  try {
    const data = await api("/api/traits");
    state.loaded.traits = data;
    renderTraits(data);
    setNotice("");
  } catch (error) {
    setNotice(`Impossibile caricare i tratti: ${error.message}`);
    clear($("#traits-content"), emptyState("Dati non disponibili", error.message));
  }
}

/* -- conoscenze ---------------------------------------------------------- */

function renderSemantic(data) {
  $("#semantic-count").textContent = `${data.count || 0} conoscenze`;
  if (!(data.items || []).length) {
    clear($("#semantic-content"), emptyState("Nessuna conoscenza trovata", "Prova a cambiare tipo, stato o testo di ricerca."));
    return;
  }

  const cards = data.items.map((item) => {
    const metadata = item.metadata && Object.keys(item.metadata).length
      ? JSON.stringify(item.metadata, null, 2)
      : null;
    return h(
      "article",
      { class: "semantic-card" },
      h(
        "div",
        { class: "semantic-top" },
        h("h3", { class: "semantic-title", text: text(item.title) }),
        h("div", { class: "tag-list" }, knowledgeBadge(item.knowledge_type), statusBadge(item.status))
      ),
      h("p", { class: "semantic-content", text: text(item.content, "") }),
      metadata
        ? h("div", { class: "metadata-box", text: metadata })
        : null,
      h(
        "div",
        { class: "semantic-meta" },
        h("span", {}, h("strong", { text: "Categoria " }), text(item.category)),
        h("span", {}, h("strong", { text: "Fonte " }), label(SOURCE_LABELS, item.source)),
        h("span", {}, h("strong", { text: "Affidabilità " }), `${Math.round((Number(item.reliability) || 0) * 100)}%`),
        h("span", {}, h("strong", { text: "Importanza " }), `${item.importance}/10`),
        h("span", {}, h("strong", { text: "Aggiornata " }), fmtDate(item.updated_at, { time: true }))
      ),
      (item.tags || []).length
        ? h("div", { class: "tag-list" }, item.tags.map((tag) => h("span", { class: "tag", text: tag })))
        : null
    );
  });

  clear($("#semantic-content"), h("div", { class: "semantic-grid" }, cards));
}

async function loadSemantic(force = false) {
  if (state.loaded.semantic && !force) return;
  const sequence = ++state.sequence.semantic;
  setLoading($("#semantic-content"), "Caricamento delle conoscenze…");
  const params = new URLSearchParams();
  const query = $("#semantic-query").value.trim();
  const type = $("#semantic-type").value;
  const status = $("#semantic-status").value;
  if (query) params.set("q", query);
  if (type) params.set("type", type);
  if (status) params.set("status", status);
  try {
    const data = await api(`/api/semantic?${params.toString()}`);
    if (sequence !== state.sequence.semantic) return;
    state.loaded.semantic = data;
    renderSemantic(data);
    setNotice("");
  } catch (error) {
    if (sequence !== state.sequence.semantic) return;
    setNotice(`Impossibile caricare le conoscenze: ${error.message}`);
    clear($("#semantic-content"), emptyState("Dati non disponibili", error.message));
  }
}

/* -- evoluzioni ---------------------------------------------------------- */

function renderEvolution(data) {
  const history = data.history || {};
  const changes = history.changes || [];
  const cascades = data.cascades || [];
  const seeds = data.seeds || [];
  const revisions = data.recent_revisions || [];
  $("#evolution-count").textContent = `${changes.length} variazioni · ${cascades.length} cascate`;

  const traitChanges = [];
  for (const entry of changes) {
    for (const change of entry.trait_changes || []) {
      traitChanges.push({ entry, change });
    }
  }

  const changesPanel = h(
    "section",
    { class: "panel" },
    h("div", { class: "panel-header" }, h("h3", { text: "Variazioni dei tratti" }), h("span", { class: "count-chip", text: `${traitChanges.length}` })),
    h(
      "div",
      { class: "panel-body" },
      traitChanges.length
        ? traitChanges.map(({ entry, change }) =>
            h(
              "div",
              { class: "change-item" },
              h("span", { class: "change-dot", "aria-hidden": "true" }),
              h(
                "div",
                {},
                h("div", { class: "change-title", text: `${change.trait_id} · ${change.old_value} → ${change.new_value}` }),
                h("div", { class: "change-meta", text: `${fmtDate(entry.changed_at, { time: true })} · ${entry.reason || entry.seed_id || "—"}` })
              ),
              h("span", {
                class: `change-delta${Number(change.delta) < 0 ? " is-negative" : ""}`,
                text: `${Number(change.delta) > 0 ? "+" : ""}${change.delta}`,
              })
            )
          )
        : emptyState("Nessuna variazione", "I tratti non sono ancora cambiati.")
    )
  );

  const cascadePanel = h(
    "section",
    { class: "panel" },
    h("div", { class: "panel-header" }, h("h3", { text: "Cascate di riconsolidamento" }), h("span", { class: "count-chip", text: `${cascades.length}` })),
    cascades.length
      ? h(
          "div",
          { class: "cascade-grid" },
          cascades.map((cascade) =>
            h(
              "article",
              { class: "cascade-card" },
              h("div", { class: "cascade-id", text: cascade.cascade_id }),
              h(
                "div",
                { class: "cascade-counts" },
                h("span", {}, h("strong", { text: cascade.memories }), " ricordi"),
                h("span", {}, h("strong", { text: cascade.memory_revisions }), " revisioni"),
                h("span", {}, h("strong", { text: cascade.semantic_items }), " conoscenze")
              ),
              h("div", { class: "cascade-date", text: `${fmtDate(cascade.first_at, { time: true })} → ${fmtDate(cascade.last_at, { time: true })}` })
            )
          )
        )
      : h("div", { class: "panel-body" }, emptyState("Nessuna cascata", "Non sono ancora stati applicati seed."))
  );

  const seedPanel = h(
    "section",
    { class: "panel" },
    h("div", { class: "panel-header" }, h("h3", { text: "Seed esterni" }), h("span", { class: "count-chip", text: `${seeds.length}` })),
    h(
      "div",
      { class: "panel-body" },
      seeds.length
        ? seeds.map((seed) =>
            h(
              "div",
              { class: "seed-item" },
              h("div", { class: "seed-title", text: text(seed.title) }),
              h("div", { class: "seed-meta", text: `${fmtDate(seed.event_time, { time: true })} · ${seed.id}` }),
              h("div", { class: "seed-meta", text: text(seed.source_ref, "") })
            )
          )
        : emptyState("Nessun seed", "Non sono presenti ricordi amministrativi.")
    )
  );

  const revisionPanel = h(
    "section",
    { class: "panel" },
    h("div", { class: "panel-header" }, h("h3", { text: "Revisioni recenti" }), h("span", { class: "count-chip", text: `${revisions.length}` })),
    h(
      "div",
      { class: "panel-body" },
      revisions.length
        ? revisions.map((revision) =>
            h(
              "div",
              { class: "revision-item" },
              h("span", { class: "revision-index", text: `v${revision.revision_number}` }),
              h(
                "div",
                {},
                h("div", { class: "revision-title", text: text(revision.title) }),
                h("div", { class: "revision-meta", text: `${fmtDate(revision.changed_at, { time: true })} · ${revision.change_reason || "—"}` })
              )
            )
          )
        : emptyState("Nessuna revisione", "Non sono presenti revisioni da cascata.")
    )
  );

  clear(
    $("#evolution-content"),
    h(
      "div",
      { class: "evolution-layout" },
      h("div", { class: "stack" }, changesPanel, cascadePanel),
      h("div", { class: "stack" }, seedPanel, revisionPanel)
    )
  );
}

async function loadEvolution(force = false) {
  if (state.loaded.evolution && !force) return;
  setLoading($("#evolution-content"), "Caricamento dello storico…");
  try {
    const data = await api("/api/evolution");
    state.loaded.evolution = data;
    renderEvolution(data);
    setNotice("");
  } catch (error) {
    setNotice(`Impossibile caricare le evoluzioni: ${error.message}`);
    clear($("#evolution-content"), emptyState("Dati non disponibili", error.message));
  }
}

/* -- vita ---------------------------------------------------------------- */

function needCard(name, value) {
  const number = Math.max(0, Math.min(100, Number(value) || 0));
  return h(
    "div",
    { class: "metric-card" },
    h("div", { class: "metric-label", text: name }),
    h("div", { class: "metric-value", text: `${number}/100` }),
    h("div", { class: "load-track" }, h("div", { class: "load-fill", style: `width:${number}%` }))
  );
}

function renderLife(data) {
  const report = data.report || {};
  const status = data.status || {};
  const body = report.body || {};
  const activity = report.activity || {};
  const emotion = report.emotion || {};
  const loops = report.open_loops || [];
  const stream = report.stream || [];
  const enabled = Boolean(data.enabled);
  const active = status.active;
  const last = status.last;

  const toggleForm = settingsForm(
    enabled ? "Disattiva motore" : "Abilita motore",
    [
      h("p", {
        class: "settings-hint",
        text: enabled
          ? "Il battito chiama il motore deliberativo a ogni ciclo. Disattivandolo torna il vecchio percorso degli impulsi."
          : "Il motore è spento. Per attivarlo serve una conferma esplicita; il battito inizierà a valutare la salienza.",
      }),
      settingsField(
        "Conferma",
        settingsInput("confirmation", "", "text", {
          placeholder: enabled ? "DISATTIVA" : "ABILITO",
          autocomplete: "off",
        }),
        enabled ? "Scrivi DISATTIVA per fermare il motore." : "Scrivi ABILITO per accendere il motore."
      ),
    ],
    async (formData) => {
      await postJson("/api/life", {
        enabled: !enabled,
        confirmation: String(formData.get("confirmation") || ""),
      });
      state.loaded.life = null;
      await loadLife(true);
      setNotice(enabled ? "Motore vita disattivato." : "Motore vita attivato.", "success");
    }
  );

  const statusCard = settingsCard(
    "Motore vita",
    enabled ? "Attivo: il battito usa la deliberazione situata." : "Spento: resta disponibile il percorso degli impulsi.",
    h(
      "div",
      { class: "settings-form" },
      h("p", { class: "settings-meta", text: `Episodi oggi: ${status.today || 0}. ${active ? `Attivo: ${active.reason || "—"}.` : "Nessun episodio attivo."}` }),
      h("p", { class: "settings-meta", text: last ? `Ultimo: ${last.state || "—"} · ${last.reason || "—"} · ${fmtDate(last.opened_at, { time: true })}` : "Nessun episodio registrato." }),
      status.next_eligible_at ? h("p", { class: "settings-meta", text: `Prossimo istante utile: ${fmtDate(status.next_eligible_at, { time: true })}.` }) : null,
      toggleForm
    )
  );

  const activityCard = settingsCard(
    "Situazione",
    "Dove sono, cosa sto facendo e con quale attenzione.",
    h(
      "div",
      { class: "settings-form" },
      h("p", { class: "state-activity", text: text(activity.label, "Nessuna attività") }),
      h("p", { class: "settings-meta", text: `Modalità ${text(activity.mode)} · luogo ${text(activity.location)}` }),
      h("p", { class: "settings-meta", text: `Emozione: ${text(emotion.label)}.` }),
      h("p", { class: "settings-hint", text: "Il rapporto è in sola lettura e non espone ombra o contenuti riservati." })
    )
  );

  const needsPanel = h(
    "section",
    { class: "panel" },
    h("div", { class: "panel-header" }, h("h3", { text: "Bisogni" })),
    h("div", { class: "metric-grid" }, Object.entries(body).map(([name, value]) => needCard(name, value)))
  );

  const loopsPanel = h(
    "section",
    { class: "panel" },
    h("div", { class: "panel-header" }, h("h3", { text: "Loop aperti" }), h("span", { class: "count-chip", text: `${loops.length}` })),
    h(
      "div",
      { class: "panel-body" },
      loops.length
        ? loops.map((loop) =>
            h(
              "div",
              { class: "recent-item" },
              h("div", { class: "recent-title", text: text(loop.title) }),
              h("div", { class: "recent-desc", text: `Priorità ${loop.priority}/100${loop.due_at ? ` · scadenza ${fmtDate(loop.due_at, { time: true })}` : ""}` })
            )
          )
        : emptyState("Nessun loop aperto", "Non ci sono impegni aperti.")
    )
  );

  const streamPanel = h(
    "section",
    { class: "panel" },
    h("div", { class: "panel-header" }, h("h3", { text: "Stream recente" }), h("span", { class: "count-chip", text: `${stream.length}` })),
    h(
      "div",
      { class: "panel-body" },
      stream.length
        ? stream.map((entry) =>
            h(
              "div",
              { class: "recent-item" },
              h("div", { class: "recent-date", text: fmtDate(entry.at, { time: true }) }),
              h("div", { class: "recent-title", text: text(entry.text) }),
              h("div", { class: "recent-desc", text: text(entry.kind, "") })
            )
          )
        : emptyState("Nessuna voce", "Lo stream è ancora vuoto.")
    )
  );

  clear(
    $("#life-content"),
    h(
      "div",
      { class: "settings-grid" },
      statusCard,
      activityCard,
      needsPanel,
      loopsPanel,
      h("div", { class: "stack" }, streamPanel)
    )
  );
}

async function loadLife(force = false) {
  if (state.loaded.life && !force) return;
  const content = $("#life-content");
  setLoading(content, "Caricamento della vita dell'agente…");
  try {
    const data = await api("/api/life");
    state.loaded.life = data;
    renderLife(data);
    setNotice("");
  } catch (error) {
    setNotice(`Impossibile caricare la vita: ${error.message}`);
    clear(content, emptyState("Vita non disponibile", error.message));
  }
}

/* -- impostazioni -------------------------------------------------------- */

function settingsCard(title, description, content) {
  return h(
    "article",
    { class: "settings-card" },
    h(
      "div",
      { class: "settings-card-head" },
      h("h3", { text: title }),
      h("p", { text: description })
    ),
    content
  );
}

function settingsCheck(name, labelText, checked, hint, disabled = false) {
  return h(
    "label",
    { class: `settings-check${disabled ? " is-locked" : ""}` },
    h("input", { type: "checkbox", name, checked, disabled }),
    h(
      "span",
      { class: "settings-check-text" },
      h("strong", { text: labelText }),
      hint ? h("small", { text: hint }) : null
    )
  );
}

function settingsField(labelText, control, hint) {
  return h(
    "label",
    { class: "settings-field" },
    h("span", { text: labelText }),
    control,
    hint ? h("small", { class: "settings-hint", text: hint }) : null
  );
}

function settingsForm(buttonText, content, handler, { danger = false } = {}) {
  const button = h("button", {
    class: `button${danger ? " button-danger" : ""}`,
    type: "submit",
    text: buttonText,
  });
  const formError = h("p", {
    class: "settings-error",
    role: "alert",
    hidden: true,
  });
  const form = h(
    "form",
    { class: "settings-form" },
    content,
    formError,
    h("div", { class: "settings-actions" }, button)
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    formError.hidden = true;
    formError.textContent = "";
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "Salvataggio…";
    try {
      await handler(new FormData(form), form);
    } catch (error) {
      setNotice(error.message);
      formError.textContent = error.message;
      formError.hidden = false;
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  });
  return form;
}

async function saveSettings(path, payload, message) {
  const data = await postJson(path, payload);
  state.loaded.settings = data;
  renderSettings(data);
  setNotice(message, "success");
}

function settingsInput(name, value, type = "text", attrs = {}) {
  return h("input", {
    type,
    name,
    value: value === null || value === undefined ? "" : value,
    ...attrs,
  });
}

function browserSettings(browser) {
  const origins = (browser.allowed_origins || []).join("\n");
  const form = settingsForm(
    "Salva browser",
    [
      settingsCheck(
        "enabled",
        "Browser controllato attivo",
        Boolean(browser.enabled),
        "Senza questo flag la whitelist non apre nulla."
      ),
      settingsCheck(
        "interaction_enabled",
        "Consenti click, compilazione e tasti",
        Boolean(browser.interaction_enabled),
        "Abilita le azioni sulla pagina; richiede il browser attivo."
      ),
      settingsField("Origini autorizzate alle azioni", h("textarea", {
        name: "interaction_origins", rows: "4", spellcheck: "false",
      }, (browser.interaction_origins || []).join("\n")), "Una origine HTTPS esatta per riga, senza wildcard. Serve anche la whitelist di navigazione."),
      h("p", { class: "settings-warning", text: "Click, testo e tasti possono inviare messaggi o compiere altre azioni. Contatti, orari e limiti dell'outbox NON proteggono queste azioni dirette, su nessun sito." }),
      settingsField("Consenso per nuove origini", settingsInput("interaction_confirmation", "", "text", { placeholder: "Scrivi AUTORIZZO" })),
      settingsCheck(
        "service_workers",
        "Permetti i service worker",
        Boolean(browser.service_workers),
        "Allarga il traffico oltre il filtro delle origini: attivalo solo per un sito già autorizzato e fidato."
      ),
      settingsField(
        "User agent del profilo",
        settingsInput("user_agent", browser.user_agent || "", "text", {
          placeholder: "Vuoto = user agent predefinito di Playwright",
          spellcheck: "false",
        }),
        "È condiviso tra la sessione headless e il login visibile: cambiarli separatamente invalida i cookie."
      ),
      settingsField(
        "Timeout di navigazione (secondi)",
        settingsInput("navigation_timeout_seconds", browser.navigation_timeout_seconds, "number", {
          min: "1",
          max: "120",
          step: "1",
        })
      ),
      settingsField(
        "Whitelist delle origini",
        h("textarea", {
          name: "allowed_origins",
          rows: "7",
          spellcheck: "false",
          placeholder: "example.org\nhttps://*.wikipedia.org",
        }, origins),
        "Una per riga. Solo HTTPS; senza percorso. Il wildcard copre soltanto i sottodomini, non il dominio nudo. Il server MCP legge la nuova whitelist al riavvio."
      ),
      h(
        "div",
        { class: "settings-status" },
        h("span", { class: `status-pill${browser.session_saved ? " is-ok" : ""}` }),
        browser.session_saved ? "Sessione browser salvata su disco." : "Nessuna sessione browser salvata."
      ),
    ],
    async (formData) => {
      const originsValue = String(formData.get("allowed_origins") || "");
      const timeout = Number(formData.get("navigation_timeout_seconds"));
      if (!Number.isFinite(timeout)) {
        throw new Error("Il timeout deve essere un numero.");
      }
      await saveSettings(
        "/api/settings/browser",
        {
          enabled: formData.has("enabled"),
          interaction_enabled: formData.has("interaction_enabled"),
          interaction_origins: String(formData.get("interaction_origins") || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
          interaction_confirmation: String(formData.get("interaction_confirmation") || ""),
          service_workers: formData.has("service_workers"),
          user_agent: String(formData.get("user_agent") || "").trim() || null,
          allowed_origins: originsValue
            .split(/\r?\n/)
            .map((origin) => origin.trim())
            .filter(Boolean),
          navigation_timeout_seconds: timeout,
        },
        "Browser aggiornato. Riavvia il server MCP per applicare la nuova whitelist."
      );
    }
  );
  return form;
}

function contactSlug(value) {
  return value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 60);
}

function contactRow(contact = {}) {
  const row = h(
    "div",
    { class: "settings-contact" },
    h(
      "label",
      { class: "settings-field" },
      h("span", { text: "ID relazione" }),
      settingsInput("contact_user_id", contact.user_id || "", "text", { placeholder: "es. marco" })
    ),
    h(
      "label",
      { class: "settings-field" },
      h("span", { text: "Nome" }),
      settingsInput("contact_display_name", contact.display_name || "", "text", { placeholder: "Nome visibile" })
    ),
    h(
      "label",
      { class: "settings-field" },
      h("span", { text: "Telefono" }),
      settingsInput("contact_phone", contact.phone || "", "text", { placeholder: "+39…" })
    ),
    h("button", {
      class: "icon-button settings-remove",
      type: "button",
      text: "×",
      "aria-label": "Rimuovi contatto",
      onClick: () => row.remove(),
    })
  );
  return row;
}

function whatsappSettings(whatsapp, behavior) {
  const contacts = h("div", { class: "settings-contacts" });
  for (const contact of whatsapp.allowed_contacts || []) {
    contacts.append(contactRow(contact));
  }
  const add = h("button", {
    class: "button button-quiet",
    type: "button",
    text: "Aggiungi contatto",
  });
  add.addEventListener("click", () => {
    const row = contactRow();
    contacts.append(row);
    row.querySelector("input").focus();
  });
  const quiet = whatsapp.quiet_hours || {};
  const sending = Boolean(behavior.flags && behavior.flags.whatsapp_sending_enabled);
  const form = settingsForm(
    "Salva WhatsApp",
    [
      settingsCheck(
        "enabled",
        "Integrazione WhatsApp attiva",
        Boolean(whatsapp.enabled),
        "Abilita la whitelist dei contatti; l'invio resta comunque subordinato al flag di sicurezza."
      ),
      h(
        "div",
        { class: "settings-inline-fields" },
        settingsField(
          "Massimo al giorno",
          settingsInput("max_per_day", whatsapp.max_per_day, "number", {
            min: "0",
            max: "24",
            step: "1",
          })
        ),
        settingsField(
          "Silenzio dalle",
          settingsInput("quiet_start", quiet.start || "00:00", "text", {
            placeholder: "HH:MM",
            pattern: "[0-2][0-9]:[0-5][0-9]",
            inputmode: "numeric",
            maxlength: "5",
          })
        ),
        settingsField(
          "Silenzio alle",
          settingsInput("quiet_end", quiet.end || "07:00", "text", {
            placeholder: "HH:MM",
            pattern: "[0-2][0-9]:[0-5][0-9]",
            inputmode: "numeric",
            maxlength: "5",
          })
        )
      ),
      h(
        "div",
        { class: "settings-list-head" },
        h("span", { text: "Contatti autorizzati" }),
        add
      ),
      contacts,
      h(
        "p",
        { class: "settings-hint" },
        sending
          ? "Il flag di invio è attivo: l'executor può scrivere nell'outbox. L'invio da WhatsApp Web richiede il comando separato mnemosine-outbox, con una sessione visibile."
          : "Il flag di invio è spento: i messaggi restano bloccati anche con l'integrazione attiva. Si abilita dalla sezione Comportamento, con conferma esplicita."
      ),
    ],
    async (formData, form) => {
      const rows = [...form.querySelectorAll(".settings-contact")];
      const contactsPayload = rows
        .map((item) => {
          const display_name = item.querySelector("[name='contact_display_name']").value.trim();
          const phone = item.querySelector("[name='contact_phone']").value.trim();
          // L'ID è interno: se manca, un nome stabile basta per aggiungere il
          // contatto. Prima la riga veniva scartata senza spiegazione.
          const user_id = item.querySelector("[name='contact_user_id']").value.trim()
            || contactSlug(display_name);
          if (!user_id && (display_name || phone)) {
            throw new Error("Inserisci il nome o l'ID relazione per ogni contatto.");
          }
          return { user_id, display_name, phone };
        })
        .filter((item) => item.user_id);
      const max = Number(formData.get("max_per_day"));
      if (!Number.isInteger(max)) {
        throw new Error("Il limite giornaliero deve essere un intero.");
      }
      await saveSettings(
        "/api/settings/whatsapp",
        {
          enabled: formData.has("enabled"),
          allowed_contacts: contactsPayload,
          quiet_hours: {
            start: String(formData.get("quiet_start") || ""),
            end: String(formData.get("quiet_end") || ""),
          },
          max_per_day: max,
        },
        "WhatsApp aggiornato."
      );
    }
  );
  return form;
}

function behaviorSettings(behavior) {
  const flags = behavior.flags || {};
  const heartbeat = behavior.heartbeat || {};
  const form = settingsForm(
    "Salva comportamento",
    [
      h(
        "div",
        { class: "settings-checks-grid" },
        settingsCheck(
          "shadow_engine_enabled",
          "Motore dell'ombra",
          Boolean(flags.shadow_engine_enabled),
          "Applica alle interazioni un suggerimento prudente quando il riepilogo corrisponde a un esempio di trigger; i tratti restano privati."
        ),
        settingsCheck(
          "maintenance_mode",
          "Modalità manutenzione",
          Boolean(flags.maintenance_mode),
          "Sospende le iniziative automatiche senza spegnere la dashboard."
        ),
        settingsCheck(
          "whatsapp_sending_enabled",
          "Invio WhatsApp",
          Boolean(flags.whatsapp_sending_enabled),
          "Autorizza l'executor a consegnare gli impulsi ai contatti in whitelist."
        ),
        settingsCheck(
          "shadow_direct_read_enabled",
          "Lettura diretta dell'ombra",
          Boolean(flags.shadow_direct_read_enabled),
          "Bloccato per progetto: l'ombra non è leggibile direttamente dal server.",
          true
        )
      ),
      h(
        "p",
        { class: "settings-warning" },
        "Attivando l'invio, l'executor potrà depositare messaggi per i contatti autorizzati nell'outbox del profilo. Il trasporto è un comando separato (mnemosine-outbox) con sessione visibile; il permesso resta scritto in current_state.json. Attivalo solo con la whitelist dei contatti e la sessione browser sotto controllo."
      ),
      settingsField(
        "Conferma per attivare l'invio",
        settingsInput("sending_confirmation", "", "text", {
          placeholder: "Scrivi ABILITA",
          autocomplete: "off",
        }),
        flags.whatsapp_sending_enabled
          ? "Il flag è già attivo: lascia vuoto per mantenerlo, spunta o deseleziona per cambiarlo."
          : "Serve solo quando passi da spento ad attivo; per disattivarlo non è richiesta."
      ),
      h(
        "div",
        { class: "settings-inline-fields" },
        settingsField(
          "Intervallo battito (minuti)",
          settingsInput("interval_minutes", heartbeat.interval_minutes, "number", {
            min: "1",
            max: "1440",
            step: "1",
          })
        ),
        settingsField(
          "Pausa dopo un impulso (minuti)",
          settingsInput("cooldown_minutes", heartbeat.cooldown_minutes, "number", {
            min: "0",
            max: "10080",
            step: "1",
          })
        ),
        settingsField(
          "Massimo impulsi al giorno",
          settingsInput("max_impulses_per_day", heartbeat.max_impulses_per_day, "number", {
            min: "0",
            max: "24",
            step: "1",
          })
        )
      ),
    ],
    async (formData) => {
      const number = (name) => Number(formData.get(name));
      const values = [
        number("interval_minutes"),
        number("cooldown_minutes"),
        number("max_impulses_per_day"),
      ];
      if (values.some((value) => !Number.isInteger(value))) {
        throw new Error("Intervallo, pausa e limite devono essere interi.");
      }
      await saveSettings(
        "/api/settings/behavior",
        {
          flags: {
            visual_generation_enabled: formData.has("visual_generation_enabled"),
            shadow_engine_enabled: formData.has("shadow_engine_enabled"),
            maintenance_mode: formData.has("maintenance_mode"),
            whatsapp_sending_enabled: formData.has("whatsapp_sending_enabled"),
          },
          confirmation: String(formData.get("sending_confirmation") || ""),
          heartbeat: {
            interval_minutes: values[0],
            cooldown_minutes: values[1],
            max_impulses_per_day: values[2],
          },
        },
        "Comportamento aggiornato."
      );
    }
  );
  return form;
}

function profileSettings(profile) {
  const content = [
    h(
      "dl",
      { class: "settings-meta" },
      h("dt", { text: "Directory" }),
      h("dd", { class: "mono", text: text(profile.root) }),
      h("dt", { text: "Identità" }),
      h("dd", { text: `${text(profile.full_name)} · ${text(profile.identity_id)}` }),
      h("dt", { text: "Stato" }),
      h("dd", { text: label(PROFILE_STATUS_LABELS, profile.status) }),
      h("dt", { text: "Backup" }),
      h("dd", { class: "mono", text: text(profile.backup_dir) })
    ),
  ];
  if (!profile.can_archive) {
    content.push(
      h("p", {
        class: "settings-blocked",
        text: text(profile.archive_blocked, "Archiviazione non disponibile."),
      })
    );
    return content;
  }
  content.push(
    h("p", {
      class: "settings-warning",
      text:
        "Prima di procedere chiudi la chat locale, autonomy loop e il browser del profilo: " +
        "su Windows i file aperti bloccano lo spostamento. L'archiviazione sposta " +
        "configurazione, database e file in una cartella di backup, poi riporta la dashboard " +
        "al wizard. Il profilo non viene cancellato: il certificato TLS e la cache del " +
        "modello restano nel profilo.",
    }),
    settingsForm(
      "Archivia e crea nuovo",
      [
        settingsField(
          "Conferma",
          settingsInput("confirmation", "", "text", {
            placeholder: "Scrivi ELIMINA",
            autocomplete: "off",
          }),
          "La frase deve essere esattamente ELIMINA."
        ),
      ],
      async (formData) => {
        const result = await postJson("/api/profile/archive", {
          confirmation: String(formData.get("confirmation") || ""),
        });
        setNotice("Profilo archiviato. Creane uno nuovo dal wizard.", "success");
        window.location.assign(result.redirect || "/setup");
      },
      { danger: true }
    )
  );
  return content;
}

function renderVisual(data) {
  const content = $("#visual-content");
  const profile = data.profile || {};
  const fields = [
    ["body", "Corporatura"],
    ["face", "Viso"],
    ["hair", "Capelli"],
    ["eyes", "Occhi"],
    ["distinctive_marks", "Segni particolari"],
    ["style", "Stile visivo"],
  ];
  const controls = {};
  function randomizeVisualField(key) {
    const pool = VISUAL_SUGGESTIONS[key] || [];
    if (!pool.length || !controls[key]) {
      return;
    }
    controls[key].value = pool[Math.floor(Math.random() * pool.length)];
    controls[key].dispatchEvent(new Event("input", { bubbles: true }));
  }
  const generatorHead = h(
    "div",
    { class: "visual-generator-head" },
    h("p", {
      class: "settings-hint",
      text: "Scegli una proposta o sorteggia una riga: il dado accanto al titolo cambia solo quel campo. Puoi sempre scrivere a mano e salvare.",
    }),
    h(
      "button",
      {
        type: "button",
        class: "button button-quiet",
        text: "🎲 Genera tutto",
        onClick: () => {
          fields.forEach(([key]) => randomizeVisualField(key));
          setNotice("Aspetto generato: salva per applicarlo.", "success");
        },
      }
    )
  );
  const fieldNodes = fields.map(([key, title]) => {
    const textarea = h(
      "textarea",
      {
        name: key,
        rows: key === "body" ? "3" : "2",
        maxlength: "2000",
        placeholder: `Descrivi ${title.toLowerCase()}…`,
      },
      profile[key] || ""
    );
    controls[key] = textarea;
    return h(
      "div",
      { class: "settings-field visual-field-block" },
      h(
        "div",
        { class: "visual-field-head" },
        h("span", { text: title }),
        h("button", {
          type: "button",
          class: "setup-creator-dice",
          text: "🎲",
          title: `Sorteggia ${title.toLowerCase()}`,
          "aria-label": `Sorteggia ${title.toLowerCase()}`,
          onClick: () => randomizeVisualField(key),
        })
      ),
      textarea,
      h(
        "div",
        { class: "visual-suggestions" },
        (VISUAL_SUGGESTIONS[key] || []).map((value) =>
          h("button", {
            type: "button",
            class: "setup-choice",
            text: value,
            onClick: () => {
              textarea.value = value;
              textarea.dispatchEvent(new Event("input", { bubbles: true }));
            },
          })
        )
      )
    );
  });
  const form = settingsForm(
    "Salva aspetto",
    [
      generatorHead,
      ...fieldNodes,
      settingsField("Seed fisso (facoltativo)", settingsInput("fixed_seed", profile.fixed_seed ?? "", "number", { min: "0", max: "2147483647", step: "1", placeholder: "Vuoto = seed casuale per generazione" }), "Il seed aiuta la ripetibilità, ma non garantisce da solo la stessa identità."),
    ],
    async (formData) => {
      const seedText = String(formData.get("fixed_seed") || "").trim();
      const seed = seedText ? Number(seedText) : null;
      if (seed !== null && (!Number.isSafeInteger(seed) || seed < 0 || seed > 2147483647)) {
        throw new Error("Il seed deve essere un intero tra 0 e 2147483647.");
      }
      const result = await postJson("/api/visual/profile", {
        profile: Object.fromEntries([
          ...fields.map(([key]) => [key, String(formData.get(key) || "")]),
          ["fixed_seed", seed],
        ]),
        enabled: Boolean(state.loaded.visual && state.loaded.visual.enabled),
      });
      state.loaded.visual = result;
      renderVisual(result);
      setNotice("Profilo Aspetto aggiornato.", "success");
    }
  );

  const currentProvider = data.provider_id || "replicate";
  const providerSelect = h(
    "select",
    { name: "provider" },
    h("option", { value: "replicate" }, "Replicate · FLUX.1 Kontext Pro"),
    h("option", { value: "openai" }, "API online compatibile OpenAI"),
    h("option", { value: "local" }, "Locale · Forge / A1111")
  );
  providerSelect.value = currentProvider;
  const replicateKey = settingsInput("api_key", "", "password", {
    autocomplete: "new-password",
    spellcheck: "false",
    placeholder: data.provider_configured && currentProvider === "replicate"
      ? "Lascia vuoto per mantenere il token salvato"
      : "Token Replicate",
  });
  const openaiBase = settingsInput("base_url", currentProvider === "openai" ? data.base_url || "https://api.openai.com/v1" : "https://api.openai.com/v1", "url", { spellcheck: "false" });
  const openaiModel = settingsInput("model", currentProvider === "openai" ? data.model || "gpt-image-1" : "gpt-image-1");
  const openaiKey = settingsInput("api_key", "", "password", {
    autocomplete: "new-password",
    spellcheck: "false",
    placeholder: data.provider_configured && currentProvider === "openai"
      ? "Lascia vuoto per mantenere la chiave salvata"
      : "Chiave API",
  });
  const localBase = settingsInput("base_url", currentProvider === "local" ? data.base_url || "http://127.0.0.1:7860" : "http://127.0.0.1:7860", "url", { spellcheck: "false" });
  const localModel = settingsInput("model", currentProvider === "local" ? data.model || "" : "", "text", { placeholder: "es. realisticVisionV60_v60", spellcheck: "false" });
  const providerPanels = {
    replicate: h(
      "div",
      { class: "visual-provider-fields", "data-provider-panel": "replicate" },
      settingsField("Token Replicate", replicateKey, "Crealo su replicate.com/account/api-tokens. Resta in data/secrets/ e non viene mai mostrato.")
    ),
    openai: h(
      "div",
      { class: "visual-provider-fields", "data-provider-panel": "openai" },
      settingsField("URL base", openaiBase, "Includi /v1 se il provider lo richiede."),
      settingsField("Modello", openaiModel, "Il nome del modello di immagini accettato dal provider."),
      settingsField("Chiave API", openaiKey, "Salvata in data/secrets/visual_api_key.")
    ),
    local: h(
      "div",
      { class: "visual-provider-fields", "data-provider-panel": "local" },
      settingsField("URL base", localBase, "Solo loopback: 127.0.0.1, localhost o ::1."),
      settingsField("Checkpoint", localModel, "Facoltativo: il nome del checkpoint caricato in Forge/A1111."),
      h("p", { class: "settings-hint", text: "Avvia Forge/A1111 con --api --nowebui --port 7860. Mnemosine usa la sua API e non invia nulla fuori dal computer." })
    ),
  };
  function syncProviderPanels() {
    Object.entries(providerPanels).forEach(([kind, panel]) => {
      const active = kind === providerSelect.value;
      panel.hidden = !active;
      panel.querySelectorAll("input, select, textarea").forEach((element) => {
        element.disabled = !active;
      });
    });
  }
  providerSelect.addEventListener("change", syncProviderPanels);
  syncProviderPanels();
  const providerForm = settingsForm(
    "Salva provider",
    [
      settingsField("Provider immagini", providerSelect, "Mnemosine non esegue il modello: usa l'API che gli indichi."),
      ...Object.values(providerPanels),
    ],
    async (formData) => {
      const payload = {
        provider: String(formData.get("provider") || ""),
        base_url: String(formData.get("base_url") || ""),
        model: String(formData.get("model") || ""),
        api_key: String(formData.get("api_key") || ""),
      };
      const result = await postJson("/api/visual/provider", payload);
      state.loaded.visual = result;
      renderVisual(result);
      setNotice("Provider immagini salvato. La generazione resta disattivata finché non la abiliti.", "success");
    }
  );

  const reference = data.reference_available
    ? h("div", { class: "visual-reference" },
        h("img", { src: "/api/visual/image/reference", alt: "Immagine di riferimento del profilo" }),
        h("button", { class: "button button-danger", type: "button", text: "Rimuovi reference", onClick: async () => {
          try {
            const updated = await postJson("/api/visual/reference/remove", {});
            state.loaded.visual = updated;
            renderVisual(updated);
            setNotice("Reference rimossa dal profilo locale.", "success");
          } catch (error) { setNotice(error.message); }
        } })
      )
    : emptyState("Nessuna reference", "Carica un'immagine solo se vuoi usarla come riferimento per la generazione.");
  const upload = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", "aria-label": "Carica immagine di riferimento" });
  const uploadButton = h("button", { class: "button button-quiet", type: "button", text: "Carica reference", onClick: async () => {
    const file = upload.files && upload.files[0];
    if (!file) { setNotice("Seleziona prima un'immagine PNG, JPEG o WebP."); return; }
    if (file.size > 8 * 1024 * 1024) { setNotice("L'immagine deve pesare al massimo 8 MiB."); return; }
    try {
      uploadButton.disabled = true;
      const session = await api("/api/session");
      const response = await fetch("/api/visual/reference", {
        method: "POST", cache: "no-store",
        headers: { "Content-Type": file.type, "X-CSRF-Token": session.csrf },
        body: file,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `Errore HTTP ${response.status}`);
      state.loaded.visual = result;
      renderVisual(result);
      setNotice("Reference salvata nel profilo locale.", "success");
    } catch (error) { setNotice(error.message); }
    finally { uploadButton.disabled = false; }
  } });
  const external = data.external !== false;
  const price = Number(data.price_per_image_usd || 0);
  const referenceCard = settingsCard(
    "Immagine di riferimento",
    external
      ? "Salvata localmente; viene inviata al provider soltanto quando confermi una generazione."
      : "Salvata localmente e usata dal server locale: non esce dal computer.",
    h("div", { class: "visual-reference-stack" }, reference, upload, uploadButton)
  );

  const costNote = data.provider_id === "local"
    ? " · nessun costo per immagine"
    : price
      ? ` · circa $${price.toFixed(2)} per immagine`
      : " · costo secondo il listino del provider";
  const warning = h("p", {
    class: "settings-warning",
    text: external
      ? `La generazione usa ${data.provider}: testo e immagine di riferimento saranno inviati al provider esterno${price ? `; ogni output costa circa $${price.toFixed(2)}` : "; il costo dipende dal listino del provider"}. La coerenza del volto non è garantita.`
      : `La generazione usa ${data.provider} su questo computer: prompt e reference non vengono inviati a servizi esterni. La coerenza del volto non è garantita.`,
  });
  const enable = settingsCheck("visual_enabled", "Consenti generazione visiva", Boolean(data.enabled), "Le richieste vengono avviate manualmente qui, mai in automatico.");
  const enableConfirm = settingsField("Conferma attivazione (solo quando abiliti)", settingsInput("enable_confirmation", "", "text", { placeholder: "Scrivi GENERA", autocomplete: "off" }));
  let generateForm;
  generateForm = settingsForm("Genera immagine", [
    h("label", { class: "settings-check" }, h("input", { type: "checkbox", name: "external_consent" }), h("span", {}, h("strong", { text: external ? "Confermo l'invio al provider immagini" : "Confermo la generazione sul server locale" }), h("small", { text: external ? "La reference e il prompt saranno elaborati da un servizio esterno; potrebbero avere un costo." : "La reference e il prompt restano su questo computer." }))),
    settingsField("Scena o modifica", h("textarea", { name: "scene", rows: "3", maxlength: "1000", placeholder: "Es. ritratto all'aperto in una giornata di sole" })),
  ], async (formData) => {
    if (!formData.has("external_consent")) {
      throw new Error(external ? "Conferma esplicitamente l'invio della reference e del prompt al provider." : "Conferma esplicitamente la generazione con la reference locale.");
    }
    const button = generateForm.querySelector('button[type="submit"]');
    button.disabled = true;
    button.textContent = "Generazione in corso…";
    try {
      const result = await postJson("/api/visual/generate", {
        scene: String(formData.get("scene") || ""),
        consent_to_external_processing: true,
      });
      state.loaded.visual = await api("/api/visual/status");
      renderVisual(state.loaded.visual);
      setNotice(`Immagine generata (seed ${result.seed}).`, "success");
    } finally {
      if (button.isConnected) {
        button.disabled = false;
        button.textContent = "Genera immagine";
      }
    }
  });
  const activationForm = settingsForm("Salva autorizzazione", [enable, enableConfirm], async (formData) => {
    const result = await postJson("/api/visual/profile", {
      profile,
      enabled: formData.has("visual_enabled"),
      confirmation: String(formData.get("enable_confirmation") || ""),
    });
    state.loaded.visual = result;
    renderVisual(result);
    setNotice(result.enabled ? "Generazione visiva autorizzata." : "Generazione visiva disattivata.", "success");
  });

  const images = (data.generated_images || []).map((item) => h("figure", { class: "visual-image" },
    h("img", { src: `/api/visual/image/${encodeURIComponent(item.id)}`, alt: item.prompt || "Immagine generata" }),
    h("figcaption", { text: `${item.created_at || ""} · seed ${item.seed}` }),
    h("p", { text: item.prompt || "" }),
    h("button", { class: "button button-danger", type: "button", text: "Elimina", onClick: async (event) => {
      const button = event.currentTarget;
      try {
        button.disabled = true;
        const updated = await postJson("/api/visual/image/delete", { id: item.id });
        state.loaded.visual = updated;
        renderVisual(updated);
        setNotice("Immagine eliminata dal profilo locale.", "success");
      } catch (error) { setNotice(error.message); }
      finally { if (button.isConnected) button.disabled = false; }
    } })
  ));
  const checkButton = h("button", { class: "button button-quiet", type: "button", text: "Verifica ora", onClick: async (event) => {
    const button = event.currentTarget;
    try {
      button.disabled = true;
      const updated = await postJson("/api/visual/token/check", {});
      state.loaded.visual = updated;
      renderVisual(updated);
      setNotice("Verifica completata: nessuna immagine generata, nessun credito usato.", "success");
    } catch (error) { setNotice(error.message); }
    finally { if (button.isConnected) button.disabled = false; }
  } });
  const providerCard = settingsCard(
    "Provider immagini",
    data.provider,
    h("div", { class: "visual-generation" },
      providerForm,
      h("p", { class: "settings-hint", text: data.provider_configured
        ? (data.provider_id === "local"
          ? `Server locale configurato su ${data.base_url}.`
          : data.provider_id === "openai"
            ? `API configurata: ${data.base_url} · ${data.model || "modello predefinito"}.`
            : `Token configurato, origine: ${data.provider_source === "environment" ? "variabile d'ambiente" : "file locale protetto"}.`)
        : "Provider non configurato: compila i campi qui sopra e salva. La generazione resta bloccata finché non lo configuri." }),
      h("p", { class: "settings-meta", text: data.provider_checked_at
        ? `Ultima verifica: ${data.provider_checked_at} — ${data.provider_valid ? "raggiungibile" : "non riuscita"}${data.provider_detail ? ` (${data.provider_detail})` : data.provider_username ? ` (account ${data.provider_username})` : ""}.`
        : "Nessuna verifica registrata." }),
      data.provider_configured ? checkButton : null,
      h("p", { class: "settings-hint", text: external ? "La verifica non invia prompt né immagini e non genera costi." : "La verifica contatta solo il server locale." })
    )
  );
  clear(content, h("div", { class: "visual-grid" },
    settingsCard("Profilo visivo", "Descrizione persistente e seed del personaggio.", form),
    referenceCard,
    providerCard,
    settingsCard("Generazione", data.provider_configured ? `${data.provider}${costNote}` : `${data.provider} · provider non configurato`, h("div", { class: "visual-generation" }, warning, activationForm, data.enabled ? generateForm : emptyState("Generazione disattivata", "Salva prima l'autorizzazione esplicita."))),
    settingsCard("Immagini generate", "Cronologia locale di scene e seed.", images.length ? h("div", { class: "visual-gallery" }, ...images) : emptyState("Ancora nessuna immagine", "Le immagini generate appariranno qui."))
  ));
}

function mcpConnectionCard(data) {
  const mcp = data.mcp || {};
  const config = {
    mcpServers: {
      mnemosine: {
        command: mcp.command || "mnemosine",
        args: mcp.args || ["--root", data.root || ""],
        ...(mcp.cwd ? { cwd: mcp.cwd } : {}),
      },
    },
  };
  const configText = JSON.stringify(config, null, 2);
  const code = h("pre", { class: "settings-code" }, h("code", { text: configText }));
  const copyButton = h("button", {
    class: "button button-quiet",
    type: "button",
    text: "Copia configurazione",
    onClick: async () => {
      try {
        await navigator.clipboard.writeText(configText);
        setNotice("Configurazione MCP copiata.", "success");
      } catch (error) {
        setNotice("Copia non riuscita: seleziona il testo e copialo a mano.");
      }
    },
  });
  return settingsCard(
    "Collega il tuo modello (MCP)",
    "Mnemosine non esegue il modello: è il server che il tuo client interroga.",
    h(
      "div",
      { class: "visual-generation" },
      h("p", {
        class: "settings-hint",
        text: "Aggiungi questo blocco alla configurazione MCP del tuo client (Claude Desktop, Cursor, LM Studio, un agente scritto da te). Il client può usare qualsiasi modello: memoria, identità e persona arrivano dal profilo, non dal prompt del client.",
      }),
      code,
      copyButton,
      h("p", {
        class: "settings-hint",
        text: "Se hai installato il pacchetto, al posto di command/args puoi usare \"mnemosine\" con gli stessi argomenti. Dopo la modifica riavvia il client MCP.",
      })
    )
  );
}

function renderSettings(data) {
  const browser = data.browser || {};
  const whatsapp = data.whatsapp || {};
  const behavior = data.behavior || {};
  const profile = data.profile || {};
  clear(
    $("#settings-content"),
    h(
      "div",
      { class: "settings-grid" },
      mcpConnectionCard(data),
      settingsCard(
        "Browser controllato",
        "Whitelist, interazione, service worker e identità del browser.",
        browserSettings(browser)
      ),
      settingsCard(
        "WhatsApp",
        "Integrazione, contatti autorizzati e finestra di silenzio.",
        whatsappSettings(whatsapp, behavior)
      ),
      settingsCard(
        "Comportamento",
        "Flag del profilo e ritmo del battito autonomo.",
        behaviorSettings(behavior)
      ),
      settingsCard(
        "Profilo",
        "Ciclo di vita dei dati e nuova configurazione.",
        profileSettings(profile)
      )
    )
  );
}

async function loadVisual(force = false) {
  if (state.loaded.visual && !force) return;
  const content = $("#visual-content");
  setLoading(content, "Caricamento del profilo visivo…");
  try {
    const data = await api("/api/visual/status");
    state.loaded.visual = data;
    renderVisual(data);
  } catch (error) {
    setNotice(`Impossibile caricare Aspetto: ${error.message}`);
    clear(content, emptyState("Profilo visivo non disponibile", error.message));
  }
}

async function loadSettings(force = false) {
  if (state.loaded.settings && !force) return;
  const content = $("#settings-content");
  setLoading(content, "Caricamento delle impostazioni…");
  try {
    const data = await api("/api/settings");
    state.loaded.settings = data;
    renderSettings(data);
    setNotice("");
  } catch (error) {
    setNotice(`Impossibile caricare le impostazioni: ${error.message}`);
    clear(content, emptyState("Impostazioni non disponibili", error.message));
  }
}

/* -- manutenzione -------------------------------------------------------- */

const CHECK_LABELS = {
  profile: "Profilo",
  config: "Configurazione",
  acquaintances: "Incontrati",
  timeline: "Timeline",
  semantic: "Conoscenze",
  vector: "Indice vettoriale",
  outbox: "Outbox",
  files: "File",
};

function fmtBytes(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) return "—";
  if (number < 1024) return `${number} B`;
  if (number < 1024 * 1024) return `${(number / 1024).toFixed(1)} KB`;
  return `${(number / (1024 * 1024)).toFixed(1)} MB`;
}

function maintenanceBadge(ok, okText = "Regolare", koText = "Da controllare") {
  return badge(
    ok ? okText : koText,
    ok ? "maintenance-badge is-ok" : "maintenance-badge is-warning"
  );
}

function maintenanceIndexCard(index) {
  const entries = Object.entries(index.indexes || {});
  if (!entries.length) {
    return emptyState(
      "Indice non leggibile",
      index.error || "Esegui `python -m agent_memory.index --status` per il dettaglio."
    );
  }
  return h(
    "div",
    { class: "maintenance-stack" },
    h(
      "div",
      { class: "maintenance-row" },
      h("span", { text: "Stato complessivo" }),
      maintenanceBadge(Boolean(index.ok))
    ),
    h(
      "p",
      { class: "settings-hint" },
      `Directory: ${text(index.index_dir)} · modello: ${text(index.model)}`
    ),
    ...entries.map(([kind, item]) =>
      h(
        "div",
        { class: "maintenance-index-item" },
        h(
          "div",
          { class: "maintenance-check-head" },
          h("strong", {
            text: kind === "episodic" ? "Ricordi episodici" : "Conoscenze",
          }),
          maintenanceBadge(
            Boolean(item.ok),
            `${item.count} voci`,
            "Da ricostruire"
          )
        ),
        h("p", {
          class: "settings-hint",
          text: `stato ${text(item.status)} · vettori ${text(item.ntotal)}`,
        }),
        ...(item.issues || []).map((issue) =>
          h("p", { class: "maintenance-issue", text: issue })
        )
      )
    ),
    h("p", {
      class: "settings-hint",
      text: "Per allineare l'indice: python -m agent_memory.index --sync (o --rebuild).",
    })
  );
}

function maintenanceDatabasesCard(databases, root) {
  return h(
    "div",
    { class: "maintenance-stack" },
    h(
      "div",
      { class: "maintenance-row" },
      h("span", { text: "Directory profilo" }),
      h("code", { class: "mono", text: text(root) })
    ),
    h(
      "ul",
      { class: "maintenance-db-list" },
      ...databases.map((item) =>
        h(
          "li",
          { class: "maintenance-db-item" },
          h("strong", { text: item.name }),
          h("span", {
            text: item.exists
              ? `schema ${text(item.schema_version)} · ${fmtBytes(item.bytes)}`
              : "assente",
          }),
          maintenanceBadge(
            item.exists && Boolean(item.schema_version),
            "Ok",
            "Manca"
          )
        )
      )
    )
  );
}

function maintenanceCheckItem(check) {
  const errors = Array.isArray(check.errors) ? check.errors : [];
  const warnings = Array.isArray(check.warnings) ? check.warnings : [];
  const state = errors.length
    ? "is-error"
    : warnings.length
      ? "is-warning"
      : "is-ok";
  return h(
    "li",
    { class: `maintenance-check-item ${state}` },
    h(
      "div",
      { class: "maintenance-check-head" },
      h("strong", { text: label(CHECK_LABELS, check.name) }),
      h("span", {
        class: "maintenance-check-count",
        text: `${errors.length} errori · ${warnings.length} avvisi`,
      })
    ),
    ...errors.map((message) =>
      h("p", { class: "maintenance-issue", text: message })
    ),
    ...warnings.map((message) =>
      h("p", { class: "maintenance-issue is-warning", text: message })
    )
  );
}

function renderCheckReport(container, report) {
  const checks = Array.isArray(report.checks) ? report.checks : [];
  clear(
    container,
    h(
      "p",
      { class: "maintenance-check-summary" },
      h("strong", {
        text: report.ok ? "Nessun errore" : `${report.error_count} errori`,
      }),
      ` · ${report.warning_count} avvisi · ${fmtDate(report.generated_at, { time: true })}`
    ),
    h(
      "ul",
      { class: "maintenance-check-list" },
      ...checks.map(maintenanceCheckItem)
    )
  );
}

function maintenanceBackupRow(item, root, confirmationLabel) {
  const command =
    `mnemosine-backup --root "${root}" restore ${item.name} ` +
    `--confirm ${confirmationLabel}`;
  const copyButton = h("button", {
    class: "button button-quiet",
    type: "button",
    text: "Copia comando",
  });
  const restorePanel = h(
    "div",
    { class: "maintenance-restore-panel", hidden: true },
    h("p", {
      class: "settings-warning",
      text: "Sostituisce configurazione, database e file del profilo con quelli del backup. Esegui il comando da terminale con la dashboard ferma (CTRL+C), poi riavvia mnemosine-start.",
    }),
    h("pre", { class: "maintenance-command" }, h("code", { text: command })),
    h(
      "p",
      { class: "settings-hint",
        text: "Prima del ripristino viene creato un backup pre-restore; i backup precedenti restano." }
    ),
    copyButton
  );
  const verifyButton = h(
    "button",
    { class: "button button-quiet", type: "button", text: "Verifica" }
  );
  const restoreButton = h(
    "button",
    { class: "button button-quiet", type: "button", text: "Come ripristinare" }
  );
  copyButton.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(command);
      setNotice("Comando copiato.", "success");
    } catch (error) {
      setNotice("Copia non riuscita: seleziona il comando e copialo a mano.");
    }
  });
  verifyButton.addEventListener("click", async () => {
    verifyButton.disabled = true;
    verifyButton.textContent = "Verifica…";
    try {
      const result = await postJson("/api/maintenance/action", {
        action: "verify",
        name: item.name,
      });
      const check = result.result || {};
      if (check.ok) {
        setNotice(
          `Backup verificato: ${check.checked ?? check.files ?? 0} file e ${check.databases ?? 0} database integri.`,
          "success"
        );
      } else {
        setNotice(`Verifica fallita: ${(check.errors || []).join(" ")}`);
      }
    } catch (error) {
      setNotice(error.message);
    } finally {
      verifyButton.disabled = false;
      verifyButton.textContent = "Verifica";
    }
  });
  restoreButton.addEventListener("click", () => {
    restorePanel.hidden = !restorePanel.hidden;
  });
  return h(
    "div",
    { class: "maintenance-backup" },
    h(
      "div",
      { class: "maintenance-backup-main" },
      h("strong", { text: item.name }),
      h("span", {
        text: `${fmtDate(item.created_at, { time: true })} · ${item.files} file · ${fmtBytes(item.bytes)}`,
      })
    ),
    h(
      "div",
      { class: "maintenance-backup-actions" },
      verifyButton,
      restoreButton
    ),
    restorePanel
  );
}

function maintenanceBackupsCard(backups, root, confirmationLabel) {
  const items = Array.isArray(backups.items) ? backups.items : [];
  const rows = items.length
    ? items.map((item) => maintenanceBackupRow(item, root, confirmationLabel))
    : [
        emptyState(
          "Nessun backup",
          "Crea il primo snapshot: configurazione, database e file del profilo."
        ),
      ];
  return h(
    "div",
    { class: "maintenance-stack" },
    h(
      "div",
      { class: "maintenance-row" },
      h("span", {
        text: `${items.length} snapshot · ne conserva ${text(backups.keep)} per volta`,
      }),
      maintenanceBadge(Boolean(backups.ok), "Cartella pronta", "Errore")
    ),
    backups.error ? h("p", { class: "maintenance-issue", text: backups.error }) : null,
    ...rows
  );
}

function renderMaintenance(data) {
  const content = $("#maintenance-content");
  const backups = data.backups || { items: [], keep: 7 };
  const databases = Array.isArray(data.databases) ? data.databases : [];
  const confirmationLabel = text(data.restore_confirmation, "RIPRISTINA");

  const createButton = h("button", {
    class: "button",
    type: "button",
    text: "Crea backup adesso",
  });
  createButton.addEventListener("click", async () => {
    createButton.disabled = true;
    createButton.textContent = "Creazione…";
    try {
      const result = await postJson("/api/maintenance/action", {
        action: "create",
      });
      const name = (result.result || {}).name || "snapshot";
      await loadMaintenance(true);
      setNotice(`Backup ${name} creato.`, "success");
    } catch (error) {
      setNotice(error.message);
    } finally {
      createButton.disabled = false;
      createButton.textContent = "Crea backup adesso";
    }
  });

  const checkButton = h("button", {
    class: "button button-quiet",
    type: "button",
    text: "Esegui controllo",
  });
  const checkBox = h(
    "div",
    { class: "maintenance-check", "aria-live": "polite" },
    h("p", {
      class: "settings-hint",
      text: "Il controllo è in sola lettura e non modifica il profilo.",
    })
  );
  checkButton.addEventListener("click", async () => {
    checkButton.disabled = true;
    checkButton.textContent = "Controllo in corso…";
    clear(checkBox, loadingBlock("Verifica di profilo, database, indice e file…"));
    try {
      const result = await postJson("/api/maintenance/action", {
        action: "check",
      });
      renderCheckReport(checkBox, result.report || {});
    } catch (error) {
      clear(checkBox, emptyState("Controllo non eseguito", error.message));
      setNotice(error.message);
    } finally {
      checkButton.disabled = false;
      checkButton.textContent = "Esegui controllo";
    }
  });

  clear(
    content,
    h(
      "div",
      { class: "settings-grid" },
      settingsCard(
        "Stato del profilo",
        "Directory, database locali e versione dello schema.",
        maintenanceDatabasesCard(databases, data.root)
      ),
      settingsCard(
        "Indice vettoriale",
        "Copertura dei ricordi e delle conoscenze, senza caricare il modello.",
        maintenanceIndexCard(data.index || {})
      ),
      settingsCard(
        "Controllo di coerenza",
        "Profili, date, promozioni, outbox e file: verifica completa in sola lettura.",
        h("div", { class: "maintenance-stack" }, checkButton, checkBox)
      ),
      settingsCard(
        "Backup",
        "Snapshot verificabili di configurazione, database e file del profilo.",
        h(
          "div",
          { class: "maintenance-stack" },
          createButton,
          maintenanceBackupsCard(backups, data.root, confirmationLabel)
        )
      )
    )
  );
}

async function loadMaintenance(force = false) {
  if (state.loaded.maintenance && !force) return;
  const content = $("#maintenance-content");
  setLoading(content, "Caricamento della manutenzione…");
  try {
    const data = await api("/api/maintenance");
    state.loaded.maintenance = data;
    renderMaintenance(data);
    setNotice("");
  } catch (error) {
    setNotice(`Impossibile caricare la manutenzione: ${error.message}`);
    clear(content, emptyState("Manutenzione non disponibile", error.message));
  }
}

/* -- navigazione --------------------------------------------------------- */

const LOADERS = {
  overview: loadOverview,
  life: loadLife,
  relations: loadRelations,
  acquaintances: loadAcquaintances,
  timeline: loadTimeline,
  traits: loadTraits,
  semantic: loadSemantic,
  evolution: loadEvolution,
  settings: loadSettings,
  visual: loadVisual,
  maintenance: loadMaintenance,
};

function navigate(view) {
  if (!VIEW_META[view]) return;
  state.view = view;
  clearModalIfNeeded();
  for (const item of document.querySelectorAll(".nav-item")) {
    const active = item.dataset.view === view;
    item.classList.toggle("is-active", active);
    item.setAttribute("aria-current", active ? "page" : "false");
  }
  for (const section of document.querySelectorAll(".view")) {
    const active = section.id === `view-${view}`;
    section.hidden = !active;
    section.classList.toggle("is-active", active);
  }
  $("#page-title").textContent = VIEW_META[view].title;
  $("#page-subtitle").textContent = VIEW_META[view].subtitle;
  window.scrollTo({ top: 0, behavior: "smooth" });
  LOADERS[view]();
}

function clearModalIfNeeded() {
  if (!$("#memory-modal").hidden) closeModal();
}

function refresh() {
  const button = $("#refresh-button");
  button.disabled = true;
  state.loaded = {};
  LOADERS[state.view](true).finally(() => {
    button.disabled = false;
  });
}

function updateClock() {
  const now = new Date();
  $("#live-clock").textContent = new Intl.DateTimeFormat("it-IT", {
    weekday: "short",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(now);
}

function bindEvents() {
  for (const item of document.querySelectorAll(".nav-item")) {
    item.addEventListener("click", () => navigate(item.dataset.view));
  }
  $("#refresh-button").addEventListener("click", refresh);

  const timelineForm = $("#timeline-filters");
  timelineForm.addEventListener("submit", (event) => {
    event.preventDefault();
    state.loaded.timeline = null;
    loadTimeline(true);
  });
  let timelineTimer = null;
  $("#timeline-query").addEventListener("input", () => {
    window.clearTimeout(timelineTimer);
    timelineTimer = window.setTimeout(() => {
      state.loaded.timeline = null;
      loadTimeline(true);
    }, 250);
  });
  $("#timeline-source").addEventListener("change", () => {
    state.loaded.timeline = null;
    loadTimeline(true);
  });
  $("#timeline-access").addEventListener("change", () => {
    if ($("#timeline-access").value) $("#timeline-hidden").checked = false;
    state.loaded.timeline = null;
    loadTimeline(true);
  });
  $("#timeline-hidden").addEventListener("change", () => {
    if ($("#timeline-hidden").checked) $("#timeline-access").value = "";
    state.loaded.timeline = null;
    loadTimeline(true);
  });

  const semanticForm = $("#semantic-filters");
  semanticForm.addEventListener("submit", (event) => {
    event.preventDefault();
    state.loaded.semantic = null;
    loadSemantic(true);
  });
  let semanticTimer = null;
  $("#semantic-query").addEventListener("input", () => {
    window.clearTimeout(semanticTimer);
    semanticTimer = window.setTimeout(() => {
      state.loaded.semantic = null;
      loadSemantic(true);
    }, 250);
  });
  $("#semantic-type").addEventListener("change", () => {
    state.loaded.semantic = null;
    loadSemantic(true);
  });
  $("#semantic-status").addEventListener("change", () => {
    state.loaded.semantic = null;
    loadSemantic(true);
  });

  $("#modal-close").addEventListener("click", closeModal);
  document.querySelector("[data-close-modal]").addEventListener("click", closeModal);
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal();
  });
}

function init() {
  bindEvents();
  updateClock();
  window.setInterval(updateClock, 30_000);
  loadSession();
  const hash = window.location.hash.replace("#", "");
  navigate(VIEW_META[hash] ? hash : "overview");
}

init();
