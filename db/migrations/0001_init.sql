-- db/schema.sql
-- Source of truth: docs/specs/spec_02_database_schema.md §2
-- Apply via: sqlite3 nursery_factory.db < db/schema.sql
-- (or via db/migrations/0001_init.sql through your migration runner)

CREATE TABLE projects (
    project_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    channel_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE characters (
    character_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    visual_signature TEXT NOT NULL,   -- JSON
    generation_control TEXT NOT NULL, -- JSON
    voice_profile TEXT NOT NULL,      -- JSON
    motion_profile TEXT NOT NULL      -- JSON
);

CREATE TABLE episodes (
    episode_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(project_id),
    title TEXT,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','in_progress','human_review','publish_ready','published','failed')),
    rhyme_theme TEXT,
    thumbnail_asset_id INTEGER REFERENCES assets(asset_id),
    hold_from_gc INTEGER NOT NULL DEFAULT 0,   -- boolean 0/1
    localized_metadata TEXT,                    -- JSON: {"es": {...}, "hi": {...}}
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE episode_characters (
    episode_id INTEGER NOT NULL REFERENCES episodes(episode_id),
    character_id TEXT NOT NULL REFERENCES characters(character_id),
    PRIMARY KEY (episode_id, character_id)
);

CREATE TABLE scenes (
    scene_id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL REFERENCES episodes(episode_id),
    sequence_order INTEGER NOT NULL,
    shot_description TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected'))
);

CREATE TABLE generations (
    generation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_id INTEGER REFERENCES scenes(scene_id),
    batch_phase TEXT NOT NULL
        CHECK (batch_phase IN ('A1','A2','B','C','D')),
    agent_type TEXT NOT NULL,
    model_used TEXT NOT NULL,
    comfyui_prompt_id TEXT,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','complete','failed','oom','manual_flag')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    batch_size_used INTEGER,
    last_qc_critique TEXT,   -- JSON, see spec_03
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE assets (
    asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER NOT NULL REFERENCES generations(generation_id),
    asset_type TEXT NOT NULL
        CHECK (asset_type IN ('still','thumbnail','clip','audio_song','audio_narration','final_render')),
    file_path TEXT NOT NULL,
    qc_similarity_score REAL,
    retention_status TEXT NOT NULL DEFAULT 'ephemeral'
        CHECK (retention_status IN ('ephemeral','persistent')),
    expires_at TEXT,
    deleted_at TEXT
);

CREATE TABLE qc_results (
    qc_id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL UNIQUE REFERENCES episodes(episode_id),
    visual_quality_score INTEGER NOT NULL,
    consistency_score INTEGER NOT NULL,
    beat_sync_score INTEGER NOT NULL,
    appropriateness_score INTEGER NOT NULL,
    repetition_score INTEGER NOT NULL,
    total_score INTEGER NOT NULL,
    decision TEXT NOT NULL
        CHECK (decision IN ('publish_ready','human_review','regenerate'))
);

CREATE TABLE credit_tracker (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id INTEGER REFERENCES generations(generation_id),
    service TEXT NOT NULL,
    units_consumed INTEGER NOT NULL,
    logged_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE gc_log (
    gc_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL REFERENCES assets(asset_id),
    action TEXT NOT NULL CHECK (action IN ('soft_delete','hard_delete','skipped_hold')),
    executed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE publishing_queue (
    queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER NOT NULL REFERENCES episodes(episode_id),
    upload_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (upload_status IN ('pending','uploaded','failed')),
    youtube_video_id TEXT,
    scheduled_at TEXT
);

CREATE TABLE backup_log (
    backup_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT NOT NULL,
    remote_path TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('success','failed')),
    bytes_transferred INTEGER,
    executed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_episodes_project ON episodes(project_id);
CREATE INDEX idx_episodes_hold_from_gc ON episodes(hold_from_gc);
CREATE INDEX idx_scenes_episode ON scenes(episode_id);
CREATE INDEX idx_generations_scene ON generations(scene_id);
CREATE INDEX idx_generations_status ON generations(status);
CREATE INDEX idx_assets_generation ON assets(generation_id);
CREATE INDEX idx_assets_retention_expiry ON assets(retention_status, expires_at);
