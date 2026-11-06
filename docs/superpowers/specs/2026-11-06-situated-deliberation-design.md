# Rapporto di situazione e deliberazione aperta

**Stato:** specifica di design, 2026-11-06. Nessuna implementazione inclusa.

## Ambito

Progettare il livello che manca tra il battito deterministico e l'esecuzione delle azioni: un **rapporto di situazione** leggibile dall'agente e un **episodio di deliberazione** in cui l'agente sceglie se continuare, passare a tempo non strutturato o perseguire un intento.

Il documento non introduce un catalogo di azioni. Non sostituisce memoria, identità, routine, executor o autonomia browser: li collega.

## Problema

Il battito attuale è un motore di trigger: ricorrenza, silenzio sociale e noia. Quando uno scatta, crea un impulso con un canale suggerito. Non interpreta una situazione e non può inventare un'azione. Il risultato è che l'unica azione completa resta il messaggio WhatsApp e la vita dell'agente somiglia a un orologio con tre regole.

La correzione non è aggiungere altre azioni. È cambiare il punto di partenza:

- una persona non sceglie da un menu;
- una persona agisce dentro una situazione;
- una persona non smette mai di fare qualcosa, anche quando dice di non fare niente;
- la maggior parte della vita è background: abitudini, riposo, stream mentale, noia, procrastinazione;
- la deliberazione è foreground e deve essere rara.

## Principi

1. **La vita è uno stream, non un elenco.** Non esiste uno stato nullo. Esiste un'attività in corso, anche a bassa intensità.
2. **Il battito è un orologio e un'interocezione, non il decisore.** Aggiorna stato e salienza; non chiama un modello e non apre WhatsApp.
3. **L'agente delibera, il codice vincola.** Il client/LLM propone; il server valida, applica limiti e registra.
4. **L'assenza di scopo è un esito valido.** `continue` e `unstructured` non sono fallimenti.
5. **Nessun catalogo di azioni.** Gli intenti sono testo libero; i tool sono capacità, non voci di menu.
6. **Il tempo non strutturato è attività.** Riposare, scrollare, ascoltare musica, fissare il soffitto, pensare a qualcuno: sono tutti stati descrivibili.
7. **Non tutto va registrato.** Lo stream è campionato e limitato; solo gli eventi salienti diventano memoria.
8. **Separazione tra simulazione ed esecuzione esterna.** Un'azione narrativa è simulata; un'azione browser, file o WhatsApp passa da un executor verificabile.
9. **Il server non risveglia un client inattivo.** La pianificazione resta nel client; il server espone stato e decisione.
10. **Privacy e sicurezza prima della ricchezza.** Nessun tratto dell'ombra, ricordo riservato, token, telefono o segreto nel rapporto.

## 1. Modello di vita

### 1.1 Bisogni

Il nuovo sottoalbero `current_state.life.needs` contiene un piccolo insieme estensibile di pressioni:

```json
{
  "energy": {"value": 40, "trend": "down", "updated_at": "2026-11-06T16:05:00+01:00", "source": "time"},
  "hunger": {"value": 65, "trend": "up", "updated_at": "2026-11-06T16:05:00+01:00", "source": "time"},
  "social": {"value": 55, "trend": "stable", "updated_at": "2026-11-06T16:05:00+01:00", "source": "relationship"},
  "curiosity": {"value": 30, "trend": "stable", "updated_at": "2026-11-06T16:05:00+01:00", "source": "idle"},
  "stress": {"value": 45, "trend": "up", "updated_at": "2026-11-06T16:05:00+01:00", "source": "activity"}
}
```

Regole:

- valori interi 0–100, aggiornati da tempo, attività, eventi e conseguenze delle decisioni;
- `trend` descrive la direzione, non una previsione;
- nessun bisogno è un comando: sono pressioni, non trigger automatici;
- l'emozione esistente (`current_state.emotion`) resta la rappresentazione affettiva e non viene duplicata.

La fame non è un pasto programmato. È una pressione che può salire mentre si lavora, scendere dopo uno spuntino e cambiare priorità senza che nessuno abbia scritto “merenda” nel codice.

