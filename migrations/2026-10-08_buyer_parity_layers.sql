-- Oct 8 2026: buyer parity. 1031 window, divorcing homeowners, landlords; deadlines; overview + evidence + integrity checks;
-- name-format-aware transfer classifier (trust funding and family deeds are not sales).
-- Oct 8 2026: name-format-aware transfer classifier. The old one compared everything before the first
-- comma, which works for Utah County ("LAST, FIRST & FIRST2") but not Wasatch/Summit ("LAST FIRST M, LAST2 FIRST2"),
-- so "DELANEY GALE PORTER -> DELANEY GALE PORTER TR" (funding a living trust) counted as a sale.
create or replace function pp_name_parts(t text) returns text[] language sql immutable as $$
  select coalesce(array_agg(btrim(p)) filter (where btrim(p) <> ''), '{}')
  from regexp_split_to_table(upper(coalesce(t,'')),
         case when upper(coalesce(t,'')) ~ '^\s*[A-Z''\-]+\s*,' then '\s*(&|;)\s*' else '\s*(,|&|;)\s*' end) p $$;
create or replace function pp_name_heads(t text) returns text[] language sql immutable as $$
  select coalesce(array_agg(distinct h), '{}') from (
    select regexp_replace(split_part(btrim(case when upper(coalesce(t,'')) ~ '^\s*[A-Z''\-]+\s*,'
                                                then case when p like '%,%' then split_part(p, ',', 1) end
                                                else p end), ' ', 1), '[^A-Z]', '', 'g') as h
    from unnest(pp_name_parts(t)) p) x
  where length(h) >= 3 and h !~ '^(THE|TRUST|TRUSTEE|TRUSTEES|TEE|TR|LLC|INC|CORP|ESTATE|JT|JR|SR|III|AND|OF|FAMILY|LIVING|REVOCABLE|CO|BY|ET|AL|WHOM|UTAH|AMERICAN|FIRST|NORTH|SOUTH|EAST|WEST|PARK|CITY|MOUNTAIN|SUMMIT|WASATCH)$' $$;
create or replace function public.pp_txn_type(grantor text, grantee text) returns text language plpgsql immutable as $function$
declare g text := upper(coalesce(grantor,'')); e text := upper(coalesce(grantee,''));
        shared boolean; ent text := '\y(LLC|L L C|INC|CORP|LP|LLP|LTD|COMPANY|HOLDINGS|INVESTMENTS|PROPERTIES)\y';
begin
  if g = '' or e = '' then return 'unknown'; end if;
  shared := pp_name_heads(g) && pp_name_heads(e);
  -- spouse removed: same family, a couple on the grantor side, one person (not a trust) on the grantee side
  if shared and cardinality(pp_name_parts(g)) >= 2 and cardinality(pp_name_parts(e)) = 1 and e !~ '\y(TR|TEE|TRUSTEE|TRUSTEES|TRUST)\y' then
    return 'spouse_removed';
  end if;
  -- same family or into their own trust / entity: not a sale
  if shared then return 'family_transfer'; end if;
  -- person selling to a company = investor purchase (still a genuine sale)
  if e ~ ent and g !~ ent then return 'investor_purchase'; end if;
  return 'arms_length_sale';
end $function$;

update pp_scraper_signals set txn_type = pp_txn_type(pp_payload(raw_payload)->>'grantor', pp_payload(raw_payload)->>'grantee') where signal_type in ('deed_transfer','comparable_sale') and not is_legacy and pp_payload(raw_payload)->>'grantor' is not null and txn_type is distinct from pp_txn_type(pp_payload(raw_payload)->>'grantor', pp_payload(raw_payload)->>'grantee');

alter table pp_buyer_intel add column if not exists deadline_at timestamptz;
alter table pp_buyer_intel add column if not exists deadline_label text;
create or replace function pp_x1031_score(p_sold timestamptz) returns int language sql stable as $$
  select case when p_sold > now()-interval '45 days'
              then 80 + round(8 * least(45, extract(epoch from now()-p_sold)/86400.0) / 45)
              else 78 - round(23 * least(135, extract(epoch from now()-p_sold)/86400.0 - 45) / 135) end::int $$;
create or replace function pp_x1031_why(p_county text, p_entry text, p_sold timestamptz) returns text language sql immutable as $$
  select 'Investor sale: sold '||p_county||' County entry #'||p_entry||' on '||to_char(p_sold,'Mon FMDD')
    ||' and has bought nothing since. If it is a 1031 exchange, the replacement must be named by '
    ||to_char(p_sold + interval '45 days','Mon FMDD')||' and closed by '||to_char(p_sold + interval '180 days','Mon FMDD, YYYY')||'.' $$;

create or replace function pp_hearing_ts(p text) returns timestamptz language plpgsql immutable as $$
begin
  if p is null or p !~ '^\d{1,2}/\d{1,2}/\d{4}' then return null; end if;
  return (to_timestamp(p, 'MM/DD/YYYY HH12:MI AM')::timestamp) at time zone 'America/Denver';
