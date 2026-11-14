#!/usr/bin/env python3
"""
Re-read EVERY parcel we hold for a county straight from the county, and keep only what the county confirms today.

  Salt Lake  owner + tax mailing address: the county's tax roll layer apps.saltlakecounty.gov/slcogis Land/MapServer/1
             (tax_year 2026).  2026 market value + owner: the Assessor's parcel page
             apps.saltlakecounty.gov/assessor/new/valuationInfoExpanded.cfm ("2026 Market Value $ X").
  Wasatch    owner + tax mailing address: maps.wasatch.utah.gov Web_Parcels (TaxAdd1/TaxCity/TaxZIP).
             2026 market value: the Assessor's Residential Property Equity Map layer (Y0Year / Y0TotalMarket).

A parcel is CONFIRMED only when the county's current owner name still matches the lead the row belongs to and the
county has a mailing address. Confirmed rows get the county's mailing address, property address, 2026 value and a
fresh fetched_at. A parcel the county cannot confirm is left out of the "confirmed" set with the reason; when the reason
is that the county's owner is now someone else, the old mailing address is removed from the row (no address is better
than a stranger's) and the old value is kept in the output file.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, COUNTIES (default "Salt Lake,Wasatch"), DRY=1 to write nothing.
Output: out/county_reread_<county>.json (one record per parcel) and a printed summary.
"""
import os, re, sys, json, time, html, logging, datetime, requests
sys.path.insert(0, os.path.dirname(__file__))
from county_parcel_intel import arcgis, name_match, lead_names, absentee, q, WAS_OWN, WAS_VAL, SL

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s'); log = logging.getLogger('county-reread')
SB = os.environ['SUPABASE_URL'].rstrip('/'); KEY = os.environ['SUPABASE_SERVICE_KEY']
H = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'}
DRY = os.environ.get('DRY') == '1'
WEB = requests.Session(); WEB.headers['User-Agent'] = 'Mozilla/5.0'
OUT = os.path.join(os.path.dirname(__file__), '..', 'out'); os.makedirs(OUT, exist_ok=True)
CURRENT_YEAR = 2026


def page(path):
    out, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/{path}&limit=1000&offset={off}', headers=H, timeout=120); r.raise_for_status(); p = r.json()
        if not p: break
        out += p; off += len(p)
    return out


def lead_name(row):
    k = row.get('owner_key') or row.get('buyer_key') or ''
    return k.split(':', 2)[-1] if k.startswith('O:') else k.split(':', 1)[-1]


def matches(lead, owner):
    return any(name_match(n, owner, True) or name_match(n, owner, False) for n in lead_names(lead))


def norm(s): return re.sub(r'\W', '', (s or '').upper())


# ── Salt Lake ──
def sl_roll(ids):
    out = {}
    for i in range(0, len(ids), 80):
        chunk = ','.join(f"'{q(x)}'" for x in ids[i:i+80])
        for r in arcgis(SL, f'parcel_id IN ({chunk})', 'parcel_id,own_name,care_of,own_addr,own_apt_num,own_citystate,own_zip,prop_location,total_full_mkt,tax_year', 200) or []:
            g = lambda k: str(r.get(k) or '').strip()
            mail = ', '.join(x for x in [' '.join(x for x in [g('own_addr'), g('own_apt_num')] if x), ' '.join(x for x in [g('own_citystate'), g('own_zip')] if x)] if x)
            out[g('parcel_id')] = {'owner': g('own_name'), 'mailing': mail or None, 'situs': g('prop_location') or None,
                                   'roll_value': int(r.get('total_full_mkt') or 0) or None, 'roll_year': g('tax_year')}
        time.sleep(0.5)
    return out


