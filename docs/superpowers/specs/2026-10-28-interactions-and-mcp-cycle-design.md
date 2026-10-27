# Interazioni affidabili e ciclo browser guidato dall’agente MCP

## Ambito

Realizzare i tre interventi richiesti: interazioni atomiche e idempotenti, storico SQLite paginato, ciclo generico osserva → agisci → verifica svolto dall’agente MCP collegato. Nessun modello integrato, servizio esterno o adattamento specifico per WhatsApp, forum o altri siti.

Il repository attuale ha due commit già pubblicati (`c9dfd76`, `0e83c8e`); questo documento non dichiara implementati gli interventi seguenti.

## 1. Archivio autorevole e transazioni

Usare il database SQLite della timeline già presente nel profilo, senza aggiungere un quinto database. Una migrazione versionata introduce:

- storico delle interazioni, indicizzato per contatto, data e identificativo;
- registro delle richieste idempotenti con payload normalizzato e risultato;
- documenti operativi autorevoli per stato corrente e grafo relazionale;
- esecuzioni autonome e registro delle loro azioni.

Stato corrente e grafo devono condividere la stessa transazione SQLite dello storico e dei risultati idempotenti: un rollback che riscrive due JSON non costituisce atomicità dopo un crash. Il punto di accesso comune `ConfigStore` deve leggere e scrivere i documenti operativi nello stesso archivio, così dashboard, heartbeat, executor e MCP non vedono versioni divergenti. Identità e altri documenti di configurazione restano JSON.

I JSON di stato e grafo diventano esportazioni di compatibilità, aggiornabili e rigenerabili dal database. Un errore dell’esportazione successivo al commit non deve far apparire fallita una transazione già completata: va segnalato come esportazione da riallineare. La documentazione deve dichiarare che modificare direttamente queste esportazioni non modifica lo stato autorevole; non si importeranno automaticamente copie obsolete a ogni lettura.

L’operazione pubblica `record_interaction` accetta `interaction_id` facoltativo. Con identificativo uguale e payload uguale restituisce il risultato originale senza aumentare carico o contatori. Con identificativo uguale e payload diverso rifiuta la richiesta. Senza identificativo mantiene la semantica precedente: ogni chiamata è una nuova interazione, senza promettere deduplicazione dei retry. CLI e tool MCP espongono lo stesso parametro.

Ogni interazione legge lo stato aggiornato all’interno di una transazione di scrittura, valida contatto e ricordo accessibile, inserisce lo storico e aggiorna contatori, timestamp e carico. Nessuna decisione deve basarsi su una copia dello stato rimasta nell’istanza precedente.

## 2. Migrazione e letture paginate

La prima migrazione importa i documenti operativi e tutte le interazioni ancora presenti nel JSON, in modo idempotente e transazionale. Conserva i contatori storici anche quando superano il numero di voci disponibili: non inventa le interazioni eliminate dal vecchio limite di 100.

Import completato e marcatura di migrazione devono essere nella stessa transazione. Un riavvio dopo un’interruzione non duplica le voci. Le sorgenti JSON non vengono eliminate prima della conferma dell’importazione.

La dashboard interroga lo storico tramite SQL con ordinamento stabile (timestamp e identificativo), conteggio separato e LIMIT/OFFSET. La pagina di un contatto non deve caricare gli storici degli altri. La panoramica recupera soltanto riepilogo e ultima interazione. La superficie MCP offre una lettura paginata dello storico; il grafo non incorpora tutto lo storico crescente. Aggiornare documentazione e conteggio dei tool.

La modifica amministrativa di un contatto conserva storico e contatori. La rimozione confermata elimina il relativo storico nella stessa transazione; non elimina ricordi episodici e non cambia automaticamente la whitelist WhatsApp. Ricreare lo stesso identificativo non deve far riapparire lo storico cancellato.

## 3. Ciclo eseguito dall’agente MCP collegato

### Avvio e confine con il client

Esporre un’operazione MCP per ottenere un incarico dal prossimo impulso autorizzato, con identificativo persistente e contesto minimo: obiettivo, origine iniziale, eventuale contatto, limiti e stato dell’esecuzione. Nessun sito viene aperto alla semplice lettura dello stato.

Il client/agente richiama l’operazione durante il proprio ciclo o mediante una pianificazione configurata nel client. Un server MCP non può risvegliare arbitrariamente un client inattivo. Non introdurre dipendenze da sampling o notifiche non supportate da tutti i client; non dichiarare un funzionamento always-on indipendente dal client.

### Osservazione, azione e verifica

L’agente usa il browser esistente: osserva testo ed elementi, sceglie un’azione e richiede l’esecuzione associata all’incarico. La policy di navigazione e il consenso per origine rimangono vincolanti. I controlli applicati all’outbox non diventano una garanzia sui destinatari di azioni browser generiche.

Ogni azione riceve un identificativo univoco e viene registrata prima di eseguire un effetto esterno. Dopo l’azione il server legge la pagina e conserva l’osservazione; l’agente decide il passo successivo. Una verifica positiva basata su testo o elemento atteso conferma soltanto un risultato osservato nell’interfaccia, non una ricevuta del servizio o l’identità certa del destinatario.

Stati: pronto, in esecuzione, in attesa di verifica, completato, bloccato, esito incerto. Un’azione già completata non viene rieseguita su retry. Se il processo cade dopo la registrazione ma prima di conoscere l’esito, il riavvio restituisce esito incerto: l’agente deve osservare o richiedere intervento umano, non ripetere automaticamente click o Enter. SQLite e un sito esterno non condividono una transazione: non si promette exactly-once sugli effetti remoti.

Una sola esecuzione attiva per profilo, limite finito di passi e scadenza dell’incarico. Il completamento chiude soltanto l’impulso da cui deriva l’incarico, senza cancellarne uno nuovo. La modalità manutenzione, le revoche della policy e le condizioni di arresto vengono ricontrollate prima delle azioni. Le azioni esplicite dell’agente fuori da un incarico restano soggette al consenso browser, senza essere dichiarate parte del registro del ciclo autonomo.

Non marcare il messaggio come inviato nell’outbox per il solo fatto di aver osservato testo nella pagina. Tenere distinti: registrazione in outbox, azione browser eseguita, risultato UI osservato, consegna remota non verificata.

## Verifiche richieste

- Import di un profilo precedente, interruzione e riavvio senza duplicati; conservazione di contatori e metadati.
- Errore prima del commit: nessuna modifica a storico, carico o contatori. Retry dopo commit: risultato originale.
- Due richieste concorrenti: nessun aggiornamento perso; identificativo riutilizzato con payload diverso rifiutato.
- Più di 100 interazioni conservate; query SQL paginata e ordinamento stabile anche a timestamp uguali.
- Contatto modificato/rimosso/ricreato e riepiloghi coerenti tra dashboard, heartbeat e MCP.
- Ciclo MCP con browser finto: successo osservato, origine negata, revoca, manutenzione, limite passi, retry e ripartenza con esito incerto senza ripetere effetti.
- Test locale del controller su una pagina di prova, senza account esterni e senza inviare messaggi reali.
- Wheel costruito e installato in ambiente separato: migrazioni, asset, entry point, letture paginate e handshake MCP.
- Aggiornare versione pacchetto e schemi, README e test; commit e push al termine del lavoro verificato.

## Esclusioni

Nessun motore visivo o consolidamento notturno; nessuna modifica del profilo o ripristino backup guidato; nessuna integrazione specifica per sito; nessun invio reale durante i test. Nessun incremento della fiducia deciso automaticamente dalla sola quantità di messaggi.
