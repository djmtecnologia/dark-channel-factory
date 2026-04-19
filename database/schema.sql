-- ============================================================
-- Dark Channel Factory — Learning Database Schema
-- SQLite (default) / PostgreSQL (produção)
-- ============================================================

-- Vídeos publicados e suas métricas
CREATE TABLE IF NOT EXISTS videos (
    id                      TEXT PRIMARY KEY,          -- UUID do job
    youtube_video_id        TEXT UNIQUE,
    channel_id              TEXT NOT NULL,
    title                   TEXT NOT NULL,
    niche                   TEXT NOT NULL,
    language                TEXT NOT NULL,
    country_code            TEXT NOT NULL DEFAULT 'US',
    topic                   TEXT NOT NULL,
    angle                   TEXT,
    primary_keyword         TEXT,
    viral_score             REAL DEFAULT 0,
    validation_passed       INTEGER DEFAULT 1,          -- Boolean
    category_id             TEXT DEFAULT '28',
    -- Performance YouTube
    ctr_percent             REAL DEFAULT 0,
    retention_percent       REAL DEFAULT 0,
    views                   INTEGER DEFAULT 0,
    likes                   INTEGER DEFAULT 0,
    comments                INTEGER DEFAULT 0,
    impressions             INTEGER DEFAULT 0,
    -- Revenue
    estimated_revenue_usd   REAL DEFAULT 0,
    affiliate_clicks        INTEGER DEFAULT 0,
    affiliate_revenue_usd   REAL DEFAULT 0,
    -- Thumbnail
    active_thumbnail        TEXT DEFAULT 'A',
    thumbnail_swapped_count INTEGER DEFAULT 0,
    -- Deep dive
    deep_dive_triggered     INTEGER DEFAULT 0,
    -- Shorts
    shorts_count            INTEGER DEFAULT 0,
    shorts_total_views      INTEGER DEFAULT 0,
    -- Timestamps
    created_at              TEXT NOT NULL,
    published_at            TEXT,
    last_analytics_at       TEXT
);

-- Shorts / Reels / TikToks publicados
CREATE TABLE IF NOT EXISTS shorts (
    id                  TEXT PRIMARY KEY,
    parent_video_id     TEXT REFERENCES videos(youtube_video_id),
    clip_index          INTEGER NOT NULL,              -- 1, 2, 3
    youtube_short_id    TEXT,
    tiktok_video_id     TEXT,
    instagram_reel_id   TEXT,
    platform            TEXT NOT NULL,                -- 'youtube_shorts' | 'tiktok' | 'instagram_reels'
    duration_s          REAL DEFAULT 55,
    hook_text           TEXT,
    views               INTEGER DEFAULT 0,
    likes               INTEGER DEFAULT 0,
    created_at          TEXT NOT NULL
);

-- Histórico de tópicos tentados e resultados
CREATE TABLE IF NOT EXISTS topics_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic           TEXT NOT NULL,
    niche           TEXT NOT NULL,
    language        TEXT NOT NULL,
    angle           TEXT,
    viral_score     REAL DEFAULT 0,
    actual_ctr      REAL DEFAULT 0,
    actual_retention REAL DEFAULT 0,
    actual_views    INTEGER DEFAULT 0,
    ab_winner       TEXT,                              -- 'A', 'B' ou 'C'
    used_at         TEXT NOT NULL,
    video_id        TEXT
);

-- Padrões vencedores aprendidos pelo sistema
CREATE TABLE IF NOT EXISTS winning_patterns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type    TEXT NOT NULL,   -- 'title_structure' | 'thumbnail_style' | 'hook_type' | 'niche_keyword'
    pattern_value   TEXT NOT NULL,
    avg_ctr         REAL DEFAULT 0,
    avg_retention   REAL DEFAULT 0,
    avg_views       INTEGER DEFAULT 0,
    sample_size     INTEGER DEFAULT 0,
    niche           TEXT NOT NULL,
    language        TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(pattern_type, pattern_value, niche, language)
);

-- Performance de afiliados por produto
CREATE TABLE IF NOT EXISTS affiliate_performance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name    TEXT NOT NULL,
    platform        TEXT NOT NULL,                     -- 'amazon' | 'clickbank' | 'direct'
    url_template    TEXT,
    total_clicks    INTEGER DEFAULT 0,
    estimated_revenue REAL DEFAULT 0,
    commission_pct  REAL DEFAULT 0,
    niche           TEXT,
    language        TEXT,
    video_count     INTEGER DEFAULT 0,
    updated_at      TEXT NOT NULL
);

-- Configuração de canais ativos
CREATE TABLE IF NOT EXISTS channels (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    language        TEXT NOT NULL,
    niche           TEXT NOT NULL,
    youtube_channel_id TEXT,
    tiktok_handle   TEXT,
    instagram_handle TEXT,
    active          INTEGER DEFAULT 1,
    created_at      TEXT NOT NULL,
    total_videos    INTEGER DEFAULT 0,
    total_views     INTEGER DEFAULT 0,
    total_revenue   REAL DEFAULT 0
);

-- Jobs da pipeline (estado e erro tracking)
CREATE TABLE IF NOT EXISTS pipeline_jobs (
    id              TEXT PRIMARY KEY,
    channel_id      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    niche           TEXT,
    language        TEXT,
    topic           TEXT,
    viral_score     REAL,
    error_message   TEXT,
    retry_count     INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL,
    completed_at    TEXT,
    video_id        TEXT
);

-- Índices para performance
CREATE INDEX IF NOT EXISTS idx_videos_niche        ON videos(niche, language);
CREATE INDEX IF NOT EXISTS idx_videos_ctr          ON videos(ctr_percent DESC);
CREATE INDEX IF NOT EXISTS idx_videos_channel      ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_patterns_niche      ON winning_patterns(niche, language, pattern_type);
CREATE INDEX IF NOT EXISTS idx_topics_niche        ON topics_history(niche, language);
CREATE INDEX IF NOT EXISTS idx_jobs_status         ON pipeline_jobs(status, created_at);
