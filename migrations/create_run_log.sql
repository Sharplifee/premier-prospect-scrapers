-- Premier Prospect — Run Log Table
-- Run this once in Supabase SQL Editor: https://supabase.com/dashboard/project/lbvaosyfikkpvcwksiph/sql
-- After running, the scraper will automatically write per-source run history

CREATE TABLE IF NOT EXISTS pp_run_log (
  id            bigserial PRIMARY KEY,
  source_slug   text NOT NULL,
  run_at        timestamptz NOT NULL DEFAULT now(),
  signal_count  int NOT NULL DEFAULT 0,
  status        text NOT NULL DEFAULT 'success',  -- 'success' | 'error'
  error_msg     text,
  run_number    int DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pp_run_log_slug_idx   ON pp_run_log (source_slug);
CREATE INDEX IF NOT EXISTS pp_run_log_run_at_idx ON pp_run_log (run_at DESC);

-- Allow service role to insert
ALTER TABLE pp_run_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Service role full access" ON pp_run_log
  USING (true) WITH CHECK (true);
