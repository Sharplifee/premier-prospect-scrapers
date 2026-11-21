-- Nov 21 2026 (overnight) — applied to lbvaosyfikkpvcwksiph via the Supabase migration tool. Record + undo steps.

-- 1. pp_court_only_cap_59_20261121
--    Court-calendar names with no confirmed property (property_confirmed = false) are capped at 59 (was 65), below every 60+ view.
--    Explicit list + scores before: pp_court_only_cap_20261121 (2,849 leads). Integrity invariant renamed unconfirmed_above_59.
--    Old defs (pp_compute_convergence, pp_integrity_check) saved in pp_function_backup.
--    Undo: re-execute both saved defs, then recompute.

-- 2. pp_estate_label_and_value_label_20261121
--    New table pp_lead_label (RLS: service all, agents read): 24 Utah top-tier leads that name only a deceased owner,
--    label 'Estate - find heir'; successor confirmed on the Utah County assessor's current title for 20, not confirmed for 4
--    (Wentz, Voorhees, Fenley, West — evidence text says why). Undo per lead: update pp_lead_label set active=false where entity_key=...
--    New column pp_entity_conviction.value_label, computed nightly in pp_compute_convergence: when there is no county home value,
--    'Small parcel, $X (YYYY value)' from the filing's own parcel, else 'No home value on file'.
--    pp_conviction_queue: appended columns value_label, lead_label, successor, successor_evidence, successor_mailing, successor_confirmed.
--    Undo: re-execute the saved convergence def; drop the appended view columns by re-creating the view from its prior definition.
