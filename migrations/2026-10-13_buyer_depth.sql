-- 2026-10-13 buyer depth (Boss: buyer side must carry the same enrichment and reach the same standard as sellers).
-- 1. Every buyer gets the property they are tied to: parcel, address, city, map point, mailing address, absentee flag.
--    Filled nightly inside pp_compute_buyers from the parcel on the buyer's newest signal (deed or court), via the
--    state parcel layer (pp_parcel_geo) and the assessor rows (pp_parcel_intel), plus any name-search hits keyed to the buyer.
-- 2. Price bands persist: pp_compute_buyers writes price_band_low/high from assessed values every run, so the nightly
--    rebuild no longer wipes them (parcel_intel.py used to PATCH them into a table that is deleted every night).
-- 3. pp_buyer_parcel_attempts remembers which buyers the assessor name search already tried, so the resolver stops
--    re-selecting the same names (its old selector keyed on a NULL the compute writes as 0 and picked almost nobody).
-- 4. People get their own scale so real households (repeat buyers, flippers, off-title, divorcing homeowners) can
--    reach the top tier instead of 96 of the top 97 being LLCs. Entity scores are untouched.
-- 5. Retired Utah County sources (slco-recorder-live, comparable-sales-slco) no longer feed the buyer purchase union.

alter table pp_buyer_intel
  add column if not exists parcel_serial text,
  add column if not exists property_address text,
  add column if not exists city text,
  add column if not exists lat double precision,
  add column if not exists lon double precision,
  add column if not exists mailing_address text,
  add column if not exists is_absentee boolean;

create table if not exists pp_buyer_parcel_attempts (
  buyer_key text primary key,
  tried_at timestamptz not null default now(),
  hits int not null default 0,
  county text
);
alter table pp_buyer_parcel_attempts enable row level security;
revoke all on pp_buyer_parcel_attempts from anon, authenticated;

