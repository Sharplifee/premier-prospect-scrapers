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
fresh fetched_at. When the county owner carries the lead's surname (estate leads: spouse or heir now holds title) the
row is confirmed with a note. A parcel the county cannot confirm is left unchanged and listed with the reason.

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


def same_family(lead, owners, county):
    """Lead's surname appears as a whole word in the county owner (estate leads: the spouse or heir now holds title).
    Wasatch recorder names are LAST FIRST; Salt Lake court names are FIRST LAST. Entities never qualify."""
    from county_parcel_intel import toks, ENTITY
    if ENTITY.search((lead or '').upper()): return False
    t = toks(lead)
    if len(t) < 2: return False
    sur = t[0] if county == 'Wasatch' else t[-1]
    return len(sur) > 2 and any(sur in set(toks(o)) for o in owners)


# ── Salt Lake ──
def sl_roll(ids):
    out = {}
    # The county's server rejects long IN lists (non-JSON reply at 80 ids), so read 20 at a time.
    for i in range(0, len(ids), 20):
        chunk = ','.join(f"'{q(x)}'" for x in ids[i:i+20])
        rows = arcgis(SL, f'parcel_id IN ({chunk})', 'parcel_id,own_name,care_of,own_addr,own_apt_num,own_citystate,own_zip,prop_location,total_full_mkt,tax_year', 100)
        if rows is None: log.error(f'  Salt Lake tax roll unreadable for parcels {ids[i]}..{ids[min(i+19, len(ids)-1)]}')
        for r in rows or []:
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
        c.update({'value': v, 'year': y, 'assessor_owner': aowner, 'source_value': 'SL Assessor parcel page', 'source_mail': 'SL tax roll layer',
                  'roll_read': pid in roll})
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


