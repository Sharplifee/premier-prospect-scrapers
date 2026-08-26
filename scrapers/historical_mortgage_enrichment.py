"""
Historical mortgage enrichment — finds the OLDEST OUTSTANDING trust deed per owner.

Why this exists: the daily recorder sweep only reads the last 30 days of filings,
so it only ever sees BRAND-NEW debt (zero equity). The high-value lead — a
homeowner in distress sitting on a 15-year-old, mostly-paid-down mortgage — is
invisible to it. This walks the recorder's party-name index instead:

  1. PartyName.asp?avname=<OWNER>&avkoigroup=2   -> matching parties (+ doc counts)
  2. PartyDetail.asp?avnameptr=<ptr>&avkoigroup=2 -> that party's mortgage history
     Row layout: [lender, "entry;year", rec_date, koi, suffix, party_type]
     The row BELOW each document carries the legal description and, in its 4th
     cell, the release marker "R Entry NNNNN Year YYYY" when the loan was repaid.

KOI group 2 = Mortgage. Only TR D / D TR count as trust deeds.
Throttled deliberately: this is 2+ requests per owner against a county server,
so it runs as a slow background enrichment, never inside the daily pipeline.
"""
import os, re, json, time, logging, requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('histloan')

SUPABASE_URL = os.environ['SUPABASE_URL'].rstrip('/')
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
H = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}',
     'Content-Type': 'application/json'}
BASE = 'https://www.utahcounty.gov/LandRecords'
S = requests.Session()
S.headers['User-Agent'] = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36')

def get(path, params, tries=3):
    for i in range(tries):
        try:
            r = S.get(f'{BASE}/{path}', params=params, timeout=60)
            if r.status_code == 200:
                return r
            # utahcounty.gov intermittently 500s even when healthy — retry.
            log.warning(f'{path} HTTP {r.status_code} (attempt {i+1})')
        except Exception as e:
            log.warning(f'{path} {type(e).__name__} (attempt {i+1})')
        time.sleep(5)
    return None

def cells(html_row):
    return [re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', c)).replace('\xa0', ' ').strip()
            for c in re.findall(r'<td[^>]*>(.*?)</td>', html_row, re.S | re.I)]

def index_name(raw):
    """
    Normalize an owner name into the form the county party index expects.

    Verified Aug 2026: the index matches on "SURNAME, FIRSTNAME" only. Any
    co-owner or suffix kills the match outright —
      "BRAITHWAITE, BRADEN & SAMANTHA" -> 0 records
      "BRAITHWAITE, BRADEN"            -> 1 record
      "BISHOP, C SCOTT (ET AL)"        -> 0 records
      "BISHOP, C SCOTT"                -> 1 record
    So strip parenthetical suffixes, trustee/representative wording, and drop
    everything after an ampersand (the second spouse).
    """
    if not raw:
        return None
    n = raw.upper()
    n = re.sub(r'\([^)]*\)', ' ', n)                                  # (ET AL), (ET UX)
    n = re.sub(r'\b(TEE|TRUSTEE|SUCTEE|PERREP|PER REP|DEC|ESTATE OF)\b', ' ', n)
    n = n.split('&')[0]                                              # drop co-owner
    n = re.sub(r'[^A-Z, ]', ' ', n)
    n = re.sub(r'\s+', ' ', n).strip().strip(',').strip()
    if ',' in n:
        sur, _, rest = n.partition(',')
        n = f"{sur.strip()}, {rest.strip()}".strip().strip(',').strip()
    return n or None


def find_party_ptrs(owner_name):
    """Return name-pointers for an owner, best-match (most documents) first."""
    r = get('PartyName.asp', {'avname': owner_name, 'avkoigroup': '2', 'Submit': 'Submit'})
    if not r:
        return []
    ptrs = []
    for m in re.finditer(r'<tr[^>]*>(.*?)</tr>', r.text, re.S | re.I):
        block = m.group(1)
        p = re.search(r'PartyDetail\.asp\?avnameptr=(\d+)', block)
        if not p:
            continue
        c = cells(block)
        docs = 0
        for x in c:
            if x.isdigit():
                docs = max(docs, int(x))
        ptrs.append((p.group(1), docs))
    ptrs.sort(key=lambda x: -x[1])
    return ptrs[:2]          # only the two strongest name matches

