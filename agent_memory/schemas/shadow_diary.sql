-- ============================================================
-- shadow_diary.db
-- Fase 1 — memoria latente, conflitti, rimozioni e trigger
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta (key, value) VALUES
    ('schema_name', 'shadow_diary'),
    ('schema_version', '1.0'),
    ('created_at', '2026-10-24T15:00:00+02:00');

-- Tutto il contenuto di questo database è considerato latente.
-- Non deve essere restituito dal recupero RAG ordinario: l'accesso
-- è mediato dal motore dell'ombra e dal pannello amministrativo.
CREATE TABLE IF NOT EXISTS shadow_entries (
    id TEXT PRIMARY KEY,
    entry_time TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    entry_type TEXT NOT NULL CHECK (
        entry_type IN (
            'repressed_memory',
            'projection',
            'latent_reaction',
            'internal_conflict',
            'trigger_response',
            'dream',
            'admin_seed'
        )
    ),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    -- Riferimento logico a timeline_episodic.db: le due basi sono separate,
    -- quindi non è possibile usare una foreign key SQLite tra database.
    linked_memory_id TEXT,
    -- Riferimento logico a un tratto in config/shadow_traits.json.
    linked_trait_id TEXT,
    trigger TEXT,
    emotions_json TEXT NOT NULL DEFAULT '{}',
    intensity INTEGER NOT NULL CHECK (intensity BETWEEN 1 AND 10),
    defense_mechanism TEXT NOT NULL DEFAULT 'none' CHECK (
        defense_mechanism IN (
            'projection',
            'repression',
            'denial',
            'displacement',
            'rationalization',
            'intellectualization',
            'sublimation',
            'avoidance',
            'none'
        )
    ),
    conscious_manifestation TEXT,
    access_level TEXT NOT NULL DEFAULT 'latent' CHECK (
        access_level IN ('latent', 'restricted')
    ),
    source TEXT NOT NULL CHECK (
        source IN ('real_interaction', 'generated', 'admin_seed', 'reflection')
    ),
    source_ref TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    previous_version_id TEXT REFERENCES shadow_entries(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_shadow_entry_time
    ON shadow_entries(entry_time);

CREATE INDEX IF NOT EXISTS idx_shadow_entry_type
    ON shadow_entries(entry_type);

CREATE INDEX IF NOT EXISTS idx_shadow_linked_memory
    ON shadow_entries(linked_memory_id);

CREATE INDEX IF NOT EXISTS idx_shadow_linked_trait
    ON shadow_entries(linked_trait_id);

CREATE INDEX IF NOT EXISTS idx_shadow_access
    ON shadow_entries(access_level);

-- Tracciabilità delle modifiche alle entry latenti, inclusi i
-- riconsolidamenti successivi a un seed.
CREATE TABLE IF NOT EXISTS shadow_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL REFERENCES shadow_entries(id) ON DELETE CASCADE,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    changed_at TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    change_reason TEXT NOT NULL,
    previous_snapshot_json TEXT,
    new_snapshot_json TEXT NOT NULL,
    cascade_id TEXT,
    UNIQUE (entry_id, revision_number)
);

CREATE INDEX IF NOT EXISTS idx_shadow_revisions_entry
    ON shadow_revisions(entry_id);

CREATE INDEX IF NOT EXISTS idx_shadow_revisions_cascade
    ON shadow_revisions(cascade_id);

-- Registro delle attivazioni dei tratti latenti: serve a capire
-- quali trigger hanno inciso sul comportamento e con quale esito.
CREATE TABLE IF NOT EXISTS shadow_trigger_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trait_id TEXT NOT NULL,
    triggered_at TEXT NOT NULL,
    trigger_description TEXT NOT NULL,
    intensity INTEGER NOT NULL CHECK (intensity BETWEEN 1 AND 10),
    defense_mechanism TEXT CHECK (
        defense_mechanism IN (
            'projection',
            'repression',
            'denial',
            'displacement',
            'rationalization',
            'intellectualization',
            'sublimation',
            'avoidance',
            'none'
        )
    ),
    response_summary TEXT,
    related_entry_id TEXT REFERENCES shadow_entries(id) ON DELETE SET NULL,
    resolved INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0, 1)),
    UNIQUE (trait_id, triggered_at, trigger_description)
);

CREATE INDEX IF NOT EXISTS idx_shadow_trigger_trait
    ON shadow_trigger_log(trait_id);

CREATE INDEX IF NOT EXISTS idx_shadow_trigger_time
    ON shadow_trigger_log(triggered_at);
