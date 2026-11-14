"""
Owner mailing address, property address and value for Summit, Wasatch and Salt Lake County leads.

Source: each county's own public ArcGIS parcel layer (no login; verified live Nov 12 2026):
  Summit    maps.summitcounty.org/arcgis/rest/services/Maps/ParcelQuery/MapServer/0          (by SERIAL or OWNER)
  Wasatch   maps.wasatch.utah.gov/arcgis/rest/services/MapLayers/Web_Parcels/MapServer/25     (owner + mailing)
            maps.wasatch.utah.gov/arcgis/rest/services/Maps/Property_Equity_Map/MapServer/0  (2026 market value)
  Salt Lake apps.saltlakecounty.gov/slcogis/rest/services/Land/MapServer/1                    (owner, mailing, value)

SAFETY RULE: an address is attached to a lead ONLY when the county's current owner name matches the lead's name
(surname + first name, or the full entity name), and a name search is accepted only when every matching parcel
carries the same mailing address. A wrong address sends an agent's letter to a stranger; no address is better.
Rows are written to pp_parcel_intel keyed to the lead (owner_key). Summit serials are namespaced 'SUMMIT:<serial>'
so a parcel that changed hands is never joined to the other party on a recorded deed.
"""
import os, re, sys, html, json, time, logging, requests
try:
    import eventlog as _eventlog; _eventlog.install(job=__file__)
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s'); log = logging.getLogger('county-intel')
DRY = '--dry' in sys.argv
SB = os.environ.get('SUPABASE_URL', '').rstrip('/'); KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')
H = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'}
S = requests.Session(); S.headers['User-Agent'] = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36'

SUMMIT = 'https://maps.summitcounty.org/arcgis/rest/services/Maps/ParcelQuery/MapServer/0/query'
WAS_OWN = 'https://maps.wasatch.utah.gov/arcgis/rest/services/MapLayers/Web_Parcels/MapServer/25/query'
WAS_VAL = 'https://maps.wasatch.utah.gov/arcgis/rest/services/Maps/Property_Equity_Map/MapServer/0/query'
SL = 'https://apps.saltlakecounty.gov/slcogis/rest/services/Land/MapServer/1/query'

ENTITY = re.compile(r'\b(LLC|L L C|INC|CORP|CORPORATION|COMPANY|CO|LP|LLP|LTD|TRUST|PARTNERS|HOLDINGS|PROPERTIES|INVESTMENTS|ASSOCIATION|BANK|CHURCH|CITY|COUNTY)\b')
NOISE = {'TR','TRS','TRUSTEE','TRUSTEES','TEE','JT','ET','AL','ETAL','JR','SR','II','III','IV','THE','OF','AND','DECEASED','SUC','SUCTEE','LIVING','REVOCABLE','FAMILY'}


def toks(s):
    s = re.sub(r'\([^)]*\)', ' ', (s or '').upper())
    return [t for t in re.findall(r"[A-Z0-9']+", s) if t not in NOISE and len(t) > 1]


def arcgis(url, where, fields, n=50):
    for attempt in range(3):
        try:
            r = S.get(url, params={'where': where, 'outFields': fields, 'returnGeometry': 'false', 'f': 'json', 'resultRecordCount': n}, timeout=60)
            j = r.json()
            if 'error' in j: raise RuntimeError(str(j['error'])[:160])
            return [f['attributes'] for f in j.get('features', [])]
        except Exception as e:
            log.warning(f'  arcgis retry {attempt+1}: {type(e).__name__} {str(e)[:120]}'); time.sleep(4)
    return None


def q(s): return s.replace("'", "''")


def street(a): return re.sub(r'[^A-Z0-9 ]', ' ', (a or '').upper()).split()


def absentee(prop, mail):
    p, m = street(prop), street(mail)
    if not p or not m or not re.search(r'\d', ' '.join(p)): return None
    return ' '.join(p[:3]) != ' '.join(m[:3])


def name_match(lead, owner, last_first=True):
    """Lead name vs county owner string. People: surname AND first name must both appear as whole words.
    Entities: every significant word of the lead's entity name must appear."""
    L, O = toks(lead), set(toks(owner))
    if not L or not O: return False
    if ENTITY.search((lead or '').upper()):
        core = [t for t in L if not ENTITY.fullmatch(t)]
        return len(core) >= 1 and all(t in O for t in core)
    if last_first: sur, first, mid = L[0], (L[1] if len(L) > 1 else None), (L[2] if len(L) > 2 else None)
    else: sur, first, mid = L[-1], (L[0] if len(L) > 1 else None), (L[1] if len(L) > 2 else None)
    if not (sur in O and (first is None or first in O)): return False
    # middle initials must agree when the county record carries one right after the first name
    ol = toks(owner)
    for i, t in enumerate(ol[:-1]):
        if t == first and mid:
            nxt = ol[i + 1]
            if nxt != sur and len(nxt) <= len(mid) and not mid.startswith(nxt[0]) and len(nxt) == 1:
                return False
    return True


