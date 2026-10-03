PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    video_id      TEXT PRIMARY KEY,
    status        TEXT NOT NULL DEFAULT 'pending',
    file_count    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS segments (
    segment_id          TEXT PRIMARY KEY,
    video_id            TEXT NOT NULL REFERENCES videos(video_id),
    start_ms            INTEGER NOT NULL,
    end_ms              INTEGER NOT NULL,
    segmentation_version INTEGER NOT NULL DEFAULT 1,
    file_path           TEXT,
    file_name           TEXT,
    file_size_bytes     INTEGER,
    sha256              TEXT,
    duration_ms         INTEGER,
    width               INTEGER,
    height              INTEGER,
    fps                 REAL,
    codec               TEXT,
    container           TEXT,
    thumbnail_path      TEXT,
    preview_path        TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (video_id, start_ms, end_ms)
);

CREATE TABLE IF NOT EXISTS annotations (
    annotation_id        TEXT PRIMARY KEY,
    segment_id           TEXT NOT NULL REFERENCES segments(segment_id),
    annotation_number    INTEGER NOT NULL,
    csv_row              INTEGER NOT NULL UNIQUE,
    description_original TEXT NOT NULL,
    declared_language    TEXT,
    detected_language    TEXT,
    detector_raw         TEXT,
    detector_confidence  REAL,
    language_rule        TEXT,
    description_english  TEXT,
    translation_status   TEXT NOT NULL DEFAULT 'PENDING',
    status               TEXT NOT NULL DEFAULT 'active',
    source               TEXT,
    worker_id            TEXT,
    annotation_time      INTEGER,
    is_duplicate         INTEGER NOT NULL DEFAULT 0,
    vector_status        TEXT NOT NULL DEFAULT 'pending',
    embedding_model      TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS searches (
    search_id    TEXT PRIMARY KEY,
    query        TEXT NOT NULL,
    query_type   TEXT NOT NULL DEFAULT 'text',
    params       TEXT,
    result_count INTEGER NOT NULL DEFAULT 0,
    is_saved     INTEGER NOT NULL DEFAULT 0,
    saved_label  TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS search_results (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id      TEXT NOT NULL REFERENCES searches(search_id),
    segment_id     TEXT NOT NULL,
    score          REAL NOT NULL,
    rank           INTEGER NOT NULL,
    annotation_ids TEXT,
    why_match      TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS history_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    payload    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS story_projects (
    project_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS story_clips (
    story_clip_id    TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL REFERENCES story_projects(project_id),
    segment_id       TEXT NOT NULL REFERENCES segments(segment_id),
    source_start_ms  INTEGER NOT NULL,
    source_end_ms    INTEGER NOT NULL,
    timeline_start_ms INTEGER NOT NULL DEFAULT 0,
    timeline_end_ms  INTEGER NOT NULL DEFAULT 0,
    order_index      INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS script_analyses (
    analysis_id TEXT PRIMARY KEY,
    script_text TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS script_beats (
    beat_id              TEXT PRIMARY KEY,
    analysis_id          TEXT NOT NULL REFERENCES script_analyses(analysis_id),
    beat_index           INTEGER NOT NULL,
    text                 TEXT NOT NULL,
    visual_intent        TEXT,
    selected_segment_id  TEXT,
    candidates           TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS explore_points (
    segment_id TEXT PRIMARY KEY REFERENCES segments(segment_id),
    x          REAL NOT NULL,
    y          REAL NOT NULL,
    model      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    job_type    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',
    progress    REAL NOT NULL DEFAULT 0,
    error       TEXT,
    detail      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Model/version metadata per scene (ARCHITECTURE §4 embedding_meta).
-- Vectors themselves live in Qdrant; this table records what produced them.
CREATE TABLE IF NOT EXISTS embedding_meta (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_id     TEXT NOT NULL,
    model_name     TEXT NOT NULL,
    model_version  TEXT,
    modality       TEXT NOT NULL,          -- 'text' | 'image'
    vector_version INTEGER NOT NULL DEFAULT 1,
    dim            INTEGER NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (segment_id, model_name, modality, vector_version)
);

-- Optional structured visual evidence (ARCHITECTURE §4 detection).
-- Writers arrive with the detector/VLM phase; schema and reads exist now.
CREATE TABLE IF NOT EXISTS detections (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_id TEXT NOT NULL,
    type       TEXT NOT NULL,
    label      TEXT NOT NULL,
    confidence REAL NOT NULL,
    bbox_x     REAL,
    bbox_y     REAL,
    bbox_w     REAL,
    bbox_h     REAL,
    source     TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_annotations_segment   ON annotations(segment_id);
CREATE INDEX IF NOT EXISTS idx_annotations_vector    ON annotations(vector_status);
CREATE INDEX IF NOT EXISTS idx_annotations_translate ON annotations(translation_status);
CREATE INDEX IF NOT EXISTS idx_segments_video        ON segments(video_id);
CREATE INDEX IF NOT EXISTS idx_results_search        ON search_results(search_id);
CREATE INDEX IF NOT EXISTS idx_results_segment       ON search_results(segment_id);
CREATE INDEX IF NOT EXISTS idx_clips_project         ON story_clips(project_id, order_index);
CREATE INDEX IF NOT EXISTS idx_beats_analysis        ON script_beats(analysis_id, beat_index);
CREATE INDEX IF NOT EXISTS idx_events_type           ON history_events(event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_embedding_meta_seg    ON embedding_meta(segment_id);
CREATE INDEX IF NOT EXISTS idx_detections_segment    ON detections(segment_id);