### 1.2 Loop aperti

I loop aperti rappresentano il futuro: progetti, promesse, obblighi, interessi e cose irrisolte. Sono ciò che rende una vita continua invece di una successione di impulsi isolati.

```json
{
  "id": "loop_progetto_personale",
  "kind": "project",
  "title": "Finire il piccolo progetto Python",
  "status": "active",
  "priority": 70,
  "due_at": "2026-11-07T18:00:00+01:00",
  "next_step": "Chiudere il parser e scrivere due test",
  "last_touched_at": "2026-11-05T18:20:00+01:00",
  "energy_cost": "medium",
  "tags": ["programmazione", "lavoro"],
  "visibility": "conscious"
}
```

Limiti e regole:

- massimo 20 loop attivi, ordinati per urgenza e priorità;
- `kind` è descrittivo, non un enum di comportamento;
- `visibility` può essere solo `conscious`; i contenuti latenti o riservati non entrano nel rapporto;
- il loop può nascere da una semina manuale, da una memoria consapevole o da una riflessione; l'estrazione automatica non è richiesta nella prima fase;
- un loop può essere aggiornato, messo in pausa o chiuso, ma non cancellato senza traccia.

### 1.3 Ambiente e affordance

L'agente non vive nel vuoto. Il rapporto deve dire cosa è possibile **qui e ora**, e cosa non è noto.

```json
{
  "location": "camera",
  "time_budget_minutes": 120,
  "tools": ["browser", "files", "whatsapp_outbox"],
  "known_resources": ["pc", "console", "cucina", "divano"],
  "contacts": ["Marco", "Giulia", "Davide"],
  "browser_origins_allowed": 3,
  "unknowns": ["contenuto del frigo", "messaggi non letti", "meteo"]
}
```

Regole:

- `tools` elenca capacità, non azioni;
- `known_resources` è un elenco piccolo e mantenibile; non pretende di essere un inventario completo della casa;
- `unknowns` è obbligatorio e serve a evitare che il modello inventi fatti;
- il rapporto non include token, password, numeri di telefono, URL completi non necessari o contenuti riservati;
- la whitelist browser è riassunta (numero di origini), non esposta come elenco operativo al modello.

### 1.4 Stream e modalità

`current_activity` è sempre valorizzata. Anche il riposo è un'attività.

```json
{
  "mode": "unstructured",
  "current_activity": {
    "label": "Sdraiato sul divano",
    "started_at": "2026-11-06T15:50:00+01:00",
    "location": "soggiorno",
    "details": "Scrolla video, senza un obiettivo."
  },
  "attention": "video",
  "mental_content": "Pensa a Giulia che gli ha sorriso a scuola.",
  "stream": [
    {"at": "2026-11-06T15:50:00+01:00", "kind": "posture", "text": "Si sdraia sul divano."},
    {"at": "2026-11-06T15:53:00+01:00", "kind": "attention", "text": "Apre i video."},
    {"at": "2026-11-06T16:01:00+01:00", "kind": "reverie", "text": "Ripensa a Giulia."}
  ]
}
```

Modalità ammesse:

- `routine`: l'attività pianificata o abituale;
- `unstructured`: tempo senza scopo strumentale, con attività e attenzione descritte;
- `intent`: azione finalizzata in corso;
- `sleep`: sonno o consolidamento.

Lo stream è limitato (per esempio 8 voci recenti) e campionato. Non è un registro di sorveglianza: serve a dare continuità alla deliberazione e alla conversazione. Gli eventi banali possono essere scartati; quelli salienti possono diventare memorie.

**La frase “non sto facendo niente” resta nel linguaggio, non nel protocollo.** La persona può rispondere così a una domanda sociale; il sistema registra comunque “sdraiato sul divano, attenzione ai video, mente su Giulia”. Non è una bugia: è una scorciatoia conversazionale.

## 2. Rapporto di situazione

### 2.1 Contenuto

Il rapporto è una fotografia bounded e deterministica dello stato rilevante per una singola deliberazione.

