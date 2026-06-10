"""
Premier Prospect™ — Real-Time Source Upgrades
1. scrape_hmda_slc_county   — CFPB HMDA 2024/2025 live API (was 2023 static)
2. scrape_hmda_utah_county  — same upgrade
3. scrape_slco_recorder     — Salt Lake County Recorder live NTS/NOD/Deed/Lien
4. scrape_utah_statewide_parcels — AGRC parcel enrichment, all counties
These replace or supplement the old static scrapers.
"""
import os, csv, io, json, requests, hashlib, datetime, logging, time
from bs4 import BeautifulSoup

log = logging.getLogger('pp.scrapers')

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
TABLE_URL = f"{SUPABASE_URL}/rest/v1/pp_scraper_signals"
HEADERS = {
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'apikey': SUPABASE_KEY,
    'Prefer': 'return=minimal,resolution=ignore-duplicates',
}

SESSION = requests.Session()
SESSION.headers['User-Agent'] = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'

JUNK_NAMES = {
    'grantor','grantee','trustee','successor trustee','recorder','unknown',
    'n/a','na','none','mers','mortgage electronic registration',
    'fannie mae','freddie mac','hud','secretary of housing',
}

JUNK_FRAGMENTS = [
    'bank','mortgage','capital','trust','finance','mers','fannie','freddie',
    'llc','inc','corp','fund','reit','holdings','securities','investment'
]

def clean_owner(name):
    if not name: return None
    name = str(name).strip()
    if len(name) < 3: return None
    nl = name.lower()
    if nl in JUNK_NAMES: return None
    if any(j in nl for j in JUNK_FRAGMENTS): return None
    return name

def clean_addr(addr):
    if not addr: return None
    addr = str(addr).strip()
    return addr[:200] if len(addr) >= 5 else None

def dedupe_hash(source_slug, owner, address):
    key = f"{source_slug}|{owner or ''}|{address or ''}"
    return hashlib.md5(key.encode()).hexdigest()

def post_batch(records):
    if not records: return 0
    seen, unique = set(), []
    for rec in records:
        rec['raw_owner_name'] = clean_owner(rec.get('raw_owner_name'))
        rec['raw_address']    = clean_addr(rec.get('raw_address'))
        h = dedupe_hash(rec.get('source_slug',''), rec.get('raw_owner_name',''), rec.get('raw_address',''))
        rec['dedupe_hash'] = h
        allowed = {'source_slug','raw_address','raw_owner_name','raw_phone','raw_url',
                   'raw_payload','signal_type','score','county','city','captured_at','dedupe_hash'}
        if h not in seen:
            seen.add(h)
            unique.append({k:v for k,v in rec.items() if k in allowed})
    inserted = 0
    for i in range(0, len(unique), 200):
        chunk = unique[i:i+200]
        for attempt in range(3):
            try:
                r = SESSION.post(TABLE_URL, json=chunk, headers=HEADERS, timeout=45)
                if r.status_code in (200, 201, 204, 409):
                    inserted += len(chunk)
                    break
                time.sleep(5)
            except Exception as e:
                log.error(f"Batch insert: {e}")
                if attempt < 2: time.sleep(5)
    return inserted

def safe_get(url, timeout=20, retries=3, **kwargs):
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=timeout, **kwargs)
            if r.status_code == 429:
                time.sleep(int(r.headers.get('Retry-After', 30)))
                continue
            return r
        except Exception as e:
            log.warning(f"GET {url[:60]}: {e}")
            if attempt < retries-1: time.sleep(5)
    return None

# ── LOAN PURPOSE CODES (HMDA) ─────────────────────────────────────────────────
# 1=Home purchase, 2=Home improvement, 31=Refinancing, 32=Cash-out refi, 4=Other
# Scoring: purchase=highest, cash-out refi=medium (possible distress), improvement=low

def hmda_score(row):
    """Score a HMDA record — purchase loan on primary residence = highest buyer signal."""
    purpose = str(row.get('loan_purpose',''))
    occupancy = str(row.get('occupancy_type',''))   # 1=primary, 2=secondary, 3=investment
    dwelling = row.get('derived_dwelling_category','')
    loan_amt = row.get('loan_amount','')
    action = str(row.get('action_taken',''))

    # Only care about funded originations (action=1) or purchases
    if action not in ('1',): return None  # skip denials, withdrawn, pre-approvals

    # Purchase loan on primary residence single family = strongest buyer signal
    if purpose == '1' and occupancy == '1' and 'Single Family' in dwelling:
        try:
            amt = float(loan_amt)
            if amt >= 400000: return 82   # high-value buyer
            if amt >= 250000: return 78
            return 72
        except: return 70

    # Cash-out refi on primary = possible seller signal (extracting equity before selling)
    if purpose == '32' and occupancy == '1':
        return 55

    # Home improvement on primary = stay signal, low interest
    if purpose == '2': return 30

    return None

