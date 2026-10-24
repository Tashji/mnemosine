-- ============================================================
-- semantic_memory.db
-- Fase 3 — conoscenze generali, revisioni e collegamenti
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta (key, value) VALUES
    ('schema_name', 'semantic_memory'),
    ('schema_version', '1.0'),
    ('created_at', '2026-10-24T15:00:00+02:00');

-- La memoria semantica contiene conoscenze generali, non esperienze
-- personali. Ogni elemento dichiara il tipo di conoscenza e la fonte:
-- un fatto verificato non è equivalente a un'opinione o a un'ipotesi.
CREATE TABLE IF NOT EXISTS semantic_items (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT NOT NULL,
    knowledge_type TEXT NOT NULL CHECK (
        knowledge_type IN (
            'verified_fact',
            'user_provided',
            'opinion',
            'hypothesis',
            'uncertain'
        )
    ),
    source TEXT NOT NULL CHECK (
        source IN (
            'conversation',
            'document',
            'admin_seed',
            'reflection',
            'inference',
            'external_source'
        )
    ),
    source_ref TEXT,
    reliability REAL NOT NULL DEFAULT 0.5 CHECK (reliability BETWEEN 0.0 AND 1.0),
    importance INTEGER NOT NULL DEFAULT 5 CHECK (importance BETWEEN 1 AND 10),
    status TEXT NOT NULL DEFAULT 'active' CHECK (
        status IN ('active', 'superseded', 'retracted')
    ),
    valid_from TEXT,
    valid_to TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    embedding_model TEXT,
    embedding_updated_at TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    previous_version_id TEXT REFERENCES semantic_items(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to)
);

CREATE INDEX IF NOT EXISTS idx_semantic_category
    ON semantic_items(category);

CREATE INDEX IF NOT EXISTS idx_semantic_type
    ON semantic_items(knowledge_type);

CREATE INDEX IF NOT EXISTS idx_semantic_status
    ON semantic_items(status);

CREATE INDEX IF NOT EXISTS idx_semantic_importance
    ON semantic_items(importance);

CREATE INDEX IF NOT EXISTS idx_semantic_updated
    ON semantic_items(updated_at);

-- La memoria ordinaria vede soltanto gli elementi attivi. Gli elementi
-- superati o ritirati restano nel database per tracciabilità, ma non
-- vengono indicizzati né restituiti dal recupero ordinario.
CREATE VIEW IF NOT EXISTS active_semantic_items AS
SELECT *
FROM semantic_items
WHERE status = 'active';

-- Ogni modifica a una conoscenza lascia uno snapshot precedente e uno
-- successivo. Le contraddizioni non sovrascrivono automaticamente il
-- contenuto: vengono registrate come collegamenti tipizzati.
CREATE TABLE IF NOT EXISTS semantic_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES semantic_items(id) ON DELETE CASCADE,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    changed_at TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    change_reason TEXT NOT NULL,
    previous_snapshot_json TEXT,
    new_snapshot_json TEXT NOT NULL,
    cascade_id TEXT,
    UNIQUE (item_id, revision_number)
);

CREATE INDEX IF NOT EXISTS idx_semantic_revisions_item
    ON semantic_revisions(item_id);

CREATE INDEX IF NOT EXISTS idx_semantic_revisions_cascade
    ON semantic_revisions(cascade_id);

-- Collegamenti tra conoscenze: associazione, raffinamento, derivazione,
-- contraddizione o sostituzione esplicita.
CREATE TABLE IF NOT EXISTS semantic_item_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_item_id TEXT NOT NULL REFERENCES semantic_items(id) ON DELETE CASCADE,
    to_item_id TEXT NOT NULL REFERENCES semantic_items(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK (
        relation_type IN (
            'related_to',
            'refines',
            'derived_from',
            'contradicts',
            'supersedes'
        )
    ),
    strength REAL NOT NULL DEFAULT 0.5 CHECK (strength BETWEEN 0.0 AND 1.0),
    note TEXT,
    created_at TEXT NOT NULL,
    CHECK (from_item_id <> to_item_id),
    UNIQUE (from_item_id, to_item_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_semantic_item_links_from
    ON semantic_item_links(from_item_id);

CREATE INDEX IF NOT EXISTS idx_semantic_item_links_to
    ON semantic_item_links(to_item_id);

-- Collegamento logico tra una conoscenza e un ricordo episodico. I due
-- archivi sono separati, quindi non è possibile una foreign key SQLite:
-- il servizio verifica che il ricordo esista e sia conscio.
CREATE TABLE IF NOT EXISTS semantic_episodic_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    semantic_id TEXT NOT NULL REFERENCES semantic_items(id) ON DELETE CASCADE,
    memory_id TEXT NOT NULL,
    relation_type TEXT NOT NULL CHECK (
        relation_type IN (
            'related_to',
            'explains',
            'illustrates',
            'derived_from',
            'contradicts'
        )
    ),
    strength REAL NOT NULL DEFAULT 0.5 CHECK (strength BETWEEN 0.0 AND 1.0),
    note TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (semantic_id, memory_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_semantic_episodic_links_semantic
    ON semantic_episodic_links(semantic_id);

CREATE INDEX IF NOT EXISTS idx_semantic_episodic_links_memory
    ON semantic_episodic_links(memory_id);