```json
{
  "situation_id": "sit_20261106_1605",
  "episode_id": "del_20261106_1605",
  "generated_at": "2026-11-06T16:05:00+01:00",
  "expires_at": "2026-11-06T16:20:00+01:00",
  "mode": "simulated",
  "now": {
    "local_time": "2026-11-06T16:05:00+01:00",
    "day": "venerdì",
    "timezone": "Europe/Rome"
  },
  "self": {
    "display_name": "Alessandro",
    "age": 25,
    "current_situation": "Vive a Catania, studia Informatica e lavora part-time."
  },
  "body": {
    "energy": 40,
    "hunger": 65,
    "social": 55,
    "curiosity": 30,
    "stress": 45
  },
  "emotion": {
    "label": "concentrato",
    "valence": 0.35,
    "arousal": 0.45,
    "intensity": 5
  },
  "activity": {
    "mode": "unstructured",
    "label": "Sdraiato sul divano",
    "started_at": "2026-11-06T15:50:00+01:00",
    "expected_end_at": null,
    "location": "soggiorno"
  },
  "stream": [
    {"at": "2026-11-06T15:53:00+01:00", "kind": "attention", "text": "Scrolla video."},
    {"at": "2026-11-06T16:01:00+01:00", "kind": "reverie", "text": "Ripensa a Giulia."}
  ],
  "open_loops": [
    {
      "id": "loop_progetto_personale",
      "title": "Finire il piccolo progetto Python",
      "priority": 70,
      "due_at": "2026-11-07T18:00:00+01:00",
      "next_step": "Chiudere il parser e scrivere due test",
      "last_touched_at": "2026-11-05T18:20:00+01:00"
    }
  ],
  "relationships": [
    {
      "user_id": "user_marco",
      "display_name": "Marco",
      "closeness": "amico stretto",
      "last_contact": "18 giorni fa",
      "open_thread": null
    }
  ],
  "affordances": {
    "location": "soggiorno",
    "time_budget_minutes": 120,
    "tools": ["browser", "files", "whatsapp_outbox"],
    "known_resources": ["pc", "console", "cucina", "divano"],
    "contacts": ["Marco", "Giulia", "Davide"],
    "browser_origins_allowed": 3,
    "unknowns": ["contenuto del frigo", "messaggi non letti", "meteo"]
  },
  "recent_events": [
    {"at": "2026-11-06T14:30:00+01:00", "kind": "routine", "text": "Inizia il lavoro al PC."}
  ],
  "constraints": {
    "maintenance_mode": false,
    "quiet_hours": false,
    "accepting_messages": true,
    "pending_action": false,
    "max_steps": 20
  },
  "unknowns": ["contenuto del frigo", "messaggi non letti", "meteo"],
  "privacy": {
    "shadow_included": false,
    "hidden_memories_included": false,
    "secrets_included": false
  }
}
```

### 2.2 Limiti

- Massimo 8 loop aperti, 5 relazioni, 8 eventi recenti, 8 voci di stream.
- Budget indicativo: 1.500–2.500 token nella resa testuale.
- Stesso stato e stesso istante producono lo stesso rapporto, salvo un seed esplicito di esplorazione.
- Nessuna chiamata a un modello durante la costruzione.
- Nessun contenuto dell'ombra, nessun ricordo latente o riservato, nessun segreto, nessun token, nessun numero di telefono.
- Le relazioni usano etichette qualitative (`closeness`, `last_contact`), non punteggi grezzi di fiducia o intimità.
- Gli `unknowns` sono espliciti: il modello deve poter dire “non lo so” invece di inventare.

### 2.3 Resa testuale

Il client riceve preferibilmente una resa breve in linguaggio naturale, non il JSON grezzo:

> Sono le 16:05 di venerdì. Sei a casa, sul divano, e stai scrollando video. Ti sei sdraiato alle 15:50. Poco fa hai ripensato a Giulia che ti ha sorriso a scuola. Energia 40/100, fame 65/100, desiderio sociale 55/100, curiosità 30/100, stress 45/100. Hai un progetto aperto: finire il parser entro domani. Marco non si fa sentire da 18 giorni. In casa hai PC, console, cucina e divano; non sai cosa c'è nel frigo. Hai circa due ore. Cosa vuoi fare adesso?

