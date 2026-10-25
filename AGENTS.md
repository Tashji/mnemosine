# Istruzioni per chi lavora su Mnemosine

Questa memoria vale per le persone e per gli agenti che modificano il progetto.
Il resto delle convenzioni è nel README; qui c'è solo ciò che è facile
dimenticare.

## Pacchetto installabile: verifica obbligatoria

I test girano dal repository, dove i file di runtime ci sono sempre per
costruzione: **non dimostrano** che il pacchetto installato sia completo.
`tests/test_packaging.py` controlla i metadati, non l'artefatto. Quando serve,
la verifica va rifatta sul wheel installato in un ambiente separato.

### Quando va rifatta

- prima di pubblicare un commit che tocca `pyproject.toml`, `package-data`, gli
  entry point o qualunque file dentro `agent_memory/`;
- quando aggiungi un file che il codice legge a runtime (asset della dashboard,
  JSON, schemi SQL, template): deve stare dentro `agent_memory/` **e** essere
  coperto da `[tool.setuptools.package-data]`, altrimenti non entra nel wheel;
- prima di una release o prima di dare il progetto a qualcun altro.

### Comandi

```bash
# 1. Costruisci il wheel e guarda cosa contiene davvero
rm -rf dist && python -m pip wheel . --no-deps -w dist
unzip -l dist/mnemosine-*.whl | grep -E "schemas/|dashboard_assets/"
unzip -p dist/mnemosine-*.whl '*/entry_points.txt'

# 2. Installalo in un venv usa-e-getta
python -m venv /tmp/mnemosine-check
/tmp/mnemosine-check/bin/pip install dist/mnemosine-*.whl

# 3. Usalo come farebbe chi scarica il progetto, da un'altra directory
cd /tmp
/tmp/mnemosine-check/bin/mnemosine --root /percorso/del/profilo --check
/tmp/mnemosine-check/bin/mnemosine-profile init --root /tmp/profilo-check \
  --first-name Prova --last-name Utente --birth-date 1990-01-01 \
  --birth-city Roma --birth-province RM --gender femmina
/tmp/mnemosine-check/bin/mnemosine-start --root /tmp/profilo-start --no-open --port 8800
```

### Cosa deve risultare

| Controllo | Esito atteso |
| --- | --- |
| `unzip -l` | quattro `.sql` in `agent_memory/schemas/`; in `agent_memory/dashboard_assets/` almeno `index.html`, `login.html`, `setup.html`, `setup_complete.html`, `app.js`, `setup.js`, `setup_complete.js`, `styles.css`, `foreign-geo.json` |
| `entry_points.txt` | sette script `mnemosine*`, incluso `mnemosine-start` |
| `mnemosine --check` da `/tmp` | JSON con `"ok": true` e i quattro database presenti |
| `mnemosine-profile init` | crea `config/` con cinque JSON e `data/` con quattro `.db`, nessun errore sugli schemi |
| `mnemosine-start --no-open` | stampa «Prima configurazione»; `curl /` risponde `303` verso `/setup`; `curl /setup` risponde `200` |
| handshake MCP | con `command="mnemosine"` e **senza** `cwd` il client riceve il server `agent-memory`, i tool e `get_identity` valido |
| `python -m unittest tests.test_packaging` | verde |

Handshake minimo, da eseguire con il Python del venv di prova:

```python
import asyncio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def main() -> None:
    params = StdioServerParameters(
        command="/tmp/mnemosine-check/bin/mnemosine",
        args=["--root", "/tmp/profilo-check"],
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        info = await session.initialize()
        tools = await session.list_tools()
        identity = await session.call_tool("get_identity", {})
        print(info.serverInfo.name, len(tools.tools), not identity.isError)


asyncio.run(main())
```

### Errori tipici e causa

| Sintomo | Causa |
| --- | --- |
| `ModuleNotFoundError: No module named 'agent_memory'` | stai usando `python -m` senza `cwd` nel repository e senza pacchetto installato: servono gli script `mnemosine*` oppure la working directory giusta |
| `Cartella di configurazione non trovata` | manca `--root` verso la directory del profilo (quella con `config/` e `data/`) |
| `Schema non trovato` | gli schemi non sono nel wheel: controlla `package-data` e che `agent_memory/schemas/*.sql` esista |
| dashboard senza stile o pagina `500` | `dashboard_assets` non inclusi nel pacchetto |
| `mnemosine-start: command not found` | entry point nuovo ma pacchetto non reinstallato (`pip install -e .` o wheel ricostruito) |

### Regola pratica

Nuovo file letto a runtime → dentro `agent_memory/`. Nuovo comando → riga in
`[project.scripts]` **e** nella tabella della sezione 2.5 del README. Nuovo dato
incluso → una riga in questa checklist, così la verifica successiva lo copre.