def lead_names(lead):
    # Summit/Wasatch recorder parties look like "LAST FIRST M, LAST2 FIRST2"; court parties "FIRST M LAST".
    return [p.strip() for p in re.split(r',(?![^()]*\))', lead or '') if p.strip()][:3]


# ── per-county resolvers: return list of dicts {serial, owner, mailing, situs, value, year} ──
def summit_by_serial(serials):
    out = []
    for i in range(0, len(serials), 80):
        ids = ','.join(f"'{q(s)}'" for s in serials[i:i+80])
        rows = arcgis(SUMMIT, f'OwnershipTable_SERIAL IN ({ids})',
                      'OwnershipTable_SERIAL,OwnershipTable_OWNER,OwnershipTable_ADDRESS1,OwnershipTable_ADDRESS2,OwnershipTable_CITY,OwnershipTable_STATE,OwnershipTable_ZIP,OwnershipTable_SITUS,OwnershipTable_MRKTVALLND,OwnershipTable_MRKTVALIMP', 200) or []
        for r in rows: out.append(summit_row(r))
        time.sleep(1)
    return out


def summit_row(r):
    g = lambda k: (r.get('OwnershipTable_' + k) or '')
    g = (lambda k: str(r.get('OwnershipTable_' + k) or '').strip())
    mail = ', '.join(x for x in [' '.join(x for x in [g('ADDRESS1'), g('ADDRESS2')] if x), ' '.join(x for x in [g('CITY'), g('STATE'), g('ZIP')] if x)] if x)
    val = (r.get('OwnershipTable_MRKTVALLND') or 0) + (r.get('OwnershipTable_MRKTVALIMP') or 0)
    return {'serial': 'SUMMIT:' + g('SERIAL'), 'owner': g('OWNER'), 'mailing': mail or None, 'situs': g('SITUS') or None, 'value': int(val) or None, 'year': 2025}


def summit_by_name(name):
    t = toks(name)
    if len(t) < 2: return []
    rows = arcgis(SUMMIT, f"OwnershipTable_OWNER LIKE '{q(t[0])} {q(t[1])}%'",
                  'OwnershipTable_SERIAL,OwnershipTable_OWNER,OwnershipTable_ADDRESS1,OwnershipTable_ADDRESS2,OwnershipTable_CITY,OwnershipTable_STATE,OwnershipTable_ZIP,OwnershipTable_SITUS,OwnershipTable_MRKTVALLND,OwnershipTable_MRKTVALIMP', 30)
    return [summit_row(r) for r in (rows or [])]


def wasatch_by_name(name):
    t = toks(name)
    if not t: return []
    pat = ' '.join(t[:2]) if not ENTITY.search(name.upper()) else ' '.join(toks(name)[:3])
    rows = arcgis(WAS_OWN, f"(Owner LIKE '{q(pat)}%' OR Owner_2 LIKE '{q(pat)}%')",
                  'TaxParcel,Owner,Owner_2,TaxAdd1,TaxAdd2,TaxCity,TaxState,TaxZIP,PropertyAddress,PropertyCity', 30) or []
    seen, out = set(), []
    for r in rows:
        p = (r.get('TaxParcel') or '').strip()
        if not p or p in seen: continue
        seen.add(p)
        g = lambda k: str(r.get(k) or '').strip()
        mail = ', '.join(x for x in [' '.join(x for x in [g('TaxAdd1'), g('TaxAdd2')] if x), ' '.join(x for x in [g('TaxCity'), g('TaxState'), g('TaxZIP')] if x)] if x)
        situs = ', '.join(x for x in [g('PropertyAddress'), g('PropertyCity')] if x)
        out.append({'serial': p, 'owner': (g('Owner') + ' ' + g('Owner_2')).strip(), 'mailing': mail or None, 'situs': situs or None, 'value': None, 'year': None})
    if out:
        ids = ','.join(f"'{q(o['serial'])}'" for o in out)
        vals = {v['Parcel']: v for v in (arcgis(WAS_VAL, f'Parcel IN ({ids})', 'Parcel,Y0Year,Y0TotalMarket', 100) or [])}
        for o in out:
            v = vals.get(o['serial'])
            if v: o['value'], o['year'] = int(v.get('Y0TotalMarket') or 0) or None, v.get('Y0Year')
    return out


