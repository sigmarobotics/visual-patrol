-- ============================================================
-- Visual Patrol: Supabase Cloud Sync Schema
-- Migration: 001_init
--
-- Mirrors the SQLite edge DB with multi-tenant support (site_id)
-- and UUID primary keys. local_id maps back to SQLite INTEGER PKs.
-- UNIQUE(site_id, local_id) enables idempotent upsert during sync.
-- ============================================================


-- ------------------------------------------------------------
-- sites
-- Top-level tenant identifier. One site = one deployment.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sites (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ------------------------------------------------------------
-- share_links
-- Viewer access tokens scoped to a site.
-- No anon SELECT policy — only service_role can read these.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS share_links (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token         TEXT NOT NULL UNIQUE,
    site_id       UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    password_hash TEXT,
    label         TEXT,
    expires_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- ------------------------------------------------------------
-- robots
-- One row per robot per site. Heartbeat status synced from edge.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS robots (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id     UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    robot_id    TEXT NOT NULL,
    robot_name  TEXT NOT NULL,
    last_seen   TIMESTAMPTZ,
    status      TEXT NOT NULL DEFAULT 'offline',
    UNIQUE (site_id, robot_id)
);


-- ------------------------------------------------------------
-- patrol_runs
-- Mirrors SQLite patrol_runs. Token columns cover all categories.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS patrol_runs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    local_id                BIGINT NOT NULL,
    site_id                 UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    robot_id                TEXT NOT NULL,
    start_time              TIMESTAMPTZ,
    end_time                TIMESTAMPTZ,
    status                  TEXT,
    report_content          TEXT,
    model_id                TEXT,
    -- grand totals (sum of all categories)
    input_tokens            INTEGER,
    output_tokens           INTEGER,
    total_tokens            INTEGER,
    -- inspection category
    inspection_input_tokens  INTEGER,
    inspection_output_tokens INTEGER,
    inspection_total_tokens  INTEGER,
    -- report category
    report_input_tokens     INTEGER,
    report_output_tokens    INTEGER,
    report_total_tokens     INTEGER,
    -- telegram category
    telegram_input_tokens   INTEGER,
    telegram_output_tokens  INTEGER,
    telegram_total_tokens   INTEGER,
    -- video category
    video_input_tokens      INTEGER,
    video_output_tokens     INTEGER,
    video_total_tokens      INTEGER,
    synced_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (site_id, local_id)
);


-- ------------------------------------------------------------
-- inspection_results
-- Per-point AI inspection records linked to a patrol run.
-- image_url replaces SQLite image_path (cloud storage URL).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS inspection_results (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    local_id       BIGINT NOT NULL,
    run_id         UUID REFERENCES patrol_runs(id) ON DELETE CASCADE,
    site_id        UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    robot_id       TEXT NOT NULL,
    point_name     TEXT,
    coordinate_x   DOUBLE PRECISION,
    coordinate_y   DOUBLE PRECISION,
    prompt         TEXT,
    ai_response    TEXT,
    is_ng          BOOLEAN,
    image_url      TEXT,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    total_tokens   INTEGER,
    timestamp      TIMESTAMPTZ,
    UNIQUE (site_id, local_id)
);


-- ------------------------------------------------------------
-- generated_reports
-- AI-generated summary reports across date ranges.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS generated_reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    local_id        BIGINT NOT NULL,
    site_id         UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    robot_id        TEXT NOT NULL,
    start_date      TEXT,
    end_date        TEXT,
    report_content  TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    total_tokens    INTEGER,
    timestamp       TIMESTAMPTZ,
    UNIQUE (site_id, local_id)
);


-- ------------------------------------------------------------
-- edge_ai_alerts
-- VILA JPS streaming alerts captured during patrol runs.
-- image_url replaces SQLite image_path (cloud storage URL).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS edge_ai_alerts (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    local_id       BIGINT NOT NULL,
    run_id         UUID REFERENCES patrol_runs(id) ON DELETE CASCADE,
    site_id        UUID NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    robot_id       TEXT NOT NULL,
    rule           TEXT,
    response       TEXT,
    image_url      TEXT,
    stream_source  TEXT,
    timestamp      TIMESTAMPTZ,
    UNIQUE (site_id, local_id)
);


-- ============================================================
-- Row Level Security
-- Enable RLS on all tables and define access policies.
--
-- Synced data tables: SELECT allowed when the JWT claim
-- site_id matches the row's site_id. Service role bypasses RLS.
--
-- share_links: NO anon SELECT policy — only service_role reads.
-- ============================================================

ALTER TABLE sites              ENABLE ROW LEVEL SECURITY;
ALTER TABLE share_links        ENABLE ROW LEVEL SECURITY;
ALTER TABLE robots             ENABLE ROW LEVEL SECURITY;
ALTER TABLE patrol_runs        ENABLE ROW LEVEL SECURITY;
ALTER TABLE inspection_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE generated_reports  ENABLE ROW LEVEL SECURITY;
ALTER TABLE edge_ai_alerts     ENABLE ROW LEVEL SECURITY;


-- Helper: extract site_id from JWT claims (returns '' if absent)
-- Used inline in every policy to avoid a separate function dep.

-- sites: viewers may read their own site row
CREATE POLICY "sites_select_by_jwt_site_id"
    ON sites
    FOR SELECT
    USING (
        id::text = coalesce(
            current_setting('request.jwt.claims', true)::json->>'site_id',
            ''
        )
    );

-- robots: SELECT by JWT site_id
CREATE POLICY "robots_select_by_jwt_site_id"
    ON robots
    FOR SELECT
    USING (
        site_id::text = coalesce(
            current_setting('request.jwt.claims', true)::json->>'site_id',
            ''
        )
    );

-- patrol_runs: SELECT by JWT site_id
CREATE POLICY "patrol_runs_select_by_jwt_site_id"
    ON patrol_runs
    FOR SELECT
    USING (
        site_id::text = coalesce(
            current_setting('request.jwt.claims', true)::json->>'site_id',
            ''
        )
    );

-- inspection_results: SELECT by JWT site_id
CREATE POLICY "inspection_results_select_by_jwt_site_id"
    ON inspection_results
    FOR SELECT
    USING (
        site_id::text = coalesce(
            current_setting('request.jwt.claims', true)::json->>'site_id',
            ''
        )
    );

-- generated_reports: SELECT by JWT site_id
CREATE POLICY "generated_reports_select_by_jwt_site_id"
    ON generated_reports
    FOR SELECT
    USING (
        site_id::text = coalesce(
            current_setting('request.jwt.claims', true)::json->>'site_id',
            ''
        )
    );

-- edge_ai_alerts: SELECT by JWT site_id
CREATE POLICY "edge_ai_alerts_select_by_jwt_site_id"
    ON edge_ai_alerts
    FOR SELECT
    USING (
        site_id::text = coalesce(
            current_setting('request.jwt.claims', true)::json->>'site_id',
            ''
        )
    );

-- share_links: intentionally NO anon/authenticated SELECT policy.
-- Only service_role (which bypasses RLS) may read share_links rows.
