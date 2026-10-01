-- TheraScout PostgreSQL schema
-- Run once against the RDS instance after deploying TheraScout-Data stack.
--
-- Local dev:  psql postgresql://localhost/therascout -f data/schema.sql
-- RDS (via tunnel or ECS): set DATABASE_URL and the backend will
--   auto-apply this via db.init_schema() on startup.

-- ── Scan history ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scans (
    id                   SERIAL PRIMARY KEY,
    execution_arn        TEXT UNIQUE NOT NULL,
    therapeutic_area     TEXT NOT NULL,
    started_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at         TIMESTAMPTZ,
    scoring_method       TEXT,           -- 'bedrock_llm' | 'deterministic_fallback'
    bedrock_model        TEXT,
    opportunity_count    INTEGER DEFAULT 0,
    top_opportunity_name TEXT,
    top_opportunity_score REAL
);

-- ── Per-scan opportunities ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS opportunities (
    id               SERIAL PRIMARY KEY,
    scan_id          INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    rank             INTEGER NOT NULL,
    name             TEXT NOT NULL,
    score            REAL NOT NULL,
    rationale        TEXT,
    key_evidence     JSONB NOT NULL DEFAULT '[]',
    dimension_scores JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_opportunities_scan_id ON opportunities(scan_id);

-- ── Scoring weights — updated by feedback loop ────────────────────────────────
CREATE TABLE IF NOT EXISTS scoring_weights (
    dimension   TEXT PRIMARY KEY,
    weight      REAL NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by  TEXT NOT NULL DEFAULT 'system'
);

-- Seed default weights (idempotent)
INSERT INTO scoring_weights (dimension, weight) VALUES
    ('unmet_medical_need',       0.30),
    ('disease_burden',           0.20),
    ('existing_treatment_gap',   0.20),
    ('scientific_evidence',      0.15),
    ('research_momentum',        0.10),
    ('competitive_landscape',    0.05)
ON CONFLICT (dimension) DO NOTHING;

-- ── Researcher feedback (feeds into future weight retraining) ─────────────────
CREATE TABLE IF NOT EXISTS researcher_feedback (
    id              SERIAL PRIMARY KEY,
    opportunity_id  INTEGER NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    scan_id         INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    decision        TEXT NOT NULL CHECK (decision IN ('approved', 'rejected', 'needs_review')),
    comment         TEXT,
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_by    TEXT NOT NULL DEFAULT 'anonymous'
);
