-- Nov 21 2026 (~11 AM MT) — Utah County deeds stored under Salt Lake lead keys, merged onto Utah leads.
-- Cause: comparable-sales-slco and slco-recorder-live read Utah County's DocDescSearch (which ignores the County field) and
-- inserted rows as county 'Salt Lake'; the county column was later corrected to 'Utah' but owner_key ('O:Salt Lake:<name>',
-- set once by pp_derive_entity_fields on insert) was not. 527 live rows -> 300 'Salt Lake' leads, 145 with an O:Utah: twin.
-- Both sources have been out of SCRAPERS since Sep 27 2026 (last rows Sep 19).

-- A. pp_sl_utah_rekey_a_20261121
--    Staging (explicit IDs): pp_rekey_sl_utah_20261121 (id, old_key, new_key, source_slug) — 527 rows.
--    Backups: pp_backup_signals_sl_rekey_20261121 (527 rows), pp_backup_conviction_sl_rekey_20261121 (386 leads: SL + twins).
--    update pp_scraper_signals set owner_key = new_key for those 527 IDs. The recompute merges each twin; duplicate
--    documents collapse on the county entry number (doc_id).
--    Undo: update pp_scraper_signals s set owner_key = r.old_key from pp_rekey_sl_utah_20261121 r where s.id = r.id; recompute.

-- B. pp_sl_utah_rekey_b_20261121
--    Backup pp_backup_sl_rekey_side_20261121 (94 rows: parcels, links, hold, override).
--    Cortese James F unlinked from Salt Lake parcel 26224580070000 (name-only cross-county match, no document).
--    81 Utah parcels re-keyed to O:Utah: (owner-keep trigger paused for that one update); redundant links deleted;
--    Wardle override and Worthington's inactive hold re-keyed.
--    pp_guard_retired_source now also drops rows from comparable-sales-slco and slco-recorder-live.
--    Code: both scrapers pinned to county 'Utah' (run_all.py, scrapers_realtime.py).
--    Undo: restore rows from pp_backup_sl_rekey_side_20261121; restore the old pp_guard_retired_source list.

-- C. pp_hold_review_martin_richins_20261121 (backup rows in pp_backup_sl_rekey_side_20261121, tbl 'pp_parcel_intel:hold_review')
--    Jennifer Martin: Salt Lake GIS owner 'JENNIFER GAIL MARTIN (JT); JOSEPH TYLER MARTIN (JT)' = both parties on divorce case
--    254904894 -> mailing 8676 S SCOTIA LN, SANDY UT 84093 filled on 28032260170000 (saltlake_intel.py never saved mailing; fixed).
--    Jennifer Richins: Wasatch parcel 00-0021-9084 (RICHINS JENNIFER / RICHINS NICOLE JT), the only parcel in her name; NOD 581887.
--    Griffin Justin stays held: no Wasatch parcel in his name (lis pendens 581857 by Geneva Rock).
-- D. pp_tr_d_is_trust_deed_20261121: Utah County 'TR D' is a trust deed (loan), not a trustee's deed. 25 slco-recorder-live rows
--    (12 leads, 9 wrongly retired as "foreclosure completed") relabelled deed_of_trust, score 35.
--    Staging pp_trd_relabel_20261121, backup pp_backup_trd_relabel_20261121. Code: scrapers_realtime.py KOI map and
--    integrity_spotcheck.py ALLOWED ('TR D' -> deed_of_trust; 'TEE D' -> trustee_deed).
--    Undo: update pp_scraper_signals s set signal_type=b.signal_type, score=b.score from pp_backup_trd_relabel_20261121 b where s.id=b.id;

-- E. pp_recompute_lease_20261121: table pp_recompute_lease (one row, RLS on, service only) + pp_recompute_lease_acquire /
--    _release / _status. scrapers/pp_recompute.sh (used by county-reread, county-backfill, utah-name-backfill,
--    parcel-backfill, enrich) waits for the lease, retries each step on 55P03/57014/5xx 6x 30s apart, and releases on exit.
--    run_all.py's recompute takes the same lease and retries. Cause: the REST role's lock_timeout is 8s, so two overlapping
--    recomputes failed (runs 44932602368, 44933819320). Undo: drop the three functions and the table; restore the inline loops.