def sl_rows(where):
    rows = arcgis(SL, where, 'parcel_id,own_name,care_of,own_addr,own_apt_num,own_citystate,own_zip,prop_location,total_full_mkt,tax_year', 30) or []
    out = []
    for r in rows:
        g = lambda k: str(r.get(k) or '').strip()
        mail = ', '.join(x for x in [' '.join(x for x in [g('own_addr'), g('own_apt_num')] if x), ' '.join(x for x in [g('own_citystate'), g('own_zip')] if x)] if x)
        out.append({'serial': g('parcel_id'), 'owner': g('own_name'), 'mailing': mail or None, 'situs': g('prop_location') or None,
                    'value': int(r.get('total_full_mkt') or 0) or None, 'year': r.get('tax_year')})
    return out


def sl_by_name(name):
    t = toks(name)
    if ENTITY.search(name.upper()):
        core = [x for x in t if not ENTITY.fullmatch(x)]
        return sl_rows(f"own_name LIKE '%{q(' '.join(core[:3]))}%'") if core else []
    if len(t) < 2: return []
    return sl_rows(f"own_name LIKE '{q(t[0])} %{q(t[-1])}%'")


def pick(lead, cands, last_first):
    """Keep candidates whose owner matches the lead; accept only if they agree on ONE mailing address."""
    ok = [c for c in cands if c.get('mailing') and any(name_match(n, c['owner'], last_first) for n in lead_names(lead))]
    if not ok: return [], 'no name match'
    if len({re.sub(r'\W', '', c['mailing'].upper()) for c in ok}) != 1: return [], f'{len(ok)} matches with different mailing addresses — ambiguous'
    return ok, 'ok'


# ── database I/O ──
def rest(path, **kw):
    r = requests.get(f'{SB}/rest/v1/{path}', headers=H, timeout=120, **kw); r.raise_for_status(); return r.json()


def page(path):
    out, off = [], 0
    while True:
        p = rest(f'{path}&limit=1000&offset={off}')
        if not p: break
        out += p; off += len(p)
    return out


# Nov 14 2026: the Summit map layer carries the PRIOR year's value (152 of 157 top-lead parcels differed from the
# assessor's 2026 market value, avg 7.6%). The value written is the assessor's own current "Market (YYYY)" figure.
_SUMMIT_TAX = None
def summit_assessor_value(serial):
    global _SUMMIT_TAX
    B = 'https://property.summitcounty.org/eaglesoftware'
    try:
        if _SUMMIT_TAX is None:
            _SUMMIT_TAX = requests.Session(); _SUMMIT_TAX.headers['User-Agent'] = 'Mozilla/5.0'
            _SUMMIT_TAX.get(B + '/web/loginPOST.jsp?guest=true&submit=Public+Login', timeout=40)
        ser = serial.replace('SUMMIT:', '')
        r = _SUMMIT_TAX.get(B + '/taxweb/results.jsp', params={'ParcelNumID': ser, 'docSearch': 'Search'}, timeout=40)
        for a in list(dict.fromkeys(re.findall(r'account\.jsp\?accountNum=(\w+)', r.text)))[:3]:
            t = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', _SUMMIT_TAX.get(B + '/taxweb/account.jsp', params={'accountNum': a}, timeout=40).text)))
            pn = re.search(r'Parcel Number (\S+)', t)
            m = re.search(r'Market \((20\d\d)\) \$([\d,]+)', t)
            if pn and pn.group(1) == ser and m: return int(m.group(2).replace(',', '')), int(m.group(1))
    except Exception as e:
        log.warning(f'    Summit assessor value for {serial}: {e}')
    return None, None


def write(lead_key, county, c):
    if county == 'Summit':
        v, y = summit_assessor_value(c['serial'])
        if v: c = {**c, 'value': v, 'year': y}
        else: log.warning(f"    {c['serial']}: assessor value unavailable — keeping the map layer's prior-year value")
    row = {'parcel_serial': c['serial'], 'owner_key': lead_key, 'county': county, 'market_value': c['value'], 'value_year': c['year'],
           'property_address': c['situs'], 'mailing_address': c['mailing'], 'is_absentee': absentee(c['situs'], c['mailing'])}
    if DRY: log.info(f'    DRY write {json.dumps(row)[:220]}'); return True
    r = requests.post(f'{SB}/rest/v1/pp_parcel_intel?on_conflict=parcel_serial', json=row, headers={**H, 'Prefer': 'resolution=merge-duplicates,return=minimal'}, timeout=30)
    if r.status_code >= 400: log.error(f'    write {r.status_code} {r.text[:120]}'); return False
    return True


