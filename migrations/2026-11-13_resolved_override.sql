-- Nov 13 2026 (applied live): homes the county shows are no longer owned by the lead's family are not seller leads.
-- pp_resolved_override(entity_key, reason, evidence) holds them; pp_compute_convergence marks them resolved_flag = true
-- just before pruning, so they leave the seller list and the top tier. 20 leads added (10 Summit, 10 Wasatch).
create table if not exists pp_resolved_override (entity_key text primary key, reason text not null, evidence text, created_at timestamptz default now());
alter table pp_resolved_override enable row level security;
