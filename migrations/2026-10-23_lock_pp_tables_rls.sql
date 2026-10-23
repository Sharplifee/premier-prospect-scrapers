-- Supabase security advisor (rls_disabled_in_public): three Premier Prospect tables were reachable with the public
-- (anon) key. Only service_role and SECURITY DEFINER functions read them, so lock them down.
alter table public.pp_buyer_backtest enable row level security;
alter table public.pp_integrity_log  enable row level security;
alter table public.pp_buyer_signals  enable row level security;
revoke all on public.pp_buyer_backtest, public.pp_integrity_log, public.pp_buyer_signals from anon, authenticated;
grant select, insert, update, delete on public.pp_buyer_backtest, public.pp_integrity_log, public.pp_buyer_signals to service_role;
