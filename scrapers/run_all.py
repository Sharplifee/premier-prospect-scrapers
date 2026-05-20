"""
Premier Prospect — GitHub Actions Scraper Runner v6
Apify fetch + text-based parsing (no HTML/soup needed).
24 active sources. Fully tested parsers.
"""
import os, hashlib, logging, requests, re

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('pp.scrapers')

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
APIFY_TOKEN  = os.environ.get('APIFY_TOKEN', '')
INGEST_URL   = f"{SUPABASE_URL}/functions/v1/pp-ingest"

TABLE_URL = f"{SUPABASE_URL}/rest/v1/pp_scraper_signals"
TABLE_HEADERS = {
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'apikey': SUPABASE_KEY,
    'Prefer': 'return=minimal',
}
SESSION = requests.Session()
SESSION.headers['User-Agent'] = 'PremierProspect/6.0'

MONTHS = ['January','February','March','April','May','June','July',
          'August','September','October','November','December']

def ensure_run_log_table():
    """Create pp_run_log table if it doesn't exist, using raw SQL via Supabase."""
    pass  # Table must be created via Supabase dashboard SQL editor — see migrations/create_run_log.sql

def write_run_log(slug, signal_count, status='success', error_msg=None):
    """Write a run log entry to pp_run_log."""
    import urllib.request, json as _json
    SUPA_URL = os.environ.get('SUPABASE_URL','')
    SUPA_KEY = os.environ.get('SUPABASE_SERVICE_KEY','')
    if not SUPA_URL or not SUPA_KEY: return
    payload = {
        'source_slug': slug,
        'run_at': __import__('datetime').datetime.utcnow().isoformat() + 'Z',
        'signal_count': signal_count,
        'status': status,
        'error_msg': error_msg,
        'run_number': int(os.environ.get('GITHUB_RUN_NUMBER', 0)),
    }
    try:
        req = urllib.request.Request(
            f"{SUPA_URL}/rest/v1/pp_run_log",
            data=_json.dumps(payload).encode(), method='POST'
        )
        req.add_header('apikey', SUPA_KEY)
        req.add_header('Authorization', f'Bearer {SUPA_KEY}')
        req.add_header('Content-Type', 'application/json')
        req.add_header('Prefer', 'return=minimal')
        with urllib.request.urlopen(req, timeout=5) as r:
            pass
    except Exception:
        pass  # Run log is non-critical — never block scraper execution

def post_signal(slug, owner, address, url, score, county, signal_type):
    payload = {
        'source_slug': slug,
        'raw_owner_name': owner or '',
        'raw_address': address or '',
        'raw_url': url or '',
        'score': score,
        'county': county,
        'signal_type': signal_type,
        'raw_payload': {'signal_type': signal_type, 'source_family': signal_type},
    }
    try:
        r = requests.post(TABLE_URL, json=payload, headers=TABLE_HEADERS, timeout=15)
        return r.status_code in (201, 409)
    except Exception as e:
        log.error(f'POST failed {slug}: {e}')
        return False

def post_signals_batch(records):
    """Batch insert up to 500 signals. Returns count inserted."""
    if not records:
        return 0
    import hashlib as _hl
    seen = set()
    unique = []
    for rec in records:
        h = rec.get('dedupe_hash') or _hl.md5(
            f"{rec['source_slug']}|{rec.get('raw_url','')}|{rec.get('raw_owner_name','')}|{rec.get('raw_address','')}".encode()
        ).hexdigest()
        rec['dedupe_hash'] = h
        if h not in seen:
            seen.add(h)
            unique.append(rec)
    count = 0
    CHUNK = 200
    for i in range(0, len(unique), CHUNK):
        chunk = unique[i:i+CHUNK]
        for attempt in range(3):
            try:
                r = SESSION.post(
                    TABLE_URL, json=chunk,
                    headers={**TABLE_HEADERS, 'Prefer': 'return=minimal,resolution=ignore-duplicates'},
                    timeout=30
                )
                if r.status_code in (200, 201, 409):
                    count += len(chunk)
                    break
                log.error(f'Batch POST failed: {r.status_code} {r.text[:60]}')
            except Exception as e:
                log.error(f'Batch POST error: {e}')
                if attempt < 2:
                    time.sleep(10)
    return count


def apify_text(url):
    """Fetch via Apifyturn plain text lines."""
    try:
        r = SESSION.post(
            f'https://api.apify.com/v2/acts/apify~website-content-crawler/run-sync-get-dataset-items?token={APIFY_TOKEN}&timeout=60',
            json={'startUrls':[{'url':url}],'maxCrawlPages':1,'crawlerType':'cheerio'},
            timeout=90
        )
        if r.status_code == 429:
            log.warning(f'Apify rate limited — waiting 30s')
            time.sleep(30)
            r = SESSION.post(
                f'https://api.apify.com/v2/acts/apify~website-content-crawler/run-sync-get-dataset-items?token={APIFY_TOKEN}&timeout=60',
                json={'startUrls':[{'url':url}],'maxCrawlPages':1,'crawlerType':'cheerio'},
                timeout=90
            )
        data = r.json()
        if isinstance(data, list) and data:
            text = data[0].get('text','') or data[0].get('markdown','') or ''
            return [l.strip() for l in text.split('\n') if l.strip()]
    except Exception as e:
        log.error(f'Apify error {url}: {e}')
    return []

def fetch_json(url, params=None):
    try:
        r = SESSION.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f'JSON fetch failed {url}: {e}')
        return None

# ─── SCRAPERS ────────────────────────────────────────────────────────────────