def scrape_hmda_live(slug, county_fips, county):
    """Pull HMDA 2024 + 2025 (if available) live from CFPB — real-time buyer intent."""
    log.info(f'[{slug}] starting — LIVE CFPB HMDA 2024/2025')
    batch = []
    BASE = "https://ffiec.cfpb.gov/v2/data-browser-api/view/csv"

    for year in ['2024', '2025']:
        params = {
            'states': 'UT',
            'counties': county_fips,
            'years': year,
            'actions_taken': '1',  # originations only
        }
        r = safe_get(BASE, params=params, timeout=60)
        if not r or r.status_code != 200:
            log.warning(f'[{slug}] {year}: {r.status_code if r else "timeout"}')
            continue

        reader = csv.DictReader(io.StringIO(r.text))
        year_count = 0
        for row in reader:
            dwelling = row.get('derived_dwelling_category','')
            if 'Single Family' not in dwelling and 'Manufactured' not in dwelling:
                continue

            score = hmda_score(row)
            if score is None: continue

            tract = row.get('census_tract','')
            loan_amt = row.get('loan_amount','')
            loan_type = row.get('derived_loan_product_type','')
            purpose_code = row.get('loan_purpose','')
            income = row.get('income','')
            prop_val = row.get('property_value','')
            age = row.get('applicant_age','')
            occupancy = row.get('occupancy_type','')

            # Build signal_type from purpose
            sig = 'mortgage_application'
            if purpose_code == '32': sig = 'cash_out_refinance'
            elif purpose_code == '2': sig = 'home_improvement_loan'

            batch.append({
                'source_slug': slug,
                'signal_type': sig,
                'score': score,
                'county': county,
                'city': None,
                'raw_owner_name': None,  # HMDA is anonymized by law
                'raw_address': tract,
                'raw_payload': json.dumps({
                    'year': year,
                    'loan_amount': loan_amt,
                    'loan_type': loan_type,
                    'income_thousands': income,
                    'property_value': prop_val,
                    'applicant_age': age,
                    'occupancy': occupancy,
                    'purpose': purpose_code,
                }),
            })
            year_count += 1

        log.info(f'[{slug}] {year}: {year_count} qualifying records')

    return post_batch(batch)

def scrape_hmda_slc_county():  return scrape_hmda_live('hmda-slc-county', '49035', 'Salt Lake')
def scrape_hmda_utah_county(): return scrape_hmda_live('hmda-utah-county', '49049', 'Utah')

# ── SLCO RECORDER — LIVE NTS / NOD / DEED / LIEN ─────────────────────────────
def scrape_slco_recorder():
    """
    Salt Lake County Recorder — real-time deed recordings, same-day filings.
    Covers NTS, NOD, Warranty Deed, Lien filings in Salt Lake County.
    """
    slug = 'slco-recorder-live'
    log.info(f'[{slug}] starting — recorder.slco.org')

    DOC_TYPES = [
        ('NOTICE OF DEFAULT',         'nod',          88),
        ('NOTICE OF TRUSTEE SALE',    'nts',          99),
        ('TRUSTEE DEED',              'trustee_deed', 85),
        ('WARRANTY DEED',             'deed_transfer', 55),
        ('QUIT CLAIM DEED',           'deed_transfer', 50),
        ('STATE TAX LIEN',            'lien_judgment', 72),
        ('IRS FEDERAL TAX LIEN',      'lien_judgment', 75),
        ('JUDGMENT LIEN',             'lien_judgment', 68),
        ('MECHANICS LIEN',            'lien_judgment', 60),
    ]

    batch = []
    base = "https://recorder.slco.org"

    # First get the search form to extract any hidden tokens
    r0 = safe_get(f"{base}/", timeout=15)
    if not r0 or r0.status_code != 200:
        log.error(f'[{slug}] Cannot reach recorder.slco.org')
        return 0

    soup0 = BeautifulSoup(r0.text, 'html.parser')

    # Find search endpoint — look for form action
    form = soup0.find('form')
    action = form['action'] if form and form.get('action') else '/search'
    search_url = base + action if action.startswith('/') else action

    for doc_name, signal_type, score in DOC_TYPES:
        # Try date range — last 30 days
        today = datetime.date.today()
        from_date = (today - datetime.timedelta(days=30)).strftime('%m/%d/%Y')
        to_date = today.strftime('%m/%d/%Y')

        try:
            r = SESSION.post(search_url, data={
                'docType': doc_name,
                'dateFrom': from_date,
                'dateTo': to_date,
                'submit': 'Search',
                'county': 'SL',
            }, timeout=20)

            if r.status_code != 200:
                # Try GET with query params
                r = safe_get(f"{base}/search", params={
                    'docType': doc_name,
                    'dateFrom': from_date,
                    'dateTo': to_date,
                }, timeout=15)

            if not r or r.status_code != 200:
                log.warning(f'[{slug}] {doc_name}: no response')
                continue

            soup = BeautifulSoup(r.text, 'html.parser')

            # Parse table rows
            for table in soup.find_all('table'):
                for row in table.find_all('tr')[1:]:
                    cells = [td.get_text(strip=True) for td in row.find_all('td')]
                    if not cells or len(cells) < 3: continue

                    # Try to extract grantor/address/entry
                    grantor = next((c for c in cells if len(c) > 4 and not c.isdigit()), None)
                    entry = next((c for c in cells if c.isdigit() and len(c) > 4), None)
                    rec_date = next((c for c in cells if '/' in c and len(c) == 10), None)

                    if grantor or entry:
                        batch.append({
                            'source_slug': slug,
                            'signal_type': signal_type,
                            'score': score,
                            'county': 'Salt Lake',
                            'city': None,
                            'raw_owner_name': grantor,
                            'raw_address': f'Entry #{entry}' if entry else grantor,
                            'raw_payload': json.dumps({
                                'doc_type': doc_name,
                                'rec_date': rec_date or to_date,
                                'entry': entry,
                            }),
                        })

        except Exception as e:
            log.warning(f'[{slug}] {doc_name}: {e}')
            continue

    log.info(f'[{slug}] {len(batch)} raw records before dedup')
    return post_batch(batch)

