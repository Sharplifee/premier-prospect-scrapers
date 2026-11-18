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

-- 3) Rhoades Spivey restored (family transfer: death certificate entry 573923, survivor Spivey David E at the same mailing).
--    Override row moved to pp_override_removed. Undo: insert it back into pp_resolved_override from pp_override_removed.
-- 4) Summit joined mailing addresses: 4 parcels (1031-WS-1, ALLC-202, RR-B-9, SNC-1010) rewritten as separate comma segments
--    by run 44296771938. Backup: pp_backup_parcel_mail_20261118.
-- 5) 21 duplicate-lead groups merged (22 duplicate keys -> 21 kept records). Map: pp_lead_merge; every changed row: pp_lead_merge_log
--    (23 pp_scraper_signals.owner_key + 4 pp_parcel_intel.owner_key). Undo one group:
--    update pp_scraper_signals s set owner_key=l.old_value from pp_lead_merge_log l where l.group_no=<n> and l.table_name='pp_scraper_signals' and s.id::text=l.row_id;
--    update pp_parcel_intel p set owner_key=l.old_value from pp_lead_merge_log l where l.group_no=<n> and l.table_name='pp_parcel_intel' and p.parcel_serial=l.row_id;
--    update pp_lead_merge set undone_at=now() where group_no=<n>;  then recompute.