def sl_assessor(pid):
    for attempt in range(3):
        try:
            t = WEB.get('https://apps.saltlakecounty.gov/assessor/new/valuationInfoExpanded.cfm', params={'parcel_id': pid}, timeout=40).text
            t = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', t)))
            m = re.search(r'(20\d\d) Market Value \$ ?([\d,]+)', t)
            o = re.search(r'Parcel Owner (.+?) Address ', t)
            return (int(m.group(2).replace(',', '')), int(m.group(1))) if m else (None, None), (o.group(1).strip() if o else None)
        except Exception as e:
            log.warning(f'  assessor {pid} retry {attempt+1}: {e}'); time.sleep(3)
    return (None, None), None


def read_salt_lake(rows):
    roll = sl_roll([r['parcel_serial'] for r in rows])
    out = {}
    for i, r in enumerate(rows):
        pid = r['parcel_serial']; c = dict(roll.get(pid) or {})
        (v, y), aowner = sl_assessor(pid)
        c.update({'value': v, 'year': y, 'assessor_owner': aowner, 'source_value': 'SL Assessor parcel page', 'source_mail': 'SL tax roll layer'})
        out[pid] = c if roll.get(pid) or v else None
        if i % 50 == 0: log.info(f'  Salt Lake assessor pages read: {i}/{len(rows)}')
        time.sleep(0.3)
    return out


# ── Wasatch ──
def read_wasatch(rows):
    ids = [r['parcel_serial'] for r in rows]; out = {}
    for i in range(0, len(ids), 80):
        chunk = ','.join(f"'{q(x)}'" for x in ids[i:i+80])
        own = arcgis(WAS_OWN, f'TaxParcel IN ({chunk})', 'TaxParcel,Owner,Owner_2,TaxAdd1,TaxAdd2,TaxCity,TaxState,TaxZIP,PropertyAddress,PropertyCity', 400) or []
        val = {v['Parcel']: v for v in (arcgis(WAS_VAL, f'Parcel IN ({chunk})', 'Parcel,Y0Year,Y0TotalMarket', 400) or [])}
        for r in own:
            g = lambda k: str(r.get(k) or '').strip()
            p = g('TaxParcel')
            if p in out: continue
            mail = ', '.join(x for x in [' '.join(x for x in [g('TaxAdd1'), g('TaxAdd2')] if x), ' '.join(x for x in [g('TaxCity'), g('TaxState'), g('TaxZIP')] if x)] if x)
            out[p] = {'owner': (g('Owner') + ' ' + g('Owner_2')).strip(), 'mailing': mail or None,
                      'situs': ', '.join(x for x in [g('PropertyAddress'), g('PropertyCity')] if x) or None,
                      'source_value': 'Wasatch Assessor equity map', 'source_mail': 'Wasatch tax parcel layer'}
        for p, v in val.items():
            out.setdefault(p, {'owner': None, 'mailing': None, 'situs': None, 'source_value': 'Wasatch Assessor equity map'})
            out[p]['value'] = int(v.get('Y0TotalMarket') or 0) or None; out[p]['year'] = v.get('Y0Year')
        log.info(f'  Wasatch parcels read: {min(i+80, len(ids))}/{len(ids)}')
        time.sleep(0.5)
    return out


def patch(pid, body):
    if DRY: return True
    r = requests.patch(f'{SB}/rest/v1/pp_parcel_intel?parcel_serial=eq.{requests.utils.quote(pid)}', json=body,
                       headers={**H, 'Prefer': 'return=minimal'}, timeout=30)
    if r.status_code >= 300: log.error(f'  write {pid} {r.status_code} {r.text[:120]}')
    return r.status_code < 300


