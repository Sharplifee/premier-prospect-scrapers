-- ===== Buyer side, built to the same standard as sellers =====
delete from pp_buyer_events where visitor_id='test' or event_type='test';

alter table pp_buyer_intel
  add column if not exists lender_class text,
  add column if not exists financing_known boolean not null default false,
  add column if not exists private_financed integer not null default 0,
  add column if not exists loan_low numeric,
  add column if not exists loan_high numeric,
  add column if not exists value_low numeric,
  add column if not exists value_high numeric,
  add column if not exists cities text[],
  add column if not exists signal_types text[],
  add column if not exists is_move_up boolean not null default false,
  add column if not exists sold_entry text,
  add column if not exists sold_at timestamptz,
  add column if not exists buyer_stage text,
  add column if not exists why text,
  add column if not exists buyer_confirmed boolean not null default false;

create table if not exists pp_buyer_signals (
  id bigserial primary key,
  buyer_key text not null,
  buyer_display text not null,
  signal_type text not null,   -- purchase | financing | cash_purchase | move_up | parcel_value | private_financing
  county text,
  entry text,
  observed_at timestamptz,
  amount numeric,
  detail jsonb not null default '{}'::jsonb,
  source_slug text,
  computed_at timestamptz not null default now()
);
create index if not exists pp_buyer_signals_key on pp_buyer_signals(buyer_key);
create index if not exists pp_buyer_signals_type on pp_buyer_signals(signal_type);

create or replace function pp_rec_date(p jsonb, fallback timestamptz) returns timestamptz language plpgsql immutable as $$
begin
  return coalesce(nullif(coalesce(p->>'rec_date', p->>'recorded', p->>'loan_date'),'')::timestamptz, fallback);
exception when others then return fallback; end $$;

create or replace function pp_lender_class(l text) returns text language sql immutable as $$
  select case
    when coalesce(trim(l),'')='' then null
    when upper(l) ~ '(CREDIT UNION)' then 'credit_union'
    when upper(l) ~ '(HOUSING AND URBAN|VETERANS AFFAIRS|USDA|RURAL HOUSING|UTAH HOUSING)' then 'government'
    when upper(l) ~ '(BANK|BANCORP|NATIONAL ASSOCIATION|\yN A\y|FSB)' then 'bank'
    when upper(l) ~ '(MORTGAGE|LENDING|FUNDING|LOAN|FINANCIAL|HOME LOANS|ROCKET|SOFI|PROVIDENT)' then 'mortgage'
    else 'private' end $$;