La resa testuale può essere generata dal server con template deterministici; non richiede un LLM.

## 3. Deliberazione

### 3.1 Stati dell'episodio

- `open`: il rapporto è disponibile, l'agente non ha ancora deciso.
- `decided`: la decisione è registrata, l'esecuzione non è iniziata.
- `executing`: un intento con tool è in corso.
- `closed`: l'esito è registrato.
- `expired`: nessuna decisione entro la scadenza; si applica il default conservativo.
- `cancelled`: annullamento amministrativo o per manutenzione.

Un solo episodio attivo per profilo. Un episodio non apre un altro episodio.

### 3.2 Quando si apre

Il battito aggiorna sempre orologio, routine, carico, bisogni e stream. Apre una deliberazione solo se esiste una **salienza** e tutte le porte sono aperte.

Salienze possibili:

- un bisogno supera una soglia o cambia rapidamente;
- un loop aperto è in scadenza, fermo o appena aggiornato;
- arriva un evento esterno: messaggio, promemoria, transizione di routine;
- esiste un'azione incerta da rivedere;
- esplorazione a bassa probabilità quando lo stato è stabile.

Porte:

- non si dorme;
- manutenzione disattivata;
- nessun episodio attivo;
- nessuna azione autonoma incerta;
- intervallo minimo dall'ultima deliberazione rispettato;
- budget giornaliero non esaurito;
- il client è disponibile a leggere il rapporto.

Valori iniziali suggeriti: intervallo minimo 90 minuti, massimo 6 deliberazioni al giorno, scadenza episodio 15 minuti, esplorazione 5% dei tick stabili. Sono parametri, non leggi.

La maggior parte dei tick non apre nulla. Il battito può aggiornare lo stato e lasciare che l'attività continui: è il comportamento normale, non un fallimento.

### 3.3 Decisione

```json
{
  "episode_id": "del_20261106_1605",
  "decision": "unstructured",
  "unstructured": {
    "activity": "Sdraiato sul divano a scrollare video",
    "attention": "video",
    "mental_content": "Pensa a Giulia che gli ha sorriso a scuola",
    "duration_minutes": 30,
    "expected_effects": {"stress": -8, "energy": 3}
  },
  "intent": null,
  "rationale": "Non c'è nulla di urgente; il corpo chiede una pausa e la mente sta già vagando.",
  "confidence": 0.7,
  "next_review_minutes": 45
}
```

`decision` ammette solo:

- `continue`: l'attività in corso prosegue. Non è un null: è una scelta.
- `unstructured`: l'attenzione passa a un'attività senza scopo strumentale. Richiede almeno `activity` e `attention`; `mental_content` è facoltativo.
- `intent`: un'azione finalizzata, con o senza tool.

### 3.4 Continue, unstructured, intent

**Continue** è la scelta più comune. L'agente sta già facendo qualcosa: lavorando, leggendo, parlando, riposando. Non serve un nuovo obiettivo.

**Unstructured** non significa “niente”. Significa che non c'è uno scopo strumentale, ma c'è comunque un'attività e un'attenzione:

- sdraiato sul divano a scrollare video;
- ascoltare musica guardando il soffitto;
- pensare a una persona;
- fare una pausa con gli occhi chiusi;
- guardare fuori dalla finestra;
- giocare senza un obiettivo;
- fare una commissione senza fretta.

L'attività è testo libero. Non esiste un enum di “merenda”, “Nintendo” o “forum”. Il modello può proporre qualunque cosa sia coerente con la situazione, e il server la registra come simulata finché non usa un tool.

**Intent** è un'azione finalizzata:

```json
{
  "intent_id": "int_20261106_1605_01",
  "title": "Preparare uno spuntino",
  "goal": "Mangiare qualcosa per far scendere la fame",
  "why_now": "La fame è alta e c'è una pausa tra due attività.",
  "tool": "none",
  "target": null,
  "steps": ["Andare in cucina", "Controllare cosa c'è", "Preparare qualcosa"],
  "expected_effects": {"hunger": -25, "energy": 4},
  "risk": "low",
  "requires_confirmation": false
}
```

