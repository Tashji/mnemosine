"use strict";

const VIEW_META = {
  overview: {
    title: "Panoramica",
    subtitle: "Stato, memoria e identità in un'unica vista.",
  },
  relations: {
    title: "Relazioni",
    subtitle: "Contatti, fiducia e storico completo delle interazioni.",
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

function renderOverview(data) {
  const identity = data.identity || {};
  const current = data.state || {};
  const counts = data.counts || {};
  const activity = current.activity || {};
  const emotion = current.emotion || {};
  const routine = current.routine || {};
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
      h("div", { class: "state-line" }, h("dt", { text: "Luogo" }), h("dd", { text: text(activity.location) }))
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

  clear(
    $("#overview-content"),
    h("div", { class: "hero-grid" }, identityCard, stateCard),
    metrics,
    h("div", { class: "overview-columns" }, recentPanel, historyPanel),
    contactsPanel
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
  const meta = [];
  if (entry.sentiment) meta.push(`Tono ${text(entry.sentiment)}`);
  if (entry.memory_id) meta.push(`Ricordo ${entry.memory_id}`);
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
      meta.length
        ? h(
            "div",
            { class: "relation-event-meta" },
            meta.map((item) => h("span", { text: item }))
          )
        : null
    )
  );
}

function relationAbout(contact) {
  const preferences = Array.isArray(contact.preferences) ? contact.preferences : [];
  const facts = Array.isArray(contact.confirmed_facts) ? contact.confirmed_facts : [];
  if (!preferences.length && !facts.length && !contact.notes) return null;
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
    contact.notes
      ? h("p", { class: "relation-notes", text: contact.notes })
      : null
  );
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
    h(
      "div",
      { class: "relation-history" },
      h(
        "div",
        { class: "relation-history-head" },
        h("h3", { text: "Storico interazioni" }),
        h("span", { class: "count-chip", text: `${interactions.length}` })
      ),
      interactions.length
        ? h("ol", { class: "relation-events" }, interactions.map(relationEvent))
        : emptyState("Storico vuoto", "Nessuna interazione registrata per questo contatto.")
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
      emptyState("Nessuna relazione", "Il grafo delle relazioni è vuoto.")
    );
    return;
  }

  const initial = contacts.find((contact) => contact.user_id === state.relationId) || contacts[0];
  const buttons = [];
  const detailHost = h("div", { class: "relation-detail-host" });

  function select(userId) {
    const selected = contacts.find((contact) => contact.user_id === userId);
    if (!selected) return;
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
    const data = await api("/api/relations");
    state.loaded.relations = data;
    renderRelations(data);
    setNotice("");
  } catch (error) {
    setNotice(`Impossibile caricare le relazioni: ${error.message}`);
    clear(content, emptyState("Relazioni non disponibili", error.message));
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
        : null
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
      shadowCards.length ? h("div", { class: "shadow-grid" }, shadowCards) : emptyState("Nessun tratto dell'ombra", "La configurazione non contiene tratti latenti."),
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
  const form = h(
    "form",
    { class: "settings-form" },
    content,
    h("div", { class: "settings-actions" }, button)
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "Salvataggio…";
    try {
      await handler(new FormData(form), form);
    } catch (error) {
      setNotice(error.message);
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
          ? "Il flag di invio è attivo: l'executor può scrivere nell'outbox, ma il trasporto verso WhatsApp Web non è implementato."
          : "Il flag di invio è spento: i messaggi restano bloccati anche con l'integrazione attiva. Si abilita dalla sezione Comportamento, con conferma esplicita."
      ),
    ],
    async (formData, form) => {
      const rows = [...form.querySelectorAll(".settings-contact")];
      const contactsPayload = rows
        .map((item) => ({
          user_id: item.querySelector("[name='contact_user_id']").value.trim(),
          display_name: item.querySelector("[name='contact_display_name']").value.trim(),
          phone: item.querySelector("[name='contact_phone']").value.trim(),
        }))
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
          "visual_generation_enabled",
          "Generazione visiva",
          Boolean(flags.visual_generation_enabled),
          "Consente le richieste di immagini quando il modello visivo sarà disponibile."
        ),
        settingsCheck(
          "shadow_engine_enabled",
          "Motore dell'ombra",
          Boolean(flags.shadow_engine_enabled),
          "Mantiene attivi tratti latenti e riconsolidamenti."
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
        "Attivando l'invio, l'executor potrà depositare messaggi per i contatti autorizzati nell'outbox del profilo. Il trasporto verso WhatsApp Web non è ancora implementato, ma il permesso resta scritto in current_state.json. Attivalo solo con la whitelist dei contatti e la sessione browser sotto controllo."
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
        "L'archiviazione sposta configurazione, database e file in una cartella di backup, " +
        "poi riporta la dashboard al wizard. Il profilo non viene cancellato: il certificato " +
        "TLS e la cache del modello restano nel profilo.",
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

/* -- navigazione --------------------------------------------------------- */

const LOADERS = {
  overview: loadOverview,
  relations: loadRelations,
  timeline: loadTimeline,
  traits: loadTraits,
  semantic: loadSemantic,
  evolution: loadEvolution,
  settings: loadSettings,
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