exception when others then
  begin return (to_date(split_part(p,' ',1), 'MM/DD/YYYY')::timestamp) at time zone 'America/Denver';
  exception when others then return null; end;
end $$;

-- recording dates are stored as the UTC calendar date. 1031 deadlines are calendar days: end of day 45 / day 180 in Utah time, counted from the recording date
create or replace function pp_x1031_due(p_sold timestamptz, p_days int) returns timestamptz language sql immutable as $$
  select ((((p_sold at time zone 'UTC')::date + p_days)::timestamp + interval '23 hours 59 minutes') at time zone 'America/Denver') $$;
create or replace function pp_x1031_score(p_sold timestamptz) returns int language sql stable as $$
  select case when pp_x1031_due(p_sold,45) > now()
              then 80 + round(8 * least(45, extract(epoch from now()-p_sold)/86400.0) / 45)
              else 78 - round(23 * greatest(0, least(135, extract(epoch from now()-p_sold)/86400.0 - 45)) / 135) end::int $$;
create or replace function pp_x1031_why(p_county text, p_entry text, p_sold timestamptz) returns text language sql stable as $$
  select 'Investor sale: sold '||p_county||' County entry #'||p_entry||' on '||to_char(p_sold at time zone 'UTC','Mon FMDD')
    ||' and has bought nothing since. If it is a 1031 exchange, the replacement must be named by '
    ||to_char(pp_x1031_due(p_sold,45) at time zone 'America/Denver','Mon FMDD')||' and closed by '||to_char(pp_x1031_due(p_sold,180) at time zone 'America/Denver','Mon FMDD, YYYY')||'.' $$;

