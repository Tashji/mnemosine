-- ============================================================
-- timeline_episodic.db
-- Fase 1 — memoria episodica, collegamenti e tracciabilità
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta (key, value) VALUES
    ('schema_name', 'timeline_episodic'),
    ('schema_version', '1.0'),
    ('created_at', '2026-10-24T15:00:00+02:00');

-- Un ricordo episodico rappresenta un'esperienza collocata nel tempo.
-- I campi JSON (people_json, emotional_state_json, tags_json) sono
-- validati dall'applicazione, non da SQLite, per massima portabilità.
CREATE TABLE IF NOT EXISTS episodic_memories (
    id TEXT PRIMARY KEY,
    event_time TEXT NOT NULL,
    event_date TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    place TEXT,
    people_json TEXT NOT NULL DEFAULT '[]',
    emotional_state_json TEXT NOT NULL DEFAULT '{}',
    importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 10),
    consequences TEXT,
    source TEXT NOT NULL CHECK (
        source IN (
            'real_interaction',
            'generated',
            'admin_seed',
            'world_event',
            'routine'
        )
    ),
    source_ref TEXT,
    reliability REAL NOT NULL DEFAULT 1.0 CHECK (reliability BETWEEN 0.0 AND 1.0),
    is_simulated INTEGER NOT NULL DEFAULT 0 CHECK (is_simulated IN (0, 1)),
    access_level TEXT NOT NULL DEFAULT 'conscious' CHECK (
        access_level IN ('conscious', 'latent', 'repressed')
    ),
    access_reason TEXT,
    is_consolidated INTEGER NOT NULL DEFAULT 0 CHECK (is_consolidated IN (0, 1)),
    tags_json TEXT NOT NULL DEFAULT '[]',
    embedding_ref TEXT,
    embedding_model TEXT,
    embedding_updated_at TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    previous_version_id TEXT REFERENCES episodic_memories(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (event_date = substr(event_time, 1, 10)),
    CHECK (source <> 'real_interaction' OR is_simulated = 0),
    CHECK (source <> 'generated' OR is_simulated = 1),
    CHECK (source <> 'world_event' OR is_simulated = 1),
    CHECK (source <> 'routine' OR is_simulated = 1),
    CHECK (access_level <> 'repressed' OR access_reason IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_episodic_event_time
    ON episodic_memories(event_time);

CREATE INDEX IF NOT EXISTS idx_episodic_event_date
    ON episodic_memories(event_date);

CREATE INDEX IF NOT EXISTS idx_episodic_source
    ON episodic_memories(source);

CREATE INDEX IF NOT EXISTS idx_episodic_access
    ON episodic_memories(access_level);

CREATE INDEX IF NOT EXISTS idx_episodic_importance
    ON episodic_memories(importance);

CREATE INDEX IF NOT EXISTS idx_episodic_consolidated
    ON episodic_memories(is_consolidated);

-- Collegamenti tra ricordi. La relazione è direzionale e può essere
-- tematica, cronologica o basata su persone/luoghi condivisi.
CREATE TABLE IF NOT EXISTS memory_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_memory_id TEXT NOT NULL REFERENCES episodic_memories(id) ON DELETE CASCADE,
    to_memory_id TEXT NOT NULL REFERENCES episodic_memories(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK (
        relation_type IN (
            'associated',
            'caused_by',
            'follows',
            'contradicts',
            'same_person',
            'same_place',
            'thematic'
        )
    ),
    strength REAL NOT NULL DEFAULT 0.5 CHECK (strength BETWEEN 0.0 AND 1.0),
    note TEXT,
    created_at TEXT NOT NULL,
    CHECK (from_memory_id <> to_memory_id),
    UNIQUE (from_memory_id, to_memory_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_memory_links_from
    ON memory_links(from_memory_id);

CREATE INDEX IF NOT EXISTS idx_memory_links_to
    ON memory_links(to_memory_id);

-- Ogni modifica a un ricordo deve lasciare una traccia. Le revisioni
-- sono usate in particolare dai seed e dal riconsolidamento a cascata.
CREATE TABLE IF NOT EXISTS memory_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL REFERENCES episodic_memories(id) ON DELETE CASCADE,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    changed_at TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    change_reason TEXT NOT NULL,
    previous_snapshot_json TEXT,
    new_snapshot_json TEXT NOT NULL,
    cascade_id TEXT,
    UNIQUE (memory_id, revision_number)
);

CREATE INDEX IF NOT EXISTS idx_memory_revisions_memory
    ON memory_revisions(memory_id);

CREATE INDEX IF NOT EXISTS idx_memory_revisions_cascade
    ON memory_revisions(cascade_id);

-- La memoria conscia è la vista predefinita per il recupero RAG ordinario.
-- I ricordi latenti o repressi richiedono un accesso esplicito.
CREATE VIEW IF NOT EXISTS conscious_episodic_memories AS
SELECT *
FROM episodic_memories
WHERE access_level = 'conscious';
