-- Applied live Nov 12 2026 (agent-readiness audit).
-- 1. pp_conviction_queue was a plain view owned by postgres, so it ignored RLS: any signed-in user, including an
--    inactive account or one with no agent row, could read every lead through REST. Now security_invoker, so the
--    pp_is_agent() policy on pp_entity_conviction applies. All RPCs that read it are SECURITY DEFINER (unaffected).
alter view public.pp_conviction_queue set (security_invoker = true);
-- 2. pp_lead_lifecycle / pp_outcome_events had an extra "auth_read using (true)" policy next to the agent-only one,
--    letting any signed-in user read every agent's call log.
drop policy if exists auth_read on public.pp_lead_lifecycle;
drop policy if exists auth_read on public.pp_outcome_events;
-- Verified in a rolled-back transaction: active agent sees 11,827 leads and can record an outcome (lifecycle moves
-- to no_answer, touch 1); inactive account sees 0; signed-in stranger sees 0.