# ── Summit (value only): every parcel still carrying a prior-year value gets the Assessor's own "Market (2026)" figure
# from the EagleWeb account page, matched on the exact parcel number. Mailing addresses are not touched here.
def run_summit_values():
    B = 'https://property.summitcounty.org/eaglesoftware'
    tax = requests.Session(); tax.headers['User-Agent'] = 'Mozilla/5.0'
    tax.get(B + '/web/loginPOST.jsp?guest=true&submit=Public+Login', timeout=40)
    rows = page('pp_parcel_intel?select=parcel_serial,owner_key,buyer_key,market_value,value_year&county=eq.Summit'
                f'&or=(value_year.is.null,value_year.lt.{CURRENT_YEAR},market_value.is.null)&order=parcel_serial')
    log.info(f'Summit: {len(rows)} parcels without a {CURRENT_YEAR} assessor value')
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    stats = {'parcels_to_reread': len(rows), 'parcels_read': 0, 'value_written': 0, 'changed': 0, 'unchanged': 0,
             'not_confirmed': 0, 'owner_differs_from_lead_note': 0, 'write_errors': 0}
    reasons, recs = {}, []
    # Stop cleanly before the workflow's hard limit so the summary and output file are always written; the next run
    # resumes on its own because it only selects parcels that still lack a 2026 value.
    deadline = time.time() + 60 * float(os.environ.get('MAX_MINUTES', '90'))
    stats['stopped_early_for_time'] = False
    for i, r in enumerate(rows):
        if time.time() > deadline:
            stats['stopped_early_for_time'] = True; stats['parcels_left_for_next_run'] = len(rows) - i
            log.info(f'  time budget reached after {i}/{len(rows)} parcels; the rest resume on the next run'); break
        pid, ser = r['parcel_serial'], r['parcel_serial'].replace('SUMMIT:', '')
        rec = {'parcel': pid, 'lead': lead_name(r), 'old': {'value': float(r['market_value']) if r['market_value'] is not None else None, 'year': r['value_year']}}
        why, got = None, None
        for attempt in range(3):
            try:
                res = tax.get(B + '/taxweb/results.jsp', params={'ParcelNumID': ser, 'docSearch': 'Search'}, timeout=40)
                accts = list(dict.fromkeys(re.findall(r'account\.jsp\?accountNum=(\w+)', res.text)))
                for a in accts[:3]:
                    t = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', tax.get(B + '/taxweb/account.jsp', params={'accountNum': a}, timeout=40).text)))
                    pn = re.search(r'Parcel Number (\S+)', t)
                    if not pn or pn.group(1) != ser: continue
                    m = re.search(r'Market \((20\d\d)\) \$([\d,]+)', t)
                    o = re.search(r' Name (.+?) Market \(', t)
                    got = {'account': a, 'year': int(m.group(1)) if m else None, 'value': int(m.group(2).replace(',', '')) if m else None,
                           'owner_block': o.group(1).strip()[:160] if o else None}
                    break
                if got is None: why = f'assessor has no account page for this parcel number ({len(accts)} search results)'
                break
            except Exception as e:
                why = f'assessor site error after 3 tries: {type(e).__name__}'; time.sleep(4)
        if got:
            stats['parcels_read'] += 1; rec['county'] = got
            if got['year'] != CURRENT_YEAR or not got['value']: why = f"assessor shows no {CURRENT_YEAR} market value (year {got['year']}, value {got['value']})"
        if why:
            stats['not_confirmed'] += 1; rec.update({'status': 'not_confirmed', 'reason': why})
            k = why.split(' (')[0].split(':')[0]; reasons[k] = reasons.get(k, 0) + 1
            recs.append(rec); print(f"PARCEL {pid} | {rec['lead'][:40]} | NOT CONFIRMED | {why}", flush=True); continue
        if got.get('owner_block') and rec['lead'] and not matches(rec['lead'], got['owner_block']):
            stats['owner_differs_from_lead_note'] += 1; rec['owner_note'] = f"assessor owner block: {got['owner_block'][:80]}"
        changed = rec['old']['value'] != float(got['value']) or rec['old']['year'] != got['year']
        ok = patch(pid, {'market_value': got['value'], 'value_year': got['year'], 'fetched_at': now})
        stats['value_written'] += ok; stats['write_errors'] += not ok; stats['changed' if changed else 'unchanged'] += 1
        rec.update({'status': 'confirmed', 'changed': changed, 'written': {'market_value': got['value'], 'value_year': got['year']}})
        recs.append(rec)
        print(f"PARCEL {pid} | {rec['lead'][:40]} | {'CHANGED' if changed else 'UNCHANGED'} | {rec['old']['value']} ({rec['old']['year']}) -> "
              f"{got['value']} ({got['year']}) | written={ok}{' | ' + rec['owner_note'] if rec.get('owner_note') else ''}", flush=True)
        if i % 100 == 0: log.info(f'  Summit assessor pages read: {i}/{len(rows)}')
        time.sleep(0.3)
    stats['not_confirmed_reasons'] = reasons; stats['dry_run'] = DRY
    stats['finished_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    json.dump({'county': 'Summit', 'summary': stats, 'parcels': recs}, open(os.path.join(OUT, 'county_reread_Summit.json'), 'w'), indent=1)
    print('\n=== Summit SUMMARY ===\n' + json.dumps(stats, indent=1))
    for x in recs:
        if x['status'] == 'not_confirmed': print(f"  NOT CONFIRMED {x['parcel']:24} {x['lead'][:34]:36} {x['reason']}")
    return stats


# ── Summit FULL re-read: every held parcel, owner + mailing + 2026 value from the Assessor's EagleWeb account page ──
# Same standard as Salt Lake and Wasatch: a parcel is confirmed only when a lead name is still on title; confirmed rows
# get the Assessor's mailing address, 2026 value and a fresh fetched_at; anything else is left unchanged and listed.
# Resumes on its own: it only selects parcels not yet fetched today (Denver), and stops cleanly at MAX_MINUTES.
SUMMIT_STOP = {'DECEASED', 'SUC', 'TRUSTEE', 'TRUSTRE', 'JR', 'SR', 'III', 'II', 'JT', 'TR', 'TEE', 'THE', 'AND', 'LIVING',
               'TRUST', 'FAMILY', 'REVOCABLE', 'IRA', 'CUSTODIAN', 'PLAN', 'LLC', 'INC', 'CO', 'TRS', 'ETAL', 'H/W'}


def summit_toks(s):
    return [w for w in re.findall(r'[A-Z]+', (s or '').upper()) if len(w) > 2 and w not in SUMMIT_STOP]


def summit_page(sess, B, ser):
    res = sess.get(B + '/taxweb/results.jsp', params={'ParcelNumID': ser, 'docSearch': 'Search'}, timeout=40)
    accts = list(dict.fromkeys(re.findall(r'account\.jsp\?accountNum=(\w+)', res.text)))
    for a in accts[:3]:
        raw = sess.get(B + '/taxweb/account.jsp', params={'accountNum': a}, timeout=40).text
        txt = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', raw)))
        pn = re.search(r'Parcel Number (\S+)', txt)
        if not pn or pn.group(1) != ser: continue
        m = re.search(r'Market \((20\d\d)\) \$([\d,]+)', txt)
        i = raw.find('<b>Name</b>'); j = raw.find('<b>Market</b>', i)
        block = raw[i:j] if i >= 0 and j > i else ''
        owners = [html.unescape(re.sub(r'<[^>]+>', ' ', x)).strip() for x in re.findall(r'<b>Name</b>(.*?)</td>', block, re.S)]
        addr_cells = [x for x in re.findall(r'<b></b>(.*?)</td>', block, re.S)]
        lines = []
        for cell in addr_cells:
            for ln in re.split(r'<br\s*/?>', cell):
                ln = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', ln))).strip()
                if ln and not ln.upper().startswith('C/O'): lines.append(ln)
        mailing = None
        if len(lines) >= 2:
            city = re.sub(r'\s*,\s*', ' ', lines[-1]).strip()
            mailing = f"{', '.join(lines[:-1])}, {re.sub(r' +', ' ', city)}"   # each county address line kept as its own segment (PO BOX 748, 1031 WOODSIDE AVE, ...)
        return {'account': a, 'year': int(m.group(1)) if m else None, 'value': int(m.group(2).replace(',', '')) if m else None,
                'owners': owners, 'mailing': mailing}
    return {'error': f'assessor has no account page for this parcel number ({len(accts)} search results)'}


def run_summit_full(only=None):
    import threading, concurrent.futures as cf
    B = 'https://property.summitcounty.org/eaglesoftware'
    today_start = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-7))).replace(hour=0, minute=0, second=0, microsecond=0)
    since = today_start.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    held = page('pp_parcel_intel?select=parcel_serial&county=eq.Summit&order=parcel_serial')
    rows = page('pp_parcel_intel?select=parcel_serial,owner_key,buyer_key,mailing_address,market_value,value_year,property_address' +
                (f'&county=eq.Summit&parcel_serial=in.({",".join("SUMMIT:"+x for x in only)})&order=parcel_serial' if only else
                 f'&county=eq.Summit&or=(fetched_at.is.null,fetched_at.lt.{since})&order=parcel_serial'))
    log.info(f'Summit full re-read: {len(held)} parcels held, {len(rows)} not yet read today (since {since})')
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    stats = {'parcels_held': len(held), 'parcels_to_read_this_run': len(rows), 'parcels_read': 0, 'confirmed': 0, 'value_confirmed': 0,
             'changed': 0, 'mailing_changed': 0, 'value_changed': 0, 'unchanged': 0, 'not_confirmed': 0, 'value_not_confirmed': 0,
             'write_errors': 0}
    reasons, recs, lock = {}, [], threading.Lock()
    deadline = time.time() + 60 * float(os.environ.get('MAX_MINUTES', '90'))
    local = threading.local()

    def sess():
        if not getattr(local, 's', None):
            local.s = requests.Session(); local.s.headers['User-Agent'] = 'Mozilla/5.0'
            local.s.get(B + '/web/loginPOST.jsp?guest=true&submit=Public+Login', timeout=40)
        return local.s

    def one(r):
        if time.time() > deadline: return None
        pid, ser = r['parcel_serial'], r['parcel_serial'].replace('SUMMIT:', '')
        lead = lead_name(r)
        rec = {'parcel': pid, 'lead': lead, 'old': {'mailing': r['mailing_address'], 'value': float(r['market_value']) if r['market_value'] is not None else None, 'year': r['value_year']}}
        got = None
        for attempt in range(3):
            try:
                got = summit_page(sess(), B, ser); break
            except Exception as e:
                got = {'error': f'assessor site error after 3 tries: {type(e).__name__}'}; local.s = None; time.sleep(4)
        line = None
        with lock:
            if 'error' in got:
                stats['not_confirmed'] += 1; why = got['error']
                rec.update({'status': 'not_confirmed', 'reason': why}); reasons[why.split(' (')[0].split(':')[0]] = reasons.get(why.split(' (')[0].split(':')[0], 0) + 1
                recs.append(rec); return f"PARCEL {pid} | {lead[:40]} | NOT CONFIRMED | {why}"
            stats['parcels_read'] += 1; rec['county'] = got
            owner = ' / '.join(got['owners'])
            shared = sorted(set(summit_toks(lead)) & set(summit_toks(owner)))
            if not lead or not shared or not got['mailing']:
                why = (f'county owner is now "{owner}" — no lead name on title (sold, renamed or different person); row left unchanged'
                       if lead and not shared else 'assessor page has no mailing address' if lead else 'no lead linked to this parcel')
                stats['not_confirmed'] += 1; rec.update({'status': 'not_confirmed', 'reason': why})
                k = why.split(' "')[0].split(' (')[0]; reasons[k] = reasons.get(k, 0) + 1
                recs.append(rec); return f"PARCEL {pid} | {lead[:40]} | NOT CONFIRMED | {why}"
        value_ok = got['year'] == CURRENT_YEAR and bool(got['value'])
        body = {'mailing_address': got['mailing'], 'is_absentee': absentee(r.get('property_address'), got['mailing']), 'fetched_at': now}
        if value_ok: body.update({'market_value': got['value'], 'value_year': got['year']})
        ok = patch(pid, body)
        mail_ch = norm(rec['old']['mailing']) != norm(got['mailing'])
        val_ch = bool(value_ok and (rec['old']['value'] != float(got['value']) or rec['old']['year'] != got['year']))
        with lock:
            stats['confirmed'] += 1; stats['value_confirmed'] += value_ok; stats['value_not_confirmed'] += not value_ok
            stats['mailing_changed'] += mail_ch; stats['value_changed'] += val_ch
            stats['changed' if (mail_ch or val_ch) else 'unchanged'] += 1; stats['write_errors'] += not ok
            rec.update({'status': 'confirmed', 'shared_names': shared, 'changed': mail_ch or val_ch,
                        'written': {k: body.get(k) for k in ('mailing_address', 'market_value', 'value_year')}})
            if not value_ok: rec['value_reason'] = f"assessor shows no {CURRENT_YEAR} market value (year {got['year']}, value {got['value']})"
            recs.append(rec)
        return (f"PARCEL {pid} | {lead[:40]} | {'CHANGED' if (mail_ch or val_ch) else 'UNCHANGED'} | mailing {got['mailing'][:50]} | "
                f"value {got['value'] if value_ok else 'not confirmed'} ({got['year']}) | written={ok}")

    workers = int(os.environ.get('SUMMIT_WORKERS', '4'))
    done = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        for line in ex.map(one, rows):
            if line is None: continue
            done += 1; print(line, flush=True)
            if done % 100 == 0: log.info(f'  Summit parcels read: {done}/{len(rows)}')
    stats['stopped_early_for_time'] = done < len(rows)
    if stats['stopped_early_for_time']: stats['parcels_left_for_next_run'] = len(rows) - done
    stats['not_confirmed_reasons'] = reasons; stats['dry_run'] = DRY
    stats['finished_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    json.dump({'county': 'Summit', 'mode': 'full', 'summary': stats, 'parcels': recs}, open(os.path.join(OUT, 'county_reread_Summit.json'), 'w'), indent=1)
    print('\n=== Summit (full) SUMMARY ===\n' + json.dumps(stats, indent=1))
    for x in sorted(recs, key=lambda x: x['parcel']):
        if x['status'] == 'not_confirmed': print(f"  NOT CONFIRMED {x['parcel']:24} {x['lead'][:34]:36} {x['reason']}")
        elif x.get('value_reason'): print(f"  VALUE NOT CONFIRMED {x['parcel']:24} {x['lead'][:34]:36} {x['value_reason']}")
    return stats


UTAH_STOP = SUMMIT_STOP | {'PERREP', 'SUCTEE', 'DEC', 'AKA', 'EST', 'ESTATE', 'PERSONAL', 'REPRESENTATIVE'}


def utah_toks(s):
    return [w for w in re.findall(r'[A-Z]+', (s or '').upper()) if len(w) > 2 and w not in UTAH_STOP]


def utah_page(sess, ser):
    """Utah County Land Records Property.asp: current owners (open-ended '2025...' rows), mailing, market value."""
    r = sess.get('https://www.utahcounty.gov/LandRecords/Property.asp', params={'av_serial': ser.replace(':', '')}, timeout=45)
    if r.status_code != 200: raise IOError(f'HTTP {r.status_code}')
    t = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', r.text, flags=re.S | re.I)
    t = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', t)))
    if 'Serial Number' not in t: return {'error': 'county has no property page for this serial (retired, split or renumbered)'}
    seg = re.search(r'Photos Documents (.*?) ----- Real Estate Values', t)
    owners = []
    if seg:
        for spec, name in re.findall(r'(\d{4}(?:\.\.\.|NV|-\d{4})?)\s+(.+?)(?=\s\d{4}(?:\.\.\.|NV|-\d{4})?\s|$)', seg.group(1)):
            if spec.endswith('...') and name.strip() not in owners: owners.append(name.strip())
    ended = None
    if seg and not owners:   # no open-ended owner row: the parcel record stops (retired, split or renumbered)
        rows_ = re.findall(r'(\d{4})(?:\.\.\.|NV|-(\d{4}))?\s+(.+?)(?=\s\d{4}(?:\.\.\.|NV|-\d{4})?\s|$)', seg.group(1))
        if rows_:
            last = max(int(e or y) for y, e, _ in rows_)
            ended = {'year': last, 'last_owners': sorted({n.strip() for y, e, n in rows_ if int(e or y) == last})}
    mv = re.search(r'Market Value\s+(\d{4})\s+((?:\$[\d,]+\s+){4,})', t)
    vals = re.findall(r'\$([\d,]+)', mv.group(2)) if mv else []
    ma = re.search(r'Mailing Address:\s*(.*?)\s*Acreage:', t)
    return {'year': int(mv.group(1)) if mv else None, 'value': int(vals[-1].replace(',', '')) if vals else None,
            'owners': owners, 'mailing': (ma.group(1).strip() if ma else None) or None, 'ended': ended}


def run_utah_full(only=None):
    import threading, concurrent.futures as cf
    today_start = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-7))).replace(hour=0, minute=0, second=0, microsecond=0)
    since = today_start.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    held = page('pp_parcel_intel?select=parcel_serial&county=eq.Utah&order=parcel_serial')
    sel = 'pp_parcel_intel?select=parcel_serial,owner_key,buyer_key,mailing_address,market_value,value_year,property_address&county=eq.Utah'
    rows = page(sel + (f'&parcel_serial=in.({",".join(only)})' if only else f'&or=(fetched_at.is.null,fetched_at.lt.{since})') + '&order=parcel_serial')
    log.info(f'Utah full re-read: {len(held)} parcels held, {len(rows)} to read (since {since})')
    leads_by_parcel = {}
    for x in page('rpc/pp_parcel_lead_keys?p_county=Utah&order=parcel_serial,owner_key'):
        leads_by_parcel.setdefault(x['parcel_serial'], []).append(x['owner_key'].split(':', 2)[-1])
    log.info(f'  lead names found through recorded filings for {len(leads_by_parcel)} parcels')
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    stats = {'parcels_held': len(held), 'parcels_to_read_this_run': len(rows), 'parcels_read': 0, 'confirmed': 0, 'value_confirmed': 0,
             'changed': 0, 'mailing_changed': 0, 'value_changed': 0, 'unchanged': 0, 'not_confirmed': 0, 'value_not_confirmed': 0,
             'write_errors': 0}
    reasons, recs, lock = {}, [], threading.Lock()
    deadline = time.time() + 60 * float(os.environ.get('MAX_MINUTES', '90'))
    local = threading.local()

    def sess():
        if not getattr(local, 's', None):
            local.s = requests.Session(); local.s.headers['User-Agent'] = 'Mozilla/5.0'
        return local.s

    def one(r):
        if time.time() > deadline: return None
        pid = r['parcel_serial']; lead = lead_name(r) or ' / '.join(leads_by_parcel.get(r['parcel_serial'], []))
        rec = {'parcel': pid, 'lead': lead, 'old': {'mailing': r['mailing_address'], 'value': float(r['market_value']) if r['market_value'] is not None else None, 'year': r['value_year']}}
        got = None
        for attempt in range(3):
            try:
                got = utah_page(sess(), pid); break
            except Exception as e:
                got = {'error': f'county site error after 3 tries: {type(e).__name__} {e}'[:120]}; local.s = None; time.sleep(5)
        with lock:
            if 'error' in got:
                stats['not_confirmed'] += 1; why = got['error']; k = why.split(' (')[0].split(':')[0]
                rec.update({'status': 'not_confirmed', 'reason': why}); reasons[k] = reasons.get(k, 0) + 1
                recs.append(rec); return f"PARCEL {pid} | {lead[:40]} | NOT CONFIRMED | {why}"
            stats['parcels_read'] += 1; rec['county'] = got
            owner = ' / '.join(got['owners'])
            shared = sorted(set(utah_toks(lead)) & set(utah_toks(owner)))
            if not lead or not got['owners'] or not shared or not got['mailing']:
                why = ((f"county record for this parcel ends in {got['ended']['year']} (retired, split or renumbered); last owner listed: {' / '.join(got['ended']['last_owners'])[:80]}"
                        if got.get('ended') else 'county page lists no current owner') if not got['owners'] else
                       f'county owner is now "{owner[:90]}" — no lead name on title (sold, renamed or different person); row left unchanged'
                       if lead and not shared else 'county page has no mailing address' if lead else 'no lead linked to this parcel')
                stats['not_confirmed'] += 1; rec.update({'status': 'not_confirmed', 'reason': why})
                k = why.split(' "')[0].split(' (')[0]; reasons[k] = reasons.get(k, 0) + 1
                recs.append(rec); return f"PARCEL {pid} | {lead[:40]} | NOT CONFIRMED | {why}"
        value_ok = got['year'] == CURRENT_YEAR and bool(got['value'])
        body = {'mailing_address': got['mailing'], 'is_absentee': absentee(r.get('property_address'), got['mailing']), 'fetched_at': now}
        if value_ok: body.update({'market_value': got['value'], 'value_year': got['year']})
        ok = patch(pid, body)
        mail_ch = (rec['old']['mailing'] or '') != got['mailing']
        val_ch = bool(value_ok and (rec['old']['value'] != float(got['value']) or rec['old']['year'] != got['year']))
        with lock:
            stats['confirmed'] += 1; stats['value_confirmed'] += value_ok; stats['value_not_confirmed'] += not value_ok
            stats['mailing_changed'] += mail_ch; stats['value_changed'] += val_ch
            stats['changed' if (mail_ch or val_ch) else 'unchanged'] += 1; stats['write_errors'] += not ok
            rec.update({'status': 'confirmed', 'shared_names': shared, 'changed': mail_ch or val_ch,
                        'written': {k: body.get(k) for k in ('mailing_address', 'market_value', 'value_year')}})
            if not value_ok: rec['value_reason'] = f"county shows no {CURRENT_YEAR} market value (year {got['year']}, value {got['value']})"
            recs.append(rec)
        return (f"PARCEL {pid} | {lead[:40]} | {'CHANGED' if (mail_ch or val_ch) else 'UNCHANGED'} | mailing {got['mailing'][:50]} | "
                f"value {got['value'] if value_ok else 'not confirmed'} ({got['year']}) | written={ok}")

    done = 0
    with cf.ThreadPoolExecutor(int(os.environ.get('UTAH_WORKERS', '4'))) as ex:
        for line in ex.map(one, rows):
            if line is None: continue
            done += 1; print(line, flush=True)
            if done % 200 == 0: log.info(f'  Utah parcels read: {done}/{len(rows)}')
    stats['stopped_early_for_time'] = done < len(rows)
    if stats['stopped_early_for_time']: stats['parcels_left_for_next_run'] = len(rows) - done
    stats['not_confirmed_reasons'] = reasons; stats['dry_run'] = DRY
    stats['finished_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    json.dump({'county': 'Utah', 'mode': 'full', 'summary': stats, 'parcels': recs}, open(os.path.join(OUT, 'county_reread_Utah.json'), 'w'), indent=1)
    print('\n=== Utah (full) SUMMARY ===\n' + json.dumps(stats, indent=1))
    for x in sorted(recs, key=lambda x: x['parcel']):
        if x['status'] == 'not_confirmed': print(f"  NOT CONFIRMED {x['parcel']:24} {x['lead'][:34]:36} {x['reason']}")
        elif x.get('value_reason'): print(f"  VALUE NOT CONFIRMED {x['parcel']:24} {x['lead'][:34]:36} {x['value_reason']}")
    return stats


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
                       'confirmed_same_family': 0, 'write_errors': 0}
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
            owners = [o for o in (c.get('owner'), c.get('assessor_owner')) if o]
            if c.get('roll_read') is False: why = 'county tax roll could not be read for this parcel (server error after 3 tries)'
            elif not owner: why = 'county roll has no owner name for this parcel'
            elif any(matches(lead, o) for o in owners): pass
            elif same_family(lead, owners, county):
                rec['owner_note'] = f'county owner is now "{owner[:60]}" — same surname as the lead (spouse/heir), mailing kept as the family\'s'
                stats['confirmed_same_family'] += 1
            else: why = f'county owner is now "{owner[:60]}" — does not match the lead (sold, renamed or different person); row left unchanged'
            if not why and not c.get('mailing'): why = 'county has no mailing address on file'
        if why:
            stats['not_confirmed'] += 1; rec['status'] = 'not_confirmed'; rec['reason'] = why
            key = why.split(' "')[0].split(' (')[0]; reasons[key] = reasons.get(key, 0) + 1
            recs.append(rec); print(f"PARCEL {pid} | {lead[:40]} | NOT CONFIRMED | {why}", flush=True); continue
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
        print(f"PARCEL {pid} | {lead[:40]} | {'CHANGED' if changed else 'UNCHANGED'} | mailing {c['mailing'][:50]} | "
              f"value {c.get('value') if value_ok else 'not confirmed'} ({c.get('year')}) | written={ok}", flush=True)
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
        if county == 'none': log.info('COUNTIES=none: no re-read; recompute + integrity gate only')
        elif county == 'Summit-full': run_summit_full()
        elif county == 'Utah-full': run_utah_full()
        elif county.startswith('Utah-full:'): run_utah_full([x.strip() for x in county.split(':', 1)[1].split('|') if x.strip()])
        elif county.startswith('Summit-full:'): run_summit_full([x.strip() for x in county.split(':', 1)[1].split('|') if x.strip()])
        elif county == 'Summit': run_summit_values()
        else: run(county)
