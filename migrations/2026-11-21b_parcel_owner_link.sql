-- Nov 21 2026 (~10:30 AM MT) — parcel owner links. Applied to lbvaosyfikkpvcwksiph as two migrations.
-- Why: pp_parcel_intel holds one owner_key per parcel (PK parcel_serial). This morning's free-source contact backfill
-- re-keyed 199 parcels from one lead to another; 136 leads lost their only mailing address.

-- A. pp_parcel_owner_link_a_20261121
--    table pp_parcel_owner_link (parcel_serial, county, owner_key, active, note, linked_at), PK (parcel_serial, owner_key), RLS on
--    (service all, pp_is_agent() read). View pp_parcel_owners (security_invoker) = pp_parcel_intel.owner_key union active links.
--    Refill from pp_snap_parcel_intel_20261121: 199 links; 183 same surname as the new owner -> active; 16 different -> inactive (deed review).
--    Trigger pp_parcel_owner_keep (BEFORE UPDATE OF owner_key): same surname -> keep old owner, add new as link;
--    different surname -> current owner takes the parcel, old lead saved as inactive link.
--    Undo: drop trigger pp_parcel_owner_keep on pp_parcel_intel; drop function pp_parcel_owner_keep();
--          drop view pp_parcel_owners; drop table pp_parcel_owner_link;  (after undoing B)

-- B. pp_parcel_owner_link_b_20261121
--    pp_compute_convergence (parcel_roll, property_confirmed, mailing_confirmed), pp_lead_city, pp_lead_point read owners from
--    pp_parcel_owners instead of pp_parcel_intel.owner_key. Old defs in pp_function_backup (saved just before).
--    Undo: re-execute the three saved defs from pp_function_backup, then recompute.