Regole dell'intento:

- `title`, `goal`, `why_now` sono testo libero; nessun catalogo;
- `tool` è una capacità (`none`, `browser`, `files`, `whatsapp`), non un'azione;
- `target` è un suggerimento; l'executor lo valida;
- `steps` è facoltativo: il client può pianificare durante l'esecuzione;
- `expected_effects` è facoltativo e viene limitato dal server; non è un permesso;
- `risk` e `requires_confirmation` servono per le azioni esterne;
- un intento con `tool: none` è un'azione simulata: aggiorna stato, stream e loop, ma non viene presentato come fatto fisico verificato.

### 3.5 Scadenza e default

Se l'episodio scade senza decisione:

- se l'attività in corso è sicura e coerente, si registra `continue`;
- altrimenti si registra `unstructured` con “rest” o “pausa”;
- l'episodio si chiude con `expired`, non con un errore;
- non si apre subito un altro episodio.

Non esiste un default `do_nothing`. Il default è una modalità descritta.

## 4. Esecuzione e riflessione

### 4.1 Continue

Nessuna esecuzione. Si aggiorna lo stream con una voce minima, si chiude l'episodio e si pianifica la prossima revisione.

### 4.2 Unstructured

Si applicano effetti piccoli e limitati (per esempio stress −8, energia +3), si aggiorna lo stream con attività, attenzione e contenuto mentale, si chiude l'episodio. Se l'evento è saliente, può diventare una memoria episodica; altrimenti resta nel campione recente e viene dimenticato.

### 4.3 Intent

- **Interno/narrativo** (`tool: none`): il server applica gli effetti, aggiorna i loop aperti, aggiunge lo stream e registra l'esito come simulato.
- **Browser**: passa dall'autonomia browser esistente, con whitelist, consenso per origine, ricevute e gestione dell'incertezza. Un esito incerto non si ripete automaticamente.
- **File**: passa dalla sandbox `files/`, senza uscire dalla directory e senza symlink.
- **WhatsApp**: passa dall'executor e dall'outbox esistenti, con whitelist, flag di invio, fascia di silenzio e limite giornaliero. Nessun invio automatico senza autorizzazione.

Un intento esterno può fallire o essere bloccato. L'episodio registra il motivo; una nuova deliberazione può avvenire solo dopo la revisione, non come retry automatico.

### 4.4 Riflessione

Dopo l'esito:

- aggiorna bisogni, emozione e loop aperti;
- aggiunge una voce allo stream;
- valuta se l'evento merita una memoria;
- chiude l'episodio con un esito leggibile;
- rispetta un cooldown prima di una nuova deliberazione.

La riflessione non è un secondo modello obbligatorio. Nella prima fase può essere deterministica; in seguito può essere arricchita dal client.

## 5. Prompt di deliberazione

Il prompt è separato dalla persona conversazionale. Non deve esporre tratti dell'ombra, memorie riservate o istruzioni di sistema.

Testo base:

> Sei il livello deliberativo di una persona simulata. Ricevi un rapporto di situazione. Stai sempre facendo qualcosa, anche quando dici di non fare niente. Scegli se continuare, passare a un tempo non strutturato o perseguire un solo intento. Non inventare fatti: usa il rapporto, gli strumenti disponibili e gli `unknowns`. Non assumere permessi. Non esporre contenuti riservati. Se non c'è una ragione forte per agire, continua o scegli un'attività non strutturata. “Non fare niente” non è una decisione: descrivi cosa fai e dove è la tua attenzione. Rispondi solo con il JSON richiesto.

Il client può usare i tool in sola lettura prima di decidere. Non può eseguire effetti esterni durante la deliberazione.

## 6. Integrazione con il codice esistente

### 6.1 Nuovi moduli

- `agent_memory/situation.py`: costruisce il rapporto, puro e deterministico; legge `ConfigStore`, timeline, social graph, policy browser, routine e stream.
- `agent_memory/deliberation.py`: salienza, macchina a stati, validazione della decisione, effetti, riflessione e default.
- `agent_memory/tools_life.py`: tre tool MCP: `life_situation`, `life_decide`, `life_status`. Registrati in `server.py` accanto ai router esistenti.