# ── UTAH AGRC STATEWIDE PARCELS — EXPAND LIR COVERAGE ────────────────────────
def scrape_agrc_parcels(slug, county_name, county_slug, county_label):
    """
    Utah AGRC statewide parcel layer — free, no auth, live ArcGIS FeatureServer.
    Pulls high-value parcels that may indicate distress (low improvement ratio,
    long ownership, or small owner-reported value vs assessment).
    """
    log.info(f'[{slug}] starting — AGRC {county_name}')
    base = f"https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_{county_slug}_LIR/FeatureServer/0/query"

    all_features = []
    offset = 0
    page_size = 500

    while True:
        try:
            r = safe_get(base, params={
                'where': '1=1',
                'outFields': 'PARCEL_ID,PARCEL_ADD,PARCEL_CITY,TOTAL_MKT_VALUE,PRIMARY_RES,PROP_CLASS,BUILT_YR',
                'resultRecordCount': page_size,
                'resultOffset': offset,
                'f': 'json'
            }, timeout=25)

            if not r or r.status_code != 200: break
            data = r.json()
            features = data.get('features', [])
            if not features: break
            all_features.extend(features)
            offset += len(features)
            if len(features) < page_size: break
            if offset >= 5000: break  # cap at 5k per county per run
            time.sleep(0.5)
        except Exception as e:
            log.warning(f'[{slug}] page {offset}: {e}')
            break

    batch = []
    for f in all_features:
        a = f.get('attributes', {})
        addr = a.get('PARCEL_ADD', '')
        city = a.get('PARCEL_CITY', '')
        val = a.get('TOTAL_MKT_VALUE', 0) or 0
        primary_res = a.get('PRIMARY_RES', 'Y')
        prop_class  = a.get('PROP_CLASS', '')
        built_yr    = a.get('BUILT_YR', 0) or 0
        parcel_id   = a.get('PARCEL_ID', '')

        if not addr: continue
        # Score: non-primary, low value, or older build = higher distress signal
        score = 40
        if primary_res == 'N': score = 52            # non-owner-occupied
        if val and val < 50000: score = 58           # low market value
        if built_yr and built_yr < 1970: score = max(score, 50)

        full_addr = f"{addr}, {city}".strip(', ') if city else addr
        batch.append({
            'source_slug': slug,
            'signal_type': 'lir_parcel',
            'score': score,
            'county': county_label,
            'city': city or None,
            'raw_owner_name': None,
            'raw_address': full_addr,
            'raw_payload': json.dumps({
                'parcel_id': parcel_id,
                'market_value': val,
                'primary_res': primary_res,
                'prop_class': prop_class,
                'built_yr': built_yr,
            }),
        })

    return post_batch(batch)

def scrape_slco_lir_parcels():  return scrape_agrc_parcels('slco-lir-parcels', 'Salt Lake', 'SaltLake', 'Salt Lake')
def scrape_davis_lir_parcels(): return scrape_agrc_parcels('davis-lir-parcels', 'Davis', 'Davis', 'Davis')
def scrape_weber_lir_parcels(): return scrape_agrc_parcels('weber-lir-parcels', 'Weber', 'Weber', 'Weber')
# Bonus: Utah County via AGRC (supplementing the direct LIR)
def scrape_utah_county_parcels(): return scrape_agrc_parcels('utah-lir-parcels', 'Utah', 'Utah', 'Utah')
# Wasatch, Summit bonus coverage
def scrape_wasatch_parcels():   return scrape_agrc_parcels('wasatch-lir-parcels', 'Wasatch', 'Wasatch', 'Wasatch')
def scrape_summit_parcels():    return scrape_agrc_parcels('summit-lir-parcels', 'Summit', 'Summit', 'Summit')

if __name__ == '__main__':
    print("Testing upgraded scrapers...")
    import logging
    logging.basicConfig(level=logging.INFO)

    # Test HMDA live
    n = scrape_hmda_slc_county()
    print(f"HMDA SLC 2024: {n} signals")

    n2 = scrape_hmda_utah_county()
    print(f"HMDA Utah 2024: {n2} signals")

    # Test AGRC parcels
    n3 = scrape_slco_lir_parcels()
    print(f"SLCO Parcels: {n3} signals")