do $mig$
declare src text; n int;
begin
  src := pg_get_functiondef('pp_compute_buyers'::regproc);

  -- (5) retired sources out of the purchase union
  n := position($a$where not is_legacy
      and upper(coalesce(pp_payload(raw_payload)->>'koi','')) in ('WD','SP WD','WARRANTY DEED','SPECIAL WARRANTY DEED')
      and pp_norm(pp_payload(raw_payload)->>'grantee') is not null$a$ in src);
  if n = 0 then raise exception 'anchor 1 (t_purch where) not found'; end if;
  src := replace(src, $a$where not is_legacy
      and upper(coalesce(pp_payload(raw_payload)->>'koi','')) in ('WD','SP WD','WARRANTY DEED','SPECIAL WARRANTY DEED')
      and pp_norm(pp_payload(raw_payload)->>'grantee') is not null$a$,
  $a$where not is_legacy
      and source_slug not in ('slco-recorder-live','comparable-sales-slco')  -- retired Sept 27: duplicates of utah-recorder-unified
      and upper(coalesce(pp_payload(raw_payload)->>'koi','')) in ('WD','SP WD','WARRANTY DEED','SPECIAL WARRANTY DEED')
      and pp_norm(pp_payload(raw_payload)->>'grantee') is not null$a$);

  -- (1)(2)(4) property roll-up, price bands, people scale: inserted before the backtest
  n := position($a$  -- backtest every layer against later recorded purchases, and drop life-event buyers who have since bought
  perform pp_compute_buyer_backtest();$a$ in src);
  if n = 0 then raise exception 'anchor 2 (backtest) not found'; end if;
  src := replace(src, $a$  -- backtest every layer against later recorded purchases, and drop life-event buyers who have since bought
  perform pp_compute_buyer_backtest();$a$,
  $a$  -- ==== Oct 13 2026 buyer depth: the property each buyer is tied to, from the parcel on their newest signal ====
  with best as (
    select distinct on (s.buyer_key) s.buyer_key, s.ps, s.county
    from (select buyer_key, county, observed_at, nullif(detail->>'parcel_serial','') as ps from pp_buyer_signals) s
    where s.ps is not null order by s.buyer_key, s.observed_at desc)
  update pp_buyer_intel b set
    parcel_serial    = best.ps,
    property_address = coalesce(nullif(g.address,''), nullif(pi.property_address,'')),
    city             = coalesce(nullif(initcap(lower(g.city)),''), nullif(initcap(lower(regexp_replace(pi.property_address, '^.*,\s*([A-Za-z .]+?)\s*(\d{5})?\s*$', '\1'))),'')),
    lat = g.lat, lon = g.lon,
    mailing_address  = nullif(pi.mailing_address,''),
    is_absentee      = pi.is_absentee
  from best
  left join pp_parcel_geo g on g.parcel_serial = best.ps
  left join pp_parcel_intel pi on pi.parcel_serial = best.ps
  where b.buyer_key = best.buyer_key;

  -- buyers with no parcel on any signal: the assessor name-search hits keyed to them (parcel_intel.py)
  update pp_buyer_intel b set
    parcel_serial    = pi.parcel_serial,
    property_address = coalesce(b.property_address, nullif(g.address,''), nullif(pi.property_address,'')),
    city             = coalesce(b.city, nullif(initcap(lower(g.city)),''), nullif(initcap(lower(regexp_replace(pi.property_address, '^.*,\s*([A-Za-z .]+?)\s*(\d{5})?\s*$', '\1'))),'')),
    lat = coalesce(b.lat, g.lat), lon = coalesce(b.lon, g.lon),
    mailing_address  = coalesce(b.mailing_address, nullif(pi.mailing_address,'')),
    is_absentee      = coalesce(b.is_absentee, pi.is_absentee)
  from (select distinct on (buyer_key) * from pp_parcel_intel where buyer_key is not null order by buyer_key, market_value desc nulls last) pi
  left join pp_parcel_geo g on g.parcel_serial = pi.parcel_serial
  where b.buyer_key = pi.buyer_key and b.parcel_serial is null;

  -- price band = the assessed values of what this buyer is tied to (bought, owns, or the shared home); persists every run
  update pp_buyer_intel b set
    price_band_low  = v.lo, price_band_high = v.hi,
    value_low = coalesce(b.value_low, v.lo), value_high = coalesce(b.value_high, v.hi),
    parcels_known = greatest(coalesce(b.parcels_known,0), v.n)
  from (select buyer_key, min(mv) as lo, max(mv) as hi, count(*) as n from (
          select pi.buyer_key, pi.market_value as mv from pp_parcel_intel pi where pi.buyer_key is not null and pi.market_value >= 20000
          union all
          select s.buyer_key, pi.market_value from pp_buyer_signals s
            join pp_parcel_intel pi on pi.parcel_serial = nullif(s.detail->>'parcel_serial','') where pi.market_value >= 20000) x
        group by 1) v
  where b.buyer_key = v.buyer_key;

  -- name search already tried with no hit: parcels_known stays 0 and the resolver skips them
  update pp_buyer_intel b set parcels_known = 0
  from pp_buyer_parcel_attempts a where a.buyer_key = b.buyer_key and a.hits = 0 and b.parcels_known is null;

  update pp_buyer_intel b set cities = array_append(coalesce(b.cities,'{}'::text[]), b.city)
  where b.city is not null and not (b.city = any(coalesce(b.cities,'{}'::text[])));

  -- ==== Oct 13 2026 people first: a person's own scale, so real households can reach the top tier ====
  -- Entities keep the investor scale above. A person is lifted only on evidence already on the row.
  update pp_buyer_intel b set buyer_score = least(100, greatest(b.buyer_score, case
      when b.purchases >= 4 then 90
      when 'flip' = any(b.signal_types) then 88
      when b.purchases >= 2 and coalesce(b.days_since_last, 999) <= 120 then 85
      when 'divorce' = any(b.signal_types) and b.deadline_at > now() and b.value_low is not null then 84
      when 'off_title' = any(b.signal_types) and coalesce(b.days_since_last, 999) <= 60 then 82
      when 'divorce' = any(b.signal_types) and b.value_low is not null then 78
      else b.buyer_score end))
  where not coalesce(b.is_entity, false);

  -- backtest every layer against later recorded purchases, and drop life-event buyers who have since bought
  perform pp_compute_buyer_backtest();$a$);

  execute src;
end $mig$;

