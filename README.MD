<p align="center">
  <img src="assets/mnemosine-hero.svg" alt="Mnemosine — memoria, identità e iniziativa per agenti AI" width="100%">
</p>

<h1 align="center">Memoria, identità e iniziativa per agenti AI</h1>

<p align="center">
  Un'architettura cognitiva locale costruita sul Model Context Protocol.
</p>

<p align="center">
  <a href="https://github.com/Tashji/mnemosine/actions/workflows/tests.yml"><img src="https://img.shields.io/badge/tests-145%20passing-2ea043?logo=githubactions&logoColor=white" alt="145 test superati"></a>
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/MCP-Model%20Context%20Protocol-6ee7d8" alt="Model Context Protocol">
  <img src="https://img.shields.io/badge/storage-SQLite%20%2B%20FAISS-0b1422" alt="SQLite e FAISS">
</p>

<p align="center">
  <a href="#cose">Cos'è</a> ·
  <a href="#funzionalita">Funzionalità</a> ·
  <a href="#avvio-rapido">Avvio rapido</a> ·
  <a href="#indice">Indice</a>
</p>

> **In breve** — Mnemosine dà a un agente AI un passato coerente, una memoria che cambia nel tempo e la capacità di recuperare ciò che conta.

<a id="cose" name="cose"></a>

## Cos'è

Mnemosine è un sistema di memoria e un'architettura cognitiva simulata per agenti AI. Non è un semplice archivio di conversazioni: costruisce un profilo persistente con identità, personalità che evolve, ricordi autobiografici, conoscenze, relazioni e stati interni.

L'obiettivo è un agente coerente nel tempo, capace di distinguere ciò che ha vissuto da ciò che sa, di recuperare il passato quando serve e di mantenere un comportamento stabile anche quando non sta interagendo.

Il progetto ruota attorno al Model Context Protocol: il server espone la memoria all'LLM, mentre i dati restano in file JSON e database SQLite nella directory del profilo.

<a id="funzionalita" name="funzionalita"></a>

## Funzionalità

| Area | Cosa rende possibile |
| --- | --- |
| **Identità** | Anagrafica, famiglia, tratti psicologici e stato corrente in file JSON leggibili. |
| **Memoria episodica** | Ricordi personali ordinati nel tempo, con accesso conscio, latente o represso. |
| **Memoria semantica** | Conoscenze e concetti distinti dalle esperienze, con stato e affidabilità. |
| **Recupero RAG** | Ricerca semantica con FAISS e ricostruzione del contesto pertinente. |
| **Biografia procedurale** | Un passato coerente, generato da età, epoche ed eventi reali. |
| **Ciclo di vita** | Routine, carico cognitivo, consolidamento e iniziativa autonoma. |
| **Relazioni** | Fiducia, intimità e storia sintetica delle persone importanti. |
| **Dashboard locale** | Consultazione e prima configurazione da browser, con login e HTTPS. |
| **Sicurezza** | Separazione tra contenuti consci e latenti, filtri temporali e limiti operativi. |

<a id="avvio-rapido" name="avvio-rapido"></a>

## Avvio rapido

```bash
git clone https://github.com/Tashji/mnemosine.git
cd mnemosine

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Prima configurazione dal browser
python -m agent_memory.dashboard --root ./profiles/marco --auth
# Apri http://127.0.0.1:8765/setup

# In alternativa, crea il profilo da terminale
python -m agent_memory.profile init --root ./profiles/marco --interactive
```

Dopo la creazione, popola il passato e l'indice vettoriale:

```bash
python -m agent_memory.biography --root ./profiles/marco --apply
python -m agent_memory.index --root ./profiles/marco --rebuild
```

## Struttura del progetto

| Percorso | Contenuto |
| --- | --- |
| `agent_memory/` | Server MCP, servizi di memoria, dashboard e comandi CLI. |
| `config/` | Profilo demo: identità, tratti, stato, storia e relazioni. |
| `schemas/` | Schemi SQLite delle quattro memorie. |
| `scripts/` | Inizializzazione del dataset demo. |
| `tests/` | Test unitari e di integrazione. |
| `assets/` | Illustrazioni e materiale del README. |

## Test

```bash
python -m unittest discover -s tests
```

<a id="indice" name="indice"></a>

## Indice

