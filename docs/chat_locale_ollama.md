# Chat locale sperimentale con Ollama

Questa chat è un **client di esempio** incluso nel repository, non il modo previsto per usare Mnemosine. Il progetto è un server MCP: memoria, identità e personalità vivono nel profilo, quindi puoi collegare qualsiasi client e qualsiasi modello (Claude Desktop, Cursor, LM Studio, Ollama, un agente scritto da te). La configurazione MCP generica è nel README, sezione **2.5 Installazione come pacchetto e collegamento dell'agente**.

La chat serve a provare il ciclo completo senza un client esterno: passa al modello un riassunto datato dei ricordi biografici, registra i turni nel profilo e tiene separate le dichiarazioni dell'utente dalle risposte del modello.

## Avvio

Da PowerShell, dopo aver predisposto l'ambiente e il modello:

```powershell
.\.venv\Scripts\python.exe scripts\chat_local.py
```

La chat usa il profilo separato `profiles/qwen_local` e conserva i turni anche dopo la chiusura; `/esci` termina la sessione. Per scegliere un altro modello Ollama già installato, usare `--model NOME`: deve supportare i tool. L'opzione `--prompt "testo"` esegue un turno singolo. Alla prima creazione del profilo, la sorgente deve essere ferma. Le domande su "ieri" senza un ricordo datato ricevono una risposta breve, senza usare l'attività corrente.

## Modello di riferimento

Il modello predefinito è `mnemosine-qwen35:9b-abliterated`: Qwen3.5 9B abliterated in Q4_K_M (5,6 GB), creato dal GGUF con `models/Modelfile.qwen35-9b`. Per installarlo:

```bash
ollama pull sachasayan/interimlabs-huihui-qwen3.5-9b-abliterated:q4_k_m
ollama create mnemosine-qwen35:9b-abliterated -f models/Modelfile.qwen35-9b
```

Il Modelfile non dichiara `TEMPLATE`: il ChatML e gli stop token arrivano dal GGUF, e sovrascriverli è l'errore che fa scadere il personaggio nel tono da assistente. Il 4B resta disponibile con `--model mnemosine-qwen35:4b-chatml`: è più veloce, ma chiude più spesso con offerte di aiuto.

Con 6 GB di VRAM il 9B gira intorno ai 17 token/s, con un piccolo offload su CPU; `num_ctx` resta 4096. Oltre i 9B non conviene salire su questa macchina: i 14B densi e i MoE da 20 GB non entrano in VRAM. Questi numeri valgono per la macchina di riferimento, non sono un requisito del progetto.

## Limiti

La memoria recupera dichiarazioni dell'utente come informazioni non verificate, distinte dalle risposte del modello e dalla biografia simulata. La ricerca conversazionale corrente è lessicale: parafrasi senza parole comuni possono non essere trovate. Anche con il ricordo corretto il modello può sbagliare: la chat è sperimentale e richiede supervisione, senza invii esterni autonomi.