def scrape_obituaries_herald():
    slug = 'obituaries-herald'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://www.heraldextra.com/obituaries/')
    count = 0
    for i, line in enumerate(lines):
        # Name is followed by a "Month DD, YYYY" date line
        if i+1 < len(lines) and any(lines[i+1].startswith(m) for m in MONTHS):
            name = line
            if 4 < len(name) < 70 and not name[0].isdigit() and '|' not in name:
                if post_signal(slug, name, None, 'https://www.heraldextra.com/obituaries/', 55, 'Utah', 'obituary'):
                    count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_uvhba_directory():
    slug = 'uvhba-directory'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://business.uvhba.com/list/ql/contractors-subcontractors-7')
    count = 0
    for i, line in enumerate(lines):
        # Address line: starts with digit, followed by city/UT line
        if re.match(r'^\d+\s+[A-Z\d]', line) and i+1 < len(lines):
            city_line = lines[i+1]
            if ('UT' in city_line or 'Utah' in city_line) and len(line) < 60:
                address = f"{line}, {city_line}"
                if post_signal(slug, None, address, 'https://business.uvhba.com/list/ql/contractors-subcontractors-7', 30, 'Utah', 'contractor_directory'):
                    count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_probate_court():
    slug = 'probate-court'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://www.utah.gov/pmn/search.html')
    count = 0
    kw = ['probate','estate','decedent','personal representative','letters testamentary']
    for line in lines:
        if any(k in line.lower() for k in kw) and len(line) > 10:
            if post_signal(slug, None, line[:200], 'https://www.utah.gov/pmn/search.html', 70, 'Utah', 'probate_notice'):
                count += 1
            if count >= 30: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_utah_county_tax_delinquency():
    slug = 'utah-county-tax-delinquency'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://propertytax.utah.gov/')
    count = 0
    kw = ['delinquent','tax sale','lien','delinquency','notice','penalty']
    for line in lines:
        if any(k in line.lower() for k in kw) and 8 < len(line) < 300:
            if post_signal(slug, None, line[:200], 'https://propertytax.utah.gov/', 80, 'Utah', 'tax_delinquency'):
                count += 1
            if count >= 30: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_utah_county_nts():
    slug = 'utah-county-nts'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://www.utah.gov/pmn/search.html')
    count = 0
    kw = ['trustee','foreclosure','notice of trustee','notice of sale','default']
    for line in lines:
        if any(k in line.lower() for k in kw) and len(line) > 15:
            if post_signal(slug, None, line[:300], 'https://www.utah.gov/pmn/search.html', 80, 'Utah', 'nts_notice'):
                count += 1
            if count >= 30: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_slc_county_nts():
    slug = 'slc-county-nts'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://www.saltlakecounty.gov/public-notice/')
    count = 0
    kw = ['trustee','foreclosure','default','notice of sale','tax']
    for line in lines:
        if any(k in line.lower() for k in kw) and len(line) > 15:
            if post_signal(slug, None, line[:300], 'https://www.saltlakecounty.gov/public-notice/', 80, 'Salt Lake', 'nts_notice'):
                count += 1
            if count >= 30: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_slc_tax_sale():
    slug = 'slc-county-tax-sale'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://www.saltlakecounty.gov/treasurer/property-taxes/')
    count = 0
    kw = ['delinquent','tax sale','lien','relief','notice','overdue']
    for line in lines:
        if any(k in line.lower() for k in kw) and 8 < len(line) < 300:
            if post_signal(slug, None, line[:200], 'https://www.saltlakecounty.gov/treasurer/property-taxes/', 70, 'Salt Lake', 'tax_sale'):
                count += 1
            if count >= 20: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_wasatch_tax_sale():
    """Wasatch County tax/delinquency — filter for property records, skip article text."""
    slug = 'wasatch-county-tax-sale'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        from bs4 import BeautifulSoup
        for url in [
            'https://wasatch.utah.gov/departments/treasurer/property-tax',
            'https://wasatch.utah.gov/departments/treasurer',
        ]:
            r = SESSION.get(url, timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            for el in soup.find_all(['p','li','td','tr']):
                text = el.get_text(separator=' ', strip=True)
                has_digits = sum(c.isdigit() for c in text) > 3
                has_kw = any(w in text.lower() for w in ['parcel','delinquent','owner','lien','notice of','sale date','tax id','account'])
                is_article = len(text.split()) > 25 and not has_digits
                if has_kw and has_digits and not is_article and 10 < len(text) < 250:
                    if post_signal(slug, None, text[:200], url, 75, 'Wasatch', 'tax_sale'):
                        count += 1
                    if count >= 15: break
            if count > 0: break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_wasatch_county_nts():
    slug = 'wasatch-county-nts'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://wasatch.utah.gov/departments/treasurer')
    count = 0
    kw = ['trustee','foreclosure','default','notice','delinquent']
    for line in lines:
        if any(k in line.lower() for k in kw) and len(line) > 15:
            if post_signal(slug, None, line[:300], 'https://wasatch.utah.gov/departments/treasurer', 75, 'Wasatch', 'nts_notice'):
                count += 1
            if count >= 20: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_slc_assessor():
    slug = 'slc-assessor'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://www.saltlakecounty.gov/assessor/')
    count = 0
    kw = ['parcel','property','value','appeal','lookup','search','data','assessment']
    for line in lines:
        if any(k in line.lower() for k in kw) and 8 < len(line) < 200:
            if post_signal(slug, None, line[:200], 'https://www.saltlakecounty.gov/assessor/', 40, 'Salt Lake', 'assessor_record'):
                count += 1
            if count >= 15: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_slc_real_estate():
    slug = 'slc-real-estate'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://www.saltlakecounty.gov/real-estate/public-sale/')
    count = 0
    for line in lines:
        if any(c.isdigit() for c in line) and 15 < len(line) < 400:
            if post_signal(slug, None, line[:300], 'https://www.saltlakecounty.gov/real-estate/public-sale/', 50, 'Salt Lake', 'surplus_property'):
                count += 1
            if count >= 30: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_slc_public_surplus():
    slug = 'slc-public-surplus'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://www.saltlakecounty.gov/real-estate/')
    count = 0
    kw = ['sale','surplus','property','real estate','auction','available']
    for line in lines:
        if any(k in line.lower() for k in kw) and 8 < len(line) < 200:
            if post_signal(slug, None, line[:200], 'https://www.saltlakecounty.gov/real-estate/', 45, 'Salt Lake', 'surplus'):
                count += 1
            if count >= 15: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_wasatch_public_surplus():
    slug = 'wasatch-public-surplus'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://www.publicsurplus.com/sms/wasatchco,ut/list/current')
    count = 0
    for line in lines:
        if any(c.isdigit() for c in line) and 10 < len(line) < 400:
            if post_signal(slug, None, line[:300], 'https://www.publicsurplus.com/sms/wasatchco,ut/list/current', 40, 'Wasatch', 'surplus_auction'):
                count += 1
            if count >= 30: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_south_slc_permits():
    slug = 'south-slc-permits'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://www.southsaltlake.org/172/Building-Permits')
    count = 0
    for line in lines:
        if any(w in line.lower() for w in ['permit','building','construction','inspection','address']) and len(line) > 10:
            if post_signal(slug, None, line[:200], 'https://www.southsaltlake.org/172/Building-Permits', 35, 'Salt Lake', 'permit'):
                count += 1
            if count >= 20: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_south_slc_permits_pdf():
    slug = 'south-slc-permits-pdf'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://www.southsaltlake.org/172/Building-Permits')
    count = 0
    for line in lines:
        if any(w in line.lower() for w in ['permit','pdf','download','form','application']) and len(line) > 5:
            if post_signal(slug, None, line[:200], 'https://www.southsaltlake.org/172/Building-Permits', 35, 'Salt Lake', 'permit_pdf'):
                count += 1
            if count >= 15: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_utah_county_codev():
    slug = 'utah-county-codev'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://www.utahcounty.gov/Dept/ComDev/CodeEnforcement.asp')
    count = 0
    kw = ['violation','complaint','nuisance','code enforcement','property','zoning','unsafe']
    for line in lines:
        if any(w in line.lower() for w in kw) and 20 < len(line) < 400:
            if post_signal(slug, None, line[:300], 'https://www.utahcounty.gov/Dept/ComDev/CodeEnforcement.asp', 50, 'Utah', 'code_enforcement'):
                count += 1
            if count >= 20: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_utah_county_directory():
    slug = 'utah-county-directory'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://recorder.utahcounty.gov/find-records')
    count = 0
    kw = ['deed','transfer','lien','mortgage','notice','record','document','title']
    for line in lines:
        if any(k in line.lower() for k in kw) and 5 < len(line) < 200:
            if post_signal(slug, None, line[:200], 'https://recorder.utahcounty.gov/find-records', 60, 'Utah', 'deed_transfer'):
                count += 1
            if count >= 15: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_utah_county_property_info():
    slug = 'utah-county-property-info'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://www.utahcounty.gov/LandRecords/Index.asp')
    count = 0
    kw = ['parcel','property','deed','record','transfer','lien','search','land']
    for line in lines:
        if any(k in line.lower() for k in kw) and 5 < len(line) < 200:
            if post_signal(slug, None, line[:200], 'https://www.utahcounty.gov/LandRecords/Index.asp', 55, 'Utah', 'property_record'):
                count += 1
            if count >= 15: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_utah_county_real_property():
    slug = 'utah-county-real-property'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://assessor.utahcounty.gov/real-property')
    count = 0
    kw = ['parcel','value','search','appeal','lookup','property','assessment','residential']
    for line in lines:
        if any(k in line.lower() for k in kw) and 5 < len(line) < 200:
            if post_signal(slug, None, line[:200], 'https://assessor.utahcounty.gov/real-property', 45, 'Utah', 'real_property_record'):
                count += 1
            if count >= 15: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_ksl_fsbo():
    slug = 'ksl-fsbo'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://homes.ksl.com/for-sale-by-owner/')
    count = 0
    # Pattern: address line (has digits + street), price line ($), beds line
    for i, line in enumerate(lines):
        # Address: contains digit + common street words + UT
        if re.search(r'\d+.*UT', line) and ',' in line and len(line) < 100:
            price = lines[i+1] if i+1 < len(lines) else ''
            beds = lines[i+2] if i+2 < len(lines) else ''
            desc = f"{line} | {price} | {beds}".strip()
            if post_signal(slug, None, line[:150], 'https://homes.ksl.com/for-sale-by-owner/', 65, 'Utah', 'fsbo'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── LIR PARCELS via ArcGIS JSON API ─────────────────────────────────────────
def scrape_lir(slug, county, svc):
    log.info(f'[{slug}] starting')
    data = fetch_json(f"{svc}/query", {
        'where':'1=1','outFields':'PARCEL_ID,PARCEL_ADD,PARCEL_CITY,TOTAL_MKT_VALUE,PROP_CLASS',
        'resultRecordCount':200,'orderByFields':'OBJECTID DESC','f':'json'
    })
    if not data: return 0
    count = 0
    for f in data.get('features',[]):
        a = f.get('attributes',{})
        addr = a.get('PARCEL_ADD','')
        city = a.get('PARCEL_CITY','')
        parcel = a.get('PARCEL_ID','')
        val = a.get('TOTAL_MKT_VALUE') or 0
        full = f"{addr}, {city}".strip(', ') if city else addr
        score = 65 if val and val < 400000 else 45
        if addr and post_signal(slug, None, full, f"{svc}/query?where=PARCEL_ID='{parcel}'", score, county, 'lir_parcel'):
            count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_uco_lir_parcels():
    # Utah County parcel data via utahcounty.gov ArcGIS instead
    slug = 'uco-lir-parcels'
    log.info(f'[{slug}] starting')
    data = fetch_json(
        'https://maps.utahcounty.gov/arcgis/rest/services/Parcels/MapServer/0/query',
        {'where':'1=1','outFields':'PARCEL_ID,SITUS_ADDRESS,SITUS_CITY,TOTAL_VALUE',
         'resultRecordCount':200,'orderByFields':'OBJECTID DESC','f':'json'}
    )
    if not data:
        # Fallback to state LIR with county filter
        data = fetch_json(
            'https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Utah_LIR/FeatureServer/0/query',
            {'where':"1=1",'outFields':'PARCEL_ID,PARCEL_ADD,PARCEL_CITY,TOTAL_MKT_VALUE',
             'resultRecordCount':200,'orderByFields':'OBJECTID DESC','f':'json'}
        )
    if not data: return 0
    count = 0
    for f in data.get('features',[]):
        a = f.get('attributes',{})
        addr = a.get('SITUS_ADDRESS','') or a.get('PARCEL_ADD','')
        city = a.get('SITUS_CITY','') or a.get('PARCEL_CITY','')
        parcel = a.get('PARCEL_ID','')
        val = a.get('TOTAL_VALUE') or a.get('TOTAL_MKT_VALUE') or 0
        full = f"{addr}, {city}".strip(', ') if city else addr
        score = 65 if val and val < 400000 else 45
        if addr and post_signal(slug, None, full, f"https://maps.utahcounty.gov/parcels/{parcel}", score, 'Utah', 'lir_parcel'):
            count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count
def scrape_davis_lir_parcels():
    return scrape_lir('davis-lir-parcels','Davis','https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Davis_LIR/FeatureServer/0')
def scrape_slco_lir_parcels():
    return scrape_lir('slco-lir-parcels','Salt Lake','https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_SaltLake_LIR/FeatureServer/0')
def scrape_weber_lir_parcels():
    return scrape_lir('weber-lir-parcels','Weber','https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Weber_LIR/FeatureServer/0')

# ─── MAIN ─────────────────────────────────────────────────────────────────────

# ─── FIRE MARSHAL SOURCES (plain HTML — no browser needed) ──────────────────

def scrape_fire_marshal(slug, path, signal_type, county='Utah'):
    """Utah State Fire Marshal licensee tables — static HTML, direct parse, all rows."""
    log.info(f'[{slug}] starting')
    src_url = f'https://firemarshal.utah.gov/licensees/{path}'
    try:
        from bs4 import BeautifulSoup
        r = SESSION.get(src_url, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        rows = soup.find_all('tr')
        count = 0
        for row in rows[1:]:  # skip header
            cols = [td.get_text(strip=True) for td in row.find_all('td')]
            if len(cols) >= 3 and cols[0]:
                company = cols[0]
                street  = cols[1] if len(cols) > 1 else ''
                city    = cols[2] if len(cols) > 2 else ''
                state   = cols[3] if len(cols) > 3 else 'UT'
                address = f"{street}, {city}, {state}".strip(', ')
                if post_signal(slug, company, address, src_url, 30, county, signal_type):
                    count += 1
        log.info(f'[{slug}] {count} signals posted')
        return count
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
        return 0

def scrape_fire_marshal_lp_hvac():
    return scrape_fire_marshal('fire-marshal-lp-hvac', 'lp-gas-hvac-companies', 'contractor_license')

def scrape_fire_marshal_lp_gas():
    return scrape_fire_marshal('fire-marshal-lp-gas', 'lp-gas-companies', 'contractor_license')

def scrape_fire_marshal_suppression():
    return scrape_fire_marshal('fire-marshal-suppression', 'fire-suppression', 'contractor_license')

# ─── ACCELA API (OAuth Client Credentials — no user login needed) ────────────

def accela_get_token(environment='PROD'):
    """Get Accela API token using client credentials flow."""
    client_id = os.environ.get('ACCELA_CLIENT_ID', '')
    client_secret = os.environ.get('ACCELA_CLIENT_SECRET', '')
    if not client_id or not client_secret:
        log.warning('No ACCELA credentials in env')
        return None
    try:
        r = SESSION.post(
            'https://auth.accela.com/oauth2/token',
            data={
                'grant_type': 'client_credentials',
                'client_id': client_id,
                'client_secret': client_secret,
                'scope': 'records',
                'environment': environment,
            },
            timeout=20
        )
        if r.status_code == 200:
            return r.json().get('access_token')
        log.error(f'Accela token error: {r.status_code} {r.text[:200]}')
    except Exception as e:
        log.error(f'Accela token fetch failed: {e}')
    return None

def accela_search_records(token, agency, module, record_type=None, max_records=100):
    """Search Accela permit records via v4 API."""
    try:
        params = {
            'limit': min(max_records, 1000),
            'offset': 0,
            'fields': 'id,type,status,statusDate,openedDate,address',
        }
        headers = {
            'Authorization': f'Bearer {token}',
            'x-accela-agency': agency,
            'x-accela-environment': 'PROD',
            'Accept': 'application/json',
        }
        body = {'type': {'module': module}}
        if record_type:
            body['type']['value'] = record_type

        r = SESSION.post(
            'https://apis.accela.com/v4/search/records',
            json=body, headers=headers, params=params, timeout=30
        )
        if r.status_code == 200:
            return r.json().get('result', [])
        log.error(f'Accela search error {agency}/{module}: {r.status_code} {r.text[:200]}')
    except Exception as e:
        log.error(f'Accela search failed: {e}')
    return []

def _scrape_accela(slug, agency, module, county, signal_type, score):
    """Generic Accela permit scraper using OAuth API."""
    log.info(f'[{slug}] starting (Accela API — {agency}/{module})')
    token = accela_get_token()
    if not token:
        log.warning(f'[{slug}] no token — falling back to Apify')
        # Apify fallback
        url = f'https://aca-prod.accela.com/{agency}/Cap/CapHome.aspx?module={module}&TabName=HOME'
        lines = apify_text(url)
        count = 0
        kw = ['permit','building','construction','address','issued','application','approved']
        for line in lines:
            if any(w in line.lower() for w in kw) and any(c.isdigit() for c in line) and 10 < len(line) < 300:
                if post_signal(slug, None, line[:200], url, score, county, signal_type):
                    count += 1
                if count >= 20: break
        log.info(f'[{slug}] {count} signals (Apify fallback)')
        return count

    records = accela_search_records(token, agency, module, max_records=200)
    count = 0
    for rec in records:
        addr = rec.get('address', {})
        street = addr.get('streetAddress', '') or addr.get('streetName', '')
        city = addr.get('city', '')
        full_addr = f"{street}, {city}".strip(', ') if city else street
        rec_id = rec.get('id', '')
        status = rec.get('status', {}).get('value', '')
        opened = rec.get('openedDate', '')
        desc = f"{full_addr} | Status: {status} | Opened: {opened}".strip(' |')
        if rec_id or full_addr:
            url = f"https://aca-prod.accela.com/{agency}/Cap/Detail.aspx?altId={rec_id}"
            if post_signal(slug, None, full_addr or desc, url, score, county, signal_type):
                count += 1
    log.info(f'[{slug}] {count} signals posted (Accela API)')
    return count

def scrape_slc_accela_building():
    """SLC building permits via Socrata open data API — no auth needed."""
    slug = 'slc-permits-accela-building'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        # SLC publishes permit data via data.slcgov.com Socrata
        for api_url in [
            'https://data.slcgov.com/resource/mamu-wqgz.json?$limit=200&$order=issued_date DESC',
            'https://data.slcgov.com/resource/building-permits.json?$limit=200',
        ]:
            r = SESSION.get(api_url, timeout=15)
            if r.status_code == 200:
                records = r.json()
                if isinstance(records, list) and records:
                    for rec in records:
                        addr = rec.get('location_1', {}).get('human_address','') or rec.get('address','') or rec.get('street_address','')
                        permit_type = rec.get('permit_type','') or rec.get('type','')
                        status = rec.get('status','')
                        desc = f"{addr} | {permit_type} | {status}".strip(' |')
                        if addr:
                            if post_signal(slug, None, addr[:200], api_url, 50, 'Salt Lake', 'building_permit'):
                                count += 1
                    log.info(f'[{slug}] {count} signals (Socrata)')
                    return count
    except Exception as e:
        log.error(f'[{slug}] Socrata failed: {e}')
    # Fallback: Apify on permit search page
    lines = apify_text('https://aca-prod.accela.com/SLCREF/Cap/CapHome.aspx?module=Building&TabName=HOME')
    for line in lines:
        if any(w in line.lower() for w in ['permit','building','issued','address']) and any(c.isdigit() for c in line) and 10 < len(line) < 200:
            if post_signal(slug, None, line[:200], 'https://aca-prod.accela.com/SLCREF/', 50, 'Salt Lake', 'building_permit'):
                count += 1
            if count >= 20: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_slc_accela_engineering():
    """SLC engineering permits via Apify citizen portal."""
    slug = 'slc-permits-accela-engineering'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://aca-prod.accela.com/SLCREF/Cap/CapHome.aspx?module=Engineering&TabName=HOME')
    count = 0
    for line in lines:
        if any(w in line.lower() for w in ['permit','engineering','utility','excavation','grading']) and 10 < len(line) < 200:
            if post_signal(slug, None, line[:200], 'https://aca-prod.accela.com/SLCREF/', 45, 'Salt Lake', 'engineering_permit'):
                count += 1
            if count >= 20: break
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── OREM BUILDING PERMITS (JS-rendered, use Apify) ─────────────────────────

def scrape_orem_building_permits():
    slug = 'orem-building-permits'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://www.orem.org/buildingpermits/')
    count = 0
    kw = ['permit','building','construction','address','issued','application','residential','commercial','approved','inspection']
    for line in lines:
        if any(w in line.lower() for w in kw) and len(line) > 10:
            if post_signal(slug, None, line[:200], 'https://www.orem.org/buildingpermits/', 40, 'Utah', 'building_permit'):
                count += 1
            if count >= 20: break
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── UTAH COUNTY BUILDING PERMITS (WebLink doc portal) ──────────────────────

def scrape_utah_county_building_permit():
    slug = 'utah-county-building-permit'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://codev.utahcounty.gov/building')
    count = 0
    kw = ['permit','building','construction','submittal','application','requirements','single family','commercial','inspection']
    for line in lines:
        if any(w in line.lower() for w in kw) and len(line) > 8:
            if post_signal(slug, None, line[:200], 'https://codev.utahcounty.gov/building', 40, 'Utah', 'building_permit'):
                count += 1
            if count >= 15: break
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── SLC CITY REAL ESTATE SURPLUS ───────────────────────────────────────────

def scrape_slc_city_real_estate():
    slug = 'slc-city-real-estate'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://www.slc.gov/can/res/real-estate/')
    count = 0
    kw = ['property','parcel','sale','available','purchase','bid','acre','square','land','building','opportunity']
    for line in lines:
        if any(w in line.lower() for w in kw) and 8 < len(line) < 300:
            if post_signal(slug, None, line[:200], 'https://www.slc.gov/can/res/real-estate/', 50, 'Salt Lake', 'surplus_property'):
                count += 1
            if count >= 20: break
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── UTAH COUNTY CODEV BROWSER ──────────────────────────────────────────────

def scrape_utah_county_codev_browser():
    slug = 'utah-county-codev-browser'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://codev.utahcounty.gov/')
    count = 0
    kw = ['violation','code','enforcement','complaint','nuisance','notice','citation','zoning','unsafe','abatement']
    for line in lines:
        if any(w in line.lower() for w in kw) and len(line) > 10:
            if post_signal(slug, None, line[:200], 'https://codev.utahcounty.gov/', 50, 'Utah', 'code_enforcement'):
                count += 1
            if count >= 15: break
    log.info(f'[{slug}] {count} signals posted')
    return count




# ─── CONVERGENCE ENGINE ───────────────────────────────────────────────────────

def run_convergence():
    """
    Cross-reference all signals in Supabase by address.
    Any address with 3+ independent source signals gets flagged Primed (score bump to 80+).
    Writes convergence results back to a pp_convergence table.
    """
    log.info('[convergence] starting cross-reference scan')
    try:
        import urllib.request, json, hashlib
        from collections import defaultdict

        SUPA_URL = os.environ['SUPABASE_URL']
        SUPA_KEY = os.environ['SUPABASE_SERVICE_KEY']
        HEADERS = {
            'Authorization': f'Bearer {SUPA_KEY}',
            'apikey': SUPA_KEY,
            'Content-Type': 'application/json',
            'Prefer': 'return=minimal',
        }

        # Pull ALL distress signals with addresses — exclude bulk reference data
        # Reference data (lir_parcel, contractor_license) only used for cross-ref, not as base
        DISTRESS_TYPES = 'obituary,tax_sale,tax_delinquency,nts_notice,fsbo,code_enforcement,surplus,probate_notice,building_permit,deed_transfer,property_record,real_property_record,assessor_record,surplus_property,engineering_permit,permit'
        req = urllib.request.Request(
            f"{SUPA_URL}/rest/v1/pp_scraper_signals"
            "?select=source_slug,raw_address,raw_owner_name,score,county,signal_type,captured_at"
            f"&raw_address=neq."
            f"&signal_type=in.({DISTRESS_TYPES})"
            "&order=captured_at.desc&limit=10000"
        )
        for k, v in HEADERS.items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=20) as r:
            signals = json.loads(r.read())

        log.info(f'[convergence] {len(signals)} signals with addresses loaded')

        # Group by normalized address
        by_address = defaultdict(list)
        for sig in signals:
            addr = sig.get('raw_address', '').upper().strip()
            # Normalize: remove apt/unit, extra spaces
            import re
            # Normalize address for cross-source matching
            # Strip city/state/zip, unit numbers, extra spaces
            # "1247 N 400 E, Orem, UT 84057" -> "1247 N 400 E"
            addr_norm = re.sub(r'\s+', ' ', re.sub(r'(APT|UNIT|STE|#|SUITE)\s*[\w-]+', '', addr.upper())).strip()
            street_only = addr_norm.split(',')[0].strip()
            street_only = re.sub(r'(?:APT|UNIT|STE|#|SUITE|BLDG)\s*[\w-]+', '', street_only, flags=re.IGNORECASE).strip()
            street_only = re.sub(r'\s+', ' ', street_only).strip().upper()
            # Also try just the house number + first word (catches format variations)
            parts = street_only.split()
            short_key = ' '.join(parts[:3]) if len(parts) >= 3 else street_only
            if len(short_key) > 4:
                by_address[short_key].append(sig)
            # Also index full street for exact matches
            if len(street_only) > 8 and street_only != short_key:
                by_address[street_only].append(sig)

        # Find convergences: 2+ different sources on same address
        hot_addresses = []
        for addr, sigs in by_address.items():
            sources = set(s['source_slug'] for s in sigs)
            if len(sources) >= 2:
                score = min(17, 10 + len(sources) * 2)  # 2 sources = 14, 3+ = 16-17
                county = sigs[0].get('county', 'Utah')
                signal_types = list(set(s['signal_type'] for s in sigs))
                hot_addresses.append({
                    'address': addr,
                    'sources': list(sources),
                    'source_count': len(sources),
                    'score': score,
                    'county': county,
                    'signal_types': signal_types,
                })

        log.info(f'[convergence] {len(hot_addresses)} addresses with 2+ source convergence (Primed)')

        # Post each convergence as a high-score signal
        conv_count = 0
        for hot in hot_addresses:
            payload = {
                'source_slug': 'convergence-engine',
                'raw_address': hot['address'][:200],
                'raw_owner_name': '',
                'raw_url': '',
                'score': hot['score'],
                'county': hot['county'],
                'signal_type': 'convergence',
                'raw_payload': json.dumps({
                    'sources': hot['sources'],
                    'source_count': hot['source_count'],
                    'signal_types': hot['signal_types'],
                }),
            }
            req2 = urllib.request.Request(
                f"{SUPA_URL}/rest/v1/pp_scraper_signals",
                data=json.dumps(payload).encode(),
                method='POST'
            )
            for k, v in HEADERS.items():
                req2.add_header(k, v)
            try:
                with urllib.request.urlopen(req2, timeout=10) as r2:
                    if r2.status in (201, 409):
                        conv_count += 1
            except Exception:
                pass

        log.info(f'[convergence] {conv_count} convergence signals posted')
        return conv_count

    except Exception as e:
        log.error(f'[convergence] failed: {e}')
        return 0



# ═══════════════════════════════════════════════════════════════════════════
# TIER 1 — PRIMARY TRIGGERS
# ═══════════════════════════════════════════════════════════════════════════

def scrape_utah_county_nts_recorder():
    """
    Utah County NTS — Notice of Trustee Sale via Land Records document search.
    Searches for recent 'TRUSTEE' type documents in the KOI group 'Contract/Notice Int'.
    Source: utahcounty.gov/LandRecords/PartyNameForm.asp
    """
    slug = 'utah-county-nts-recorder'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        from bs4 import BeautifulSoup
        import datetime
        BASE = 'https://www.utahcounty.gov/LandRecords'
        # Search for blank name in Contract/Notice Interests — gets recent NTS filings
        for koi in ['Contract/Notice Int']:
            url = f'{BASE}/PartyNameForm.asp'
            r = SESSION.get(url, params={
                'avname': '',
                'avkoigroup': koi,
                'avstartdate': (datetime.date.today() - datetime.timedelta(days=60)).strftime('%m/%d/%Y'),
                'avenddate': datetime.date.today().strftime('%m/%d/%Y'),
                'Submit': 'Search'
            }, timeout=20)
            soup = BeautifulSoup(r.text, 'html.parser')
            tables = soup.find_all('table')
            for t in tables:
                rows = t.find_all('tr')
                if len(rows) < 3:
                    continue
                headers = [th.get_text(strip=True).lower() for th in rows[0].find_all(['th','td'])]
                for row in rows[1:]:
                    cols = [td.get_text(strip=True) for td in row.find_all('td')]
                    if not cols or len(cols) < 3:
                        continue
                    # Typical columns: Entry#, Date, Grantor, Grantee, Description
                    desc = ' | '.join(cols)
                    if any(w in desc.upper() for w in ['TRUST','NTS','NOTICE','FORECLOSE','LIEN']):
                        link_tag = row.find('a', href=True)
                        link = f"https://www.utahcounty.gov{link_tag['href']}" if link_tag else url
                        # Owner name is typically grantor (col index ~2)
                        owner = cols[2] if len(cols) > 2 else ''
                        address = cols[4] if len(cols) > 4 else desc[:200]
                        if post_signal(slug, owner, address, link, 80, 'Utah', 'nts_notice'):
                            count += 1
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_slc_county_nts_recorder():
    """
    Salt Lake County NTS — SLCO recorder does not have a public bulk NTS search.
    Best available public source: Daily Herald and SL Tribune legal notices via Apify.
    Filters strictly for NTS/foreclosure-specific content with parcel/address patterns.
    """
    slug = 'slc-county-nts-recorder'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        from bs4 import BeautifulSoup
        # Use tribune legal notice submissions page — NTS notices are published here by law
        for url, county in [
            ('https://www.sltrib.com/legal-notices/', 'Salt Lake'),
            ('https://www.heraldextra.com/classifieds/legal_notices/', 'Utah'),
        ]:
            lines = apify_text(url)
            for line in lines:
                # Must match NTS/foreclosure pattern AND contain address/parcel data
                is_nts = any(w in line.upper() for w in [
                    'NOTICE OF TRUSTEE', 'TRUSTEE SALE', 'NTS', 'NOTICE OF DEFAULT',
                    'FORECLOSURE', 'DEED OF TRUST', 'T.S. NO', 'TS NO', 'TRUSTEE SALE',
                ])
                has_data = (
                    re.search(r'\d{4,}\s+\w', line) or  # street address
                    re.search(r'\d{2}[:-]\d{3}[:-]\d{4}', line) or  # parcel
                    re.search(r'\$[\d,]+', line)  # dollar amount
                )
                if is_nts and has_data and 20 < len(line) < 400:
                    if post_signal(slug, None, line[:200], url, 80, county, 'nts_notice'):
                        count += 1
                if count >= 20:
                    break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_utah_county_tax_delinquency_pdf():
    """
    Utah County Delinquent Property Tax Report — official PDF published by the Treasurer.
    URL: utahcounty.gov/Dept/Treas/.../UtahCounty_Delinquent_Property_Tax_report.pdf
    Parsed with pdfplumber for parcel, owner, address, amount.
    """
    slug = 'utah-county-tax-delinquency-pdf'
    log.info(f'[{slug}] starting')
    count = 0
    PDF_URL = ('https://www.utahcounty.gov/Dept/Treas/production-single-forms/'
               'delinquent-property-tax-report/UtahCounty_Delinquent_Property_Tax_report.pdf')
    batch = []
    try:
        import io, pdfplumber
        r = SESSION.get(PDF_URL, timeout=30)
        if r.status_code != 200:
            log.warning(f'[{slug}] PDF not available: {r.status_code}')
            return 0
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if not table:
                    text = page.extract_text() or ''
                    # Parse free-form text — look for parcel/name/address patterns
                    for line in text.split('\n'):
                        line = line.strip()
                        # Parcel pattern: xx:xxx:xxxx or digits
                        if re.search(r'\d{2}:\d{3}:\d{4}', line) or \
                           re.search(r'\d{4,}\s+\w+\s+(?:ST|AVE|DR|LN|BLVD|WAY|RD|CT)', line.upper()):
                            if post_signal(slug, None, line[:200], PDF_URL, 75, 'Utah', 'tax_delinquency'):
                                count += 1
                    continue
                headers = [str(c).lower().strip() if c else '' for c in table[0]]
                for row in table[1:]:
                    if not row or not any(row):
                        continue
                    cells = [str(c).strip() if c else '' for c in row]
                    # Map by header or positional
                    record = dict(zip(headers, cells)) if headers else {}
                    owner   = record.get('owner','') or record.get('name','') or (cells[1] if len(cells)>1 else '')
                    address = record.get('address','') or record.get('mailing address','') or (cells[2] if len(cells)>2 else '')
                    parcel  = record.get('parcel','') or record.get('serial','') or (cells[0] if cells else '')
                    amount  = record.get('amount','') or record.get('tax due','') or ''
                    desc = f"{parcel} | {owner} | {address} | {amount}".strip(' | ')
                    if owner or parcel:
                        raw = f"{slug}|{PDF_URL}|{owner}|{address or parcel}"
                        batch.append({
                            'source_slug': slug, 'raw_address': (address or parcel)[:200],
                            'raw_owner_name': (owner or '')[:200], 'raw_url': PDF_URL,
                            'score': 75, 'county': 'Utah', 'signal_type': 'tax_delinquency',
                            'dedupe_hash': hashlib.md5(raw.encode()).hexdigest(),
                        })
        count = post_signals_batch(batch)
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_nod_tracker():
    """
    Notice of Default / NOD tracker — monitors Utah County Land Records for
    recent deed-of-trust related filings (NOD, NOS, reconveyances) via
    the document description search endpoint. Also checks obituary-cross-reference
    for probate-initiated property transfers.
    Source: utahcounty.gov/LandRecords/DocDescSearchForm.asp
    """
    slug = 'nod-tracker'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        from bs4 import BeautifulSoup
        import datetime
        BASE = 'https://www.utahcounty.gov/LandRecords'
        # NOD-related document description keywords
        for keyword in ['NOTICE OF DEFAULT', 'SUBSTITUTION TRUSTEE', 'NOTICE TRUSTEE SALE']:
            r = SESSION.get(f'{BASE}/DocDescSearchForm.asp',
                params={'avdescription': keyword, 'Submit': 'Search'},
                timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            for t in soup.find_all('table'):
                rows = t.find_all('tr')
                if len(rows) < 3:
                    continue
                for row in rows[1:]:
                    cols = [td.get_text(strip=True) for td in row.find_all('td')]
                    if not cols or len(cols) < 2:
                        continue
                    desc = ' | '.join(cols)
                    link_tag = row.find('a', href=True)
                    link = f"https://www.utahcounty.gov{link_tag['href']}" if link_tag else f'{BASE}/DocDescSearchForm.asp'
                    owner = cols[2] if len(cols) > 2 else ''
                    addr  = cols[3] if len(cols) > 3 else desc[:200]
                    if post_signal(slug, owner, addr or desc[:200], link, 80, 'Utah', 'nod_notice'):
                        count += 1
                    if count >= 30:
                        break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


# ═══════════════════════════════════════════════════════════════════════════
# TIER 2 — LIFE EVENT TRIGGERS
# ═══════════════════════════════════════════════════════════════════════════

def scrape_probate_court_xchange():
    """
    Probate court filings — Utah Courts Xchange public case search.
    Searches for probate (case type PR) filings in Utah and Salt Lake County.
    Source: utcourts.gov/xchange
    Falls back to Apify on the probate notice search page.
    """
    slug = 'probate-court-xchange'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        from bs4 import BeautifulSoup
        # Try xchange search for probate cases
        for county_code, county_name in [('054', 'Utah'), ('035', 'Salt Lake')]:
            r = SESSION.get(
                'https://www.utcourts.gov/xchange/',
                params={'caseType': 'PR', 'countyId': county_code},
                timeout=15
            )
            soup = BeautifulSoup(r.text, 'html.parser')
            rows = soup.find_all('tr')
            found = False
            for row in rows:
                cols = [td.get_text(strip=True) for td in row.find_all('td')]
                if len(cols) >= 3 and any(w in ' '.join(cols).upper() for w in ['ESTATE','PROBATE','DECEDENT','IN RE']):
                    name = cols[0] if cols else ''
                    case_num = cols[1] if len(cols)>1 else ''
                    desc = ' | '.join(cols[:5])
                    if post_signal(slug, name, case_num or desc[:200],
                                   'https://www.utcourts.gov/xchange/', 70, county_name, 'probate_notice'):
                        count += 1
                        found = True
            # Apify fallback for JS-rendered content
            if not found:
                lines = apify_text(f'https://www.utcourts.gov/xchange/?caseType=PR&countyId={county_code}')
                for line in lines:
                    if any(w in line.upper() for w in ['ESTATE OF','IN RE','PROBATE','DECEASED','DECEDENT']) \
                            and len(line) > 10:
                        if post_signal(slug, None, line[:200],
                                       'https://www.utcourts.gov/xchange/', 70, county_name, 'probate_notice'):
                            count += 1
                        if count >= 20:
                            break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_divorce_court():
    """
    Divorce court — DEFERRED.
    Utah divorce case records are not publicly searchable without a case number
    or party name via xchange. The filing index is not bulk-accessible without
    a registered account. This scraper monitors the xchange public search for
    dissolution/divorce-type case codes (DV) but results are sparse.
    Marking signal_type as 'divorce_notice' for routing purposes.
    Full implementation requires either:
      (a) Utah Courts API access (request via utcourts.gov/records)
      (b) Manual monitoring of published legal notices in local newspapers
    """
    slug = 'divorce-court'
    log.info(f'[{slug}] starting (limited public access — deferred)')
    count = 0
    try:
        from bs4 import BeautifulSoup
        # Best available: Utah County Clerk legal notices page
        for url in [
            'https://www.utahcounty.gov/Dept/Clerk/LegalPublications/',
            'https://www.utahcounty.gov/Dept/Clerk/',
        ]:
            r = SESSION.get(url, timeout=12)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            for el in soup.find_all(['p','li','td']):
                text = el.get_text(strip=True)
                if any(w in text.upper() for w in ['DISSOLUTION','DIVORCE','PETITION','RESPONDENT','PETITIONER']) \
                        and 15 < len(text) < 300:
                    if post_signal(slug, None, text[:200], url, 55, 'Utah', 'divorce_notice'):
                        count += 1
                    if count >= 10:
                        break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted (divorce data limited)')
    return count


# ═══════════════════════════════════════════════════════════════════════════
# TIER 3 — ACTIVE INTENT
# ═══════════════════════════════════════════════════════════════════════════

def scrape_ksl_fsbo_craigslist():
    """
    FSBO listings — Craigslist SLC + Provo real estate by owner.
    Both regions combined, deduped by listing title+price hash.
    Already live as scrape_ksl_fsbo() — this version adds Utah County / Wasatch.
    """
    slug = 'ksl-fsbo-extended'
    log.info(f'[{slug}] starting')
    count = 0
    regions = [
        ('https://saltlake.craigslist.org/search/reo?sort=date&limit=120', 'Salt Lake'),
        ('https://provo.craigslist.org/search/reo?sort=date&limit=120', 'Utah'),
        ('https://provo.craigslist.org/search/reo?sort=date&limit=120&postal=84060&search_distance=30', 'Wasatch'),
    ]
    try:
        import urllib.request as _ur
        from bs4 import BeautifulSoup
        for url, county in regions:
            req = _ur.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            })
            with _ur.urlopen(req, timeout=20) as resp:
                html = resp.read()
            soup = BeautifulSoup(html, 'html.parser')
            items = soup.select('li.cl-static-search-result, .result-row, li[data-pid]')
            for item in items:
                title_el = item.select_one('.title, a.posting-title, .result-title, a[href*="/reo/"]')
                title = title_el.get_text(strip=True) if title_el else item.get_text(separator=' ',strip=True)[:100]
                link_el = item.find('a', href=True)
                link = link_el['href'] if link_el else url
                if not link.startswith('http'):
                    link = url.split('/search')[0] + link
                price_el = item.select_one('.price, .result-price')
                price = price_el.get_text(strip=True) if price_el else ''
                full = f"{title} {price}".strip()
                if full and any(w in full.lower() for w in ['bed','bath','$','sqft','home','house','bdrm','br','ba']):
                    if post_signal(slug, None, full[:200], link, 60, county, 'fsbo'):
                        count += 1
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


# ═══════════════════════════════════════════════════════════════════════════
# TIER 4 — ENRICHMENT LAYERS
# ═══════════════════════════════════════════════════════════════════════════

def scrape_deed_transfers_utah_county():
    """
    Deed transfers — Utah County Land Records 'Documents by Name' search.
    Targets recent DEED, WARRANTY DEED, QUIT CLAIM, GRANT DEED filings.
    Used as enrichment — cross-referenced against obituaries and distress signals.
    Source: utahcounty.gov/LandRecords/PartyNameForm.asp (KOI: Conveyance Documents)
    """
    slug = 'deed-transfers-utah-county'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        from bs4 import BeautifulSoup
        import datetime
        BASE = 'https://www.utahcounty.gov/LandRecords'
        r = SESSION.get(f'{BASE}/PartyNameForm.asp',
            params={
                'avname': '',
                'avkoigroup': 'Conveyance Documents',
                'avstartdate': (datetime.date.today() - datetime.timedelta(days=14)).strftime('%m/%d/%Y'),
                'avenddate': datetime.date.today().strftime('%m/%d/%Y'),
                'Submit': 'Search'
            }, timeout=20)
        soup = BeautifulSoup(r.text, 'html.parser')
        for t in soup.find_all('table'):
            rows = t.find_all('tr')
            if len(rows) < 3:
                continue
            for row in rows[1:]:
                cols = [td.get_text(strip=True) for td in row.find_all('td')]
                if not cols or len(cols) < 3:
                    continue
                doc_type = cols[3] if len(cols) > 3 else ''
                # Filter to actual deed types
                if not any(w in doc_type.upper() for w in ['DEED','GRANT','CONVEY','QUIT','WARRANTY']):
                    continue
                grantor  = cols[2] if len(cols) > 2 else ''
                grantee  = cols[3] if len(cols) > 3 else ''
                rec_date = cols[1] if len(cols) > 1 else ''
                link_tag = row.find('a', href=True)
                link = f"https://www.utahcounty.gov{link_tag['href']}" if link_tag else f'{BASE}/PartyNameForm.asp'
                desc = f"{grantor} → {grantee} | {doc_type} | {rec_date}"
                if post_signal(slug, grantor, desc[:200], link, 60, 'Utah', 'deed_transfer'):
                    count += 1
                if count >= 50:
                    break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_obituaries_enrichment():
    """
    Obituaries from Daily Herald (heraldextra.com) — cross-reference enrichment layer.
    Signals are created but scored at 55 (Qualify) only when the deceased
    appears as a property owner in LIR parcel data or deed records.
    Standalone obituary signals score 40 (Watch) — useful for convergence engine.
    Source: heraldextra.com/obituaries + legacy.com/us/obituaries/heraldextra
    """
    slug = 'obituaries-enrichment'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        from bs4 import BeautifulSoup
        for url, county in [
            ('https://www.heraldextra.com/obituaries/', 'Utah'),
            ('https://www.sltrib.com/obituaries/', 'Salt Lake'),
        ]:
            r = SESSION.get(url, timeout=15)
            if r.status_code != 200:
                lines = apify_text(url)
                for line in lines:
                    if any(w in line.lower() for w in ['passed away','obituary','beloved','survived by','funeral']) \
                            and len(line) > 10:
                        if post_signal(slug, None, line[:200], url, 40, county, 'obituary'):
                            count += 1
                        if count >= 20:
                            break
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            # Find obituary cards/links
            for card in soup.select('.obit-card, .obituary-listing, article, .card'):
                name_el = card.select_one('h2, h3, .name, a[href*="obituar"]')
                name = name_el.get_text(strip=True) if name_el else ''
                link_el = card.find('a', href=True)
                link = link_el['href'] if link_el else url
                if not link.startswith('http'):
                    link = url.rstrip('/') + '/' + link.lstrip('/')
                date_el = card.select_one('.date, time, .published')
                date_str = date_el.get_text(strip=True) if date_el else ''
                if name and len(name) > 3:
                    desc = f"{name} | {date_str}".strip(' | ')
                    if post_signal(slug, name, desc[:200], link, 40, county, 'obituary'):
                        count += 1
                if count >= 30:
                    break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


# ═══════════════════════════════════════════════════════════════════════════
# TIER 5 — BUYER-SIDE
# ═══════════════════════════════════════════════════════════════════════════

def scrape_utah_county_public_surplus():
    """
    Utah County Public Surplus — tax sale and surplus property auctions.
    Source: publicsurplus.com/sms/utahco,ut (public, no auth)
    Returns real parcel numbers and auction details.
    """
    slug = 'utah-county-public-surplus'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        from bs4 import BeautifulSoup
        url = 'http://www.publicsurplus.com/sms/utahco,ut/list/current?orgid=50608'
        r = SESSION.get(url, timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')
        for t in soup.find_all('table'):
            rows = t.find_all('tr')
            if len(rows) < 2:
                continue
            for row in rows[1:]:
                cols = [td.get_text(strip=True) for td in row.find_all('td')]
                if not cols or len(cols) < 2:
                    continue
                auction_id = cols[0] if cols else ''
                title      = cols[1] if len(cols) > 1 else ''
                time_left  = cols[2] if len(cols) > 2 else ''
                price      = cols[4] if len(cols) > 4 else ''
                # Filter to real property auctions (parcel-based)
                if not any(w in title.upper() for w in ['PARCEL','PROPERTY','LOT','ACRE','REAL']):
                    continue
                link_el = row.find('a', href=True)
                link = f"http://www.publicsurplus.com{link_el['href']}" if link_el else url
                # Parcel number often in title
                parcel_match = re.search(r'\d{2}:\d{3}:\d{4}', title)
                parcel = parcel_match.group(0) if parcel_match else ''
                desc = f"{title} | Parcel: {parcel} | Price: {price} | Closes: {time_left}".strip(' | ')
                if post_signal(slug, None, desc[:200], link, 65, 'Utah', 'surplus_property'):
                    count += 1
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_slc_county_public_surplus():
    """
    Salt Lake County Public Surplus — tax sale and surplus auctions.
    Source: publicsurplus.com/sms/slco,ut or slco.gov surplus listings.
    """
    slug = 'slc-county-public-surplus'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        from bs4 import BeautifulSoup
        # Try SLCO surplus page
        for url in [
            'https://www.publicsurplus.com/sms/slcounty,ut/list/current',
            'https://slco.org/surplus/',
            'https://slco.org/administrative-services/surplus-property/',
        ]:
            r = SESSION.get(url, timeout=12)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            tables = soup.find_all('table')
            found = False
            for t in tables:
                rows = t.find_all('tr')
                if len(rows) < 2:
                    continue
                for row in rows[1:]:
                    cols = [td.get_text(strip=True) for td in row.find_all('td')]
                    if not cols or len(cols) < 2:
                        continue
                    title = cols[1] if len(cols) > 1 else cols[0]
                    price = cols[4] if len(cols) > 4 else ''
                    if any(w in title.upper() for w in ['PARCEL','PROPERTY','LOT','REAL','ACRE']):
                        link_el = row.find('a', href=True)
                        link = link_el['href'] if link_el else url
                        if not link.startswith('http'):
                            link = 'https://www.publicsurplus.com' + link
                        parcel_match = re.search(r'\d{2}[:\-]\d{3,}[:\-]\d{4,}', title)
                        parcel = parcel_match.group(0) if parcel_match else ''
                        desc = f"{title} | {parcel} | {price}".strip(' | ')
                        if post_signal(slug, None, desc[:200], link, 65, 'Salt Lake', 'surplus_property'):
                            count += 1
                            found = True
            if found:
                break
            # Fallback: Apify
            lines = apify_text(url)
            for line in lines:
                if any(w in line.upper() for w in ['PARCEL','PROPERTY','AUCTION','BID','SURPLUS']) \
                        and len(line) > 8:
                    if post_signal(slug, None, line[:200], url, 65, 'Salt Lake', 'surplus_property'):
                        count += 1
                if count >= 20:
                    break
            break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count



SCRAPERS = [
    # ── HIGH SIGNAL — distress & life events ──
    scrape_obituaries_herald,
    scrape_probate_court,
    scrape_utah_county_tax_delinquency,
    scrape_utah_county_nts,
    scrape_slc_county_nts,
    scrape_slc_tax_sale,
    scrape_wasatch_tax_sale,
    scrape_wasatch_county_nts,
    # ── PROPERTY RECORDS ──
    scrape_slc_assessor,
    scrape_slc_real_estate,
    scrape_slc_public_surplus,
    scrape_wasatch_public_surplus,
    scrape_slc_city_real_estate,
    # ── PERMITS ──
    scrape_south_slc_permits,
    scrape_south_slc_permits_pdf,
    scrape_utah_county_codev,
    scrape_utah_county_codev_browser,
    scrape_utah_county_directory,
    scrape_utah_county_property_info,
    scrape_utah_county_real_property,
    scrape_utah_county_building_permit,
    scrape_slc_accela_building,
    scrape_slc_accela_engineering,
    scrape_orem_building_permits,
    # ── FSBO & DIRECTORIES ──
    scrape_uvhba_directory,
    scrape_ksl_fsbo,
    # ── FIRE MARSHAL LICENSE ROLLS ──
    scrape_fire_marshal_lp_hvac,
    scrape_fire_marshal_lp_gas,
    scrape_fire_marshal_suppression,
    # ── LIR PARCEL DATA ──
    scrape_uco_lir_parcels,
    scrape_davis_lir_parcels,
    scrape_slco_lir_parcels,
    scrape_weber_lir_parcels,
    # ── TIER 1 — PRIMARY TRIGGERS (new) ──
    scrape_utah_county_nts_recorder,
    scrape_slc_county_nts_recorder,
    scrape_utah_county_tax_delinquency_pdf,
    scrape_nod_tracker,
    # ── TIER 2 — LIFE EVENTS (new) ──
    scrape_probate_court_xchange,
    scrape_divorce_court,
    # ── TIER 3 — ACTIVE INTENT (new) ──
    scrape_ksl_fsbo_craigslist,
    # ── TIER 4 — ENRICHMENT (new) ──
    scrape_deed_transfers_utah_county,
    scrape_obituaries_enrichment,
    # ── TIER 5 — BUYER SIDE (new) ──
    scrape_utah_county_public_surplus,
    scrape_slc_county_public_surplus,
]

if __name__ == '__main__':
    log.info(f'=== Premier Prospect v10 — {len(SCRAPERS)} sources ===')
    total = 0
    for fn in SCRAPERS:
        try:
            n = fn() or 0
            total += n
            write_run_log(fn.__name__.replace('scrape_',''), n, 'success')
        except Exception as e:
            log.error(f'{fn.__name__} crashed: {e}')
            write_run_log(fn.__name__.replace('scrape_',''), 0, 'error', str(e)[:200])
    # Run convergence engine after all scrapers complete
    try:
        conv = run_convergence()
        total += conv
    except Exception as e:
        log.error(f'convergence crashed: {e}')
    log.info(f'=== Done — {total} total signals (incl. {conv} convergence) ===')



if __name__ == '__main__':
    log.info(f'=== Premier Prospect v10 — {len(SCRAPERS)} sources ===')
    total = 0
    for fn in SCRAPERS:
        try:
            n = fn() or 0
            total += n
            write_run_log(fn.__name__.replace('scrape_',''), n, 'success')
        except Exception as e:
            log.error(f'{fn.__name__} crashed: {e}')
            write_run_log(fn.__name__.replace('scrape_',''), 0, 'error', str(e)[:200])
    # Run convergence engine after all scrapers complete
    try:
        conv = run_convergence()
        total += conv
    except Exception as e:
        log.error(f'convergence crashed: {e}')
    log.info(f'=== Done — {total} total signals (incl. {conv} convergence) ===')