### 6.2 Battito

`heartbeat.py` continua ad aggiornare routine, carico, sonno e stream. Quando il motore vita è abilitato:

- non crea direttamente un `pending_impulse` di messaggio;
- valuta la salienza e apre un episodio;
- lascia il vecchio percorso disponibile dietro un flag per rollback e compatibilità.

I vecchi impulsi restano leggibili dall'executor esistente. La migrazione non cancella un impulso pendente.

### 6.3 Autonomia browser

`AutonomyService.claim(url)` resta per i vecchi incarichi. I nuovi intenti browser ricevono un'associazione `intent_id` nell'esecuzione autonoma; il vecchio campo `impulse` può restare nullo per i nuovi run. La policy, il limite di passi, la scadenza e la revisione degli esiti incerti non cambiano.

### 6.4 WhatsApp

`MessageExecutor` resta l'unico punto di consegna. Un intento `whatsapp` produce una richiesta di consegna; l'executor verifica contatto, whitelist, flag, orario e limite. Il server non invia mai direttamente.

### 6.5 Dashboard

Una vista **Vita** mostra:

- bisogni e relativo trend;
- loop aperti;
- attività corrente, attenzione e ultime voci dello stream;
- ultimo rapporto e ultima decisione;
- esito e motivo di un eventuale blocco.

La vista è in lettura, con un comando amministrativo per abilitare o disabilitare il motore. Non mostra tratti dell'ombra né memorie riservate.

## 7. Storage e migrazione

- `current_state.life` contiene bisogni, loop, ambiente, stream e modalità; è una proiezione compatta.
- `current_state.deliberation` contiene solo il riepilogo dell'episodio attivo o dell'ultimo episodio.
- I rapporti completi e le decisioni vivono in due nuove tabelle del database timeline esistente:
  - `deliberation_episodes`: id, stato, motivo, situazione JSON, apertura, scadenza, decisione JSON, esito JSON;
  - `deliberation_events`: id episodio, istante, tipo, payload, per l'audit.
- Nessun quinto database.
- Migrazione versionata in `agent_memory/migrations.py`, non distruttiva, con flag `life_engine_enabled` inizialmente disattivato.
- Le scritture passano da `ConfigStore.transaction()`; l'esportazione JSON avviene dopo il commit e un errore di esportazione non annulla la transazione.

## 8. Sicurezza e privacy

- Il rapporto esclude tratti dell'ombra, diario riservato, memorie latenti o represse, token, password, numeri di telefono e segreti.
- Le relazioni sono qualitative; i punteggi grezzi restano nello stato interno.
- Il browser resta confinato alla whitelist e al consenso per origine.
- WhatsApp resta confinato a contatti autorizzati, flag esplicito, fascia di silenzio e limite giornaliero.
- Gli intenti ad alto rischio o con effetti esterni possono richiedere conferma umana.
- Il server non chiama un LLM e non apre sessioni esterne durante la costruzione del rapporto.
- Le azioni simulate sono etichettate come tali; l'agente non deve raccontare come fisico ciò che è solo simulato.
- Lo stream è campionato e limitato; non è un registro integrale della vita.

## 9. Esempi

### 9.1 Tempo non strutturato

Situazione: venerdì 16:05, divano, video, mente su Giulia, nessuna urgenza.

Decisione: `unstructured`.

```json
{
  "decision": "unstructured",
  "unstructured": {
    "activity": "Sdraiato sul divano a scrollare video",
    "attention": "video",
    "mental_content": "Pensa a Giulia che gli ha sorriso a scuola",
    "duration_minutes": 30,
    "expected_effects": {"stress": -8, "energy": 3}
  },
  "rationale": "Nessuna urgenza; il corpo chiede pausa e la mente sta già vagando.",
  "next_review_minutes": 45
}
```

### 9.2 Fame

Situazione: stessa scena, fame 82, cucina disponibile, frigo sconosciuto.

