-- 2026-11-18 (run by SQL through the Supabase MCP, recorded here for the log)
-- 1) Lead retirements: 56 explicit entity_keys inserted into pp_resolved_override (reason + county evidence per row).
--    Score snapshot before: pp_snap_conviction_20261118. Undo a retirement: delete from pp_resolved_override where entity_key = '<key>'; then recompute.
-- 2) txn_type backfill. Backup: pp_backup_txn_type_20261118 (id, signal_type, txn_type) — 6,655 rows.
--    Criteria: txn_type is not null and signal_type not in the title-transfer set below.
update pp_scraper_signals s set txn_type = null
from pp_backup_txn_type_20261118 b
where s.id = b.id and s.txn_type is not null
  and s.signal_type not in ('deed_transfer','comparable_sale','family_transfer','divorce_transfer','trustee_deed','probate_deed');
--    Result: 6,655 rows nulled; non-deed rows with txn_type 6,655 -> 0; deed rows with txn_type 11,691 -> 11,691.
--    Score snapshot before: pp_snap_conviction_20261118b; after pp_compute_convergence(): 0 of 15,544 scores changed.
--    Undo: update pp_scraper_signals s set txn_type = b.txn_type from pp_backup_txn_type_20261118 b where s.id = b.id;
