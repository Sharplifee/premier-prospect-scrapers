-- Nov 13 2026 (applied live): Summit parcels are stored in pp_parcel_intel as 'SUMMIT:<serial>' while recorder filings
-- carry the bare serial, so a death affidavit that names its parcel never linked to that parcel's assessor value or
-- mailing address unless the lead's own name matched the owner. pp_compute_convergence now also joins
-- pi.parcel_serial = 'SUMMIT:' || s.parcel_serial (Summit only, non-institutional signals) in parcel_roll and in the
-- mailing_confirmed check. Effect on the top tier: Summit leads with an assessor value 118 -> 139 of 168.
do $$
declare d text := pg_get_functiondef('pp_compute_convergence(numeric,numeric,integer,numeric)'::regprocedure); n text;
begin
  if d like '%''SUMMIT:'' || s.parcel_serial%' then return; end if;
  n := replace(d,
'      where s.owner_key is not null and not s.is_legacy and pi.market_value > 20000
      union
      select pi.owner_key,',
'      where s.owner_key is not null and not s.is_legacy and pi.market_value > 20000
      union
      select s.owner_key, pi.market_value, pi.property_address, pi.mailing_address, pi.is_absentee
      from pp_scraper_signals s join pp_parcel_intel pi on pi.parcel_serial = ''SUMMIT:'' || s.parcel_serial
      where s.county = ''Summit'' and s.owner_key is not null and not s.is_legacy and not s.is_institutional and pi.market_value > 20000
      union
      select pi.owner_key,');
  n := replace(n,
'      exists (select 1 from pp_scraper_signals s join pp_parcel_intel pi on pi.parcel_serial = s.parcel_serial
              where s.owner_key = c.entity_key and not s.is_legacy and pi.mailing_address = c.mailing_address)',
'      exists (select 1 from pp_scraper_signals s join pp_parcel_intel pi
                on pi.parcel_serial = s.parcel_serial or (s.county = ''Summit'' and pi.parcel_serial = ''SUMMIT:'' || s.parcel_serial)
              where s.owner_key = c.entity_key and not s.is_legacy and pi.mailing_address = c.mailing_address)');
  execute n;
end $$;
