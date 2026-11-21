-- Nov 20 2026 — Utah retirements, top-tier contact hold, agent copy audit.
-- Record of what was applied to lbvaosyfikkpvcwksiph (applied via the Supabase migration tool), with undo steps.
-- Every replaced function definition is saved in pp_function_backup (fn, saved_at, def) before it changed.

-- 1. Retirements (explicit IDs) — 48 leads into pp_resolved_override, overrides 191 -> 239.
--    Staged list: pp_utah_retire_20261120. Backups: pp_backup_override_20261120 (191), pp_backup_override_20261120b (232).
--    Undo: delete from pp_resolved_override where entity_key in (select entity_key from pp_utah_retire_20261120); then recompute.

-- 2. pp_mailing_confirm_normalized_20261120
--    pp_norm_addr(text) added; pp_compute_convergence compares mailing addresses normalised (case, punctuation, ZIP+4)
--    and prefers the filing parcel's mailing address. Undo: re-execute the saved def from pp_function_backup.

-- 3. pp_top_tier_hold_20261120
--    Table pp_top_tier_hold (RLS on). Inside pp_compute_convergence, right after contactable is set:
--    a live 85+ lead with no confirmed mailing address and no phone is held at 84 (row active=true);
--    the hold releases itself when the lead becomes contactable.
--    Undo for one lead: update pp_top_tier_hold set active=false where entity_key = '<key>'; (sticks: released_at stays null)
--    Undo all: re-execute the pre-hold def from pp_function_backup and recompute.

-- 4. pp_top_tier_hold_skip_retired_20261120
create table pp_backup_top_tier_hold_20261120 as
  select h.*, now() as backed_up_at from pp_top_tier_hold h
  where h.entity_key in (select entity_key from pp_resolved_override);          -- 28 rows
alter table pp_backup_top_tier_hold_20261120 enable row level security;
-- hold insert now also requires: and entity_key not in (select entity_key from pp_resolved_override)
delete from pp_top_tier_hold where entity_key in (select entity_key from pp_backup_top_tier_hold_20261120);
-- Undo: insert into pp_top_tier_hold select entity_key, score_before, reason, held_at, active, released_at
--       from pp_backup_top_tier_hold_20261120;

-- 5. pp_agent_copy_audit_20261120
--    pp_refresh_app_cache stage labels: 5 Trustee sale noticed, 4 Default or lawsuit filed, 3 Trustee substituted,
--    2 Lien, judgment or trustee change, 1 Tax, divorce or probate record, 0 Other county record
--    (was Auction scheduled / Foreclosure filed / Lender preparing / Creditor action / Financial pressure / Watch).
--    pp_lead_situation: divorce_transfer (a deed dropping one name from title) no longer makes a lead 'divorce';
--    14 leads with no divorce court case moved to their next record type.
--    Undo: re-execute both saved defs from pp_function_backup, then select pp_refresh_app_cache();

-- 6. pp_e2e_test_cleanup_20261120 (8:5x PM MT)
--    End-to-end logging test by throwaway device b494b320 on the Beckstead lead (outcomes 50, 51; lifecycle row; deal 8; agent row).
--    All 5 rows backed up to pp_backup_e2e_test_20261120 (tbl, row_data jsonb), then deleted by explicit ID. Beckstead back to 'new', 0 touches.
--    Auth user b494b320 and the append-only event_log entries 334525-334529 left in place on purpose.

-- 7. pp_mailing_from_small_filing_parcels_20261120 (~10 PM MT)
--    Gap: 289 Utah tax-delinquent leads had an assessor mailing address on the parcel named in their own filing,
--    but parcel_roll only read parcels over $20,000, so the address was dropped and the lead had no contact path.
--    Change in pp_compute_convergence.parcel_roll: the two filing-parcel branches accept any valued parcel;
--    assessed_value, property_address and absentee_verified still use only parcels over $20,000 (scores unchanged);
--    the mailing address prefers a parcel over $20,000, then a filing parcel, then value.
--    Snapshot: pp_snap_conviction_20261120b. Old def: pp_function_backup.
--    Undo: re-execute the pre-change def from pp_function_backup, then recompute.