-- the queue view and the app overview carry the new columns (view columns are APPENDED, never reordered)
create or replace view pp_buyer_queue as
 select buyer_key, buyer_display, is_entity, purchases, distinct_counties, counties, first_seen, last_seen, cash_purchases,
        financed_purchases, cash_ratio, days_since_last, buyer_score, buyer_type, entries, computed_at, price_band_low, price_band_high,
        parcels_known, lender_class, financing_known, private_financed, loan_low, loan_high, value_low, value_high, cities, signal_types,
        is_move_up, sold_entry, sold_at, buyer_stage, why, buyer_confirmed,
        deadline_at, deadline_label, parcel_serial, property_address, city, lat, lon, mailing_address, is_absentee
   from pp_buyer_intel
  order by buyer_score desc, last_seen desc;

create or replace function public.pp_app_buyer_overview()
 returns json language sql stable security definer set search_path to 'public','pg_temp' as $function$
  select json_build_object(
    'total',        (select count(*) from pp_buyer_intel),
    'people',       (select count(*) from pp_buyer_intel where not coalesce(is_entity,false)),
    'people_top',   (select count(*) from pp_buyer_intel where not coalesce(is_entity,false) and buyer_score >= 85),
    'with_address', (select count(*) from pp_buyer_intel where property_address is not null),
    'with_price_band', (select count(*) from pp_buyer_intel where price_band_low is not null),
    'repeat',       (select count(*) from pp_buyer_intel where purchases >= 2),
    'highly_active',(select count(*) from pp_buyer_intel where purchases >= 4),
    'entities',     (select count(*) from pp_buyer_intel where is_entity and buyer_type not like 'Builder%'),
    'builders',     (select count(*) from pp_buyer_intel where buyer_type like 'Builder%'),
    'multi_county', (select count(*) from pp_buyer_intel where distinct_counties > 1),
    'last_30d',     (select count(*) from pp_buyer_intel where days_since_last <= 30),
    'just_sold',    (select count(*) from pp_buyer_intel where is_move_up),
    'cash',         (select count(*) from pp_buyer_intel where cash_purchases > 0),
    'private_financed', (select count(*) from pp_buyer_intel where private_financed > 0),
    'exchange_window',  (select count(*) from pp_buyer_intel where 'exchange_window' = any(signal_types)),
    'exchange_due_45',  (select count(*) from pp_buyer_intel where 'exchange_window' = any(signal_types) and deadline_label = 'Name replacement by'),
    'divorce',      (select count(*) from pp_buyer_intel where 'divorce' = any(signal_types)),
    'landlord',     (select count(*) from pp_buyer_intel where 'landlord' = any(signal_types)),
    'flip',         (select count(*) from pp_buyer_intel where 'flip' = any(signal_types)),
    'off_title',    (select count(*) from pp_buyer_intel where 'off_title' = any(signal_types)),
    'with_deadline',(select count(*) from pp_buyer_intel where deadline_at > now()),
    'by_county',    (select json_object_agg(c, n) from (select unnest(counties) c, count(*) n from pp_buyer_intel group by 1) x),
    'captured',     (select count(*) from pp_buyer_leads l where l.status in ('new','working','matched') and l.agent_id in (select agent_id from pp_agents where user_id = pp_uid())),
    'proof',        (select coalesce(json_agg(row_to_json(t) order by t.layer), '[]'::json) from (select layer, flagged, checkable, followed_30d, bought, bought_pct, median_days, bought_90d, note from pp_buyer_backtest) t),
    'computed_at',  (select max(computed_at) from pp_buyer_intel)
  ); $function$;

-- buyer map points: same shape the seller map uses (agent-gated)
create or replace function public.pp_app_buyer_points()
 returns json language sql stable security definer set search_path to 'public','pg_temp' as $function$
  select case when pp_is_agent() then coalesce(json_agg(json_build_object(
      'buyer_key', buyer_key, 'name', buyer_display, 'score', buyer_score, 'type', buyer_type, 'county', county, 'city', city,
      'lat', lat, 'lon', lon, 'address', property_address, 'is_entity', coalesce(is_entity,false))), '[]'::json)
    else null end
  from (select b.*, b.counties[1] as county from pp_buyer_intel b where lat is not null and lon is not null) x; $function$;
revoke execute on function public.pp_app_buyer_points() from public, anon;
grant execute on function public.pp_app_buyer_points() to authenticated;