CREATE OR REPLACE FUNCTION public.pp_compute_buyers()
 RETURNS integer
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare v int;
begin
  perform set_config('statement_timeout','300000', true);
  delete from pp_buyer_signals where true;
  delete from pp_buyer_intel where true;

  -- fractional and timeshare units change hands several times a year (Escala, Marriott Mountainside): not home buyers or sellers
  create temp table t_frac on commit drop as
  select parcel_serial from pp_scraper_signals
  where not is_legacy and parcel_serial is not null and upper(coalesce(pp_payload(raw_payload)->>'koi','')) in ('WD','SP WD','WARRANTY DEED','SPECIAL WARRANTY DEED')
    and captured_at > now() - interval '365 days'
  group by parcel_serial having count(*) >= 3;

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
      -- a deed into your own trust, or between family, is not a purchase
      and pp_txn_type(pp_payload(raw_payload)->>'grantor', pp_payload(raw_payload)->>'grantee') not in ('family_transfer','spouse_removed')
      and upper(coalesce(pp_payload(raw_payload)->>'grantor','')||' '||coalesce(pp_payload(raw_payload)->>'grantee','')) !~ '(WESTGATE|MARRIOTT|HYATT|HILTON|WYNDHAM|ESCALA|VACATION|TIMESHARE|INTERVAL OWNERS|DIAMOND RESORTS|BLUEGREEN)'
      and coalesce(parcel_serial,'') not in (select parcel_serial from t_frac)
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
      and pp_txn_type(pp_payload(raw_payload)->>'grantor', pp_payload(raw_payload)->>'grantee') not in ('family_transfer','spouse_removed')
      and upper(coalesce(pp_payload(raw_payload)->>'grantor','')||' '||coalesce(pp_payload(raw_payload)->>'grantee','')) !~ '(WESTGATE|MARRIOTT|HYATT|HILTON|WYNDHAM|ESCALA|VACATION|TIMESHARE|INTERVAL OWNERS|DIAMOND RESORTS|BLUEGREEN)'
      and coalesce(parcel_serial,'') not in (select parcel_serial from t_frac)
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

  -- Oct 8 2026 (Boss): a person who already bought their one house is not a buyer lead. Keep only people still
  -- in the market: just sold with no purchase since, repeat buyers (2+), and investors (entity or hard-money financed).
  delete from pp_buyer_signals s using pp_buyer_intel b
   where s.buyer_key=b.buyer_key and not b.is_move_up and b.purchases < 2
     and not coalesce(b.is_entity,false) and coalesce(b.private_financed,0)=0;
  delete from pp_buyer_intel b
   where not b.is_move_up and b.purchases < 2 and not coalesce(b.is_entity,false) and coalesce(b.private_financed,0)=0;

  -- ==== Oct 8 2026 buyer parity: predictive layers that need no purchase on record ====

  -- 4. 1031 exchange window. An investor (entity, repeat buyer, or hard-money borrower) sold by
  --    warranty deed in the last 180 days and has bought nothing since. If the sale is a 1031,
  --    the replacement must be named by day 45 and closed by day 180: a hard, dated pressure point.
  create temp table t_x1031 on commit drop as
  select seller_norm as buyer_norm, max(seller_raw) as buyer_raw, max(county) as county,
         max(captured_at) as sold_at, (array_agg(entry order by captured_at desc))[1] as sold_entry,
         count(distinct entry) as sales, bool_or(is_ent) as is_ent,
         (array_agg(parcel_serial order by captured_at desc) filter (where parcel_serial is not null))[1] as parcel_serial
  from (
    select pp_norm(g) as seller_norm, g as seller_raw, entry, county, captured_at, parcel_serial,
           upper(g) ~ '\y(LLC|L L C|INC|LP|LLLP|LTD|CORP|CORPORATION|COMPANY|HOLDINGS|PROPERTIES|PROPERTY|INVESTMENTS?|INVESTORS?|CAPITAL|VENTURES|PARTNERS|PARTNERSHIP|GROUP|REALTY|REAL ESTATE|RENTALS?|ASSETS|EQUITIES|ENTERPRISES)\y' as is_ent
    from (
      select pp_payload(raw_payload)->>'grantor' as g, pp_payload(raw_payload)->>'grantee' as gee,
             regexp_replace(coalesce(pp_payload(raw_payload)->>'entry', raw_address),'[[:space:] ]+',' ','g') as entry,
             county, pp_rec_date(pp_payload(raw_payload), captured_at) as captured_at, parcel_serial
      from pp_scraper_signals
      where not is_legacy and upper(coalesce(pp_payload(raw_payload)->>'koi','')) in ('WD','SP WD','WARRANTY DEED','SPECIAL WARRANTY DEED')
    ) z
    where pp_norm(g) is not null and captured_at > now() - interval '180 days'
      and pp_txn_type(g, gee) not in ('family_transfer','spouse_removed')
      and upper(g||' '||coalesce(gee,'')) !~ '(WESTGATE|MARRIOTT|HYATT|HILTON|WYNDHAM|ESCALA|VACATION|TIMESHARE|INTERVAL OWNERS|DIAMOND RESORTS|BLUEGREEN)'
      and coalesce(parcel_serial,'') not in (select parcel_serial from t_frac)
      and upper(g) !~ '\y(BANK|CREDIT UNION|TITLE|ESCROW|MORTGAGE|LENDING|LOAN|SERVICING|FEDERAL|SECRETARY|HOUSING|FANNIE|FREDDIE|HUD|VETERANS|CITY|TOWN|COUNTY|STATE OF|UNITED STATES|SCHOOL|DISTRICT|CHURCH|LATTER|BISHOP|HOMES|HOMEBUILDERS?|BUILDERS?|CONSTRUCTION|COMMUNITIES|DEVELOPMENT|DEVELOPERS?|OPENDOOR|OFFERPAD|RELOCATION|CARTUS|SIRVA|TRUST|TRUSTEE|TRUSTEES|TEE|TR|ESTATE|DECEASED|DEC|PERSONAL REP|PERS REP|ET AL|UNIVERSITY|HOSPITAL|POWER|WATER|IRRIGATION|RAILROAD|ASSOCIATION|FOUNDATION|HOA|CONDOMINIUM|OWNERS|LAND HOLDINGS|TOLL|PULTE|HORTON|LENNAR|FIELDSTONE|IVORY|RICHMOND|WOODSIDE|SFR|EREI|INVITATION|TRICON|PROGRESS RESIDENTIAL|AMERICAN HOMES)\y'
  ) s
  -- a developer selling off lots is not exchanging: more than 3 sales in 180 days is inventory, not an investment sale
  where s.seller_norm not in (select pp_norm(pp_payload(raw_payload)->>'grantor') from pp_scraper_signals
                              where not is_legacy and upper(coalesce(pp_payload(raw_payload)->>'koi','')) in ('WD','SP WD','WARRANTY DEED','SPECIAL WARRANTY DEED')
                                and captured_at > now() - interval '180 days' and pp_payload(raw_payload)->>'grantor' is not null
                              group by 1 having count(distinct coalesce(pp_payload(raw_payload)->>'entry', raw_address)) > 3)
    and (is_ent
         or s.seller_norm in (select buyer_norm from t_purch group by buyer_norm having count(distinct entry) >= 2)
         or s.seller_norm in (select buyer_norm from t_fin where lender_class = 'private'))
    and not exists (select 1 from t_purch p where p.buyer_norm = s.seller_norm and p.captured_at >= s.captured_at)
  group by seller_norm;

  insert into pp_buyer_signals(buyer_key,buyer_display,signal_type,county,entry,observed_at,detail)
  select 'B:'||buyer_norm, buyer_raw, 'exchange_window', county, sold_entry, sold_at,
         jsonb_build_object('basis','investor sold by warranty deed; no replacement purchase recorded since',
                            'identify_by', (pp_x1031_due(sold_at,45) at time zone 'America/Denver')::date, 'close_by', (pp_x1031_due(sold_at,180) at time zone 'America/Denver')::date,
                            'parcel_serial', parcel_serial)
  from t_x1031;

  update pp_buyer_intel b set
    signal_types = array_append(array_remove(b.signal_types,'exchange_window'),'exchange_window'),
    buyer_score  = greatest(b.buyer_score, pp_x1031_score(x.sold_at)),
    buyer_type   = 'Investor sold, 1031 window',
    sold_entry = x.sold_entry, sold_at = x.sold_at,
    deadline_at = case when pp_x1031_due(x.sold_at,45) > now() then pp_x1031_due(x.sold_at,45) else pp_x1031_due(x.sold_at,180) end,
    deadline_label = case when pp_x1031_due(x.sold_at,45) > now() then 'Name replacement by' else 'Close replacement by' end,
    buyer_stage = 'active',
    why = pp_x1031_why(x.county, x.sold_entry, x.sold_at) || ' ' || coalesce(b.why,'')
  from t_x1031 x where b.buyer_key = 'B:'||x.buyer_norm;

  insert into pp_buyer_intel (buyer_key, buyer_display, is_entity, purchases, distinct_counties, counties, first_seen, last_seen,
    cash_purchases, financed_purchases, days_since_last, buyer_score, buyer_type, entries, computed_at,
    financing_known, signal_types, is_move_up, sold_entry, sold_at, buyer_stage, why, buyer_confirmed, deadline_at, deadline_label)
  select 'B:'||x.buyer_norm, x.buyer_raw, x.is_ent, 0, 1, array[x.county], x.sold_at, x.sold_at,
    0, 0, extract(day from (now()-x.sold_at))::int, pp_x1031_score(x.sold_at), 'Investor sold, 1031 window', array[x.sold_entry], now(),
    false, array['exchange_window'], false, x.sold_entry, x.sold_at, 'active', pp_x1031_why(x.county, x.sold_entry, x.sold_at), true,
    case when pp_x1031_due(x.sold_at,45) > now() then pp_x1031_due(x.sold_at,45) else pp_x1031_due(x.sold_at,180) end,
    case when pp_x1031_due(x.sold_at,45) > now() then 'Name replacement by' else 'Close replacement by' end
  from t_x1031 x
  where not exists (select 1 from pp_buyer_intel b where b.buyer_key = 'B:'||x.buyer_norm);

  -- 5. Divorcing homeowners. A divorce case where at least one spouse owns a home: the house is
  --    usually sold or bought out, and each spouse needs somewhere to live. Both spouses are leads.
  --    Utah County: matched to a parcel by the court scraper. Salt Lake: matched by the assessor resolver.
  create temp table t_div on commit drop as
  select distinct on (spouse_norm) * from (
    select pp_norm(sp) as spouse_norm, initcap(lower(sp)) as spouse_raw, d.*
    from (
      select s.county, s.captured_at, pp_payload(s.raw_payload)->>'entry' as case_entry,
             pp_payload(s.raw_payload)->>'parties' as parties, pp_payload(s.raw_payload)->>'courthouse' as courthouse,
             coalesce((pp_payload(s.raw_payload)->>'household')::boolean, false) as household,
             nullif(pp_payload(s.raw_payload)->>'hearing','') as hearing_txt,
             coalesce(s.parcel_serial, pi.parcel_serial) as parcel_serial, pi.market_value, pi.property_address
      from pp_scraper_signals s
      left join lateral (select parcel_serial, market_value, property_address from pp_parcel_intel x
                         where (s.parcel_serial is not null and x.parcel_serial = s.parcel_serial and x.county = s.county)
                            or (s.parcel_serial is null and x.owner_key = s.owner_key)
                         order by market_value desc nulls last limit 1) pi on true
      where not s.is_legacy and s.captured_at > now() - interval '180 days'
        and (s.signal_type = 'divorce_homeowner'
             or (s.signal_type = 'divorce_filing' and s.county <> 'Utah'
                 and exists (select 1 from pp_parcel_intel x where x.owner_key = s.owner_key)))
    ) d, regexp_split_to_table(d.parties, '\s+(?:and|AND|And)\s+') as sp
    where pp_norm(sp) is not null and length(pp_norm(sp)) > 4
  ) q order by spouse_norm, captured_at desc;

  insert into pp_buyer_signals(buyer_key,buyer_display,signal_type,county,entry,observed_at,amount,detail,source_slug)
  select 'B:'||spouse_norm, spouse_raw, 'divorce', county, case_entry, captured_at, market_value,
         jsonb_build_object('basis','divorce case with a home on title', 'parties', parties, 'courthouse', courthouse,
                            'parcel_serial', parcel_serial, 'property_address', property_address, 'hearing', hearing_txt,
                            'marital_home', household), 'utah-court-calendars'
  from t_div;

  update pp_buyer_intel b set
    signal_types = array_append(array_remove(b.signal_types,'divorce'),'divorce'),
    buyer_score = greatest(b.buyer_score, 74 + case when d.household then 6 else 0 end),
    deadline_at = coalesce(b.deadline_at, case when pp_hearing_ts(d.hearing_txt) > now() then pp_hearing_ts(d.hearing_txt) end),
    deadline_label = coalesce(b.deadline_label, case when pp_hearing_ts(d.hearing_txt) > now() then 'Divorce hearing' end),
    why = coalesce(b.why,'') || ' Also in a divorce case (' || coalesce(d.courthouse,'Utah court') || ').'
  from t_div d where b.buyer_key = 'B:'||d.spouse_norm;

  insert into pp_buyer_intel (buyer_key, buyer_display, is_entity, purchases, distinct_counties, counties, first_seen, last_seen,
    cash_purchases, financed_purchases, days_since_last, buyer_score, buyer_type, entries, computed_at,
    financing_known, signal_types, is_move_up, buyer_stage, why, buyer_confirmed, deadline_at, deadline_label,
    value_low, value_high, parcels_known)
  select 'B:'||d.spouse_norm, d.spouse_raw, false, 0, 1, array[d.county], d.captured_at, d.captured_at,
    0, 0, extract(day from (now()-d.captured_at))::int, 74 + case when d.household then 6 else 0 end,
    'Divorcing homeowner', array[d.case_entry], now(), false, array['divorce'], false, 'active',
    'Divorce case '||d.case_entry||' in '||coalesce(d.courthouse, d.county||' County')||' between '||initcap(lower(d.parties))||'. '
      ||case when d.household then 'The marital home is on title to both' else 'One spouse is on title to a home' end
      ||coalesce(' (parcel '||d.parcel_serial||coalesce(', assessed '||to_char(d.market_value,'FM$999,999,999'),'')||')','')
      ||'. It is usually sold or bought out, and each spouse needs a new place to live.'
      ||case when pp_hearing_ts(d.hearing_txt) > now() then ' Next hearing '||d.hearing_txt||'.' when d.hearing_txt is not null then ' Last hearing '||split_part(d.hearing_txt,' ',1)||'.' else '' end,
    false,
    case when pp_hearing_ts(d.hearing_txt) > now() then pp_hearing_ts(d.hearing_txt) end, case when pp_hearing_ts(d.hearing_txt) > now() then 'Divorce hearing' end,
    d.market_value, d.market_value, case when d.parcel_serial is not null then 1 else 0 end
  from t_div d
  where not exists (select 1 from pp_buyer_intel b where b.buyer_key = 'B:'||d.spouse_norm);

  -- 6. Small landlords. Filing an eviction as the plaintiff proves the person or company owns rental
  --    property. Rental owners are the investors who buy the next house. Apartment complexes and
  --    management companies are excluded: they are operators, not buyers of single homes.
  create temp table t_land on commit drop as
  select pp_norm(landlord) as buyer_norm, max(initcap(lower(landlord))) as buyer_raw, count(distinct entry) as cases,
         array_agg(distinct county) as counties, max(captured_at) as last_at, min(captured_at) as first_at,
         (array_agg(entry order by captured_at desc))[1:6] as entries,
         bool_or(upper(landlord) ~ '\y(LLC|L L C|INC|LP|LTD|CORP|COMPANY|HOLDINGS|PROPERTIES|INVESTMENTS?|CAPITAL|VENTURES|PARTNERS|GROUP|RENTALS?|ENTERPRISES)\y') as is_ent
  from (
    select btrim(regexp_replace(split_part(p->>'parties', ' vs. ', 1), '\s+et al\.?$', '', 'i')) as landlord,
           p->>'entry' as entry, county, captured_at
    from (select pp_payload(raw_payload) as p, county, captured_at from pp_scraper_signals
          where not is_legacy and signal_type = 'eviction_landlord' and captured_at > now() - interval '180 days') a
  ) b
  where pp_norm(landlord) is not null and length(landlord) > 3
    -- named complexes ("Seasons Pebble Creek", "Grace Mary Manor", "Plaza") have no entity suffix and no person shape
    and (upper(landlord) ~ '\y(LLC|L L C|INC|LP|LTD|CORP|COMPANY|HOLDINGS|INVESTMENTS?|VENTURES|PARTNERS|RENTALS?|ENTERPRISES)\y'
         or (landlord ~ '^\S+(\s+\S+){1,3}$'
             and upper(landlord) !~ '\y(MANOR|CREEK|ESTATES?|PLAZA|STUDIOS?|SQUARED|SEASONS|APARTMEN\w*|HAVEN|DELL|PARK|TERRACE|COURT|RIDGE|VIEW|GROVE|MEADOWS?|HILLS?|LAKE|PINES|OAKS|WILLOWS|CANYON|TOWNE|CENTER|CENTRE|HOUSE|HALL|LODGE|SUITES|PROPERTIES|PROPERTY|HOMES|GATE|GATEWAY|CLUB|COTTAGES?|BLUFFS?|SPRINGS?|PEAKS?|VISTA|LANE|CIRCLE|WAY|STREET|AVENUE|UNITS?|ON|ALTITUDE|SANDALWOOD|GREENPRINT|REALTY|MANAG\w*|PLLC)\y'))
    and upper(landlord) !~ '\y(REALTY|MANAG\w*|PLLC)\y'
    and upper(landlord) !~ '\y(APARTMENTS?|APTS|LIVING|RESIDENCES?|RESIDENTIAL|STATION|COMMUNITIES|COMMUNITY|VILLAGE|VILLAS?|TOWNHOMES|TOWNHOUSES|LOFTS|FLATS|COMMONS|MANAGEMENT|MGMT|AUTHORITY|SENIOR|TOWERS?|GARDENS?|MOBILE|MANUFACTURED|HOUSING|AT|THE|LANDING|CROSSING|POINTE|PLACE|SQUARE|HEIGHTS|BANK|CREDIT UNION|STORAGE|HOTEL|MOTEL|INN|UNIVERSITY|CHURCH)\y'
  group by 1;

  insert into pp_buyer_signals(buyer_key,buyer_display,signal_type,county,entry,observed_at,detail,source_slug)
  select 'B:'||l.buyer_norm, l.buyer_raw, 'landlord', l.counties[1], e, l.last_at,
         jsonb_build_object('basis','filed an eviction as landlord: owns rental property', 'cases', l.cases), 'utah-court-calendars'
  from t_land l, unnest(l.entries) e;

  update pp_buyer_intel b set
    signal_types = array_append(array_remove(b.signal_types,'landlord'),'landlord'),
    buyer_score = greatest(b.buyer_score, 56 + least(12, (l.cases-1)*4)),
    why = coalesce(b.why,'') || ' Owns rental property (' || l.cases || ' eviction case' || case when l.cases=1 then '' else 's' end || ' as landlord).'
  from t_land l where b.buyer_key = 'B:'||l.buyer_norm;

  insert into pp_buyer_intel (buyer_key, buyer_display, is_entity, purchases, distinct_counties, counties, first_seen, last_seen,
    cash_purchases, financed_purchases, days_since_last, buyer_score, buyer_type, entries, computed_at,
    financing_known, signal_types, is_move_up, buyer_stage, why, buyer_confirmed)
  select 'B:'||l.buyer_norm, l.buyer_raw, l.is_ent, 0, cardinality(l.counties), l.counties, l.first_at, l.last_at,
    0, 0, extract(day from (now()-l.last_at))::int, 56 + least(12, (l.cases-1)*4),
    case when l.cases >= 3 then 'Landlord, several rentals' else 'Landlord (rental owner)' end, l.entries, now(),
    false, array['landlord'], false, case when l.last_at > now()-interval '45 days' then 'active' else 'recent' end,
    'Filed '||l.cases||' eviction case'||case when l.cases=1 then '' else 's' end||' as the landlord in '||array_to_string(l.counties,', ')
      ||' County, so they own rental property. Rental owners buy the next rental. Also a possible tired-landlord seller.',
    false
  from t_land l
  where not exists (select 1 from pp_buyer_intel b where b.buyer_key = 'B:'||l.buyer_norm);

  get diagnostics v = row_count;
  return (select count(*) from pp_buyer_intel);