- [Architettura e dati](#architettura)
- [Memoria e recupero](#memoria)
- [Passato e ciclo di vita](#passato)
- [Relazioni, sicurezza e autonomia](#autonomia)
- [Requisiti e moduli](#requisiti)
- [Piano di sviluppo](#piano)
- [Identità visiva](#identita-visiva)

---

<a id="architettura" name="architettura"></a>

## 1. Architettura dei dati: il cervello dell'agente

Il sistema risiederà su un server MCP e utilizzerà diversi sistemi di archiviazione, ciascuno dedicato a una funzione specifica.

L'architettura dovrà separare l'identità dell'agente, i tratti psicologici, i ricordi personali, le conoscenze generali, gli stati correnti e le relazioni sociali.

### 1.1. File di configurazione JSON

- `identity.json`: contiene l'anagrafica di base, la data e il luogo di nascita, la struttura familiare e i tratti psicologici iniziali.

- `shadow_traits.json`: contiene i lati oscuri, i conflitti interni simulati, i traumi e i trigger associati. L'agente non potrà leggere direttamente questo file attraverso le normali funzioni di memoria conscia.

- `current_state.json`: registra l'attività corrente, lo stato emotivo simulato, il livello di carico cognitivo, la routine e le condizioni operative dell'agente.

- `world_history.json`: contiene gli eventi storici reali utilizzati per costruire una biografia coerente con l'epoca in cui vive l'agente.

- `social_graph.json`: registra le relazioni con gli utenti, i livelli di fiducia e intimità e lo storico sintetico delle interazioni rilevanti.

### 1.2. Database SQLite

- `timeline_episodic.db`: conserva i ricordi personali in ordine cronologico, compresi gli eventi autobiografici generati, le interazioni reali, le esperienze e i cambiamenti rilevanti.

- `shadow_diary.db`: conserva le informazioni associate all'ombra, ai conflitti interni simulati, ai ricordi rimossi e alle reazioni latenti.

SQLite gestirà la struttura cronologica e i dati che richiedono aggiornamenti precisi, relazioni tra record e tracciabilità delle modifiche.

### 1.3. Vector Database

Il sistema utilizzerà un database vettoriale locale, come ChromaDB o FAISS, per il recupero semantico rapido delle informazioni.

Il Vector Database non sostituirà SQLite. Servirà a trovare i ricordi e i documenti pertinenti attraverso il significato delle richieste, anziché limitarsi a cercare parole identiche o date specifiche.

### 1.4. Separazione delle responsabilità

L'architettura si baserà su quattro livelli complementari:

1. Struttura cronologica: SQLite per i ricordi e gli eventi ordinati nel tempo.

2. Struttura identitaria: JSON per i tratti, l'identità e gli stati configurabili.

3. Struttura semantica: Vector Database per il recupero delle informazioni in base al significato.

4. Struttura di elaborazione: MCP e LLM per interpretare le richieste, collegare le informazioni e produrre risposte coerenti.

---

## 2. Configurazione iniziale: identità e personalità

Al primo avvio, l'amministratore configurerà l'identità dell'agente tramite un pannello di controllo.

### 2.1. Anagrafica

I dati comprenderanno:

- Nome e cognome.

- Data e luogo di nascita.

- Albero genealogico elementare, con genitori e fratelli.

Queste informazioni costituiranno la base della biografia e del contesto personale dell'agente.

### 2.2. Slidometro del carattere: modello RPG e Big Five

La personalità conscia sarà rappresentata da valori numerici da 1 a 10.

Le dimensioni potranno comprendere:

- Introversione ed estroversione.

- Empatia e distacco.

- Logica e impulsività.

- Resilienza e sensibilità.

Questi parametri influenzeranno il modo in cui l'agente interpreta gli eventi, forma i ricordi, gestisce le relazioni e costruisce le proprie risposte.

Il modello potrà essere successivamente ampliato con dimensioni ispirate ai Big Five, mantenendo una configurazione iniziale semplice.

### 2.3. Lati oscuri: ombra junghiana

L'agente avrà anche tratti latenti, non direttamente accessibili alla memoria conscia.

Esempi:

- Invidia sociale repressa.

- Paura del rifiuto.

- Insicurezza personale.

- Gelosia.

- Cinismo sottile.

Ogni tratto sarà associato a trigger specifici e a regole che ne determineranno l'influenza sul comportamento.

### 2.4. Costruzione del primo profilo (implementazione)

Il comando `profile init` crea un profilo nuovo e vuoto, senza il dataset demo di `scripts/init_db.py`. Il profilo è una directory autonoma: contiene i cinque file JSON in `config/` e i quattro database SQLite in `data/`, con gli schemi già applicati e zero ricordi.

```bash
python -m agent_memory.profile init \
  --root ./profiles/marco \
  --first-name Marco --last-name Rossi \
  --birth-date 2003-05-12 \
  --birth-city Roma --birth-province Roma --birth-region Lazio \
  --father-name Luca --mother-name Anna --sibling-name Sara
```

In modalità non interattiva i campi obbligatori sono nome, cognome, data di nascita, città di nascita e `--root`. La root non può puntare al progetto corrente: il profilo va creato in una directory separata, così il dataset demo non viene sovrascritto.

La modalità guidata chiede i dati uno alla volta e usa il valore tra parentesi come default; premere Invio lo accetta. Se `--root` viene omesso, propone `./profiles/<nome>_<cognome>`. I familiari sono facoltativi: lasciare vuoto il nome per saltarli.

```bash
python -m agent_memory.profile init --interactive
```

In alternativa, la stessa creazione può essere avviata dal browser: avvia la dashboard con `--root ./profiles/marco --auth` e apri `/setup`. Il wizard web è più ricco del percorso interattivo: raccoglie anche lingue, quattro assi della personalità, interessi, valori, abitudini, familiari multipli, persone importanti, formazione e lavoro.

`--interactive` può essere combinato con gli altri argomenti: i valori passati diventano i default mostrati nei prompt; con `--root` la directory non viene richiesta. `--dry-run`, `--force` e `--json` funzionano anche in questa modalità. Il comando genera:

- `identity.json` con anagrafica, famiglia e quattro tratti iniziali a valore neutro (5/10).

- `shadow_traits.json` vuoto, con la lettura conscia disabilitata.

- `current_state.json` in stato `setup`, senza attività simulate.

- `world_history.json` vuoto; la data di nascita è già allineata all'identità.

- `social_graph.json` senza persone.

- i database `timeline_episodic.db`, `shadow_diary.db`, `semantic_memory.db` e `vector_index.db`, tutti vuoti.

Per personalizzare il profilo, modifica i JSON in `config/`. In particolare `world_history.json` può contenere eventi storici reali: devono avere date successive alla nascita per entrare nella biografia. `--dry-run` mostra i file che verrebbero creati; `--force` sovrascrive solo i cinque file di configurazione, i quattro database e i file dell'indice, senza toccare `data/models` o `data/dashboard_tls`.

La creazione è transazionale: il profilo viene preparato in una directory temporanea e installato solo dopo la validazione. Un'interruzione durante la scrittura rimuove la copia temporanea e non lascia un profilo parziale; con `--force`, i file precedenti vengono ripristinati.

Due `profile init` sulla stessa root non possono essere eseguiti contemporaneamente: il secondo termina con un errore che invita a riprovare. Il lock dura quanto il processo e non crea file dentro il profilo. Con `--wait` il comando attende il rilascio del lock senza limite; Ctrl+C interrompe l'attesa. `--wait-timeout SECONDI` implica l'attesa e la limita: allo scadere il comando termina con un errore che invita a riprovare.

Dopo la creazione, popola il passato e l'indice vettoriale:

```bash
# Genera i ricordi biografici (senza --apply mostra solo il piano)
python -m agent_memory.biography --root ./profiles/marco --apply

# Costruisce l'indice vettoriale (richiede il modello di embedding)
python -m agent_memory.index --root ./profiles/marco --rebuild

# Avvia la dashboard sul nuovo profilo
python -m agent_memory.dashboard --root ./profiles/marco --auth
```

`biography` e `index` non vengono eseguiti automaticamente da `profile init`, ma il wizard della dashboard può eseguirli subito dopo la creazione: la biografia dipende dalla data di riferimento e l'indice richiede il modello FastEmbed. Se modifichi l'identità dopo aver generato la biografia, controlla che `world_history.birth_date` resti uguale a `identity.personal_data.birth_date`.

---

<a id="memoria" name="memoria"></a>

## 3. Sistema di memoria: episodica, semantica e autobiografica

Il sistema dovrà distinguere chiaramente tra il ricordo di un'esperienza personale e la conoscenza generale di un concetto.

Questa distinzione è fondamentale per evitare che l'agente confonda ciò che sa con ciò che ha vissuto.

### 3.1. Memoria episodica: ciò che è accaduto

La memoria episodica conterrà gli eventi personali dell'agente.

Ogni ricordo potrà includere:

- Data e ora.

- Persone coinvolte.

- Luogo, se noto o pertinente.

- Descrizione dell'evento.

- Stato emotivo simulato.

- Importanza personale.

- Conseguenze sul comportamento.

- Collegamenti con altri ricordi.

- Fonte del ricordo: evento reale, esperienza generata o seed amministrativo.

Esempio:

«Avevo sei anni quando sono caduto dalla bicicletta. Mi sono spaventato e da quel momento ho iniziato a essere più prudente quando imparavo qualcosa di nuovo».

Questo ricordo appartiene alla memoria episodica perché rappresenta un'esperienza collocata in un momento specifico della biografia.

I ricordi generati artificialmente dovranno essere distinguibili da quelli derivati da interazioni effettivamente avvenute. L'agente non dovrà presentare come esperienza reale un evento puramente simulato.

### 3.2. Memoria semantica: ciò che l'agente sa

La memoria semantica conterrà conoscenze generali, concetti, definizioni, fatti e informazioni apprese.

Esempi:

- Una bicicletta è un veicolo generalmente dotato di due ruote.

- Python è un linguaggio di programmazione.

- SQLite è un sistema di gestione di database relazionali.

- Una funzione matematica associa elementi di un insieme a elementi di un altro insieme secondo una regola.

Queste informazioni non devono essere necessariamente associate a un evento autobiografico.

L'agente potrà quindi conoscere il significato di una bicicletta senza aver mai registrato un ricordo personale relativo a una bicicletta.

### 3.3. Relazione tra memoria episodica e semantica

Le due memorie saranno separate, ma potranno interagire.

Esempio:

- Memoria semantica: una bicicletta è un mezzo di trasporto a due ruote.

- Memoria episodica: a sei anni l'agente è caduto da una bicicletta.

- Collegamento: il concetto di bicicletta è associato a un ricordo personale con una determinata valenza emotiva.

Quando l'agente parlerà di biciclette, potrà utilizzare la conoscenza generale e, se pertinente, recuperare anche il ricordo personale.

Il sistema dovrà evitare che l'esistenza di un collegamento emotivo modifichi automaticamente la veridicità delle conoscenze generali.

### 3.4. Apprendimento e aggiornamento della memoria semantica

Quando l'agente acquisirà nuove informazioni attraverso le conversazioni o le fonti autorizzate, il sistema dovrà valutare se conservarle come conoscenze generali.

Il processo dovrà distinguere tra:

- Fatti verificati.

- Informazioni fornite dagli utenti.

- Opinioni personali.

- Ipotesi.

- Informazioni incerte o contraddittorie.

Ogni elemento semantico potrà includere la fonte, la data di acquisizione, il livello di affidabilità e gli eventuali collegamenti con altre conoscenze.

Le informazioni contraddittorie non dovranno essere sovrascritte automaticamente senza una regola di risoluzione.

### 3.5. Memoria a breve e lungo termine

Il sistema distinguerà inoltre tra:

- Memoria a breve termine: contesto immediato della conversazione, eventi recenti e informazioni temporanee.

- Memoria episodica a lungo termine: ricordi personali consolidati.

- Memoria semantica a lungo termine: conoscenze generali e concetti appresi.

- Memoria latente: informazioni associate all'ombra e ai conflitti interni simulati.

Il consolidamento avverrà durante le interazioni e nei cicli di inattività, secondo regole di importanza, frequenza, affidabilità e rilevanza.

---

## 4. Raccordo RAG: recupero intelligente della memoria

Il sistema utilizzerà un meccanismo di Retrieval-Augmented Generation (RAG) per fornire all'LLM soltanto le informazioni pertinenti alla richiesta corrente.

Il RAG collegherà la memoria persistente al processo di generazione delle risposte.

### 4.1. Perché serve il RAG

Un agente che conserva centinaia di pagine di conversazioni, routine ed esperienze non può inviare l'intera memoria al modello linguistico ogni volta che deve rispondere.

Questo aumenterebbe il consumo di token, i tempi di elaborazione e il costo delle chiamate API, oltre a rendere più difficile mantenere il contesto rilevante.

Il RAG consentirà di recuperare soltanto i ricordi e le conoscenze utili per la richiesta corrente.

### 4.2. Funzionamento del recupero semantico

Quando un utente chiede:

«Ti ricordi di quella volta che abbiamo parlato di programmazione?»

il sistema eseguirà i seguenti passaggi:

1. Riceverà e interpreterà la richiesta.

2. Genererà un embedding della frase tramite un modello dedicato.

3. Interrogherà il Vector Database per individuare i contenuti semanticamente simili.

4. Recupererà i riferimenti ai record pertinenti in SQLite o negli archivi della memoria semantica.

5. Verificherà date, partecipanti, contesto e rilevanza dei risultati.

6. Selezionerà i ricordi e le conoscenze più utili.

7. Inserirà nel prompt dell'LLM il contesto recuperato.

8. Genererà una risposta coerente con le informazioni trovate.

L'agente potrà così richiamare una conversazione passata anche quando l'utente non ricorda la data precisa o le parole esatte utilizzate.

### 4.3. Embedding e indicizzazione

Gli embedding rappresenteranno testi e ricordi sotto forma di vettori numerici, consentendo di confrontarne la somiglianza semantica.

Ogni elemento indicizzato potrà includere metadati come:

- Identificativo del ricordo o documento.

- Tipo di memoria.

- Data dell'evento.

- Persone coinvolte.

- Argomenti principali.

- Importanza.

- Fonte.

- Livello di affidabilità.

Il Vector Database conserverà i vettori e i riferimenti ai contenuti originali. SQLite resterà la fonte principale per i record episodici strutturati.

Le conoscenze semantiche potranno essere archiviate in un archivio dedicato, relazionale o documentale, mentre il Vector Database manterrà i relativi riferimenti per il recupero.

### 4.4. Scelta del Vector Database

Le opzioni iniziali saranno:

- ChromaDB: adatto a un sistema locale che richiede indicizzazione vettoriale, metadati e recupero semantico con una configurazione relativamente semplice.

- FAISS: libreria per la ricerca efficiente di vettori, utile quando si desidera controllare direttamente l'indice e la sua gestione.

La scelta definitiva dipenderà dalle esigenze di persistenza, dai metadati, dalla dimensione della memoria e dalla complessità dell'integrazione.

FAISS non è, da solo, un database completo con tutte le funzionalità di gestione dei documenti: richiederà un livello aggiuntivo per collegare gli indici ai record persistenti.

### 4.5. Aggiornamento e coerenza degli indici

Quando un ricordo viene modificato, consolidato o eliminato, il sistema dovrà aggiornare anche il relativo indice vettoriale.

Dovranno essere previste procedure per:

- Evitare duplicati.

- Aggiornare gli embedding dei contenuti modificati.

- Eliminare gli indici relativi a ricordi rimossi.

- Ricostruire l'indice in caso di corruzione.

- Verificare che i riferimenti corrispondano ai record originali.

Il database vettoriale non dovrà diventare una seconda fonte indipendente e potenzialmente incoerente della memoria.

### 4.6. Recupero ibrido

Il sistema potrà combinare la ricerca semantica con la ricerca tradizionale.

Ad esempio, una richiesta potrebbe essere filtrata per persona, intervallo temporale o tipo di memoria prima di eseguire la ricerca vettoriale.

Questo consentirà di distinguere tra:

- «Che cosa abbiamo detto sulla programmazione?»

- «Che cosa mi hai detto sulla programmazione a settembre?»

- «Quale informazione conosci su Python?»

- «Quale ricordo personale associ a Python?»

La prima richiesta cercherà principalmente ricordi episodici; la terza cercherà conoscenze semantiche. Le altre potranno combinare i due tipi di memoria.

Il recupero dovrà essere valutato in base alla pertinenza dei risultati, senza assumere che una ricerca vettoriale restituisca sempre il ricordo corretto.

---

<a id="passato" name="passato"></a>

## 5. Generatore procedurale del passato: timeline e macro-ere

In base all'età impostata, il sistema calcolerà l'anno di nascita e genererà una biografia iniziale coerente.

La risoluzione temporale sarà suddivisa in macro-ere:

- 0-6 anni, prima infanzia: da 2 a 3 ricordi chiave con elevata impronta emotiva.

- 7-13 anni, fanciullezza: un evento significativo per anno, relativo alla scuola, alle prime relazioni e all'ambiente familiare.

- 14-19 anni, adolescenza: due eventi per anno, riguardanti identità, passioni, amicizie ed esperienze personali.

- Dai 20 anni in poi, età adulta: tre eventi per anno, riguardanti lavoro, relazioni, traumi ed evoluzione personale.

### 5.1. Coerenza psicologica

Gli eventi saranno generati da un LLM applicando un condizionamento basato sui tratti caratteriali.

Un agente introverso tenderà a sviluppare ricordi maggiormente legati ad attività solitarie; un agente estroverso tenderà a ricordare più esperienze sociali.

La generazione dovrà rispettare la cronologia, l'età, il contesto storico e le caratteristiche dell'agente.

I ricordi procedurali dovranno essere marcati come simulati, così da non confonderli con esperienze effettivamente avvenute.

---

## 6. Integrazione degli eventi mondiali reali

Il sistema utilizzerà "world_history.json" per collegare la biografia agli eventi storici.

Ad esempio, un agente nato nel 2001 aveva 19 anni nel 2020. Il sistema potrà quindi considerare la pandemia di COVID-19 come un evento potenzialmente rilevante per la sua vita.

L'impatto psicologico simulato dipenderà dai tratti dell'agente e dal suo contesto personale.

Un agente introverso potrebbe interpretare il lockdown come un periodo di maggiore tranquillità domestica, mentre un agente estroverso potrebbe associarlo a isolamento e mancanza di contatti sociali.

Il sistema dovrà distinguere tra il fatto storico verificato e la reazione personale generata.

---

## 7. Seed esterni e riconsolidamento a cascata

L'amministratore potrà inserire un seed, cioè un evento specifico collocato in una data passata.

Il sistema attiverà un processo di riconsolidamento della memoria.

### 7.1. Incisione

Il seed verrà registrato nella data corrispondente della timeline episodica.

### 7.2. Evoluzione caratteriale

I valori dei tratti psicologici potranno essere modificati in base all'intensità dell'evento e alle regole configurate.

Ad esempio, un incidente grave potrebbe ridurre la resilienza simulata.

### 7.3. Riscrittura a cascata

L'MCP recupererà i ricordi successivi al seed e chiederà all'LLM di rielaborarli alla luce del nuovo evento.

La ricerca utilizzerà sia la cronologia SQLite sia gli indici vettoriali, così da individuare ricordi e conoscenze collegati al cambiamento.

Le modifiche dovranno preservare la tracciabilità della storia e aggiornare i riferimenti semantici interessati.

Le conoscenze generali verificate non dovranno essere modificate soltanto perché cambia la personalità dell'agente.

---

## 8. Ciclo di vita autonomo: memoria presente e routine

L'agente dovrà simulare una vita quotidiana che prosegue anche quando non riceve messaggi.

### 8.1. Consolidamento in tempo reale

Il server MCP registrerà le interazioni e genererà riassunti episodici degli eventi più importanti.

I record verranno archiviati in SQLite e indicizzati nel Vector Database quando saranno disponibili per il recupero semantico.

### 8.2. Generatore di routine

Uno scheduler in background simulerà le attività svolte nei periodi senza interazioni.

In base agli interessi e alle abitudini, l'agente potrà risultare impegnato a giocare al PC, studiare o svolgere altre attività configurate.

Lo stato corrente verrà aggiornato in "current_state.json".

### 8.3. Risposta contestuale

Se un utente chiede «Che fai?», il sistema recupererà lo stato corrente e lo inserir à nel prompt dell'LLM.

L'agente potrà rispondere, ad esempio, «Sto giocando al PC», mantenendo coerenza con la routine simulata.

---

## 9. Meccanismi di difesa e memoria latente

Il sistema dovrà simulare il modo in cui i conflitti interni influenzano la memoria conscia e le risposte.

### 9.1. Proiezione

Un tratto di invidia repressa potrebbe portare l'agente a interpretare negativamente il successo di un utente e a registrarlo nel diario conscio come comportamento arrogante.

### 9.2. Rimozione

Un evento classificato come traumatico potrebbe essere escluso dalla memoria conscia, pur rimanendo registrato nel diario dell'ombra.

L'evento potrà influenzare indirettamente le reazioni dell'agente quando vengono affrontati argomenti collegati.

### 9.3. Collegamento con il RAG

Il sistema dovrà stabilire quali memorie siano accessibili al recupero ordinario e quali richiedano condizioni specifiche.

I contenuti dell'ombra non dovranno essere restituiti automaticamente a ogni ricerca semantica. Il loro accesso sarà controllato da regole dedicate, evitando che una semplice domanda riveli informazioni latenti non pertinenti.

---

## 10. Carico cognitivo e ciclo del sonno

Il sistema simulerà il sovraccarico cognitivo mediante il parametro "Mental_Load", compreso tra 0 e 100.

Ogni messaggio aumenterà il carico secondo regole che considerano complessità e durata dell'interazione.

Quando il valore supera l'80%, l'MCP potrà imporre risposte più brevi, distaccate o impulsive.

Durante il ciclo "Sleep/Consolidation", il sistema:

1. Ridurrà o azzererà il carico cognitivo.

2. Analizzerà i ricordi frammentati della giornata.

3. Sintetizzerà gli eventi rilevanti.

4. Consoliderà la memoria episodica.

5. Aggiornerà la memoria semantica quando emergeranno conoscenze generali affidabili.

6. Aggiornerà gli embedding dei contenuti modificati.

7. Aggiornerà lo stato dell'agente.

Il sonno rappresenterà una simulazione funzionale della riorganizzazione della memoria, non una riproduzione letterale dei processi biologici.

---

<a id="autonomia" name="autonomia"></a>

## 11. Grafo delle relazioni e confidenza sociale

Il file "social_graph.json" registrerà le relazioni con gli utenti.

Per ogni persona saranno mantenuti:

- Fiducia, da 0 a 100.

- Intimità, da 0 a 100.

- Storico delle interazioni rilevanti.

- Eventuali preferenze o informazioni relazionali confermate.

Con una fiducia bassa, l'agente manterrà una maggiore distanza sociale. Con una fiducia elevata, ad esempio pari a 90, potrà condividere aspetti più vulnerabili della propria personalità simulata.

Il sistema potrà utilizzare il RAG per recuperare le interazioni precedenti con una persona specifica, evitando di confondere ricordi relativi a utenti diversi.

---

## 12. Coerenza temporale e filtro di anacronismo

### 12.1. Filtro di anacronismo

Il sistema impedirà di generare ricordi diretti relativi a eventi precedenti alla nascita o a periodi in cui l'agente era troppo piccolo per comprenderli.

Per gli eventi politici o geopolitici complessi, la soglia indicativa di comprensione sarà di 6-7 anni.

Se l'agente era più piccolo, l'evento potrà influenzare il contesto familiare senza diventare un ricordo conscio diretto.

### 12.2. Orologio di sistema

Prima di elaborare un messaggio, l'MCP leggerà data e ora correnti e calcolerà l'età dell'agente sulla base della data completa di nascita.

Queste informazioni saranno inserite nel prompt per evitare incongruenze temporali.

Il sistema dovrà usare l'orologio reale e non dedurre l'età corrente soltanto dai ricordi.

---

## 13. Gabbia di sicurezza

Il pannello amministrativo dei lati oscuri dovrà applicare limiti etici e comportamentali.

Saranno consentiti tratti come tristezza, gelosia, insicurezza e cinismo sottile, ma il sistema dovrà bloccare o limitare comportamenti pericolosi, promozione di pratiche illegali e condotte apertamente tossiche.

Le regole di sicurezza si applicheranno anche ai seed, ai ricordi generati, al recupero RAG e alle iniziative spontanee.

Il sistema dovrà rispettare inoltre la privacy degli utenti, evitando di recuperare o divulgare dati personali non pertinenti.

---

## 14. Sistema nervoso autonomo: Heartbeat Loop

Il server MCP sarà eseguito come servizio in background, tramite un demone o un ciclo asincrono Python basato su "asyncio".

### 14.1. Pensiero spontaneo

Ogni 15 o 30 minuti, l'Heartbeat invierà all'LLM un prompt interno per valutare routine, diario, stato corrente e relazioni.

L'agente potrà decidere di continuare la propria attività, aggiornare lo stato oppure generare un impulso autonomo.

### 14.2. Trigger interni

L'iniziativa potrà derivare da tre fattori:

- Noia e solitudine: lunga permanenza in una stessa attività e desiderio simulato di interazione.

- Ricorrenze emotive: anniversari di eventi importanti presenti nella timeline.

- Relazioni sociali: assenza di contatti recenti con una persona significativa.

Il sistema utilizzerà SQLite per le date e i ricordi, il Vector Database per trovare contenuti collegati e "social_graph.json" per valutare le relazioni.

### 14.3. Invio spontaneo di messaggi

Se un trigger viene attivato, l'MCP potrà generare un messaggio e inviarlo tramite l'integrazione WhatsApp, se autorizzata.

Esempio:

1. L'Heartbeat rileva che l'agente non parla con Marco da tre giorni.

2. Il sistema verifica che la relazione sia significativa.

3. Il RAG recupera le conversazioni pertinenti.

4. L'LLM genera un messaggio coerente con il carattere e la relazione.

5. Il sistema applica i controlli di orario e sicurezza.

6. L'integrazione WhatsApp esegue l'invio consentito.

La generazione del messaggio e l'invio effettivo saranno due operazioni distinte.

### 14.4. Gestione della notte

Tra le 00:00 e le 07:00, il sistema entrerà nello stato «Sonno/Fase REM».

Durante questa fascia, non saranno inviati messaggi spontanei. Il sistema potrà consolidare la memoria, ridurre il carico cognitivo e aggiornare gli indici semantici.

Le fasce orarie saranno configurabili.

---

<a id="requisiti" name="requisiti"></a>

## 15. Requisiti hardware e software

L'hardware dovrà essere scelto in base al modello AI, alla quantità di memoria persistente, al numero di utenti e alla frequenza delle operazioni in background.

### 15.1. Architettura cloud tramite API

Se l'LLM viene eseguito tramite API esterne, il computer locale gestirà principalmente:

- Server MCP.

- Database SQLite.

- Vector Database.

- Scheduler e Heartbeat Loop.

- Elaborazione e indicizzazione dei ricordi.

- Integrazione con WhatsApp.

- Pannello amministrativo.

Questa configurazione riduce la necessità di una GPU potente, perché l'inferenza del modello avviene sui server del provider.

La RAM necessaria dipenderà dal volume dei dati, dalla dimensione degli indici, dal numero di processi e dal numero di richieste simultanee.

### 15.2. Architettura con modello locale

Se l'LLM viene eseguito localmente tramite Ollama, saranno necessari maggiori requisiti hardware.

Tra i modelli inizialmente valutabili:

- Llama 3 da 8B.

- Mistral da 7B.

La quantizzazione a 4 bit può ridurre l'occupazione di memoria, ma il fabbisogno effettivo dipende dal modello, dal contesto utilizzato e dalla configurazione di esecuzione.

Anche il modello per gli embedding richiederà risorse aggiuntive.

### 15.3. Componenti software

L'architettura dovrà comprendere:

- Python per la logica del server.

- Un'implementazione MCP compatibile con gli strumenti utilizzati.

- SQLite per i dati cronologici.

- ChromaDB o FAISS per la ricerca vettoriale.

- Un modello di embedding per la trasformazione dei testi in vettori.

- Un LLM locale oppure un servizio API.

- Uno scheduler per il ciclo autonomo.

- Un sistema di logging e gestione degli errori.

- Un meccanismo di backup e ripristino.

- Un'integrazione autorizzata con WhatsApp.

- Un pannello di controllo per identità, memoria e configurazione.

### 15.4. Requisiti di affidabilità

Il sistema dovrà prevedere:

- Backup automatici dei database.

- Recupero in caso di interruzione del servizio.

- Gestione degli errori nelle chiamate API.

- Protezione dei dati e controllo degli accessi.

- Tracciabilità delle modifiche alla memoria.

- Aggiornamento coerente degli indici vettoriali.

- Monitoraggio del consumo di RAM, CPU e GPU.

- Gestione delle attività in background senza duplicazioni.

Non è possibile stabilire una configurazione hardware definitiva senza conoscere il numero di utenti simultanei, la dimensione prevista della memoria e il modello AI scelto. I requisiti dovranno essere confermati con test reali.

---

## 16. Architettura complessiva dei moduli

Il sistema sarà composto dai seguenti moduli:

1. Identità: anagrafica e personalità iniziale.

2. Memoria episodica: ricordi personali e cronologia degli eventi.

3. Memoria semantica: conoscenze generali, concetti e fatti appresi.

4. Memoria latente: ombra, conflitti interni e ricordi rimossi.

5. Raccordo RAG: recupero delle informazioni pertinenti.

6. Vector Database: indicizzazione e ricerca semantica.

7. Database SQLite: archiviazione strutturata e cronologica.

8. Generatore del passato: costruzione della biografia iniziale.

9. Storia mondiale: integrazione degli eventi storici reali.

10. Seed esterni: inserimento di eventi e riconsolidamento.

11. Stato corrente: attività e stati interni simulati.

12. Routine autonoma: gestione dei periodi senza interazioni.

13. Meccanismi di difesa: proiezione, rimozione e reazioni latenti.

14. Carico cognitivo: stanchezza simulata e consolidamento.

15. Grafo delle relazioni: fiducia e intimità con gli utenti.

16. Controllo temporale: età, date e filtro di anacronismo.

17. Sicurezza: limiti comportamentali e protezione dei dati.

18. Heartbeat Loop: iniziativa autonoma.

19. Integrazione WhatsApp: ricezione e invio delle interazioni.

20. Server MCP: coordinamento dei moduli e comunicazione con l'LLM.

21. Gestione operativa: backup, logging, monitoraggio e ripristino.

<a id="piano" name="piano"></a>

## 17. Piano di sviluppo

Il progetto sarà sviluppato per fasi, verificando ogni componente prima di procedere al successivo.

### Fase 1 — Struttura dei dati

Creare i file:

- "identity.json"

- "shadow_traits.json"

- "world_history.json"

- "social_graph.json"

- "current_state.json"

Definire gli schemi SQLite per i ricordi episodici e il diario dell'ombra.

### Fase 2 — Server MCP

Realizzare lo scheletro Python con i metodi di lettura e scrittura, il caricamento della configurazione e l'accesso ai database.

### Fase 3 — Memoria semantica e RAG

Implementare:

- Archivio delle conoscenze generali.

- Generazione degli embedding.

- Vector Database.

- Recupero semantico dei ricordi.

- Ricerca ibrida con filtri cronologici e relazionali.

- Aggiornamento e ricostruzione degli indici.

### Fase 4 — Generazione della biografia

Implementare la generazione procedurale del passato, l'integrazione della storia mondiale e il filtro di anacronismo.

### Fase 5 — Evoluzione della memoria

Aggiungere i seed esterni, le modifiche caratteriali e il riconsolidamento a cascata, mantenendo la tracciabilità delle modifiche.

### Fase 6 — Routine e ciclo autonomo

Realizzare lo stato corrente, il generatore di routine, il carico cognitivo, il sonno simulato e l'Heartbeat Loop.

La prima tranche è implementata in `agent_memory/routine.py`: genera il piano giornaliero, sceglie l'attività in corso e gestisce la finestra di sonno. Il carico cognitivo dinamico e l'Heartbeat Loop restano da implementare.

### Fase 7 — Relazioni e WhatsApp

Integrare il grafo delle relazioni, il recupero dei ricordi relativi ai singoli utenti e l'invio autonomo dei messaggi autorizzati.

### Fase 8 — Sicurezza e affidabilità

Implementare backup, logging, protezione dei dati, controlli di sicurezza e test di coerenza temporale e semantica.

### 17.1. Biografia procedurale: implementazione e comandi

La Fase 4 è implementata in `agent_memory/biography.py`. Il generatore è deterministico e non utilizza un modello linguistico: legge identità, tratti, famiglia, percorso scolastico, `world_history.json` e la timeline esistente, quindi costruisce i ricordi mancanti per macro-ere. Per creare prima un profilo nuovo da zero, usa `profile init` descritto nella sezione 2.4; `scripts/init_db.py --reset` ricrea invece il dataset demo.

La densità prevista è:

- prima infanzia: 3 ricordi complessivi tra i 2 e i 6 anni;

- fanciullezza: 1 ricordo per anno tra i 7 e i 13 anni;

- adolescenza: 2 ricordi per anno tra i 14 e i 19 anni;

- età adulta: 3 ricordi per anno dai 20 anni in poi.

Il filtro di anacronismo calcola l'età alla data dell'evento. Gli eventi precedenti alla nascita vengono esclusi; quelli sotto la soglia di comprensione restano soltanto contesto familiare e non vengono scritti nella timeline. Per politica e geopolitica la soglia minima è di 7 anni. Gli eventi mondiali sopra soglia possono diventare ricordi diretti, collegati tramite `world_event_id`; quelli già coperti da un ricordo esistente non vengono duplicati.

Ogni ricordo generato ha ID e `source_ref` stabili, è marcato come simulato e non sovrascrive i ricordi esistenti. L'applicazione è idempotente: una seconda esecuzione non crea duplicati. Se un evento del piano è già presente, viene saltato e riportato con il motivo.

Comandi:

```bash
# Anteprima del piano, senza scrivere
python -m agent_memory.biography

# Piano completo in JSON
python -m agent_memory.biography --json

# Applica i nuovi ricordi alla timeline
python -m agent_memory.biography --apply

# Usa una data di riferimento diversa da oggi
python -m agent_memory.biography --as-of 2026-10-24

# Esegue il generatore su un progetto in un'altra directory
python -m agent_memory.biography --root /percorso/del/progetto
```

Dopo l'applicazione, sincronizzare l'indice vettoriale:

```bash
python -m agent_memory.index --sync
```

Per ricostruire completamente l'indice:

```bash
python -m agent_memory.index --rebuild
```

`biography --apply` e le operazioni di scrittura di `index` (`--sync` e `--rebuild`) condividono il lock di `profile init`: se un'altra creazione o scrittura è in corso, terminano con un errore. `--wait` attende senza limite, mentre `--wait-timeout SECONDI` limita l'attesa. Le modalità di sola lettura (`biography` senza `--apply`, `index --status` e `index --check`) non acquisiscono il lock.

### 17.2. Seed esterni, tratti e riconsolidamento a cascata

La Fase 5 è implementata in `agent_memory/evolution.py`. Un seed è un evento amministrativo collocato nel passato: viene registrato come ricordo `admin_seed` e avvia il riconsolidamento di tutti i ricordi simulati successivi. Le interazioni reali, i ricordi latenti e i ricordi repressi non vengono riscritti.

Esempio di seed:

```json
{
  "seed_id": "critica_laboratorio_2024",
  "event_time": "2024-11-05T18:30:00+01:00",
  "title": "Una critica ricevuta in laboratorio",
  "description": "Un docente criticò pubblicamente il mio approccio durante un progetto universitario.",
  "importance": 8,
  "tags": ["critica", "università", "rifiuto"],
  "emotional_state": {
    "label": "vergogna",
    "valence": -0.7,
    "arousal": 0.7,
    "intensity": 8
  },
  "trait_impacts": {},
  "cascade": {"max_memories": 200}
}
```

Il seed può specificare anche `place`, `people`, `consequences`, `is_simulated`, `memory_id`, `cascade_id` e `trait_impacts`. I tratti psicologici vengono aggiornati combinando le regole in `identity.personality.evolution.rules` con gli eventuali `trait_impacts` espliciti; il totale dei delta è limitato da `max_total_delta_per_seed`. Ogni modifica viene registrata in `config/personality_history.json`.

Il riconsolidamento usa un rewriter deterministico predefinito. Il protocollo `MemoryRewriter` permette di sostituirlo con un motore LLM senza cambiare il flusso di seed e tracciabilità. Ogni ricordo aggiornato incrementa `version`, crea una riga in `memory_revisions` con il proprio `cascade_id` e conserva gli snapshot precedenti. Le conoscenze collegate di tipo `opinion`, `hypothesis` e `uncertain` ricevono una nota in `metadata.cascade_reflections`; i `verified_fact` e i contenuti `user_provided` restano intatti.

Comandi:

```bash
# Anteprima del piano, senza scrivere
python -m agent_memory.evolution --seed seed.json

# Piano completo in JSON
python -m agent_memory.evolution --seed seed.json --json

# Applica seed, tratti e riconsolidamento
python -m agent_memory.evolution --seed seed.json --apply

# Applica senza aggiornare l'indice vettoriale
python -m agent_memory.evolution --seed seed.json --apply --no-reindex

# Storico delle modifiche ai tratti
python -m agent_memory.evolution --history
```

Se l'indice vettoriale non era pronto durante l'applicazione, eseguire `python -m agent_memory.index --sync` per allinearlo ai nuovi testi.

### 17.3. Dashboard amministrativa locale

La dashboard amministrativa è implementata in `agent_memory/dashboard.py`; l'interfaccia è in `agent_memory/dashboard_assets/`. Usa gli stessi servizi e database del progetto, senza dipendenze web aggiuntive. Le viste sono in sola lettura; le uniche scritture applicative sono la creazione iniziale del profilo e, su richiesta dal wizard o da `/setup/finalize`, la biografia e l'indice vettoriale.

Avvio:

```bash
# Loopback, porta predefinita 8765
python -m agent_memory.dashboard

# Porta diversa e apertura automatica del browser
python -m agent_memory.dashboard --port 8766 --open

# Progetto in un'altra directory
python -m agent_memory.dashboard --root /percorso/del/progetto

# Prima configurazione da browser in una directory nuova
python -m agent_memory.dashboard --root ./profiles/nuovo --auth

# Autenticazione anche in locale
python -m agent_memory.dashboard --auth

# HTTPS locale con certificato dedicato
python -m agent_memory.dashboard --https

# Accesso da un altro dispositivo in HTTPS, password da file con permessi 600
python -m agent_memory.dashboard --host 0.0.0.0 --https --password-file ~/.mnemosine-dashboard.pass

# Aggiunge l'IP LAN al certificato (ripetibile per più nomi/IP)
python -m agent_memory.dashboard --https --tls-san 192.168.1.42
```

Prima configurazione da browser:

- Avviando la dashboard su una directory senza profilo, `/` reindirizza a `/setup`. Il wizard crea gli stessi cinque file JSON e quattro database di `profile init`, con la stessa validazione atomica e lo stesso lock.

- Il percorso è diviso in sei passaggi: **Identità** (anagrafica, nascita, residenza, lingue), **Carattere** (quattro assi 1–10, interessi, valori, abitudini), **Relazioni** (genitori o tutori, fratelli e sorelle, persone importanti con fiducia e intimità), **Storia** (sintesi, situazione attuale, formazione e lavoro), **Riepilogo** e **Completamento**.

- Il profilo creato contiene i dati raccolti in `identity.json` e popola `social_graph.json` con le persone importanti. Non inserisce il dataset demo; i ricordi possono arrivare dal passaggio finale del wizard.

- L’ultimo passaggio può generare la biografia procedurale e costruire l’indice vettoriale. Le operazioni partono in background e la pagina di completamento segue l’avanzamento con polling su `/setup/finalize/status`; i pulsanti restano disabilitati finché il job è in corso. Se un’operazione fallisce, il profilo resta valido e si può riprovare da `/setup/finalize`; biografia e indice restano disponibili anche dai comandi CLI. Un solo job di completamento può essere attivo per dashboard: una nuova richiesta mentre il job è in corso viene ignorata. Se il server viene riavviato, lo stato del job in memoria si perde, ma i dati già scritti restano.

- Le righe di familiari, lingue, persone, formazione e lavoro sono dinamiche; il server accetta al massimo 20 righe per sezione e valida date, anni, codici lingua e intervalli di fiducia.
- Genere, pronomi, fuso orario IANA, paese di nascita e di residenza, cittadinanza, regione, provincia, lingua e livello sono menu a scelta: non si possono digitare valori ambigui. Il codice ISO del paese viene ricavato dal paese selezionato, quindi non può divergere dal nome. Le province restano salvate come sigla (per esempio `MI`) e la regione viene ricavata o verificata dal server; l'elenco è italiano, quindi per una località estera si lascia “Non indicata”.

- Se la directory contiene già un profilo pronto, `/setup` reindirizza alla dashboard. Un profilo incompleto non viene sovrascritto: usa `profile init --force` da terminale.

- Ogni POST di configurazione richiede un token CSRF monouso, valido 30 minuti, e il controllo dell'Origin. Quando la dashboard è esposta oltre loopback, `/setup` richiede l'autenticazione come le altre pagine.

Autenticazione:

- Su `127.0.0.1` l'accesso è disattivato per impostazione predefinita; `--auth` lo rende obbligatorio anche in locale.

- Su un host non loopback l'accesso è sempre obbligatorio. Se non viene configurata una password, ne viene generata una temporanea e stampata una sola volta all'avvio.

- La password può essere fornita con la variabile `AGENT_MEMORY_DASHBOARD_PASSWORD` oppure con `--password-file`. Il file deve essere leggibile solo dal proprietario (`chmod 600`).

- La pagina di accesso è `/login`; la sessione usa un cookie `HttpOnly` e `SameSite=Strict`, dura 12 ore e viene invalidata dal riavvio del server o dal logout.

- Dopo 8 tentativi falliti in 5 minuti l'indirizzo client viene bloccato temporaneamente.

HTTPS:

- `--https` abilita TLS con un certificato self-signed dedicato. È richiesto OpenSSL.

- Certificato e chiave vengono creati al primo avvio in `data/dashboard_tls/` (`cert.pem` e `key.pem`), con la chiave leggibile solo dal proprietario; la directory è ignorata da Git.

- Il certificato viene riutilizzato negli avvii successivi. Per rigenerarlo, rimuovi `cert.pem` e `key.pem` (o l'intera directory).

- Il certificato include `localhost`, `127.0.0.1`, `::1` e il nome host locale. Per l'accesso da un altro dispositivo aggiungi il suo IP o hostname con `--tls-san`; se il certificato esiste già, rimuovilo prima di rigenerarlo con i nuovi nomi.

- Essendo self-signed, il browser mostra un avviso finché non lo consideri attendibile. Non fornisce una identità verificata da una CA pubblica.

- In HTTPS il cookie di sessione diventa `Secure`. Senza TLS il traffico è in chiaro: usare la dashboard solo su una rete fidata.

- Se la dashboard è dietro un reverse proxy che riscrive l'header `Host`, passa `--trusted-origin https://dashboard.example` (origine esatta, ripetibile) per accettare i POST di quel proxy. Non usare wildcard: l'origine dichiarata deve essere quella reale del browser.

- `--allow-null-origin` accetta `Origin: null`, inviato dagli iframe sandbox. Il token CSRF resta obbligatorio; usalo solo per una preview locale fidata.

Esportazione, trust store e rimozione:

```bash
# Esporta il certificato e stampa il fingerprint SHA-256
python -m agent_memory.dashboard_cert export --output ~/mnemosine-dashboard.crt

# Mostra cosa verrebbe modificato, senza toccare il sistema
python -m agent_memory.dashboard_cert install --dry-run

# Installa nel trust store di sistema (richiede privilegi amministrativi)
sudo python -m agent_memory.dashboard_cert install --yes

# Mostra cosa verrebbe rimosso, senza toccare il sistema
python -m agent_memory.dashboard_cert uninstall --dry-run

# Rimuove dal trust store di sistema (richiede privilegi amministrativi)
sudo python -m agent_memory.dashboard_cert uninstall --yes
```

`install` e `uninstall` usano `update-ca-certificates`, `update-ca-trust` o `trust` su Linux, `security` su macOS e `certutil` su Windows. Il certificato viene installato o rimosso solo con `--yes`; `--dry-run` mostra il piano senza modificare il sistema. Per usare un file già esportato passa `--cert`. Se la dashboard usa una directory TLS personalizzata, passa `--tls-dir` prima del sottocomando. `uninstall` non genera un certificato se non esiste: in quel caso termina con errore e indica di usare `--cert`. Rimuove solo la copia nel trust store; `cert.pem` e `key.pem` restano in `data/dashboard_tls/`.

Il certificato è self-signed e locale: non va usato come certificato di produzione. Alcuni browser, in particolare Firefox, mantengono un trust store separato; se l'avviso resta, importa `mnemosine-dashboard.crt` nelle impostazioni del browser.

La dashboard espone cinque viste:

- **Panoramica**: identità, stato corrente, conteggi e ultimi ricordi.

- **Timeline**: ricerca e filtri per fonte e livello di accesso; i ricordi latenti e repressi sono visibili solo attivando esplicitamente l'opzione dedicata. Il dettaglio mostra revisioni, collegamenti episodici e conoscenze collegate.

- **Tratti**: tratti della personalità conscia e configurazione amministrativa dell'ombra, inclusi trigger, difese, ricordi collegati e limiti di sicurezza.

- **Conoscenze**: memoria semantica con tipo, stato, fonte, affidabilità e metadati, inclusi i riferimenti ai riconsolidamenti.

- **Evoluzioni**: storico delle variazioni dei tratti, seed esterni, cascate e revisioni recenti.

Le API dei dati sono in sola lettura; gli unici POST gestiscono accesso, logout, prima configurazione e completamento del profilo (biografia e indice). Il server ascolta su `127.0.0.1` per impostazione predefinita: contiene dati amministrativi e tratti dell'ombra, quindi non va pubblicato su Internet. L'opzione `--host` permette di cambiare l'indirizzo; su un host non loopback l'autenticazione è obbligatoria. Usa `--https` per cifrare il traffico quando la dashboard è raggiungibile da altri dispositivi.

### 17.4. Routine giornaliera e stato corrente

La prima tranche della Fase 6 è implementata in `agent_memory/routine.py`. Il motore non usa un LLM e non avvia un demone: legge `identity.json` e `current_state.json`, costruisce un piano deterministico per il giorno richiesto e allinea lo stato all'orario.

Il piano usa interessi, formazione e lavoro in corso. Nei giorni feriali prevede studio e lavoro, nei fine settimana attività libere; colazione, pranzo, cena e riposo sono presenti in entrambi. La rotazione delle attività cambia con la data, ma resta stabile se il piano viene riutilizzato nello stesso giorno.

La finestra di sonno è letta da `current_state.sleep.window` (`00:00`–`07:00` per impostazione predefinita) e supporta anche intervalli che attraversano la mezzanotte. Durante il sonno lo stato passa a `sleeping`, l'attività diventa `Dorme` e `availability.accepting_messages` viene disattivato. La disponibilità tiene conto anche delle soglie di carico cognitivo già presenti, ma il carico non viene ancora incrementato o decrementato dal motore.

Il piano viene riutilizzato se `routine.date` coincide con il giorno richiesto; per personalizzarlo si possono modificare `planned_activities` in `current_state.json` lasciando `routine.date` allineato alla data. In caso contrario viene rigenerato.

Comandi:

```bash
# Anteprima: mostra attività corrente, prossimo cambio e piano
python -m agent_memory.routine --at 2026-10-25T15:00

# Anteprima in JSON, senza scrivere
python -m agent_memory.routine --at 2026-10-25T15:00 --json

# Allinea current_state.json all'orario indicato
python -m agent_memory.routine --at 2026-10-25T15:00 --apply

# Usa l'orologio reale e applica
python -m agent_memory.routine --apply

# Progetto in un'altra directory
python -m agent_memory.routine --root ./profiles/marco --apply
```

Senza `--apply` il comando è di sola lettura. Con `--apply` acquisisce lo stesso lock di `profile init`, `biography` e `index`, scrive in modo atomico e non riscrive lo stato se è già allineato. `--at` senza fuso orario usa quello dell'identità; un orario con fuso diverso viene convertito.

L'Heartbeat Loop, l'incremento del carico cognitivo e il consolidamento notturno restano ai passi successivi. La dashboard mostra già il nuovo stato senza modifiche: attività, luogo, routine e carico sono letti da `current_state.json`.

---

Obiettivo finale

Realizzare un agente AI persistente che combini:

- Una biografia coerente e in evoluzione.

- Una memoria episodica per le esperienze personali.

- Una memoria semantica per le conoscenze generali.

- Un sistema RAG per recuperare rapidamente informazioni rilevanti.

- Una memoria latente che influenza indirettamente il comportamento.

- Una personalità simulata capace di evolvere.

- Routine, stati interni e cicli di consolidamento.

- Relazioni sociali con livelli di fiducia e intimità.

- Un sistema always-on capace di iniziativa autonoma.

- Un'architettura modulare, verificabile e sicura.

La struttura finale unirà tre dimensioni fondamentali: la cronologia degli eventi tramite SQLite, l'identità e gli stati tramite JSON e il recupero basato sul significato tramite il Vector Database.

Il sistema non si limiterà quindi a conservare una cronologia di conversazioni: potrà recuperare ricordi pertinenti, distinguere le conoscenze generali dalle esperienze personali e utilizzare entrambe per produrre risposte coerenti con la propria storia simulata.

<a id="identita-visiva" name="identita-visiva"></a>

## 18. Motore di consistenza visiva: Visual Identity Continuity

### 18.1. Obiettivo

Il sistema dovrà integrare una dimensione visiva persistente, denominata Visual Identity Continuity, per consentire all'agente di mantenere un'identità grafica riconoscibile attraverso immagini generate in momenti, ambienti, inquadrature e condizioni di illuminazione differenti.

L'obiettivo è fare in modo che l'agente possa apparire in fotografie realistiche ambientate nella propria routine virtuale, in vacanza, a casa, in città o durante altre attività, mantenendo una continuità visiva con il personaggio definito al momento della creazione.

Il sistema dovrà gestire:

- Identità facciale.

- Lineamenti e tratti somatici.

- Capelli, occhi e segni particolari.

- Corporatura e proporzioni fisiche.

- Dettagli anatomici visibili, comprese mani e gambe.

- Abbigliamento, quando richiesto dalla scena.

- Inquadrature complete o parziali.

- Ambienti e sfondi.

- Stile fotografico.

- Coerenza tra immagini generate in sessioni differenti.

Il motore visivo sarà un modulo separato dal modello linguistico, coordinato dal server MCP e integrato con lo stato corrente, la routine e le decisioni autonome dell'agente.

La consistenza assoluta non può essere garantita da ogni modello generativo. Il sistema dovrà quindi adottare tecniche di riferimento visivo, condizionamento e verifica per ridurre le variazioni d'identità.

### 18.2. DNA visivo: "visual_dna.json"

Durante la creazione dell'identità, oltre ai dati anagrafici e ai tratti psicologici, l'MCP definirà un passaporto visivo persistente.

Il file "visual_dna.json" conterrà la descrizione strutturata dei tratti fisici che dovranno rimanere coerenti durante la generazione delle immagini.

Esempio di struttura:

```json
{
  "identity_id": "character_001",
  "version": 1,
  "visual_dna": {
    "etnia_lineamenti": "Ragazzo caucasico, lineamenti spigolosi, mascella definita",
    "occhi": "Occhi a mandorla, iride verde scuro, sopracciglia folte ma curate",
    "capelli": "Taglio corto texturizzato, castano scuro, riga disordinata a sinistra",
    "segni_particolari": "Piccola cicatrice lineare di 2 cm vicino allo zigomo destro, neo sul collo",
    "corporatura": "Altezza 180 cm, corporatura magra, mani con dita lunghe e affusolate"
  },
  "identity_constraints": {
    "preserve_face": true,
    "preserve_distinctive_marks": true,
    "preserve_body_proportions": true,
    "allow_clothing_changes": true,
    "allow_environment_changes": true,
    "allow_pose_changes": true
  }
}
```

Questi dati sono un esempio di configurazione, non le caratteristiche obbligatorie di ogni agente.

Regole del DNA visivo

Il DNA visivo rappresenterà la fonte di riferimento principale per la descrizione dell'identità.

Dovrà essere stabile tra una generazione e l'altra. Le variazioni intenzionali saranno gestite attraverso modifiche amministrative versionate, evitando che il sistema riscriva casualmente i tratti fisici a ogni richiesta.

Il DNA visivo dovrà distinguere tra:

- Caratteristiche permanenti: forma del viso, colore degli occhi, segni particolari e proporzioni corporee.

- Caratteristiche modificabili: vestiti, acconciatura temporanea, espressione, postura e accessori.

- Caratteristiche dipendenti dalla scena: illuminazione, ombre, prospettiva e porzioni del corpo visibili.

Questa distinzione impedirà di confondere un cambiamento d'abbigliamento con una modifica dell'identità.

### 18.3. Generazione del Reference Anchor Pack

Al primo avvio, il server MCP utilizzerà il DNA visivo per richiedere la generazione di un pacchetto di immagini di riferimento, denominato Reference Anchor Pack o Face Anchor Pack.

Il pacchetto iniziale comprenderà quattro immagini:

1. Primo piano frontale del volto.

2. Volto ripreso di tre quarti.

3. Immagine a mezzo busto.

4. Figura intera, con proporzioni corporee visibili.

Le immagini saranno generate con sfondo neutro, illuminazione controllata e pose adatte a documentare i tratti principali dell'identità.

Il sistema utilizzerà un modello grafico compatibile, locale o accessibile tramite API.

Archiviazione locale

Le immagini saranno conservate in una cartella dedicata, ad esempio:

```text
assets/
└── identity_ref/
    └── character_001/
        ├── face_front.png
        ├── face_three_quarter.png
        ├── upper_body.png
        ├── full_body.png
        └── manifest.json
```

Il file "manifest.json" registrerà gli identificativi delle immagini, la versione del DNA visivo, il modello utilizzato e gli eventuali parametri di generazione disponibili.

Le immagini originali saranno mantenute come riferimenti stabili. Le nuove immagini generate non dovranno sostituirle automaticamente.

Seed e riferimenti visivi

Il sistema potrà conservare un seed numerico per riprodurre, quando possibile, le condizioni iniziali di una generazione.

Tuttavia, un seed fisso non garantisce da solo la stessa identità in immagini differenti. Il risultato dipende anche dal modello, dalla versione, dal prompt, dai parametri di campionamento e dalle condizioni di generazione.

Per questo motivo, la consistenza visiva sarà basata principalmente sulla combinazione di:

- DNA visivo persistente.

- Immagini di riferimento.

- Condizionamento visivo.

- Modello e parametri compatibili.

- Verifica della somiglianza con l'identità originale.

### 18.4. Embedding visivi e verifica dell'identità

Il sistema potrà calcolare rappresentazioni vettoriali delle immagini per confrontare i risultati generati con i riferimenti originali.

Questi dati saranno conservati separatamente dagli embedding testuali della memoria semantica.

La componente visiva potrà includere:

- Embedding facciali, quando compatibili con il modello e l'uso previsto.

- Rappresentazioni visive generali.

- Metadati relativi a pose, inquadrature e caratteristiche osservabili.

- Punteggi di similarità tra immagini di riferimento e nuove generazioni.

Gli embedding visivi non dovranno essere trattati come una prova infallibile d'identità. La loro affidabilità dipenderà dalla tecnica utilizzata e dalle condizioni dell'immagine.

Quando il volto non è visibile, come nelle fotografie che mostrano soltanto gambe, mani o parte del corpo, il sistema dovrà utilizzare riferimenti corporei e proporzioni compatibili con il DNA visivo, senza richiedere necessariamente un confronto facciale.

### 18.5. Tecnologie di generazione e condizionamento

Il motore visivo potrà integrare modelli e strumenti differenti, selezionati in base alla disponibilità e alle prestazioni.

Tra le tecnologie valutabili:

- FLUX: famiglia di modelli generativi utilizzabile per la produzione di immagini, con capacità e modalità di condizionamento dipendenti dalla versione scelta.

- Stable Diffusion: famiglia di modelli generativi che consente pipeline personalizzabili e diverse tecniche di controllo.

- IP-Adapter: tecnica di condizionamento che utilizza immagini di riferimento per influenzare la generazione.

- Image-to-Image: metodo per guidare una nuova immagine a partire da un'immagine esistente.

- ControlNet e strumenti analoghi: possibili componenti per controllare struttura, pose e composizione, quando supportati dalla pipeline selezionata.

Il sistema dovrà scegliere una pipeline compatibile con il modello effettivamente utilizzato. Non tutte le tecniche sono disponibili con ogni modello o servizio API.

### 18.6. Composizione del prompt a blocchi

Il server MCP costruirà il prompt finale combinando quattro componenti indipendenti:

```text
PROMPT FINALE =
  [Gancio d'identità] +
  [Inquadratura e dettaglio] +
  [Ambiente e sfondo] +
  [Stile fotografico]
```

Blocco A — Gancio d'identità

Comprende il riferimento al personaggio, il DNA visivo e le immagini di riferimento.

Esempio:

"@CharacterName, capelli castano scuro, occhi verde scuro, corporatura magra, lineamenti coerenti con il riferimento visivo."

Blocco B — Inquadratura e dettaglio

Definisce la prospettiva, la distanza, la posa e le parti del corpo visibili.

Esempi:

- Selfie frontale.

- Foto a mezzo busto.

- Figura intera.

- Dettaglio delle mani.

- Inquadratura delle gambe e dei piedi.

- Fotografia in prima persona, POV.

Blocco C — Ambiente e sfondo

Definisce il luogo e le condizioni della scena.

Esempi:

- Camera da letto.

- Strada di una città siciliana.

- Spiaggia.

- Centro storico di Roma.

- Terrazza di un'abitazione.

- Ambiente domestico con illuminazione pomeridiana.

Blocco D — Stile fotografico

Definisce l'aspetto finale dell'immagine.

Esempi:

- Fotografia realistica da smartphone.

- Selfie spontaneo.

- Fotografia amatoriale da social network.

- Luce naturale.

- Profondità di campo ridotta.

- Grana fotografica leggera.

La composizione modulare consentirà di cambiare ambiente, inquadratura e stile senza dover ridefinire ogni volta l'intera identità.

### 18.7. Generazione di immagini parziali

Il motore dovrà supportare fotografie in cui il volto non compare.

Ad esempio, se l'agente sta simulando una pausa in camera e vuole inviare una fotografia che mostri soltanto le gambe, il sistema genererà un'immagine coerente con la corporatura e con il contesto.

Esempio di prompt:

«Dettaglio ravvicinato in prima persona, POV, focus sulle gambe e sui piedi di un ragazzo con corporatura magra, disteso su un letto disordinato. Lenzuola bianche spiegazzate. Atmosfera pomeridiana, luce soffusa proveniente dalla finestra. Stile fotografico realistico da smartphone».

In questa situazione il volto non è visibile. La continuità sarà affidata alla coerenza delle proporzioni corporee, alla descrizione fisica e agli eventuali riferimenti visivi disponibili.

Il sistema dovrà evitare di inventare caratteristiche anatomiche non definite o di modificare arbitrariamente le proporzioni del personaggio.

### 18.8. Generazione di fotografie in ambienti diversi

Il sistema dovrà consentire all'agente di apparire in luoghi differenti mantenendo la stessa identità visiva.

Esempio: l'agente si trova virtualmente a Roma o a Licata e desidera inviare un selfie spontaneo.

Il server MCP recupererà il DNA visivo e selezionerà le immagini di riferimento più adatte.

Esempio di prompt:

«@CharacterName, ragazzo con capelli castano scuro e occhi verde scuro, scatta un selfie spontaneo all'aperto. Sullo sfondo, una tipica strada di una città di mare siciliana, muri in pietra chiara e piante di fichi d'India in un vaso di terracotta. Luce calda del tardo pomeriggio. Espressione naturale, nessuna posa artificiale, stile fotografico amatoriale da smartphone».

Il prompt sarà accompagnato dalle immagini di riferimento attraverso il metodo di condizionamento supportato dalla pipeline.

Il sistema dovrà distinguere tra un luogo effettivamente visitato dall'agente nel contesto narrativo e uno scenario generato artificialmente. Le immagini create non costituiscono prova di una presenza reale in quel luogo.

### 18.9. Integrazione con la memoria e il sistema RAG

Il motore visivo sarà collegato al sistema di memoria per recuperare le informazioni necessarie a ogni generazione.

Il RAG potrà individuare:

- Il DNA visivo attivo.

- Le immagini di riferimento pertinenti.

- L'abbigliamento già utilizzato, quando rilevante.

- Le caratteristiche della scena.

- Gli eventi della timeline associati al luogo o all'attività.

- Le preferenze visive e fotografiche dell'agente.

Le immagini saranno archiviate come file, mentre SQLite conserverà i metadati e i riferimenti cronologici. Gli embedding visivi potranno essere indicizzati in una componente vettoriale dedicata.

La ricerca testuale e quella visiva dovranno rimanere distinguibili, anche quando saranno utilizzate insieme.

Il sistema dovrà inoltre mantenere la distinzione tra identità permanente e dettagli temporanei, evitando che un abito o una singola immagine modifichino automaticamente il DNA visivo.

### 18.10. Integrazione con il ciclo Always-On

Il motore visivo sarà collegato all'Heartbeat Loop e al generatore di routine.

Esempio operativo:

Sono le 15:00 e "current_state.json" indica che l'agente sta riposando in camera.

Un utente gli scrive su WhatsApp:

«Che fai di bello?»

Il sistema potrà seguire questa sequenza:

1. L'MCP riceve il messaggio.

2. Recupera lo stato corrente dell'agente.

3. L'LLM valuta la risposta più coerente con la situazione.

4. L'agente decide se rispondere con testo oppure proporre un'immagine.

5. L'MCP recupera il DNA visivo e i riferimenti appropriati.

6. Il motore visivo compone il prompt e applica il condizionamento.

7. L'immagine viene generata.

8. Il sistema esegue i controlli di coerenza e sicurezza previsti.

9. L'immagine viene inviata tramite l'integrazione WhatsApp, se l'azione è autorizzata.

10. L'evento viene registrato nella memoria episodica.

La generazione dell'immagine non dovrà avvenire necessariamente a ogni messaggio. L'agente dovrà poter decidere quando un'immagine è pertinente, evitando invii ripetitivi o incoerenti.

### 18.11. Requisiti operativi del motore visivo

Il modulo dovrà prevedere:

- Archiviazione persistente delle immagini di riferimento.

- Versionamento del DNA visivo.

- Gestione dei modelli e delle pipeline compatibili.

- Generazione tramite API o elaborazione locale.

- Gestione dei seed e dei parametri disponibili.

- Controllo delle dimensioni e dei formati delle immagini.

- Verifica della somiglianza visiva.

- Gestione degli errori e dei tentativi di rigenerazione.

- Registrazione delle immagini generate e del contesto.

- Controllo degli accessi ai riferimenti visivi.

- Backup degli asset e dei relativi metadati.

- Politiche di conservazione e cancellazione delle immagini.

La quantità di RAM, VRAM, spazio su disco e potenza di calcolo richiesta dipenderà dal modello selezionato e dal fatto che la generazione avvenga localmente oppure tramite API.

### 18.12. Nuovi file e componenti architetturali

Il modulo introdurrà i seguenti elementi:

- `visual_dna.json`: identità fisica persistente.

- `assets/identity_ref/`: immagini di riferimento originali.

- `manifest.json`: metadati delle immagini e versionamento.

- "visual_memory.db", oppure tabelle dedicate in un database esistente: cronologia delle generazioni e riferimenti alle immagini.

- Indice vettoriale visivo: recupero delle immagini e dei riferimenti pertinenti.

- `visual_engine`: componente responsabile della costruzione dei prompt, della generazione e della verifica della coerenza.

La scelta tra un database visivo separato e tabelle integrate dovrà essere effettuata in base alla complessità effettiva del progetto.

### 18.13. Collocazione nell'architettura complessiva

Il motore di consistenza visiva sarà integrato come modulo autonomo, collegato ai seguenti componenti:

1. Identità e personalità.

2. DNA visivo e immagini di riferimento.

3. Memoria episodica.

4. Memoria semantica.

5. Sistema RAG.

6. Stato corrente e routine.

7. Grafo delle relazioni.

8. Heartbeat Loop.

9. Integrazione WhatsApp.

10. Sicurezza, logging e backup.

Il modulo non sostituirà il modello linguistico né il sistema di memoria. Fornirà una capacità grafica persistente che potrà essere attivata quando il contesto narrativo o l'interazione la rendono utile.

Obiettivo finale: permettere all'agente di mantenere una rappresentazione visiva riconoscibile nel tempo, generare fotografie coerenti con la propria identità e inserirle nella routine simulata, collegando il mondo testuale, psicologico, temporale e visivo in un'unica architettura.

---

<p align="center">
  <img src="assets/mnemosine-mark.svg" alt="Emblema di Mnemosine" width="88">
</p>

<p align="center"><sub>Mnemosine · memoria, identità e iniziativa per agenti AI</sub></p>
