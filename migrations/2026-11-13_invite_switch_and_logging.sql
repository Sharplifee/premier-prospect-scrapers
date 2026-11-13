-- Applied live Nov 13 2026 (daily standard run). Record of what changed; see the session report for evidence.

-- 1. Invite-only switch (OFF until Connor approves). pp_public_config() is readable by anyone; pp_set_invite_only()
--    is admin/service only. ON = every anonymous device session deactivated, new anonymous sessions start inactive.
create table if not exists public.pp_settings (key text primary key, value jsonb not null, updated_at timestamptz default now());
alter table public.pp_settings enable row level security;
grant select, insert, update on public.pp_settings to service_role;
insert into public.pp_settings(key, value) values ('invite_only', 'false'::jsonb) on conflict (key) do nothing;
-- pp_public_config(): json {invite_only}; security definer; execute granted to anon, authenticated.
-- pp_set_invite_only(p_on boolean): admin only; when true: update pp_agents set active=false where is_anonymous.
-- pp_ensure_agent: active := (anon AND NOT invite_only) OR first OR invited   (was: anon OR first OR invited)
-- Verified in a rolled-back transaction: switch ON deactivates 31 anonymous sessions; admin still sees 11,853 leads;
-- an anonymous device sees 0.

-- 2. Agent work logging
-- pp_record_outcome: refuses callers that are signed in but not active agents; actor is always the signed-in agent
--   (the website passed p_actor='crm', so its calls never counted toward the agent's own stats).
-- pp_save_deal: refuses callers that are not active agents.
-- Verified rolled back: claim -> call (no_answer) -> appointment_set -> deal saved -> my_stats appointments=1,
--   worked_today=1 -> ledger shows the deal; an inactive account is refused.

-- 3. Data truth
-- pp_split_mail: understands "STREET, CITY ST ZIP" (county parcel layers) and Utah "%CARE OF" prefixes; 31 of 2,336
--   addresses (foreign / no street suffix) stay unsplit and are excluded from the mail export.
-- pp_conviction_queue: estate reason line says the named owner may be the deceased — reach the heirs or estate.
-- Summit signals on Westgate timeshare parcels (WGC-*) marked is_institutional (22 rows; 12 top-tier leads removed).
-- Salt Lake: 2 parcels now owned by "TRUST NOT IDENTIFIED" unlinked from their court-calendar leads (can't re-confirm).