create or replace function pp_compute_buyers() returns integer
language plpgsql security definer set search_path=public,pg_temp as $$
declare v int;
begin
  perform set_config('statement_timeout','300000', true);
  delete from pp_buyer_signals where true;
  delete from pp_buyer_intel where true;

  -- 1. Recorded purchases: warranty deed grantees only (lenders on trust deeds are not buyers)
  create temp table t_purch on commit drop as
  select distinct on (buyer_norm, entry) * from (
    select pp_norm(pp_payload(raw_payload)->>'grantee') as buyer_norm,
           pp_payload(raw_payload)->>'grantee' as buyer_raw,
           pp_norm(pp_payload(raw_payload)->>'grantor') as seller_norm,
           regexp_replace(coalesce(pp_payload(raw_payload)->>'entry', raw_address),'[[:space:]\u00a0]+',' ','g') as entry,
           county, pp_rec_date(pp_payload(raw_payload), captured_at) as captured_at, source_slug, parcel_serial
    from pp_scraper_signals
    where not is_legacy
      and upper(coalesce(pp_payload(raw_payload)->>'koi','')) in ('WD','SP WD','WARRANTY DEED','SPECIAL WARRANTY DEED')
      and pp_norm(pp_payload(raw_payload)->>'grantee') is not null
      and upper(pp_payload(raw_payload)->>'grantee') !~ '(WHOM OF INTEREST|WHOM IT MAY|UNKNOWN|OCCUPANT)'
      and not (upper(pp_payload(raw_payload)->>'grantee') ~ '\y(BANK|CREDIT UNION|TITLE|ESCROW|MORTGAGE|LENDING|SUBTEE|SUCTEE|TRUSTEE|POWER|ENERGY|GAS|TELECOM|PIPELINE|RAILROAD)\y')
      and upper(pp_payload(raw_payload)->>'grantee') !~ '\y(DEPARTMENT OF TRANSPORTATION|TRANSIT AUTHORITY|CITY|TOWN OF|COUNTY OF|STATE OF|UNITED STATES|MUNICIPAL|REDEVELOPMENT|SCHOOL DISTRICT|WATER|SEWER|IRRIGATION)\y'
      and upper(pp_payload(raw_payload)->>'grantee') !~ '(UDOT|UTAH TRANSIT|CITY CORPORATION|ROCKY MOUNTAIN POWER)'
  ) x;

  -- 2. Financing: trust deeds where the borrower is a purchase grantee within 14 days
  create temp table t_fin on commit drop as
  select p.buyer_norm, p.entry as purchase_entry, d.entry as loan_entry, d.county, d.captured_at,
         d.lender, pp_lender_class(d.lender) as lender_class, d.loan_amount, d.source_slug
  from t_purch p
  join (
    select pp_norm(coalesce(pp_payload(raw_payload)->>'borrower', pp_payload(raw_payload)->>'grantor')) as b,
           coalesce(pp_payload(raw_payload)->>'lender', pp_payload(raw_payload)->>'grantee') as lender,
           nullif(pp_payload(raw_payload)->>'loan_amount','')::numeric as loan_amount,
           regexp_replace(pp_payload(raw_payload)->>'entry','[[:space:]\u00a0]+',' ','g') as entry,
           county, pp_rec_date(pp_payload(raw_payload), captured_at) as captured_at, source_slug
    from pp_scraper_signals
    where not is_legacy and (signal_type='deed_of_trust' or source_slug='utah-deeds-of-trust')
  ) d on d.b=p.buyer_norm and d.county=p.county
       and abs(extract(epoch from d.captured_at-p.captured_at)) < 14*86400;

  -- counties where a trust-deed source exists, so "no loan found" can mean cash
  -- a county qualifies for cash inference only when the SAME feed records both
  -- warranty deeds and trust deeds (complete coverage). Utah County's trust-deed
  -- feed is a separate partial sweep, so absence there proves nothing.
  create temp table t_fincounty on commit drop as
  select county from pp_scraper_signals
  where not is_legacy group by county, source_slug
  having count(*) filter (where signal_type='deed_of_trust')>0 and count(*) filter (where signal_type='deed_transfer')>0;

  -- 3. Move-up buyers: a person who just sold and has not since bought in the data
  create temp table t_moveup on commit drop as
  select seller_norm as buyer_norm, max(seller_raw) as buyer_raw, max(county) as county,
         max(captured_at) as sold_at, (array_agg(entry order by captured_at desc))[1] as sold_entry
  from (
    select pp_norm(pp_payload(raw_payload)->>'grantor') as seller_norm,
           pp_payload(raw_payload)->>'grantor' as seller_raw,
           regexp_replace(coalesce(pp_payload(raw_payload)->>'entry', raw_address),'[[:space:]\u00a0]+',' ','g') as entry,
           county, pp_rec_date(pp_payload(raw_payload), captured_at) as captured_at
    from pp_scraper_signals
    where not is_legacy
      and upper(coalesce(pp_payload(raw_payload)->>'koi','')) in ('WD','SP WD','WARRANTY DEED','SPECIAL WARRANTY DEED')
      and pp_norm(pp_payload(raw_payload)->>'grantor') is not null
      and not pp_is_institutional(pp_payload(raw_payload)->>'grantor')
      and upper(pp_payload(raw_payload)->>'grantor') !~ '\y(TEE|TR|TRUST|TRUSTEE|ESTATE|PERSONAL REP|PERS REP|DEC|DECEASED|ET AL)\y'
      and captured_at > now() - interval '120 days'
  ) s
  where not exists (select 1 from t_purch p where p.buyer_norm=s.seller_norm and p.captured_at >= s.captured_at)
  group by seller_norm;

  -- ---- signal rows (auditable evidence) ----
  insert into pp_buyer_signals(buyer_key,buyer_display,signal_type,county,entry,observed_at,detail,source_slug)
  select 'B:'||buyer_norm, buyer_raw, 'purchase', county, entry, captured_at,
         jsonb_build_object('grantor', seller_norm, 'parcel_serial', parcel_serial), source_slug from t_purch;

  insert into pp_buyer_signals(buyer_key,buyer_display,signal_type,county,entry,observed_at,amount,detail,source_slug)
  select 'B:'||f.buyer_norm, max(p.buyer_raw),
         case when f.lender_class='private' then 'private_financing' else 'financing' end,
         f.county, f.loan_entry, f.captured_at, f.loan_amount,
         jsonb_build_object('lender', f.lender, 'lender_class', f.lender_class, 'purchase_entry', f.purchase_entry), f.source_slug
  from t_fin f join t_purch p on p.buyer_norm=f.buyer_norm group by f.buyer_norm,f.lender_class,f.county,f.loan_entry,f.captured_at,f.loan_amount,f.lender,f.purchase_entry,f.source_slug;

  insert into pp_buyer_signals(buyer_key,buyer_display,signal_type,county,entry,observed_at,detail,source_slug)
  select 'B:'||p.buyer_norm, p.buyer_raw, 'cash_purchase', p.county, p.entry, p.captured_at,
         jsonb_build_object('basis','no trust deed recorded for this buyer within 14 days on a feed that records deeds and trust deeds together'), p.source_slug
  from t_purch p
  where p.county in (select county from t_fincounty)
    and p.captured_at < now() - interval '14 days'
    and not exists (select 1 from t_fin f where f.buyer_norm=p.buyer_norm and f.purchase_entry=p.entry);

  insert into pp_buyer_signals(buyer_key,buyer_display,signal_type,county,entry,observed_at,detail)
  select 'B:'||buyer_norm, buyer_raw, 'move_up', county, sold_entry, sold_at,
         jsonb_build_object('basis','sold by warranty deed, no later purchase recorded') from t_moveup;

  insert into pp_buyer_signals(buyer_key,buyer_display,signal_type,county,entry,observed_at,amount,detail)
  select pi.buyer_key, coalesce(b.buyer_raw, pi.buyer_key), 'parcel_value', pi.county, pi.parcel_serial, pi.fetched_at, pi.market_value,
         jsonb_build_object('property_address', pi.property_address, 'value_year', pi.value_year)
  from pp_parcel_intel pi left join (select distinct on (buyer_norm) buyer_norm, buyer_raw from t_purch) b on 'B:'||b.buyer_norm=pi.buyer_key
  where pi.buyer_key is not null and pi.market_value is not null;

  -- ---- profiles ----
  with agg as (
    select p.buyer_norm,
      max(p.buyer_raw) as buyer_display,
      pp_is_institutional(max(p.buyer_raw)) as is_entity,
      (upper(max(p.buyer_raw)) ~ '\y(HOMES|HOMEBUILDERS?|BUILDERS?|CONSTRUCTION|COMMUNITIES|DEVELOPMENT|DEVELOPERS?)\y') as is_builder,
      count(distinct p.entry) as purchases,
      count(distinct p.county) as distinct_counties,
      array_agg(distinct p.county) as counties,
      min(p.captured_at) as first_seen, max(p.captured_at) as last_seen,
      (array_agg(distinct p.entry))[1:12] as entries,
      count(distinct p.entry) filter (where p.county in (select county from t_fincounty)) as purchases_in_fin_county
    from t_purch p group by p.buyer_norm
  ), fin as (
    select buyer_norm, count(distinct purchase_entry) as financed, count(distinct purchase_entry) filter (where lender_class='private') as private_financed,
           min(loan_amount) as loan_low, max(loan_amount) as loan_high,
           (array_agg(lender_class order by captured_at desc))[1] as lender_class
    from t_fin group by buyer_norm
  ), val as (
    select buyer_key, count(*) as parcels_known, min(market_value) as value_low, max(market_value) as value_high,
           array_agg(distinct regexp_replace(property_address, '^.*,\s*','')) filter (where property_address is not null) as cities
    from pp_parcel_intel where buyer_key is not null group by buyer_key
  )
  insert into pp_buyer_intel (
    buyer_key, buyer_display, is_entity, purchases, distinct_counties, counties, first_seen, last_seen,
    cash_purchases, financed_purchases, cash_ratio, days_since_last, buyer_score, buyer_type, entries, computed_at,
    lender_class, financing_known, private_financed, loan_low, loan_high, value_low, value_high, parcels_known, cities,
    signal_types, is_move_up, buyer_stage, why, buyer_confirmed)
  select
    'B:'||a.buyer_norm, a.buyer_display, a.is_entity, a.purchases, a.distinct_counties, a.counties, a.first_seen, a.last_seen,
    greatest(0, a.purchases_in_fin_county - coalesce(f.financed,0)) as cash_purchases,
    coalesce(f.financed,0),
    case when a.purchases_in_fin_county>0 then round(greatest(0, a.purchases_in_fin_county - coalesce(f.financed,0))::numeric / a.purchases_in_fin_county, 2) end,
    extract(day from (now() - a.last_seen))::int,
    least(100, greatest(0, round(
        38
      + least(34, (a.purchases - 1) * 11)
      + (case when a.distinct_counties > 1 then 6 else 0 end)
      + (14 * exp(-1 * extract(epoch from (now()-a.last_seen))/86400.0 / 120))
      + (case when a.purchases_in_fin_county>0 and a.purchases_in_fin_county - coalesce(f.financed,0) > 0 then 8 else 0 end)
      + (case when coalesce(f.private_financed,0) > 0 then 6 else 0 end)
      + (case when a.is_builder then -6 else 0 end)
    )))::int,
    case
      when a.is_builder                     then 'Builder / land acquisition'
      when a.purchases >= 4 and a.is_entity then 'Active investment entity'
      when a.purchases >= 4                 then 'Highly active buyer'
      when a.purchases >= 2 and a.is_entity then 'Repeat investment entity'
      when a.purchases >= 2                 then 'Repeat buyer'
      when a.is_entity                      then 'Entity buyer'
      when coalesce(f.private_financed,0)>0 then 'Privately financed buyer'
      else 'Recent buyer' end,
    a.entries, now(),
    f.lender_class, (a.purchases_in_fin_county>0 or coalesce(f.financed,0)>0), coalesce(f.private_financed,0), f.loan_low, f.loan_high,
    v.value_low, v.value_high, coalesce(v.parcels_known,0), v.cities,
    array_remove(array['purchase',
      case when coalesce(f.financed,0)>0 then 'financing' end,
      case when coalesce(f.private_financed,0)>0 then 'private_financing' end,
      case when a.purchases_in_fin_county - coalesce(f.financed,0) > 0 then 'cash_purchase' end,
      case when coalesce(v.parcels_known,0)>0 then 'parcel_value' end], null),
    false,
    case when a.last_seen > now()-interval '45 days' then 'active' when a.last_seen > now()-interval '120 days' then 'recent' else 'dormant' end,
    concat_ws(' ',
      a.purchases||' recorded purchase'||case when a.purchases=1 then '' else 's' end||' in '||array_to_string(a.counties,', ')||'.',
      case when coalesce(f.financed,0)>0 then 'Financed '||f.financed||' through a '||replace(f.lender_class,'_',' ')||' lender'||case when f.loan_high is not null then ' (loans '||to_char(f.loan_low,'FM$999,999,999')||' to '||to_char(f.loan_high,'FM$999,999,999')||')' else '' end||'.' end,
      case when a.purchases_in_fin_county - coalesce(f.financed,0) > 0 then (a.purchases_in_fin_county - coalesce(f.financed,0))||' with no trust deed recorded, likely cash.' end,
      case when coalesce(v.parcels_known,0)>0 then 'Property bought assessed at '||to_char(v.value_low,'FM$999,999,999')||case when v.value_high<>v.value_low then ' to '||to_char(v.value_high,'FM$999,999,999') else '' end||'.' end,
      case when a.purchases_in_fin_county=0 and coalesce(f.financed,0)=0 then 'Financing unknown: this county''s trust-deed coverage is partial.' end),
    true
  from agg a left join fin f on f.buyer_norm=a.buyer_norm left join val v on v.buyer_key='B:'||a.buyer_norm;

  -- move-up buyers: no purchase yet, so purchases=0 and buyer_confirmed via the sale deed
  insert into pp_buyer_intel (buyer_key, buyer_display, is_entity, purchases, distinct_counties, counties, first_seen, last_seen,
    cash_purchases, financed_purchases, days_since_last, buyer_score, buyer_type, entries, computed_at,
    financing_known, signal_types, is_move_up, sold_entry, sold_at, buyer_stage, why, buyer_confirmed)
  select 'B:'||m.buyer_norm, m.buyer_raw, false, 0, 1, array[m.county], m.sold_at, m.sold_at,
    0, 0, extract(day from (now()-m.sold_at))::int,
    least(100, greatest(0, round(40 + 30 * exp(-1 * extract(epoch from (now()-m.sold_at))/86400.0 / 60))))::int,
    'Just sold, likely buying', array[m.sold_entry], now(),
    false, array['move_up'], true, m.sold_entry, m.sold_at,
    case when m.sold_at > now()-interval '45 days' then 'active' else 'recent' end,
    'Sold by warranty deed '||extract(day from (now()-m.sold_at))::int||' days ago in '||m.county||' County with no later purchase recorded. Move-up or relocation window.',
    true
  from t_moveup m
  where not exists (select 1 from pp_buyer_intel b where b.buyer_key='B:'||m.buyer_norm);

  get diagnostics v = row_count;
  return (select count(*) from pp_buyer_intel);
