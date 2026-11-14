-- 2026-11-14 evlog_capture_on_action_tables_2026_11_14 (applied as migration 20261114224952)
-- Agent actions and manual decisions were missing from the permanent event log: the two outcome-event deletes of the
-- Nov 14 build-104 test never reached event_log. Same capture trigger as every other logged table.
-- Bulk tables rebuilt by the nightly recompute (pp_entity_conviction, pp_buyer_intel, pp_score_history, caches, run logs)
-- stay out on purpose.
create trigger evlog_capture after insert or update or delete on public.pp_outcome_events for each row execute function public.event_log_capture_change();
create trigger evlog_capture after insert or update or delete on public.pp_resolved_override for each row execute function public.event_log_capture_change();
create trigger evlog_capture after insert or update or delete on public.pp_outreach_queue for each row execute function public.event_log_capture_change();
create trigger evlog_capture after insert or update or delete on public.pp_assistant_log for each row execute function public.event_log_capture_change();
create trigger evlog_capture after insert or update or delete on public.pp_settings for each row execute function public.event_log_capture_change();
create trigger evlog_capture after insert or update or delete on public.pp_buyer_events for each row execute function public.event_log_capture_change();
create trigger evlog_capture after insert or update or delete on public.pp_job_events for each row execute function public.event_log_capture_change();