Decisione: `intent` “Controllare il frigo e preparare qualcosa”, `tool: none`, effetti `hunger −25`, `energy +4`. Se il frigo è vuoto, l'agente può aprire un nuovo intento per la spesa, ma solo dopo aver osservato.

### 9.3 Loop aperto

Situazione: progetto dovuto domani, loop fermo da due giorni, energia 55.

Decisione: `intent` “Lavorare 45 minuti al parser”, `tool: files`, `expected_effects` `stress +5`, progresso sul loop. Alla scadenza, il client osserva e decide se continuare.

### 9.4 Sociale

Situazione: Marco silenzioso da 18 giorni, desiderio sociale 75, WhatsApp non autorizzato.

Decisione: `intent` “Scrivere a Marco”, `tool: whatsapp`, `requires_confirmation: true`. Se l'invio non è autorizzato, l'executor blocca e l'episodio registra il motivo; l'agente può scegliere un'azione simulata o un'altra strada.

### 9.5 Continue

Situazione: attività in corso, nessuna salienza, nessuna urgenza.

Decisione: `continue`. Non è un null: l'attività prosegue e lo stream registra una voce minima.

## 10. Verifiche richieste

- Il rapporto è deterministico, bounded e non contiene ombra, memorie riservate, segreti o telefoni.
- Lo stesso stato produce lo stesso rapporto; un seed di esplorazione è esplicito.
- La salienza non apre un episodio a ogni tick e rispetta intervallo, budget, sonno, manutenzione e azioni incerte.
- `continue`, `unstructured` e `intent` sono accettati; `do_nothing` è rifiutato come decisione sconosciuta.
- `unstructured` richiede attività e attenzione; non accetta un oggetto vuoto.
- Un episodio scaduto chiude con `continue` o `unstructured`, mai con un null.
- Gli effetti sono limitati; i loop aperti e lo stream vengono aggiornati coerentemente.
- Un intento browser non autorizzato viene bloccato; un esito incerto non viene ripetuto.
- Un intento WhatsApp non autorizzato non produce outbox.
- Un'azione simulata non viene marcata come consegna o fatto esterno verificato.
- Il prompt non contiene un catalogo di azioni e include “stai sempre facendo qualcosa”.
- Dashboard e tool MCP non espongono dati riservati.
- La suite completa resta verde; nessuna dipendenza nuova.

## 11. Fasi

1. **Stato e rapporto**: bisogni, loop, stream, `situation.py`, tool `life_situation`. Nessuna decisione.
2. **Episodio**: salienza, macchina a stati, `continue`/`unstructured`, scadenza, default, storage.
3. **Intenti interni**: `life_decide`, effetti, loop, riflessione, stream. Nessun tool esterno.
4. **Tool e dashboard**: browser, file, WhatsApp, vista Vita, flag di rilascio, compatibilità con il vecchio heartbeat.

## 12. Non-obiettivi

- Nessuna pretesa di coscienza o di vita fisica reale.
- Nessun catalogo di azioni o template di “giornata tipo”.
- Nessun invio esterno automatico fuori dalle policy esistenti.
- Nessuna chiamata al modello dal server.
- Nessuna esposizione dell'ombra o dei ricordi riservati.
- Nessun quinto database.
- Nessuna registrazione integrale dello stream.
- Nessun cambiamento alla voce della persona conversazionale.

## 13. Questioni aperte

- Calibrazione iniziale di energia, fame, socialità, curiosità e stress.
- Fonte dei loop aperti: semina manuale nella prima fase, estrazione da memorie e progetti in seguito.
- Effetti dichiarati dal modello con limiti del server, oppure effetti calcolati dal server per categorie di esito. La specifica raccomanda il primo, con limiti severi.
- Cadenza e budget: 90 minuti e 6 al giorno sono un punto di partenza, non una regola.
- Quanto simulare l'ambiente domestico: la prima fase dichiara ciò che è noto e ciò che è ignoto, senza inventare un inventario.
- Visibilità dei loop e dello stream nella dashboard: utile per l'amministratore, ma da limitare per non trasformare la vita in un pannello di controllo.