end $$;
revoke all on function pp_compute_buyers() from public, anon;

-- ---- Agent-captured buyer leads (open house, sign call, referral, sphere, web) ----
create table if not exists pp_buyer_leads (
  id bigserial primary key,
  agent_id text not null,
  name text not null,
  phone text, email text,
  county text, cities text[],
  price_low numeric, price_high numeric,
  beds_min int, timeline_days int,
  financing text check (financing in ('cash','preapproved','needs_lender','unknown')) default 'unknown',
  source text check (source in ('open_house','sign_call','referral','sphere','web','other')) default 'other',
  notes text,
  status text check (status in ('new','working','matched','under_contract','closed','dead')) default 'new',
  created_at timestamptz default now(), updated_at timestamptz default now()
);
create or replace function pp_guard_buyer_lead() returns trigger language plpgsql as $$
begin
  if not exists (select 1 from pp_agents a where a.agent_id=new.agent_id and a.active) then
    raise exception 'buyer lead rejected: agent % is not a registered active agent', new.agent_id; end if;
  if upper(new.name) ~ '\y(TEST|SAMPLE|DEMO|FAKE|DUMMY|LOREM|JOHN DOE|JANE DOE)\y' then
    raise exception 'buyer lead rejected: placeholder name'; end if;
  if new.price_low is not null and new.price_high is not null and new.price_low > new.price_high then
    raise exception 'buyer lead rejected: price range inverted'; end if;
  new.updated_at := now();
  return new;
