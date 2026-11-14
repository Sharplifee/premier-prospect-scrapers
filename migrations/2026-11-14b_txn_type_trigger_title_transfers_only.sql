-- 2026-11-14 txn_type_trigger_title_transfers_only_2026_11_14 (applied as migration 20261114185219)
-- Trigger change only. The backfill of existing rows is NOT part of this migration; Connor decides it separately.
-- The insert trigger ran the grantor/grantee sale classifier on every recorded document that has a grantor and grantee,
-- so non-transfer filings were stored as sales (e.g. Smith Dianne MH-II-48, entry 01260126, an affidavit naming
-- Alan Dean Smith as deceased, was stored txn_type = 'arms_length_sale'). A transaction type only means something
-- for a document that moves title. Everything else gets txn_type = null on insert. Readers (pp_compute_convergence,
-- pp_compute_buyers, pp_backtest_*) only read it for deeds, so scores do not change.

create or replace function public.pp_derive_entity_fields()
 returns trigger language plpgsql
as $function$
declare v_owner text; v_p jsonb;
begin
  if NEW.signal_type = 'tax_delinquency' and NEW.raw_address ~ '^\d{2}:\d{3}:\d{4}' then
    NEW.parcel_serial := coalesce(NEW.parcel_serial, substring(NEW.raw_address from '^(\d{2}:\d{3}:\d{4})'));
    v_owner := substring(NEW.raw_address from '^\d{2}:\d{3}:\d{4}\s+(.*?)\s+[\d,]+\.\d{2}\s+[\d,]+\.\d{2}\s*$');
    NEW.tax_owed  := coalesce(NEW.tax_owed,  nullif(replace(substring(NEW.raw_address from '([\d,]+\.\d{2})\s+[\d,]+\.\d{2}\s*$'),',',''),'')::numeric);
    NEW.tax_total := coalesce(NEW.tax_total, nullif(replace(substring(NEW.raw_address from '([\d,]+\.\d{2})\s*$'),',',''),'')::numeric);
  else
    v_owner := NEW.raw_owner_name;
  end if;

  NEW.owner_norm := coalesce(NEW.owner_norm, pp_norm(v_owner));
  v_p := pp_payload(NEW.raw_payload);

  if NEW.parcel_serial is null then
    NEW.parcel_serial := nullif(v_p->>'parcel_id','');
  end if;

  if NEW.signal_type = 'deed_of_trust' then
    NEW.loan_amount   := nullif(v_p->>'loan_amount','')::numeric;
    NEW.lender_name   := nullif(v_p->>'lender','');
    NEW.loan_released := coalesce((v_p->>'released')::boolean, false);
    begin NEW.loan_date := to_date(nullif(v_p->>'loan_date',''), 'MM/DD/YYYY');
    exception when others then NEW.loan_date := null; end;
  elsif NEW.signal_type = 'historical_mortgage' then
    NEW.lender_name   := nullif(v_p->>'lender','');
    NEW.loan_released := false;   -- released loans are skipped at collection
    begin
      NEW.loan_date := coalesce(
        to_date(nullif(v_p->>'loan_date',''), 'MM/DD/YYYY'),
        make_date((v_p->>'loan_year')::int, 1, 1));
    exception when others then
      begin NEW.loan_date := make_date((v_p->>'loan_year')::int, 1, 1);
      exception when others then NEW.loan_date := null; end;
    end;
  end if;

  if NEW.signal_type = 'lir_parcel' then
    NEW.is_absentee  := (upper(coalesce(v_p->>'primary_res','')) in ('N','NO','0','FALSE'));
    NEW.market_value := nullif(v_p->>'market_value','')::numeric;
  end if;

  -- Only documents that move title get a transaction type.
  if NEW.signal_type in ('deed_transfer','comparable_sale','family_transfer','divorce_transfer','trustee_deed','probate_deed') then
    if NEW.raw_payload is not null and v_p ? 'grantor' and v_p ? 'grantee' then
      NEW.txn_type := coalesce(NEW.txn_type, pp_txn_type(v_p->>'grantor', v_p->>'grantee'));
    end if;
  else
    NEW.txn_type := null;
  end if;

  NEW.owner_key := case when NEW.owner_norm is not null
                        then 'O:'||coalesce(NEW.county,'?')||':'||NEW.owner_norm end;
  NEW.property_key := case
      when NEW.parcel_serial is not null then 'P:'||coalesce(NEW.county,'?')||':'||NEW.parcel_serial
      when NEW.signal_type in ('lir_parcel','fsbo','contractor_license','contractor_directory')
           and coalesce(trim(NEW.raw_address),'') <> ''
        then 'A:'||coalesce(NEW.county,'?')||':'||pp_norm(NEW.raw_address) end;
  return NEW;
end $function$;