# Nov 13 2026: 100 Summit parcels behind top-tier "estate" and "lien" leads belong to timeshare operators. The person on
# the affidavit or HOA lien owned a week of a resort unit, not a home, so the lead is not a home seller. The assessor
# owner is the proof; such signals are set aside (is_institutional) the same way the Westgate intervals were.
TIMESHARE_OWNER = re.compile(r'MARRIOTT OWNERSHIP RESORTS|HILTON RESORTS|SUNRISE PARK CITY|GRAND SUMMIT RESORT|VIP CLUB|'
                             r'SWEETWATER PROPERTIES|SWEETWATER CONDOSHARE|CONDOSHARE|SECURITY PACIFIC FINANCIAL|PARK REGENCY|FIRST AMERICAN TRUST FSB|'
                             r'WESTGATE|VACATION (CLUB|OWNERSHIP|RESORT)|TIMESHARE|INTERVAL OWNERS', re.I)


def set_aside_timeshare(key, cands):
    n = 0
    for c in cands:
        if not TIMESHARE_OWNER.search(c.get('owner') or ''): continue
        ser = c['serial'].replace('SUMMIT:', '')
        log.info(f"    {ser} is owned by {c['owner'][:40]} (timeshare) — setting the lead's signals on it aside")
        if DRY: n += 1; continue
        r = requests.patch(f'{SB}/rest/v1/pp_scraper_signals?owner_key=eq.{requests.utils.quote(key)}&parcel_serial=eq.{requests.utils.quote(ser)}',
                           json={'is_institutional': True}, headers={**H, 'Prefer': 'return=minimal'}, timeout=30)
        n += r.status_code < 300
    return n


def run_county(county, leads, resolver, last_first, serials_by_lead=None):
    found = skipped = 0
    for i, ld in enumerate(leads):
        key, name = ld['entity_key'], ld['owner_display'] or ''
        cands = []
        if serials_by_lead and serials_by_lead.get(key):
            cands = summit_by_serial(sorted(serials_by_lead[key])[:10])
            if set_aside_timeshare(key, cands):
                cands = [c for c in cands if not TIMESHARE_OWNER.search(c.get('owner') or '')]
        if not pick(name, cands, last_first)[0]:
            for n in lead_names(name)[:2]:
                cands += resolver(n); time.sleep(0.6)
        good, why = pick(name, cands, last_first)
        if not good:
            skipped += 1
            if i < 40 or i % 50 == 0: log.info(f'  {county} {name[:40]:42} skip: {why}')
            continue
        for c in good[:6]:
            write(key, county, c)
        found += 1
        log.info(f"  {county} {name[:40]:42} -> {good[0]['owner'][:30]} | {good[0]['mailing'][:45]} | ${good[0]['value'] or 0:,}")
    log.info(f'{county}: {found} leads matched, {skipped} skipped (no safe match)')
    return found


def main():
    limit = int(os.environ.get('COUNTY_LIMIT', '3000'))
    counties = [c.strip() for c in os.environ.get('COUNTIES', 'Summit,Wasatch,Salt Lake').split(',')]
    for county in counties:
        ce = requests.utils.quote(county)
        leads = page(f'pp_entity_conviction?select=entity_key,owner_display&county=eq.{ce}&resolved_flag=is.false&mailing_address=is.null&order=conviction_score.desc,entity_key')[:limit]
        log.info(f'{county}: {len(leads)} leads without a mailing address')
        serials = None
        if county == 'Summit':
            serials = {}
            for s in page('pp_scraper_signals?select=owner_key,parcel_serial&county=eq.Summit&is_legacy=eq.false&is_institutional=eq.false&parcel_serial=not.is.null&order=id'):
                serials.setdefault(s['owner_key'], set()).add(s['parcel_serial'])
        resolver = {'Summit': summit_by_name, 'Wasatch': wasatch_by_name, 'Salt Lake': sl_by_name}[county]
        run_county(county, leads, resolver, last_first=(county != 'Salt Lake'), serials_by_lead=serials)


if __name__ == '__main__':
    if '--test' in sys.argv:   # offline check of the resolvers against a few names: --test County "NAME" ...
        i = sys.argv.index('--test'); county = sys.argv[i+1]
        for n in sys.argv[i+2:]:
            res = {'Summit': summit_by_name, 'Wasatch': wasatch_by_name, 'Salt Lake': sl_by_name}[county](n)
            good, why = pick(n, res, county != 'Salt Lake')
            print(f'{n!r}: {len(res)} candidates -> {why}', [(g["serial"], g["owner"], g["mailing"], g["situs"], g["value"]) for g in good][:3])
    else:
        main()
