-- ============================================================
-- vector_index.db
-- Fase 3 — metadati dell'indice vettoriale
-- ============================================================
--
-- FAISS conserva i vettori in data/indexes/*.faiss. Questo database
-- conserva soltanto la mappa tra gli ID FAISS e i record originali,
-- oltre allo stato dell'indice. SQLite resta la fonte di verità: se
-- l'indice viene perso o corrotto, può essere ricostruito dai testi.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta (key, value) VALUES
    ('schema_name', 'vector_index'),
    ('schema_version', '1.0'),
    ('created_at', '2026-10-24T15:00:00+02:00');

-- Un record indicizzato appartiene a uno dei due archivi consci:
-- 'episodic' (solo ricordi con access_level='conscious') oppure
-- 'semantic' (solo conoscenze attive). Il vincolo su access_level
-- impedisce a livello di database di indicizzare contenuti latenti.
CREATE TABLE IF NOT EXISTS vector_entries (
    faiss_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('episodic', 'semantic')),
    record_id TEXT NOT NULL,
    access_level TEXT NOT NULL DEFAULT 'conscious' CHECK (access_level = 'conscious'),
    text_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    indexed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (kind, record_id)
);

CREATE INDEX IF NOT EXISTS idx_vector_entries_kind
    ON vector_entries(kind);

CREATE INDEX IF NOT EXISTS idx_vector_entries_hash
    ON vector_entries(kind, text_hash);

-- Stato logico dell'indice per ciascun tipo di memoria. La colonna
-- count viene confrontata con index.ntotal da --check.
CREATE TABLE IF NOT EXISTS index_meta (
    kind TEXT PRIMARY KEY CHECK (kind IN ('episodic', 'semantic')),
    model TEXT NOT NULL,
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    metric TEXT NOT NULL DEFAULT 'cosine' CHECK (metric = 'cosine'),
    count INTEGER NOT NULL DEFAULT 0 CHECK (count >= 0),
    status TEXT NOT NULL DEFAULT 'empty' CHECK (
        status IN ('empty', 'ready', 'stale', 'inconsistent')
    ),
    last_error TEXT,
    updated_at TEXT NOT NULL
);
