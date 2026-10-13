-- 2026-10-13b: flip evidence carries the bought parcel so flippers get a home/city/map point;
-- pp_backfill_state tracks the Utah County recorder history walk (scrapers/utah_deed_backfill.py).
do $mig$
declare src text; n int;
begin
  src := pg_get_functiondef('pp_compute_buyers'::regproc);
  n := position($a$'same_property', (a.parcel_serial is not null and a.parcel_serial = b.parcel_serial))) as pairs$a$ in src);
  if n = 0 then raise exception 'anchor flip-pairs not found'; end if;
  src := replace(src, $a$'same_property', (a.parcel_serial is not null and a.parcel_serial = b.parcel_serial))) as pairs$a$,
                    $a$'same_property', (a.parcel_serial is not null and a.parcel_serial = b.parcel_serial), 'parcel_serial', a.parcel_serial)) as pairs$a$);
  n := position($a$'hold_days', (p->>'hold_days')::int, 'same_property', (p->>'same_property')::boolean)$a$ in src);
  if n = 0 then raise exception 'anchor flip-detail not found'; end if;
  src := replace(src, $a$'hold_days', (p->>'hold_days')::int, 'same_property', (p->>'same_property')::boolean)$a$,
                    $a$'hold_days', (p->>'hold_days')::int, 'same_property', (p->>'same_property')::boolean, 'parcel_serial', nullif(p->>'parcel_serial',''))$a$);
  execute src;
end $mig$;

create table if not exists pp_backfill_state (
  key text primary key, offset_next int not null default 0, done boolean not null default false,
  last_pages int, last_inserted int, updated_at timestamptz not null default now());
alter table pp_backfill_state enable row level security;
revoke all on pp_backfill_state from anon, authenticated;
