-- Premier Prospect — Migration 002
-- Score tier column, new tables: pp_score_history, pp_identity_match
-- Run once in Supabase SQL Editor: https://supabase.com/dashboard/project/lbvaosyfikkpvcwksiph/sql

-- ── TASK 1: Score Scale Unification ──────────────────────────────────────────
-- Unified scale: HOT = 70-100, WARM = 40-69, COOL = 0-39
-- Scores are already 0-100 in code. Add computed tier column for query convenience.

ALTER TABLE pp_scraper_signals
  ADD COLUMN IF NOT EXISTS tier text
    GENERATED ALWAYS AS (
      CASE
        WHEN score >= 70 THEN 'HOT'
        WHEN score >= 40 THEN 'WARM'
        ELSE 'COOL'
      END
    ) STORED;

-- ── TASK 2a: Augment pp_run_log ───────────────────────────────────────────────
ALTER TABLE pp_run_log
  ADD COLUMN IF NOT EXISTS duration_seconds  numeric(8,1),
  ADD COLUMN IF NOT EXISTS records_skipped   integer DEFAULT 0;

-- ── TASK 2b: pp_score_history ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pp_score_history (
  id               bigserial PRIMARY KEY,
  lead_id          bigint NOT NULL,              -- references pp_scraper_signals.id
  score            integer NOT NULL,
  tier             text NOT NULL,
  scored_at        timestamptz NOT NULL DEFAULT now(),
  scoring_version  text NOT NULL DEFAULT 'v1'
);

CREATE INDEX IF NOT EXISTS pp_score_history_lead_idx     ON pp_score_history (lead_id);
CREATE INDEX IF NOT EXISTS pp_score_history_scored_at_idx ON pp_score_history (scored_at DESC);

ALTER TABLE pp_score_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role full access" ON pp_score_history USING (true) WITH CHECK (true);

-- ── TASK 2c: pp_identity_match ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pp_identity_match (
  id               bigserial PRIMARY KEY,
  lead_id          bigint NOT NULL,
  matched_name     text,
  matched_phone    text,
  matched_email    text,
  address          text,
  confidence_score numeric(5,2),
  matched_at       timestamptz NOT NULL DEFAULT now(),
  source           text NOT NULL DEFAULT 'manual'
);

CREATE INDEX IF NOT EXISTS pp_identity_match_lead_idx ON pp_identity_match (lead_id);
CREATE INDEX IF NOT EXISTS pp_identity_match_phone_idx ON pp_identity_match (matched_phone);

ALTER TABLE pp_identity_match ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role full access" ON pp_identity_match USING (true) WITH CHECK (true);

-- ── HELPER VIEW: Staleness dashboard ─────────────────────────────────────────
-- Sources not producing signals in 48+ hours
CREATE OR REPLACE VIEW pp_source_staleness AS
SELECT
  r.source_slug,
  MAX(r.run_at) AS last_run_at,
  MAX(r.signal_count) AS last_signal_count,
  MAX(r.status) AS last_status,
  CASE
    WHEN MAX(r.run_at) < now() - INTERVAL '48 hours' THEN true
    ELSE false
  END AS is_stale,
  EXTRACT(EPOCH FROM (now() - MAX(r.run_at))) / 3600 AS hours_since_last_run
FROM pp_run_log r
GROUP BY r.source_slug;
