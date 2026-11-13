#!/usr/bin/env python3
"""
One-time / on-demand: give stored Utah County recorder distress documents the parcel number their own county
document page lists (plus the tax-notice address and recording party, kept as context only).

Why: an affidavit of death, probate deed, lien or notice of default arrives from the recorder index with a name and
an entry number but no parcel. Without the parcel the assessor lookup (parcel_intel.py) cannot confirm the owner's
mailing address or the property's value, so the lead stays "not reachable". The nightly sweep now captures the parcel
for new documents; this script fills in the ones already stored. Highest-conviction leads first.

Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, DOC_LIMIT (default 600), DRY=1 to print without writing.
"""
import os, sys, json, time, logging, requests
sys.path.insert(0, os.path.dirname(__file__))
import run_all

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s'); log = logging.getLogger('utah-doc')
SB = os.environ['SUPABASE_URL'].rstrip('/'); KEY = os.environ['SUPABASE_SERVICE_KEY']
H = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'}
LIMIT = int(os.environ.get('DOC_LIMIT', '600'))
DRY = os.environ.get('DRY') == '1'
TYPES = ('death_affidavit', 'probate_deed', 'lien_judgment', 'mechanics_lien', 'nod', 'nod_cancelled',
         'trustee_substitution', 'lis_pendens', 'trustee_deed')


def payload(row):
    p = row.get('raw_payload')
    for _ in range(2):
        if isinstance(p, str):
            try: p = json.loads(p)
            except Exception: return {}
    return p if isinstance(p, dict) else {}


def main():
    # Open leads by conviction, so the most valuable documents are fixed first if the run is cut short.
    conv, off = {}, 0
    while True:
        r = requests.get(f'{SB}/rest/v1/pp_entity_conviction?select=entity_key,conviction_score&county=eq.Utah&resolved_flag=is.false'
                         f'&limit=1000&offset={off}', headers=H, timeout=60).json()
        if not r: break
        conv.update((x['entity_key'], x['conviction_score'] or 0) for x in r); off += len(r)
    rows, off = [], 0
    while True:
        r = requests.get(f'{SB}/rest/v1/pp_scraper_signals?select=id,owner_key,signal_type,raw_payload'
                         f'&source_slug=eq.utah-recorder-unified&parcel_serial=is.null&is_legacy=eq.false'
                         f'&signal_type=in.({",".join(TYPES)})&limit=1000&offset={off}', headers=H, timeout=60).json()
        if not r: break
        rows += r; off += len(r)
    # A document the county has not abstracted yet lists no parcel; it is re-checked a week later, not every night.
    week_ago = time.strftime('%Y-%m-%d', time.gmtime(time.time() - 7 * 86400))
    rows = [x for x in rows if x.get('owner_key') in conv and (payload(x).get('doc_checked') or '') < week_ago]
    rows.sort(key=lambda x: -conv[x['owner_key']])
    log.info(f'{len(rows)} stored distress documents with no parcel (open leads); fixing up to {LIMIT}')
    fixed = nopage = noserial = 0
    for x in rows[:LIMIT]:
        p = payload(x)
        entry = p.get('entry')
        if not entry: continue
        page = run_all._doc_page(entry)
        if not page:
            nopage += 1; continue
        parcel, why = run_all._doc_parcel(entry, page)
        if not parcel:
            noserial += 1; log.info(f"  {x['signal_type']:18} {entry:12} {why}")
            if not DRY:
                p['doc_checked'] = time.strftime('%Y-%m-%d')
                requests.patch(f"{SB}/rest/v1/pp_scraper_signals?id=eq.{x['id']}", headers={**H, 'Prefer': 'return=minimal'},
                               json={'raw_payload': json.dumps(p)}, timeout=30)
            continue
        for f in ('serials', 'tax_address', 'rec_party'):
            if page.get(f): p[f] = page[f]
        p['parcel_source'] = why
        body = {'parcel_serial': parcel, 'raw_payload': json.dumps(p)}
        if DRY:
            log.info(f"  DRY {x['signal_type']:18} {entry:12} -> {parcel} ({why}) | {page.get('tax_address')}")
            fixed += 1; continue
        u = requests.patch(f"{SB}/rest/v1/pp_scraper_signals?id=eq.{x['id']}", headers={**H, 'Prefer': 'return=minimal'},
                           json=body, timeout=30)
        if u.status_code < 300: fixed += 1
        else: log.error(f"  patch {x['id']} {u.status_code} {u.text[:120]}")
    log.info(f'parcel added: {fixed} · page unreachable: {nopage} · no proven parcel: {noserial}')


if __name__ == '__main__':
    main()