def run(county):
    rows = page(f'pp_parcel_intel?select=parcel_serial,owner_key,buyer_key,mailing_address,market_value,value_year,property_address&county=eq.{requests.utils.quote(county)}&order=parcel_serial')
    log.info(f'{county}: {len(rows)} parcels held')
    data = {'Salt Lake': read_salt_lake, 'Wasatch': read_wasatch}[county](rows)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    recs, stats = [], {'parcels_held': len(rows), 'parcels_read': 0, 'confirmed': 0, 'value_confirmed': 0, 'changed': 0,
                       'mailing_changed': 0, 'value_changed': 0, 'unchanged': 0, 'not_confirmed': 0, 'value_not_confirmed': 0,
                       'mailing_removed_owner_changed': 0, 'write_errors': 0}
    reasons = {}
    for r in rows:
        pid, lead = r['parcel_serial'], lead_name(r)
        c = data.get(pid)
        old = {'mailing': r['mailing_address'], 'value': float(r['market_value']) if r['market_value'] is not None else None, 'year': r['value_year']}
        rec = {'parcel': pid, 'lead': lead, 'old': old}
        why = None
        if not c: why = 'parcel not found on the county roll (retired, split or renumbered)'
        else:
            stats['parcels_read'] += 1
            rec['county'] = {k: c.get(k) for k in ('owner', 'assessor_owner', 'mailing', 'situs', 'value', 'year', 'roll_value', 'roll_year')}
            owner = c.get('owner') or c.get('assessor_owner') or ''
            if not owner: why = 'county roll has no owner name for this parcel'
            elif not (matches(lead, owner) or (c.get('assessor_owner') and matches(lead, c['assessor_owner']))): why = f'county owner is now "{owner[:60]}" — does not match the lead'
            elif not c.get('mailing'): why = 'county has no mailing address on file'
        if why:
            stats['not_confirmed'] += 1; rec['status'] = 'not_confirmed'; rec['reason'] = why
            key = why.split(' "')[0].split(' (got')[0]; reasons[key] = reasons.get(key, 0) + 1
            if why.startswith('county owner is now') and r['mailing_address']:
                if patch(pid, {'mailing_address': None, 'is_absentee': None, 'fetched_at': now}): stats['mailing_removed_owner_changed'] += 1
                else: stats['write_errors'] += 1
                rec['action'] = 'old mailing address removed (owner changed)'
            recs.append(rec); continue
        body = {'mailing_address': c['mailing'], 'property_address': c.get('situs') or r['property_address'],
                'is_absentee': absentee(c.get('situs') or r['property_address'], c['mailing']), 'fetched_at': now}
        value_ok = bool(c.get('value')) and c.get('year') == CURRENT_YEAR
        if value_ok: body.update({'market_value': c['value'], 'value_year': c['year']})
        else:
            stats['value_not_confirmed'] += 1
            rec['value_reason'] = f'assessor shows no {CURRENT_YEAR} market value for this parcel (got year {c.get("year")}, value {c.get("value")})'
        changed = norm(old['mailing']) != norm(c['mailing']) or (value_ok and (old['value'] != float(c['value']) or old['year'] != c['year']))
        ok = patch(pid, body)
        stats['confirmed'] += 1; stats['value_confirmed'] += value_ok; stats['changed' if changed else 'unchanged'] += 1
        if changed:
            stats['mailing_changed'] += norm(old['mailing']) != norm(c['mailing'])
            stats['value_changed'] += bool(value_ok and (old['value'] != float(c['value']) or old['year'] != c['year']))
        if not ok: stats['write_errors'] += 1
        rec.update({'status': 'confirmed', 'changed': changed, 'written': {k: body.get(k) for k in ('mailing_address', 'market_value', 'value_year')}})
        recs.append(rec)
    stats['not_confirmed_reasons'] = reasons
    stats['dry_run'] = DRY; stats['finished_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    fn = os.path.join(OUT, f"county_reread_{county.replace(' ', '_')}.json")
    json.dump({'county': county, 'summary': stats, 'parcels': recs}, open(fn, 'w'), indent=1)
    print(f'\n=== {county} SUMMARY ===\n' + json.dumps(stats, indent=1))
    for x in recs:
        if x['status'] == 'not_confirmed': print(f"  NOT CONFIRMED {x['parcel']:18} {x['lead'][:34]:36} {x['reason']}")
        elif x.get('value_reason'): print(f"  VALUE NOT CONFIRMED {x['parcel']:18} {x['lead'][:34]:36} {x['value_reason']}")
    return stats


if __name__ == '__main__':
    for county in [c.strip() for c in os.environ.get('COUNTIES', 'Salt Lake,Wasatch').split(',') if c.strip()]:
        run(county)