end $function$
;
CREATE OR REPLACE FUNCTION public.pp_integrity_check()
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare f jsonb := '[]'::jsonb; c jsonb := '{}'::jsonb; n bigint; t text; begin
  select count(*) into n from pp_scraper_signals where not is_legacy and signal_type='nts'; c := c || jsonb_build_object('live_nts', n); if n>0 then f := f || jsonb_build_object('check','no fake sale notices','count',n); end if;
  select count(*) into n from pp_agents where email ~* '@example\.|\.test$'; c := c || jsonb_build_object('test_agents', n); if n>0 then f := f || jsonb_build_object('check','no test agents','count',n); end if;
  select count(*) into n from pp_outcome_events e where not exists (select 1 from pp_agents a where a.user_id::text=e.actor or a.agent_id=e.actor); c := c || jsonb_build_object('orphan_outcomes', n); if n>0 then f := f || jsonb_build_object('check','every outcome has a real agent','count',n); end if;
  select count(*) into n from pp_lead_lifecycle l where not exists (select 1 from pp_outcome_events e where e.entity_key=l.entity_key); c := c || jsonb_build_object('lifecycle_without_events', n); if n>0 then f := f || jsonb_build_object('check','lifecycle rows have events','count',n); end if;
  select count(*) into n from pp_scraper_signals where not is_legacy and raw_owner_name ~* '\m(TEST|SAMPLE|DEMO|FAKE|DUMMY|PLACEHOLDER|LOREM)\M'; c := c || jsonb_build_object('placeholder_names', n); if n>0 then f := f || jsonb_build_object('check','no placeholder names','count',n); end if;
  select count(*) into n from pp_scraper_signals where not is_legacy and source_slug in ('slco-recorder-live','comparable-sales-slco') and county<>'Utah'; c := c || jsonb_build_object('mislabelled_county', n); if n>0 then f := f || jsonb_build_object('check','Utah-endpoint rows labelled Utah','count',n); end if;
  select count(*) into n from pp_scraper_signals where not is_legacy and signal_type='nod' and county='Utah' and pp_payload(raw_payload)->>'koi' not in ('ND'); c := c || jsonb_build_object('utah_nod_wrong_kind', n); if n>0 then f := f || jsonb_build_object('check','Utah NOD rows are kind ND','count',n); end if;
  select count(*) into n from pp_scraper_signals where not is_legacy and signal_type='divorce_filing' and (raw_payload::text ilike '%custody%' or raw_payload::text ilike '%paternity%' or raw_payload::text ilike '%protective%'); c := c || jsonb_build_object('divorce_misclass', n); if n>0 then f := f || jsonb_build_object('check','divorces are divorces','count',n); end if;
  select count(*) into n from pp_conviction_queue q where not exists (select 1 from pp_scraper_signals s where s.owner_key=q.entity_key and not s.is_legacy and not s.is_institutional); c := c || jsonb_build_object('queue_without_signal', n); if n>0 then f := f || jsonb_build_object('check','every queue lead has a live signal','count',n); end if;
  select count(*) into n from pp_conviction_queue where max_stage=5; c := c || jsonb_build_object('stage5', n); if n>0 then f := f || jsonb_build_object('check','no auction stage without a sale notice source','count',n); end if;
  select count(*) into n from pp_conviction_queue where owner_display ~* '\m(BANK|MORTGAGE|SERVICING|LENDING|FEDERAL|CREDIT UNION|MERS)\M'; c := c || jsonb_build_object('lenders_in_queue', n); if n>0 then f := f || jsonb_build_object('check','no lenders in the queue','count',n); end if;
  select count(*) into n from pp_conviction_queue where conviction_score > 65 and not property_confirmed; c := c || jsonb_build_object('unconfirmed_above_65', n); if n>0 then f := f || jsonb_build_object('check','no lead above 65 without a property tie','count',n); end if;
  select count(*) into n from pp_buyer_intel b left join (select 'B:'||pp_norm(pp_payload(s.raw_payload)->>'grantee') k, count(distinct regexp_replace(coalesce(pp_payload(s.raw_payload)->>'entry', s.raw_address),'[[:space:]\u00a0]+',' ','g')) d from pp_scraper_signals s where not s.is_legacy and s.signal_type in ('deed_transfer','comparable_sale') group by 1) g on g.k=b.buyer_key where b.purchases > coalesce(g.d,0); c := c || jsonb_build_object('buyers_overcounted', n); if n>0 then f := f || jsonb_build_object('check','buyer purchases are distinct documents','count',n); end if;
  select count(*) into n from pp_buyer_intel where buyer_display ~* '\m(BANK|MORTGAGE|SERVICING|LENDING|FEDERAL|CREDIT UNION|MERS|TITLE|ESCROW)\M'; c := c || jsonb_build_object('lenders_as_buyers', n); if n>0 then f := f || jsonb_build_object('check','no lenders or title companies as buyers','count',n); end if;
  select count(*) into n from pp_buyer_intel b where not b.is_move_up and not exists (select 1 from pp_buyer_signals s where s.buyer_key=b.buyer_key and s.signal_type in ('purchase','exchange_window','divorce','landlord')); c := c || jsonb_build_object('buyers_without_deed', n); if n>0 then f := f || jsonb_build_object('check','every buyer rests on a recorded deed or court case','count',n); end if;
  select count(*) into n from pp_buyer_intel b where b.is_move_up and (b.sold_entry is null or pp_is_institutional(b.buyer_display) or not exists (select 1 from pp_buyer_signals s where s.buyer_key=b.buyer_key and s.signal_type='move_up')); c := c || jsonb_build_object('moveup_without_sale', n); if n>0 then f := f || jsonb_build_object('check','every move-up buyer rests on a recorded sale','count',n); end if;
  select count(*) into n from pp_buyer_intel b where b.cash_purchases>0 and not (b.counties && (select coalesce(array_agg(county),'{}') from (select county from pp_scraper_signals where not is_legacy and signal_type in ('deed_of_trust','deed_transfer') group by county, source_slug having count(*) filter (where signal_type='deed_of_trust')>0 and count(*) filter (where signal_type='deed_transfer')>0) cov)); c := c || jsonb_build_object('cash_without_coverage', n); if n>0 then f := f || jsonb_build_object('check','cash is only inferred where trust-deed coverage is complete','count',n); end if;
  select count(*) into n from pp_buyer_intel b where b.signal_types && array['exchange_window','divorce','landlord'] and exists (select 1 from unnest(b.signal_types) st where st in ('exchange_window','divorce','landlord') and not exists (select 1 from pp_buyer_signals s where s.buyer_key=b.buyer_key and s.signal_type=st)); c := c || jsonb_build_object('predictive_without_evidence', n); if n>0 then f := f || jsonb_build_object('check','every 1031, divorce and landlord buyer has its evidence row','count',n); end if;
  select count(*) into n from pp_buyer_intel b where 'exchange_window'=any(b.signal_types) and (b.sold_at is null or b.sold_at < now()-interval '181 days' or b.deadline_at is null); c := c || jsonb_build_object('exchange_outside_window', n); if n>0 then f := f || jsonb_build_object('check','1031 buyers sold within 180 days and carry a deadline','count',n); end if;
  select count(*) into n from pp_buyer_intel where deadline_at < now()-interval '1 day'; c := c || jsonb_build_object('buyer_deadline_past', n); if n>0 then f := f || jsonb_build_object('check','buyer deadlines shown are still ahead','count',n); end if;
  select count(*) into n from pp_buyer_intel where buyer_display ~* '\m(TEST|SAMPLE|DEMO|FAKE|DUMMY|LOREM)\M'; c := c || jsonb_build_object('placeholder_buyers', n); if n>0 then f := f || jsonb_build_object('check','no placeholder buyer names','count',n); end if;
  select count(*) into n from pp_buyer_leads l where not exists (select 1 from pp_agents a where a.agent_id=l.agent_id and a.active); c := c || jsonb_build_object('orphan_buyer_leads', n); if n>0 then f := f || jsonb_build_object('check','every captured buyer lead belongs to a registered agent','count',n); end if;
  select count(*) into n from pp_buyer_intel where computed_at < now()-interval '36 hours'; c := c || jsonb_build_object('buyers_stale', n); if n>0 then f := f || jsonb_build_object('check','buyer intelligence recomputed nightly','count',n); end if;
  select string_agg(source_slug, ', ') into t from (
    select source_slug from pp_run_log where run_at>now()-interval '37 days'
      and source_slug <> 'utah-county-tax-delinquency-pdf'  -- county republishes on its own schedule; checked for reachability + parse in integrity_spotcheck.py instead
    group by 1
    having max(run_at) > now()-interval '20 hours'  -- still in the daily run (retired sources drop out)
       and sum(signal_count) filter (where run_at between now()-interval '37 days' and now()-interval '7 days') > 0
       and sum(signal_count) filter (where run_at > now()-interval '7 days') = 0) x;
  c := c || jsonb_build_object('registered_sources_gone_quiet', coalesce(t,'')); if t is not null then f := f || jsonb_build_object('check','registered sources still producing','sources',t); end if;
  insert into pp_integrity_log(passed, failures, checked) values (jsonb_array_length(f)=0, f, c);
  return jsonb_build_object('passed', jsonb_array_length(f)=0, 'failures', f, 'checked', c);