end $$;
drop trigger if exists pp_guard_buyer_lead on pp_buyer_leads;
create trigger pp_guard_buyer_lead before insert or update on pp_buyer_leads for each row execute function pp_guard_buyer_lead();
alter table pp_buyer_leads enable row level security;
drop policy if exists pp_buyer_leads_own on pp_buyer_leads;
create policy pp_buyer_leads_own on pp_buyer_leads for all to authenticated
  using (agent_id in (select agent_id from pp_agents where user_id=auth.uid()))
  with check (agent_id in (select agent_id from pp_agents where user_id=auth.uid()));
grant select, insert, update on pp_buyer_leads to authenticated;
grant usage, select on sequence pp_buyer_leads_id_seq to authenticated;

-- ---- Queue view + matching ----
create or replace view pp_buyer_queue as
  select * from pp_buyer_intel order by buyer_score desc, last_seen desc;
grant select on pp_buyer_queue to authenticated;
grant select on pp_buyer_signals to authenticated;

create or replace function pp_buyers_for_seller(p_entity_key text) returns table (
  buyer_key text, buyer_display text, buyer_type text, buyer_score int, match_reason text, is_captured boolean)
language sql stable security definer set search_path=public,pg_temp as $$
  with s as (select county, assessed_value from pp_conviction_queue where entity_key=p_entity_key limit 1)
  select b.buyer_key, b.buyer_display, b.buyer_type, b.buyer_score,
    concat_ws(' ', 'Same county.',
      case when s.assessed_value is not null and b.value_low is not null and s.assessed_value between b.value_low*0.7 and b.value_high*1.3 then 'Has bought at this value.' end,
      case when b.cash_purchases>0 then 'Cash history.' end,
      case when b.is_move_up then 'Just sold, needs a home.' end), false
  from pp_buyer_intel b, s
  where s.county = any(b.counties) and b.buyer_stage in ('active','recent')
  union all
  select 'L:'||l.id, l.name, 'Captured lead ('||l.source||')', 70,
    concat_ws(' ', 'Agent-captured buyer.', case when s.assessed_value between l.price_low and l.price_high then 'Price range fits.' end, case when l.financing='cash' then 'Cash.' end), true
  from pp_buyer_leads l, s
  where l.status in ('new','working') and (l.county is null or l.county=s.county)
  order by buyer_score desc limit 25 $$;
revoke all on function pp_buyers_for_seller(text) from public, anon;
grant execute on function pp_buyers_for_seller(text) to authenticated;

create or replace function pp_sellers_for_buyer(p_buyer_key text) returns table (
  entity_key text, owner_display text, county text, conviction_score int, assessed_value numeric, match_reason text)
language sql stable security definer set search_path=public,pg_temp as $$
  with b as (select counties, value_low, value_high, cash_purchases, is_move_up from pp_buyer_intel where buyer_key=p_buyer_key limit 1)
  select q.entity_key, q.owner_display, q.county, q.conviction_score, q.assessed_value,
    concat_ws(' ', 'Same county.', case when b.value_low is not null and q.assessed_value between b.value_low*0.7 and b.value_high*1.3 then 'Value fits their history.' end)
  from pp_conviction_queue q, b
  where q.county = any(b.counties) and q.property_confirmed and q.conviction_score>=65
  order by q.conviction_score desc limit 25 $$;
revoke all on function pp_sellers_for_buyer(text) from public, anon;
grant execute on function pp_sellers_for_buyer(text) to authenticated;
