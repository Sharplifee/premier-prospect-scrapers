-- 2026-09-27: pp_compute_convergence had an UPDATE with no WHERE; pg-safeupdate (preloaded for the REST authenticator role)
-- rejected it with 21000, so nightly Enrichment failed Sept 24-27. Fixed with an explicit `where true`.
CREATE OR REPLACE FUNCTION public.pp_compute_convergence(p_diversity_w numeric DEFAULT 6, p_half_life numeric DEFAULT 90, p_cluster_win integer DEFAULT 30, p_cluster_bonus numeric DEFAULT 5)
 RETURNS integer
 LANGUAGE plpgsql
AS $function$
declare v int;
begin
  with raw as (
    select owner_key, signal_type, score, captured_at, raw_owner_name, owner_norm, county,
           tax_total, tax_owed, parcel_serial, id, is_absentee, market_value, txn_type,
           loan_amount, loan_date, loan_released,
           -- DOCUMENT IDENTITY. The same county filing is captured by several
           -- scrapers under different labels (nod-tracker as 'nod', utah-county-nts
           -- as 'nts', the unified scraper as 'trustee_substitution') and re-captured
           -- on every daily run. Before this fix a single Notice of Trustee Sale
           -- produced "6 signal types · ESCALATING · clustered" — 245 of the top 279
           -- leads were ONE document counted ~7 times. Collapse to one row per
           -- (owner, document), keeping the highest-scoring interpretation.
           coalesce(nullif(pp_payload(raw_payload)->>'entry',''),
                    nullif(parcel_serial,''),
                    raw_address, id::text) as doc_id
    from pp_scraper_signals
    where owner_key is not null and not is_legacy
      and not coalesce(is_institutional,false) and score is not null
  ),
  base as (
    select distinct on (owner_key, doc_id) *,
           pp_foreclosure_stage(signal_type) as stage,
           pp_signal_scarcity(signal_type)   as scarcity
    from raw
    order by owner_key, doc_id, score desc, captured_at desc
  ),
  agg as (
    select owner_key as entity_key,
      max(coalesce(raw_owner_name, owner_norm)) as owner_display,
      max(county) as county, max(score) as anchor_score,
      count(distinct signal_type) as distinct_types,
      count(*) as signal_count,                     -- now = distinct DOCUMENTS
      array_agg(distinct signal_type) as signal_types,
      max(tax_total/nullif(tax_owed,0)) as escalation_ratio,
      sum(tax_total) as total_tax_debt,
      count(*) filter (where parcel_serial is not null) as parcels_held,
      avg(exp(-1*extract(epoch from (now()-captured_at))/86400.0/p_half_life)) as avg_recency,
      -- clustering now requires 2+ DIFFERENT documents inside the window
      (count(*) filter (where captured_at >= now()-(p_cluster_win||' days')::interval) >= 2) as cluster_flag,
      max(captured_at) as last_signal_at, min(captured_at) as first_signal_at,
      (array_agg(id order by score desc))[1:50] as contributing_ids,
      bool_or(coalesce(trim(coalesce(raw_owner_name, owner_norm)),'') <> '') as contactable,
      bool_or(coalesce(is_absentee,false)) as absentee_flag,
      max(market_value) as est_market_value,
      max(stage) as max_stage,
      -- stage span across DIFFERENT documents only
      max(stage) - min(stage) filter (where stage > 0) as stage_span,
      max(scarcity) as scarcity_score,
      sum(loan_amount) filter (where not coalesce(loan_released,false)) as total_loan_amount,
      max(extract(epoch from (now()-loan_date))/86400.0/365.0)
        filter (where not coalesce(loan_released,false)) as oldest_loan_years,
      max(pp_equity_proxy(loan_date, loan_released)) as equity_proxy,
      max(captured_at) filter (
        where signal_type in ('trustee_deed','lis_pendens_release','nod_cancelled','loan_reconveyance')
           or (signal_type in ('comparable_sale','deed_transfer')
               and txn_type in ('arms_length_sale','investor_purchase'))) as resolved_at,
      max(captured_at) filter (where signal_type in
          ('nts','nod','lien_judgment','tax_delinquency','divorce_transfer',
           'trustee_substitution','lis_pendens','probate_deed','death_affidavit','divorce_filing','probate_filing','creditor_suit','civil_property','code_violation','deceased_owner','divorce_homeowner','eviction_landlord','judicial_foreclosure','guardianship')) as last_distress_at,
      bool_or(signal_type='trustee_deed') as had_trustee_deed,
      bool_or(signal_type in ('comparable_sale','deed_transfer')
              and txn_type in ('arms_length_sale','investor_purchase')) as had_sale
    from base group by owner_key
  )
  , parcel_roll as (
    select s.owner_key as entity_key,
           max(s.market_value) as assessed_value,
           (array_agg(s.property_address order by s.market_value desc))[1] as property_address,
           (array_agg(s.mailing_address order by s.market_value desc))[1] as mailing_address,
           bool_or(s.is_absentee) as absentee_verified
    from (
      select s.owner_key, pi.market_value, pi.property_address, pi.mailing_address, pi.is_absentee
      from pp_scraper_signals s join pp_parcel_intel pi on pi.parcel_serial = s.parcel_serial
      where s.owner_key is not null and not s.is_legacy and pi.market_value > 20000
      union
      select pi.owner_key, pi.market_value, pi.property_address, pi.mailing_address, pi.is_absentee
      from pp_parcel_intel pi where pi.owner_key is not null and pi.market_value > 20000
    ) s join lateral (select 1) pi on true
    group by s.owner_key
  ),
  agg2 as (
    select agg.*, pr.assessed_value, pr.property_address, pr.mailing_address, pr.absentee_verified
    from agg left join parcel_roll pr on pr.entity_key = agg.entity_key
  )
  insert into pp_entity_conviction (
    entity_key, entity_kind, owner_display, county, conviction_score, anchor_score,
    distinct_types, signal_count, signal_types, escalation_ratio, total_tax_debt,
    parcels_held, cluster_flag, last_signal_at, first_signal_at, contributing_ids,
    resolved_flag, resolved_reason, contactable, absentee_flag, est_market_value,
    max_stage, stage_span, progressing, scarcity_score,
    total_loan_amount, oldest_loan_years, equity_proxy,
    assessed_value, property_address, mailing_address, absentee_verified, real_equity_pct, computed_at)
  select entity_key,'owner',owner_display,county,
    least(100, greatest(0, round(
        anchor_score
      + p_diversity_w*(distinct_types-1)*coalesce(avg_recency,0)
      + (case when cluster_flag then p_cluster_bonus else 0 end)
      + least(6, greatest(0,(coalesce(escalation_ratio,1)-1)*4))
      + (case when absentee_verified then 7 when absentee_flag then 4 else 0 end)
      + (case when contactable then 3 else -8 end)
      + (case when coalesce(stage_span,0) >= 2 then 8
              when coalesce(stage_span,0) = 1 then 4 else 0 end)
      + (coalesce(scarcity_score,0.4) * 5)
      + (case when equity_proxy >= 60 then 5
              when equity_proxy >= 35 then 2
              when equity_proxy is not null and equity_proxy < 15 then -4
              else 0 end)
      + (case when resolved_at is not null
                   and resolved_at >= coalesce(last_distress_at,'-infinity'::timestamptz)
              then -60 else 0 end)
    )))::int,
    anchor_score, distinct_types, signal_count, signal_types, escalation_ratio,
    total_tax_debt, parcels_held, cluster_flag, last_signal_at, first_signal_at, contributing_ids,
    (resolved_at is not null and resolved_at >= coalesce(last_distress_at,'-infinity'::timestamptz)),
    case when had_trustee_deed then 'trustee deed recorded — foreclosure completed'
         when had_sale then 'arms-length sale recorded after distress' end,
    contactable, absentee_flag, est_market_value,
    max_stage, stage_span, (coalesce(stage_span,0) >= 1), scarcity_score,
    total_loan_amount, round(oldest_loan_years,1), equity_proxy,
    assessed_value, property_address, mailing_address, absentee_verified,
    case when assessed_value > 0 and total_loan_amount is not null
         then least(100, greatest(0, round(100 * (assessed_value - total_loan_amount) / assessed_value))) end,
    now()
  from agg2
  on conflict (entity_key) do update set
    owner_display=excluded.owner_display, county=excluded.county,
    conviction_score=excluded.conviction_score, anchor_score=excluded.anchor_score,
    distinct_types=excluded.distinct_types, signal_count=excluded.signal_count,
    signal_types=excluded.signal_types, escalation_ratio=excluded.escalation_ratio,
    total_tax_debt=excluded.total_tax_debt, parcels_held=excluded.parcels_held,
    cluster_flag=excluded.cluster_flag, last_signal_at=excluded.last_signal_at,
    first_signal_at=excluded.first_signal_at, contributing_ids=excluded.contributing_ids,
    resolved_flag=excluded.resolved_flag, resolved_reason=excluded.resolved_reason,
    contactable=excluded.contactable, absentee_flag=excluded.absentee_flag,
    est_market_value=excluded.est_market_value, max_stage=excluded.max_stage,
    stage_span=excluded.stage_span, progressing=excluded.progressing,
    scarcity_score=excluded.scarcity_score, total_loan_amount=excluded.total_loan_amount,
    oldest_loan_years=excluded.oldest_loan_years, equity_proxy=excluded.equity_proxy,
    assessed_value=excluded.assessed_value, property_address=excluded.property_address,
    mailing_address=excluded.mailing_address, absentee_verified=excluded.absentee_verified,
    real_equity_pct=excluded.real_equity_pct,
    computed_at=now();
  get diagnostics v = row_count;
  -- PROPERTY CONFIRMATION GATE (Sept 23): a lead reaches the top tier only when a document or record ties the
  -- person to real property — a recorder filing, a parcel number, a tax-roll or assessor match. A name on a court
  -- calendar alone is a candidate awaiting the nightly assessor resolution, not a seller lead; it is capped at 65.
  update pp_entity_conviction c set property_confirmed = (
      exists (select 1 from pp_parcel_intel pi where pi.owner_key = c.entity_key)
   or exists (select 1 from pp_scraper_signals s where s.owner_key = c.entity_key and not s.is_legacy
              and (s.parcel_serial is not null or s.source_slug not in ('utah-court-calendars','obituaries-enrichment','obituaries-utah'))))
   where true;   -- explicit: the REST role runs pg-safeupdate, which rejects an UPDATE with no WHERE
  update pp_entity_conviction set conviction_score = least(conviction_score, 65) where not property_confirmed;
  -- MARKET-CONFIRMED WEIGHT: recorded liens/judgments are followed by an arms-length sale 21.8% of the time in our own
  -- backtest, more than any other layer; give them the weight the evidence shows.
  update pp_entity_conviction set conviction_score = least(100, conviction_score + 12)
   where property_confirmed and 'lien_judgment' = any(signal_types) and not resolved_flag;
  -- prune stale entities: no live, non-institutional, keyed signal remains
  delete from pp_entity_conviction c
   where not exists (select 1 from pp_scraper_signals s
                     where s.owner_key = c.entity_key and not s.is_legacy
                       and not coalesce(s.is_institutional,false) and s.score is not null);
  return v;
end $function$