end $function$
;
CREATE OR REPLACE FUNCTION public.pp_app_buyer_overview()
 RETURNS json
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
  select json_build_object(
    'total',        (select count(*) from pp_buyer_intel),
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
    'with_deadline',(select count(*) from pp_buyer_intel where deadline_at > now()),
    'by_county',    (select json_object_agg(c, n) from (select unnest(counties) c, count(*) n from pp_buyer_intel group by 1) x),
    'captured',     (select count(*) from pp_buyer_leads l where l.status in ('new','working','matched') and l.agent_id in (select agent_id from pp_agents where user_id = pp_uid())),
    'computed_at',  (select max(computed_at) from pp_buyer_intel)
  ); $function$
;
CREATE OR REPLACE FUNCTION public.pp_app_buyer_evidence(p_buyer_key text)
 RETURNS json
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
  select coalesce(json_agg(row_to_json(t) order by t.observed_at desc nulls last), '[]'::json) from (
    select signal_type, county, entry, observed_at, amount, detail->>'lender' as lender, detail->>'lender_class' as lender_class,
           detail->>'property_address' as property_address, detail->>'basis' as basis, source_slug,
           detail->>'parties' as parties, detail->>'courthouse' as courthouse, detail->>'hearing' as hearing,
           detail->>'identify_by' as identify_by, detail->>'close_by' as close_by,
           jsonb_strip_nulls(jsonb_build_object('parties',detail->>'parties','courthouse',detail->>'courthouse','identify_by',detail->>'identify_by','close_by',detail->>'close_by','hearing',detail->>'hearing')) as detail
    from pp_buyer_signals where buyer_key = p_buyer_key) t; $function$
;