def oldest_outstanding(ptr):
    """Oldest un-released trust deed for a party pointer."""
    r = get('PartyDetail.asp', {'avnameptr': ptr, 'avkoigroup': '2'})
    if not r:
        return None
    rows = [cells(m.group(1)) for m in re.finditer(r'<tr[^>]*>(.*?)</tr>', r.text, re.S | re.I)]
    best = None
    for i, c in enumerate(rows):
        if len(c) < 4:
            continue
        koi = c[3].strip()
        if koi not in ('TR D', 'D TR'):
            continue
        em = re.match(r'(\d+);(\d{4})', c[1].strip())
        dm = re.match(r'(\d+/\d+/\d{4})', c[2].strip())
        if not (em and dm):
            continue
        # the following row carries the release marker in its 4th cell
        released = False
        if i + 1 < len(rows) and len(rows[i + 1]) >= 4:
            released = bool(re.search(r'R\s+Entry\s+\d+', rows[i + 1][3]))
        if released:
            continue
        rec = dm.group(1)
        yr = int(em.group(2))
        if best is None or yr < best['year']:
            best = {'entry': em.group(1), 'year': yr, 'rec_date': rec,
                    'lender': c[0][:80], 'koi': koi}
    return best

def main(limit=40):
    # Highest-conviction, contactable owners with no loan data yet.
    q = (f'{SUPABASE_URL}/rest/v1/pp_entity_conviction'
         f'?select=entity_key,owner_display,conviction_score'
         f'&contactable=is.true&resolved_flag=is.false'
         f'&oldest_loan_years=is.null&county=eq.Utah'
         f'&order=conviction_score.desc&limit={limit}')
    targets = requests.get(q, headers=H, timeout=30).json()
    log.info(f'{len(targets)} owners to enrich')

    written = 0
    for t in targets:
        raw_name = (t.get('owner_display') or '').strip()
        name = index_name(raw_name)
        if not name:
            continue
        ptrs = find_party_ptrs(name)
        time.sleep(1.5)
        if not ptrs:
            log.info(f'  {name[:34]:36} no mortgage records')
            continue
        found = None
        for ptr, _docs in ptrs:
            found = oldest_outstanding(ptr)
            time.sleep(1.5)
            if found:
                break
        if not found:
            log.info(f'  {name[:34]:36} no OUTSTANDING trust deed')
            continue
        payload = [{
            'source_slug': 'utah-historical-mortgage',
            'signal_type': 'historical_mortgage',
            'score': 30,                       # context only — never anchors a lead
            'county': 'Utah',
            'raw_owner_name': raw_name,
            'raw_address': f"Trust Deed — Entry #{found['entry']} ({found['year']})",
            'raw_payload': json.dumps({
                'entry': found['entry'], 'loan_year': found['year'],
                'loan_date': found['rec_date'], 'lender': found['lender'],
                'koi': found['koi'], 'released': False,
                'source': 'PartyDetail historical index',
            }),
        }]
        w = requests.post(f'{SUPABASE_URL}/rest/v1/pp_scraper_signals',
                          json=payload,
                          headers={**H, 'Prefer': 'return=minimal,resolution=ignore-duplicates'},
                          timeout=30)
        if w.status_code < 400:
            written += 1
            age = 2026 - found['year']
            log.info(f"  {name[:34]:36} oldest outstanding {found['year']} ({age}y) — {found['lender'][:26]}")
        else:
            log.error(f'  {name[:34]:36} write failed {w.status_code}: {w.text[:120]}')
    log.info(f'enriched {written} owners')
    return written

if __name__ == '__main__':
    main(int(os.environ.get('HIST_LIMIT', '40')))
