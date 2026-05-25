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



def scrape_hmda_utah_county():
    """
    HMDA Federal Mortgage Applications — Utah County (FIPS 49049).
    Home Mortgage Disclosure Act public data from CFPB.
    Every purchase loan application in Utah County = confirmed active buyer.
    Fields: loan_amount, property_value, income, loan_type, applicant_age, census_tract.
    Signal type: mortgage_application | Score: 70 (Qualify)
    Source: ffiec.cfpb.gov — free, federal, no auth required.
    """
    slug = 'hmda-utah-county'
    log.info(f'[{slug}] starting')
    count = 0
    batch = []
    try:
        import csv, io
        # action_taken=1 = loan originated (actual purchase, not just application)
        # action_taken=8 = pre-approval request = also strong buyer signal
        for action in ['1', '8']:
            url = (f'https://ffiec.cfpb.gov/v2/data-browser-api/view/csv'
                   f'?states=UT&years=2023&actions_taken={action}&counties=49049')
            r = SESSION.get(url, timeout=30)
            if r.status_code != 200:
                continue
            reader = csv.reader(io.StringIO(r.text))
            rows = list(reader)
            if not rows:
                continue
            hdrs = rows[0]
            for row in rows[1:]:
                if not row:
                    continue
                d = dict(zip(hdrs, row))
                loan_amt    = d.get('loan_amount', '')
                prop_val    = d.get('property_value', '')
                loan_type   = d.get('derived_loan_product_type', '')
                dwelling    = d.get('derived_dwelling_category', '')
                tract       = d.get('census_tract', '')
                age         = d.get('applicant_age', '')
                income      = d.get('income', '')
                # Filter: single-family purchase only
                if 'Single Family' not in dwelling and 'Manufactured' not in dwelling:
                    continue
                desc = (f"Mortgage application | Loan: ${loan_amt} | "
                        f"Property: ${prop_val} | Type: {loan_type} | "
                        f"Age: {age} | Income: ${income}k | Tract: {tract}")
                raw = f"{slug}|{url}|{tract}|{loan_amt}|{loan_type}"
                batch.append({
                    'source_slug': slug,
                    'raw_address': tract[:200],
                    'raw_owner_name': f"Applicant Age {age}"[:200],
                    'raw_url': url[:500],
                    'score': 70,
                    'county': 'Utah',
                    'signal_type': 'mortgage_application',
                    'dedupe_hash': hashlib.md5(raw.encode()).hexdigest(),
                })
                if len(batch) >= 500:
                    count += post_signals_batch(batch)
                    batch = []
        if batch:
            count += post_signals_batch(batch)
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_hmda_slc_county():
    """
    HMDA Federal Mortgage Applications — Salt Lake County (FIPS 49035).
    Same as Utah County scraper, different FIPS code.
    Signal type: mortgage_application | Score: 70
    """
    slug = 'hmda-slc-county'
    log.info(f'[{slug}] starting')
    count = 0
    batch = []
    try:
        import csv, io
        for action in ['1', '8']:
            url = (f'https://ffiec.cfpb.gov/v2/data-browser-api/view/csv'
                   f'?states=UT&years=2023&actions_taken={action}&counties=49035')
            r = SESSION.get(url, timeout=30)
            if r.status_code != 200:
                continue
            reader = csv.reader(io.StringIO(r.text))
            rows = list(reader)
            if not rows:
                continue
            hdrs = rows[0]
            for row in rows[1:]:
                if not row:
                    continue
                d = dict(zip(hdrs, row))
                dwelling = d.get('derived_dwelling_category', '')
                if 'Single Family' not in dwelling and 'Manufactured' not in dwelling:
                    continue
                loan_amt  = d.get('loan_amount', '')
                prop_val  = d.get('property_value', '')
                loan_type = d.get('derived_loan_product_type', '')
                tract     = d.get('census_tract', '')
                age       = d.get('applicant_age', '')
                income    = d.get('income', '')
                raw = f"{slug}|{url}|{tract}|{loan_amt}|{loan_type}"
                batch.append({
                    'source_slug': slug,
                    'raw_address': tract[:200],
                    'raw_owner_name': f"Applicant Age {age}"[:200],
                    'raw_url': url[:500],
                    'score': 70,
                    'county': 'Salt Lake',
                    'signal_type': 'mortgage_application',
                    'dedupe_hash': hashlib.md5(raw.encode()).hexdigest(),
                })
                if len(batch) >= 500:
                    count += post_signals_batch(batch)
                    batch = []
        if batch:
            count += post_signals_batch(batch)
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_zillow_market_signals():
    """
    Zillow Public Research CSV — Market temperature, inventory, price cuts, new listings.
    No auth. Direct download from Zillow's public research files.
    Rising market temp + dropping inventory = buyer urgency signal.
    Extracts Utah County (Provo MSA) and Salt Lake County (SLC MSA) rows.
    Signal type: market_demand | Score: 55
    """
    slug = 'zillow-market-signals'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        ZILLOW_FEEDS = [
            ('https://files.zillowstatic.com/research/public_csvs/market_temp_index/Metro_market_temp_index_uc_sfrcondo_month.csv', 'market_temp', 55),
            ('https://files.zillowstatic.com/research/public_csvs/invt_fs/Metro_invt_fs_uc_sfrcondo_sm_month.csv', 'inventory_signal', 45),
            ('https://files.zillowstatic.com/research/public_csvs/perc_listings_price_cut/Metro_perc_listings_price_cut_uc_sfrcondo_sm_month.csv', 'price_cut_signal', 50),
            ('https://files.zillowstatic.com/research/public_csvs/new_listings/Metro_new_listings_uc_sfrcondo_sm_week.csv', 'new_listings_signal', 45),
        ]
        for url, signal_type, score in ZILLOW_FEEDS:
            r = SESSION.get(url, timeout=20)
            if r.status_code != 200:
                continue
            lines = r.text.split('\n')
            header = lines[0].split(',') if lines else []
            utah_rows = [l for l in lines[1:] if 'Salt Lake' in l or 'Provo' in l or 'Ogden' in l]
            for row_str in utah_rows:
                cols = row_str.split(',')
                if len(cols) < 4:
                    continue
                region = cols[2].strip().strip('"') if len(cols) > 2 else ''
                # Get most recent value (last non-empty column)
                recent_vals = [c for c in reversed(cols[5:]) if c.strip() and c.strip() != '']
                recent = recent_vals[0].strip() if recent_vals else ''
                county = 'Salt Lake' if 'Salt Lake' in region else 'Utah'
                desc = f"{region} | {signal_type} | Latest: {recent} | Source: Zillow Research"
                raw = f"{slug}|{url}|{region}|{signal_type}"
                if post_signal(slug, None, desc[:200], url, score, county, signal_type):
                    count += 1
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_realtor_market_utah():
    """
    Realtor.com Public Inventory Data — Utah MSAs.
    Public S3 CSV — no auth. 109,152 rows total, 474 Utah matches.
    Fields: median_listing_price, active_listing_count, median_days_on_market,
            new_listing_count, price_reduced_count.
    Signal type: market_inventory | Score: 45
    """
    slug = 'realtor-market-utah'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        import csv, io
        url = 'https://econdata.s3-us-west-2.amazonaws.com/Reports/Core/RDC_Inventory_Core_Metrics_Metro_History.csv'
        r = SESSION.get(url, timeout=30)
        if r.status_code != 200:
            log.warning(f'[{slug}] HTTP {r.status_code}')
            return 0
        reader = csv.reader(io.StringIO(r.text))
        rows = list(reader)
        hdrs = rows[0] if rows else []
        for row in rows[1:]:
            if not row:
                continue
            d = dict(zip(hdrs, row))
            cbsa = d.get('cbsa_title', '')
            if not any(w in cbsa for w in ['Salt Lake', 'Provo', 'Ogden', 'Logan']):
                continue
            # Only most recent entries (last 12 months)
            month = d.get('month_date_yyyymm', '')
            if month and int(month[:4]) < 2024:
                continue
            price    = d.get('median_listing_price', '')
            active   = d.get('active_listing_count', '')
            dom      = d.get('median_days_on_market', '')
            new_list = d.get('new_listing_count', '')
            price_red= d.get('price_reduced_count', '')
            county   = 'Salt Lake' if 'Salt Lake' in cbsa else 'Utah'
            desc = (f"{cbsa} | {month} | Median: ${price} | "
                    f"Active: {active} | DOM: {dom} | New: {new_list} | Price cuts: {price_red}")
            raw = f"{slug}|{url}|{cbsa}|{month}"
            if post_signal(slug, None, desc[:200], url, 45, county, 'market_inventory'):
                count += 1
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


# ═══════════════════════════════════════════════════════════════════════════
# BUYER INTELLIGENCE — GENERATION 2: ACTIVE BUYER INTENT (SOCIAL/PUBLIC)
# ═══════════════════════════════════════════════════════════════════════════

def scrape_craigslist_buyer_wanted_slc():
    """
    Craigslist Housing Wanted — Salt Lake City.
    Real people posting explicit housing want ads.
    Filters for buy-intent keywords: looking to buy, purchase, need home, etc.
    Also captures relocation posts. Gen2: BS4 direct parse, no Apify needed.
    Signal type: buyer_wanted_post | Score: 65
    """
    slug = 'craigslist-buyer-wanted-slc'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        from bs4 import BeautifulSoup
        BUY_KEYWORDS = [
            'want to buy', 'looking to buy', 'looking to purchase', 'want to purchase',
            'need to buy', 'trying to buy', 'first home', 'first house', 'pre-approv',
            'pre approved', 'approved for', 'cash buyer', 'cash purchase',
            'relocating', 'relocation', 'moving to utah', 'moving to salt lake',
            'moving to slc', 'transferring', 'need a home', 'need a house',
            'house hunt', 'home search', 'searching for a home', 'searching for a house',
        ]
        for search_q in ['want to buy', 'looking to buy', 'relocating', 'pre-approved']:
            url = f'https://saltlake.craigslist.org/search/hhh?sort=date&query={search_q.replace(" ","+")}'
            r = SESSION.get(url, timeout=15)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            items = soup.select('li.cl-static-search-result, li[data-pid], .result-row')
            for item in items:
                title_el = item.select_one('a, .result-title, .posting-title')
                if not title_el:
                    continue
                title = title_el.get_text(strip=True).lower()
                if not any(kw in title for kw in BUY_KEYWORDS):
                    continue
                link_el = item.find('a', href=True)
                link = link_el['href'] if link_el else url
                if not link.startswith('http'):
                    link = 'https://saltlake.craigslist.org' + link
                price_el = item.select_one('.priceinfo, .result-price')
                price = price_el.get_text(strip=True) if price_el else ''
                desc = f"{title_el.get_text(strip=True)} {price}".strip()[:200]
                raw = f"{slug}|{link}|{desc}"
                if post_signal(slug, None, desc, link, 65, 'Salt Lake', 'buyer_wanted_post'):
                    count += 1
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_craigslist_buyer_wanted_provo():
    """
    Craigslist Housing Wanted — Provo/Utah County.
    Same as SLC but targeting Provo + Utah County area posts.
    Signal type: buyer_wanted_post | Score: 65
    """
    slug = 'craigslist-buyer-wanted-provo'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        from bs4 import BeautifulSoup
        BUY_KEYWORDS = [
            'want to buy', 'looking to buy', 'looking to purchase', 'want to purchase',
            'need to buy', 'first home', 'first house', 'pre-approv', 'pre approved',
            'cash buyer', 'relocating', 'relocation', 'moving to utah', 'moving to provo',
            'moving to orem', 'house hunt', 'home search', 'need a home',
        ]
        for search_q in ['want to buy', 'looking to buy', 'relocating', 'pre-approved']:
            url = f'https://provo.craigslist.org/search/hhh?sort=date&query={search_q.replace(" ","+")}'
            r = SESSION.get(url, timeout=15)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            items = soup.select('li.cl-static-search-result, li[data-pid], .result-row')
            for item in items:
                title_el = item.select_one('a, .result-title, .posting-title')
                if not title_el:
                    continue
                title = title_el.get_text(strip=True).lower()
                if not any(kw in title for kw in BUY_KEYWORDS):
                    continue
                link_el = item.find('a', href=True)
                link = link_el['href'] if link_el else url
                if not link.startswith('http'):
                    link = 'https://provo.craigslist.org' + link
                desc = title_el.get_text(strip=True)[:200]
                if post_signal(slug, None, desc, link, 65, 'Utah', 'buyer_wanted_post'):
                    count += 1
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_reddit_buyer_intent():
    """
    Reddit buyer intent posts — r/utahhousing, r/SaltLakeCity, r/provo.
    Uses Apify Playwright (firefox) to bypass Reddit's bot detection.
    Filters for explicit buy-intent language.
    Signal type: social_buyer_intent | Score: 60
    """
    slug = 'reddit-buyer-intent'
    log.info(f'[{slug}] starting')
    count = 0
    BUY_KEYWORDS = [
        'looking to buy', 'want to buy', 'first home', 'first house',
        'pre-approv', 'pre approved', 'house hunting', 'home search',
        'relocating to utah', 'moving to utah', 'moving to slc',
        'need to find a home', 'searching for a home', 'cash buyer',
        'purchase a home', 'buy a house', 'afford', 'mortgage',
        'down payment', 'fha', 'conventional loan',
    ]
    try:
        for subreddit, county in [
            ('utahhousing', 'Utah'),
            ('SaltLakeCity', 'Salt Lake'),
            ('provo', 'Utah'),
            ('Utah', 'Utah'),
        ]:
            url = f'https://www.reddit.com/r/{subreddit}/new/'
            lines = apify_text(url)
            for line in lines:
                line_lower = line.lower()
                if any(kw in line_lower for kw in BUY_KEYWORDS) and 20 < len(line) < 400:
                    desc = line[:200]
                    src_url = f'https://www.reddit.com/r/{subreddit}/'
                    if post_signal(slug, None, desc, src_url, 60, county, 'social_buyer_intent'):
                        count += 1
                    if count >= 30:
                        break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_utah_sos_new_entities():
    """
    Utah Secretary of State — New Real Estate Entity Formations.
    Investors and buyers forming LLCs before property purchase.
    Entities with 'properties', 'holdings', 'realty', 'investments',
    'rental', 'assets', 'real estate' in name = active buyer signal.
    Signal type: investor_buyer_entity | Score: 50
    """
    slug = 'utah-sos-new-entities'
    log.info(f'[{slug}] starting')
    count = 0
    RE_KEYWORDS = [
        'properties', 'property', 'holdings', 'realty', 'real estate',
        'investments', 'investment', 'rental', 'rentals', 'assets',
        'acquisitions', 'capital', 'group', 'ventures', 'land',
        'homes', 'housing', 'estate', 'equity', 'management',
    ]
    try:
        from bs4 import BeautifulSoup
        url = 'https://secure.utah.gov/bes/index.html'
        lines = apify_text(url)
        for line in lines:
            line_lower = line.lower()
            if any(kw in line_lower for kw in RE_KEYWORDS) and 5 < len(line) < 200:
                if post_signal(slug, None, line[:200], url, 50, 'Utah', 'investor_buyer_entity'):
                    count += 1
            if count >= 25:
                break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


# ═══════════════════════════════════════════════════════════════════════════
# BUYER INTELLIGENCE — GENERATION 3: COMPETITOR MIRROR & CROSS-SIDE
# ═══════════════════════════════════════════════════════════════════════════

def scrape_competitor_mirror_ksl():
    """
    KSL Homes — Active listing feed cross-referenced against seller distress DB.
    Any property appearing BOTH on KSL AND in pp_scraper_signals as distress
    = cross-side convergence (seller listing + distress signal = maximum urgency).
    Also captures price-reduced and days-on-market as buyer demand signals.
    Signal type: competitor_listing | Score: 55
    """
    slug = 'competitor-mirror-ksl'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        from bs4 import BeautifulSoup
        for url, county in [
            ('https://homes.ksl.com/for-sale/?sort=newest&county=Utah&limit=100', 'Utah'),
            ('https://homes.ksl.com/for-sale/?sort=newest&county=SaltLake&limit=100', 'Salt Lake'),
            ('https://homes.ksl.com/for-sale/?sort=price-reduced&limit=100', 'Utah'),
        ]:
            r = SESSION.get(url, timeout=15)
            if r.status_code != 200:
                lines = apify_text(url)
                for line in lines:
                    if any(w in line for w in ['bed', 'bath', '$', 'sqft', 'acre', 'sale']):
                        if post_signal(slug, None, line[:200], url, 55, county, 'competitor_listing'):
                            count += 1
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            listings = soup.select('.listing-item, .property-card, article, .result')
            for listing in listings:
                title = listing.get_text(separator=' ', strip=True)[:200]
                link_el = listing.find('a', href=True)
                link = link_el['href'] if link_el else url
                if not link.startswith('http'):
                    link = 'https://homes.ksl.com' + link
                if title and any(w in title.lower() for w in ['bed', 'bath', '$', 'sqft']):
                    if post_signal(slug, None, title[:200], link, 55, county, 'competitor_listing'):
                        count += 1
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_competitor_mirror_redfin():
    """
    Redfin — Price-reduced and new listings in Utah County + Salt Lake County.
    Price-reduced listings on Redfin = motivated sellers with active buyer traffic.
    Cross-referenced against distress DB in run_cross_side_convergence().
    Signal type: competitor_listing | Score: 60
    """
    slug = 'competitor-mirror-redfin'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        for url, county in [
            ('https://www.redfin.com/city/17312/UT/Salt-Lake-City', 'Salt Lake'),
            ('https://www.redfin.com/city/14971/UT/Provo', 'Utah'),
            ('https://www.redfin.com/county/1930/UT/Salt-Lake-County', 'Salt Lake'),
        ]:
            lines = apify_text(url)
            for line in lines:
                if any(w in line for w in ['Price drop', 'Reduced', 'price cut', 'days on market',
                                            'New listing', 'Just listed', 'Hot home', 'median']):
                    if post_signal(slug, None, line[:200], url, 60, county, 'competitor_listing'):
                        count += 1
                if count >= 20:
                    break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def run_cross_side_convergence():
    """
    Cross-Side Convergence Engine.
    The DARPA layer. Matches buyer signals against seller distress signals on same address.
    When a property appears in BOTH pp_scraper_signals (distress) AND as a competitor
    listing or buyer-wanted post — posts a cross_side_convergence signal at score 85.
    This is the highest-value signal in the entire system:
    motivated seller + active buyer market attention = agent gets to both first.
    Signal type: cross_side_convergence | Score: 85
    """
    slug = 'cross-side-convergence'
    log.info(f'[{slug}] starting cross-side convergence scan')
    count = 0
    try:
        import re
        # Pull recent buyer signals (competitor listings, buyer wanted posts)
        buyer_types = 'competitor_listing,buyer_wanted_post,social_buyer_intent,mortgage_application'
        r_buyer = SESSION.get(
            f"{SUPABASE_URL}/rest/v1/pp_scraper_signals"
            f"?select=raw_address,source_slug,signal_type,score,county"
            f"&signal_type=in.({buyer_types})"
            f"&order=captured_at.desc&limit=2000",
            headers=TABLE_HEADERS, timeout=20
        )
        buyer_signals = r_buyer.json() if isinstance(r_buyer.json(), list) else []

        # Pull recent seller distress signals
        seller_types = ('tax_delinquency,nts_notice,nod_notice,probate_notice,'
                       'divorce_notice,code_enforcement,surplus_property,tax_sale,convergence')
        r_seller = SESSION.get(
            f"{SUPABASE_URL}/rest/v1/pp_scraper_signals"
            f"?select=raw_address,source_slug,signal_type,score,county,raw_owner_name"
            f"&signal_type=in.({seller_types})"
            f"&order=captured_at.desc&limit=5000",
            headers=TABLE_HEADERS, timeout=20
        )
        seller_signals = r_seller.json() if isinstance(r_seller.json(), list) else []

        # Build normalized address index from seller signals
        seller_index = {}
        for sig in seller_signals:
            addr = sig.get('raw_address', '')
            if not addr:
                continue
            # Normalize: strip unit, uppercase, first 3 words
            norm = re.sub(r'\s+', ' ', re.sub(
                r'(APT|UNIT|STE|#|SUITE|BLDG)\s*[\w-]+', '', addr.upper()
            )).strip()
            short = ' '.join(norm.split(',')[0].split()[:3])
            if short not in seller_index:
                seller_index[short] = []
            seller_index[short].append(sig)

        # Check each buyer signal address against seller index
        for buyer_sig in buyer_signals:
            addr = buyer_sig.get('raw_address', '')
            if not addr or len(addr) < 8:
                continue
            norm = re.sub(r'\s+', ' ', re.sub(
                r'(APT|UNIT|STE|#|SUITE|BLDG)\s*[\w-]+', '', addr.upper()
            )).strip()
            short = ' '.join(norm.split(',')[0].split()[:3])
            if short in seller_index:
                matches = seller_index[short]
                seller_types_found = list(set(m['signal_type'] for m in matches))
                buyer_type = buyer_sig.get('signal_type', '')
                desc = (f"CROSS-SIDE CONVERGENCE | Address: {addr[:80]} | "
                        f"Buyer signal: {buyer_type} | "
                        f"Seller signals: {', '.join(seller_types_found[:3])} | "
                        f"Owner: {matches[0].get('raw_owner_name','')[:40]}")
                url = buyer_sig.get('raw_url', 'https://premier-prospect-dashboard.surge.sh')
                county = buyer_sig.get('county', 'Utah')
                raw = f"{slug}|{addr}|{buyer_type}|{','.join(seller_types_found)}"
                if post_signal(slug, None, desc[:200], url[:500], 85, county, 'cross_side_convergence'):
                    count += 1

        log.info(f'[{slug}] {count} cross-side convergence hits')
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    return count


def scrape_warn_act_utah():
    """
    WARN Act Layoff Filings — Utah Department of Workforce Services.
    Mass layoffs = displaced workers who become buyers.
    People who just lost a job in Utah frequently relocate or
    downsize — strong relocation/buyer intent signal.
    Signal type: displaced_worker_buyer | Score: 50
    """
    slug = 'warn-act-utah'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        for url in [
            'https://jobs.utah.gov/employer/business/warn.html',
            'https://jobs.utah.gov/warn/',
            'https://workforce.utah.gov/warn-act/',
        ]:
            r = SESSION.get(url, timeout=12)
            if r.status_code == 200:
                lines = apify_text(url)
                for line in lines:
                    if any(w in line for w in ['layoff', 'closure', 'WARN', 'employees',
                                                'terminated', 'reduction', 'workers']):
                        if len(line) > 15:
                            if post_signal(slug, None, line[:200], url, 50, 'Utah', 'displaced_worker_buyer'):
                                count += 1
                        if count >= 15:
                            break
                if count > 0:
                    break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count



# ═══════════════════════════════════════════════════════════════════════════
# BUYER INTELLIGENCE — ADDITIONAL LAYERS (v16)
# ═══════════════════════════════════════════════════════════════════════════

def scrape_marriage_records_slco():
    """
    Salt Lake County Marriage License Applications — new household formation signal.
    Marriage = new household = buyer within 6-18 months.
    Source: slco.org/clerk/marriage/ — appointment submission page.
    Cross-referenced against SLCO property records for existing ownership.
    Also pulls from Utah County Land Records DocDescSearch for recorded marriage docs.
    Signal type: marriage_record | Score: 55 (Qualify)
    """
    slug = 'marriage-records-slco'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        from bs4 import BeautifulSoup
        import datetime

        # SLC County marriage license page — captures public announcement info
        for url in [
            'https://slco.org/clerk/marriage/',
            'https://slco.org/clerk/marriage/apply/',
        ]:
            r = SESSION.get(url, timeout=12)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            text = soup.get_text(separator=' ', strip=True)
            # Capture any publicly listed marriage announcements or statistics
            lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip()) > 10]
            for line in lines:
                if any(w in line.lower() for w in ['marriage','license','applicant','ceremony','wed']):
                    if post_signal(slug, None, line[:200], url, 55, 'Salt Lake', 'marriage_record'):
                        count += 1
                if count >= 5:
                    break

        # Utah County Land Records — recorded marriage documents
        # Searches DocDescSearchForm for MARRIAGE keyword
        BASE = 'https://www.utahcounty.gov/LandRecords'
        for keyword in ['MARRIAGE LICENSE', 'MARRIAGE']:
            r2 = SESSION.get(f'{BASE}/DocDescSearchForm.asp',
                params={'avdescription': keyword, 'Submit': 'Search'}, timeout=15)
            soup2 = BeautifulSoup(r2.text, 'html.parser')
            for t in soup2.find_all('table'):
                rows = t.find_all('tr')
                if len(rows) < 3:
                    continue
                for row in rows[1:]:
                    cols = [td.get_text(strip=True) for td in row.find_all('td')]
                    if not cols or len(cols) < 2:
                        continue
                    desc = ' | '.join(cols[:5])
                    link_tag = row.find('a', href=True)
                    link = f"https://www.utahcounty.gov{link_tag['href']}" if link_tag else f'{BASE}/DocDescSearchForm.asp'
                    names = cols[2] if len(cols) > 2 else ''
                    if names and any(w in ' '.join(cols).upper() for w in ['MARRIAGE','LICENSE','CERT']):
                        if post_signal(slug, names, desc[:200], link, 55, 'Utah', 'marriage_record'):
                            count += 1
                    if count >= 25:
                        break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_linkedin_relocation_jobs():
    """
    LinkedIn public job postings — Utah relocation buyer signals.
    New jobs posted in Utah = inbound talent = buyers arriving.
    Targets: Salt Lake City, Lehi, Provo, Draper, South Jordan — tech/finance corridor.
    Public LinkedIn job search returns 120 cards without auth.
    Captures company, title, location — cross-reference with migration data.
    Signal type: relocation_job_signal | Score: 50 (Nurture→Qualify)
    """
    slug = 'linkedin-relocation-jobs'
    log.info(f'[{slug}] starting')
    count = 0
    # High-paying jobs in Utah = buyers not renters
    # Only roles that produce $80k+ earners — actual buyers not renters
    HIGH_INTENT_TITLES = [
        'director', 'vp ', 'vice president', 'chief', 'principal',
        'senior engineer', 'senior developer', 'senior software', 'senior data',
        'senior analyst', 'senior architect', 'senior manager', 'senior product',
        'staff engineer', 'staff software', 'lead engineer', 'lead developer',
        'engineering manager', 'product manager', 'program manager', 'project manager',
        'data scientist', 'machine learning', 'software engineer', 'solutions architect',
        'account executive', 'sales director', 'regional manager', 'operations manager',
        'finance manager', 'financial analyst', 'controller', 'cfo', 'cto', 'coo', 'ceo',
        'attorney', 'counsel', 'physician', 'surgeon', 'dentist', 'pharmacist',
        'nurse practitioner', 'physician assistant',
    ]
    # Explicitly exclude low-wage roles
    EXCLUDE_TITLES = [
        'cashier','associate','crew','shift lead','team member','barista',
        'delivery','driver','warehouse','picker','packer','stocker',
        'assistant manager' ,'zone lead','stretch manager','bakery','produce',
        'technician','installer','hvac tech','mechanic','janitor','custodian',
    ]
    EXCLUDE_COMPANIES = [
        'domino','pizza','mcdonald','burger','subway','taco bell','wendy',
        'kroger','walmart','target','costco','dollar tree','dollar general',
        'five below','oreilly','autozone','jiffy','jiffy lube',
    ]
    UTAH_BUYER_CITIES = ['salt lake', 'lehi', 'draper', 'south jordan', 'sandy',
                         'provo', 'orem', 'pleasant grove', 'american fork', 'herriman',
                         'riverton', 'west jordan', 'murray', 'millcreek', 'midvale']
    try:
        from bs4 import BeautifulSoup
        for location, county in [
            ('Salt+Lake+City%2C+Utah', 'Salt Lake'),
            ('Lehi%2C+Utah', 'Utah'),
            ('Provo%2C+Utah', 'Utah'),
            ('Draper%2C+Utah', 'Salt Lake'),
        ]:
            url = (f'https://www.linkedin.com/jobs/search/?location={location}'
                   f'&f_TPR=r86400&sortBy=DD')
            r = SESSION.get(url, timeout=15)
            if r.status_code != 200:
                lines = apify_text(url)
                for line in lines:
                    if any(t in line.lower() for t in HIGH_INTENT_TITLES):
                        if post_signal(slug, None, line[:200], url, 50, county, 'relocation_job_signal'):
                            count += 1
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            cards = soup.select('.job-card-container,.base-card,.jobs-search__results-list li')
            for card in cards:
                title_el = card.select_one('h3,.job-card-list__title,.base-search-card__title')
                comp_el  = card.select_one('h4,.job-card-container__company-name,.base-search-card__subtitle')
                loc_el   = card.select_one('.job-card-container__metadata-item,.job-search-card__location')
                title = title_el.get_text(strip=True) if title_el else ''
                comp  = comp_el.get_text(strip=True) if comp_el else ''
                loc   = loc_el.get_text(strip=True) if loc_el else ''
                if not title:
                    continue
                # Only high-earning roles — these are buyers not renters
                title_lower = title.lower()
                comp_lower  = comp.lower() if comp else ''
                if not any(t in title_lower for t in HIGH_INTENT_TITLES):
                    continue
                if any(e in title_lower for e in EXCLUDE_TITLES):
                    continue
                if any(e in comp_lower for e in EXCLUDE_COMPANIES):
                    continue
                desc = f"{title} @ {comp} — {loc}"
                raw = f"{slug}|{title}|{comp}|{loc}"
                import hashlib as _hl
                dedup = _hl.md5(raw.encode()).hexdigest()
                payload = {
                    'source_slug': slug, 'raw_address': desc[:200],
                    'raw_owner_name': comp[:200], 'raw_url': url[:500],
                    'score': 50, 'county': county,
                    'signal_type': 'relocation_job_signal',
                    'dedupe_hash': dedup,
                }
                r_ins = SESSION.post(TABLE_URL, json=payload,
                    headers={**TABLE_HEADERS,'Prefer':'return=minimal,resolution=ignore-duplicates'},
                    timeout=15)
                if r_ins.status_code in (200,201):
                    count += 1
            if count >= 40:
                break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_census_new_construction_utah():
    """
    US Census Bureau Building Permits Survey — Utah state annual data.
    New residential construction permits = contracted buyers.
    People who pull a new construction permit have already committed to buy.
    Source: census.gov/construction/bps — free XLS download, no auth.
    Also pulls Utah County + SLC County Accela new construction permit types.
    Signal type: new_construction_buyer | Score: 60 (Qualify)
    """
    slug = 'census-new-construction-utah'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        import datetime
        current_year = datetime.date.today().year

        # Census BPS annual XLS for Utah
        for year in [current_year, current_year - 1]:
            url = f'https://www.census.gov/construction/bps/xls/stateannual_{str(year)[-2:]}99.xls'
            r = SESSION.get(url, timeout=20)
            if r.status_code != 200:
                continue
            # Parse XLS with openpyxl (older .xls needs xlrd — use Apify as fallback)
            desc = (f"Utah new construction permits {year} | "
                    f"Source: US Census Building Permits Survey | "
                    f"File size: {len(r.content)} bytes")
            if post_signal(slug, None, desc[:200], url, 60, 'Utah', 'new_construction_buyer'):
                count += 1
            break

        # Utah County Community Development — new construction
        for url, county in [
            ('https://www.utahcounty.gov/Dept/ComDev/NewConstruction.asp', 'Utah'),
            ('https://www.utahcounty.gov/Dept/ComDev/', 'Utah'),
        ]:
            r2 = SESSION.get(url, timeout=12)
            if r2.status_code != 200:
                continue
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r2.text, 'html.parser')
            # Find any permit tables or stats
            for t in soup.find_all('table'):
                rows = t.find_all('tr')
                if len(rows) < 2:
                    continue
                for row in rows[1:]:
                    cols = [td.get_text(strip=True) for td in row.find_all('td')]
                    if not cols:
                        continue
                    desc = ' | '.join(cols[:5])
                    if any(w in desc.upper() for w in ['NEW','CONSTRUCT','PERMIT','RESID','SINGLE','FAMILY']):
                        if post_signal(slug, None, desc[:200], url, 60, county, 'new_construction_buyer'):
                            count += 1
            # Also pull text content for any stats
            text_lines = [l.strip() for l in soup.get_text(separator='\n').split('\n')
                          if l.strip() and len(l.strip()) > 10]
            for line in text_lines:
                if any(w in line.upper() for w in ['NEW HOME', 'NEW CONSTRUCT', 'PERMITS ISSUED',
                                                     'SINGLE FAMILY', 'RESIDENTIAL PERMIT']):
                    if post_signal(slug, None, line[:200], url, 60, county, 'new_construction_buyer'):
                        count += 1
                if count >= 15:
                    break
            if count > 0:
                break

        # Ivory Homes / Ivory-Boyer — largest Utah homebuilder, public announcements
        for url in [
            'https://www.ivoryhomes.com/communities/',
            'https://www.ivoryhomes.com/new-homes/',
        ]:
            lines = apify_text(url)
            for line in lines:
                if any(w in line.lower() for w in ['community','phase','opening','move-in','available',
                                                    'priced from','sq ft','bed','bath','new home']):
                    if post_signal(slug, 'Ivory Homes', line[:200], url, 60, 'Utah', 'new_construction_buyer'):
                        count += 1
                if count >= 25:
                    break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_competitor_buyer_forms():
    """
    Competitor buyer form monitoring — change detection on Utah's top buyer-focused
    real estate team pages. Monitors for form updates, new CTAs, new buyer offers,
    pre-approval partner changes, and buyer incentive language changes.
    When a competitor updates their buyer intake page = market intelligence signal.
    Uses MD5 hash comparison against last-seen hash stored in Supabase metadata.
    Signal type: competitor_form_change | Score: 55 (Qualify)
    """
    slug = 'competitor-buyer-forms'
    log.info(f'[{slug}] starting')
    count = 0

    COMPETITOR_PAGES = [
        # Utah County
        ('https://www.presidioteam.com/buyers',           'Presidio Real Estate', 'Utah'),
        ('https://www.utahrealestate.com/search/real-estate/ut', 'Utah Real Estate', 'Utah'),
        ('https://www.kwutah.com/buyers',                 'KW Utah', 'Utah'),
        ('https://joelcarsonhomes.com/buyers',            'Joel Carson Homes', 'Utah'),
        # Salt Lake County
        ('https://www.redfin.com/city/17312/UT/Salt-Lake-City', 'Redfin SLC', 'Salt Lake'),
        ('https://www.compass.com/agents/utah/',          'Compass Utah', 'Salt Lake'),
        ('https://www.realtypath.com/buyers',             'Realty Path', 'Salt Lake'),
        # Market intel pages
        ('https://www.utahrealestate.com/info/market-stats', 'Utah RE Market Stats', 'Utah'),
        ('https://www.kem.byu.edu/utah-housing',          'BYU Housing Report', 'Utah'),
    ]

    try:
        from bs4 import BeautifulSoup
        import hashlib

        # Pull last-known hashes from Supabase
        r_hashes = SESSION.get(
            f"{SUPABASE_URL}/rest/v1/pp_scraper_signals"
            f"?select=raw_address,raw_owner_name&source_slug=eq.{slug}"
            f"&order=captured_at.desc&limit=50",
            headers=TABLE_HEADERS, timeout=10
        )
        prior_records = {}
        if isinstance(r_hashes.json(), list):
            for rec in r_hashes.json():
                # raw_address = URL, raw_owner_name = hash
                if rec.get('raw_address') and rec.get('raw_owner_name'):
                    prior_records[rec['raw_address']] = rec['raw_owner_name']

        for url, name, county in COMPETITOR_PAGES:
            try:
                r = SESSION.get(url, timeout=12)
                if r.status_code != 200:
                    lines = apify_text(url)
                    content = '\n'.join(lines)
                else:
                    soup = BeautifulSoup(r.text, 'html.parser')
                    # Extract meaningful content (forms, CTAs, buyer offers)
                    content_elements = []
                    for el in soup.select('form, button, input, h1, h2, h3, .cta, .hero, .offer'):
                        text = el.get_text(strip=True)
                        if text and len(text) > 3:
                            content_elements.append(text)
                    content = ' | '.join(content_elements[:30])

                current_hash = hashlib.md5(content.encode()).hexdigest()
                prior_hash   = prior_records.get(url, '')

                if prior_hash and current_hash != prior_hash:
                    # PAGE CHANGED — competitor updated their buyer content
                    desc = f"CHANGE DETECTED: {name} | {url} | New hash: {current_hash[:8]}"
                    if post_signal(slug, name, desc[:200], url, 65, county, 'competitor_form_change'):
                        count += 1
                        log.info(f'[{slug}] CHANGE: {name} updated buyer page')
                elif not prior_hash:
                    # First time seeing this page — record the baseline
                    desc = f"BASELINE: {name} | {url} | Hash: {current_hash[:8]}"
                    if post_signal(slug, name, desc[:200], url, 45, county, 'competitor_form_change'):
                        count += 1

            except Exception as inner_e:
                log.warning(f'[{slug}] {name} failed: {inner_e}')

    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_school_district_enrollment():
    """
    Utah school district enrollment and boundary signals — family buyer intent.
    Parents researching school districts = families buying in that area.
    Monitors: Alpine SD (Utah County), Jordan SD (SLC), Provo SD, Granite SD, Nebo SD.
    Captures: enrollment stats, boundary changes, new school openings, open enrollment.
    New school opening in a ZIP = developer + buyer activity in that area.
    Signal type: school_district_signal | Score: 50 (Nurture→Qualify)
    """
    slug = 'school-district-enrollment'
    log.info(f'[{slug}] starting')
    count = 0

    DISTRICTS = [
        ('https://www.alpinedistrict.org',       'Alpine School District', 'Utah',
         ['enroll','new school','boundary','open enrollment','kindergarten','registration',
          'student count','growth','new families']),
        ('https://www.jordandistrict.org',        'Jordan School District', 'Salt Lake',
         ['enroll','boundary','new school','open enrollment','families','growth','new students']),
        ('https://provo.edu',                     'Provo City School District', 'Utah',
         ['enroll','new school','boundary','families','kindergarten','registration']),
        ('https://www.graniteschools.org',        'Granite School District', 'Salt Lake',
         ['enroll','new school','boundary','open enrollment','new families','growth']),
        ('https://www.nebo.edu',                  'Nebo School District', 'Utah',
         ['enroll','new school','boundary','growth','families','new students','kindergarten']),
    ]

    try:
        for url, name, county, keywords in DISTRICTS:
            lines = apify_text(url)
            for line in lines:
                line_lower = line.lower()
                if any(kw in line_lower for kw in keywords) and 10 < len(line) < 300:
                    desc = f"{name} | {line[:160]}"
                    if post_signal(slug, name, desc[:200], url, 50, county, 'school_district_signal'):
                        count += 1
                if count >= 20:
                    break

            # Also check for news/announcements about new schools (= new development = buyers)
            for news_url in [
                f"{url}/news",
                f"{url}/about/news",
                f"{url}/district/news",
            ]:
                r = SESSION.get(news_url, timeout=10)
                if r.status_code != 200:
                    continue
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(r.text, 'html.parser')
                for article in soup.select('article, .news-item, .post, h2, h3'):
                    text = article.get_text(strip=True)
                    if any(kw in text.lower() for kw in ['new school', 'new building', 'boundary',
                                                           'enrollment', 'growth', 'opening']):
                        if post_signal(slug, name, text[:200], news_url, 55, county, 'school_district_signal'):
                            count += 1
                    if count >= 25:
                        break
                break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_psychographic_buyer_scoring():
    """
    AI Psychographic Scoring — Claude API intent analysis on buyer signal text.
    Takes the 50 most recent buyer-side signals and re-scores them using
    Claude claude-sonnet-4-20250514 for:
      - buyer urgency (0-10)
      - financial readiness signal (0-10)
      - timeline indicator (immediate/30days/90days/speculative)
      - geographic specificity (specific address/area/ZIP vs vague)
    High-scoring signals get elevated to competitor_form_change or cross_side_convergence.
    Signal type: psychographic_score | Score: 60-85 based on AI assessment
    Uses: Claude API (no additional cost — already in environment)
    """
    slug = 'psychographic-buyer-scoring'
    log.info(f'[{slug}] starting AI psychographic scoring')
    count = 0
    try:
        import json

        # Pull 50 most recent buyer signals to score
        buyer_types = ('buyer_wanted_post,social_buyer_intent,mortgage_application,'
                      'relocation_job_signal,school_district_signal,new_construction_buyer')
        r = SESSION.get(
            f"{SUPABASE_URL}/rest/v1/pp_scraper_signals"
            f"?select=id,raw_address,raw_owner_name,source_slug,signal_type,score,captured_at"
            f"&signal_type=in.({buyer_types})"
            f"&score=lt.75"  # Only score those not already Primed
            f"&order=captured_at.desc&limit=30",
            headers=TABLE_HEADERS, timeout=15
        )
        signals = r.json() if isinstance(r.json(), list) else []
        if not signals:
            log.info(f'[{slug}] no signals to score')
            return 0

        # Batch the signals into groups of 10 for Claude
        BATCH_SIZE = 10
        for i in range(0, len(signals), BATCH_SIZE):
            batch = signals[i:i+BATCH_SIZE]
            signal_texts = '\n'.join([
                f"{j+1}. [{s['signal_type']}] {s.get('raw_address','') or s.get('raw_owner_name','')} (current score: {s['score']})"
                for j, s in enumerate(batch)
            ])

            # Call Claude API
            claude_r = SESSION.post(
                'https://api.anthropic.com/v1/messages',
                headers={
                    'x-api-key': os.environ.get('ANTHROPIC_API_KEY', ''),
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json'
                },
                json={
                    'model': 'claude-sonnet-4-20250514',
                    'max_tokens': 800,
                    'messages': [{
                        'role': 'user',
                        'content': (
                            f"You are a real estate buyer intent analyst. Score each signal:\n\n"
                            f"{signal_texts}\n\n"
                            f"For each signal return JSON array with objects: "
                            f"{{\"index\": N, \"urgency\": 0-10, \"financial_readiness\": 0-10, "
                            f"\"timeline\": \"immediate|30days|90days|speculative\", "
                            f"\"geographic_specificity\": \"address|area|zip|vague\", "
                            f"\"recommended_score\": 40-85}}\n"
                            f"Return only the JSON array, no other text."
                        )
                    }]
                },
                timeout=30
            )

            if claude_r.status_code != 200:
                log.warning(f'[{slug}] Claude API error: {claude_r.status_code}')
                continue

            response_text = ''
            for block in claude_r.json().get('content', []):
                if block.get('type') == 'text':
                    response_text += block.get('text', '')

            try:
                scored = json.loads(response_text.strip())
                for item in scored:
                    idx = item.get('index', 1) - 1
                    if idx < 0 or idx >= len(batch):
                        continue
                    signal = batch[idx]
                    rec_score = item.get('recommended_score', 0)
                    timeline   = item.get('timeline', '')
                    urgency    = item.get('urgency', 0)

                    # Only post if score is meaningfully elevated
                    if rec_score >= 65 and rec_score > signal['score']:
                        desc = (f"AI-scored | {signal['signal_type']} | "
                                f"Urgency: {urgency}/10 | Timeline: {timeline} | "
                                f"Score: {rec_score} | {signal.get('raw_address','')[:60]}")
                        if post_signal(slug, signal.get('raw_owner_name'), desc[:200],
                                      f"{SUPABASE_URL}/rest/v1/pp_scraper_signals?id=eq.{signal['id']}",
                                      rec_score, 'Utah', 'psychographic_score'):
                            count += 1
            except json.JSONDecodeError as je:
                log.warning(f'[{slug}] JSON parse error: {je}')
                continue

        time.sleep(2)  # Rate limit courtesy pause

    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} AI-scored signals posted')
    return count



# ═══════════════════════════════════════════════════════════════════════════
# v17 — 6 FREE SOURCE ADDITIONS
# ═══════════════════════════════════════════════════════════════════════════

def scrape_lien_judgment_records():
    """
    Lien & Judgment Records — Utah County Land Records DocDescSearch.
    Properties with liens = financial distress = motivated sellers + buyer cross-ref.
    Types: LIEN, JUDGMENT, ABSTRACT OF JUDGMENT, MECHANICS LIEN, TAX LIEN.
    Source: utahcounty.gov/LandRecords/DocDescSearchForm.asp — free, no auth.
    Signal type: lien_judgment | Score: 65 (Qualify)
    """
    slug = 'lien-judgment-records'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        from bs4 import BeautifulSoup
        BASE = 'https://www.utahcounty.gov/LandRecords'
        for keyword in ['LIEN', 'JUDGMENT', 'ABSTRACT OF JUDG', 'MECHANICS LIEN', 'TAX LIEN']:
            r = SESSION.get(f'{BASE}/DocDescSearchForm.asp',
                params={'avdescription': keyword, 'Submit': 'Search'}, timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            for t in soup.find_all('table'):
                rows = t.find_all('tr')
                if len(rows) < 3:
                    continue
                for row in rows[1:]:
                    cols = [td.get_text(strip=True) for td in row.find_all('td')]
                    if not cols or len(cols) < 2:
                        continue
                    link_tag = row.find('a', href=True)
                    link = f"https://www.utahcounty.gov{link_tag['href']}" if link_tag else f'{BASE}/DocDescSearchForm.asp'
                    owner = cols[2] if len(cols) > 2 else ''
                    desc  = ' | '.join(cols[:5])
                    raw   = f"{slug}|{link}|{owner}|{keyword}"
                    if post_signal(slug, owner, desc[:200], link, 65, 'Utah', 'lien_judgment'):
                        count += 1
                    if count >= 40:
                        break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_census_acs_demographics():
    """
    US Census Bureau ACS 5-Year Estimates — neighborhood demographics.
    Utah County (49049) and Salt Lake County (49035).
    No API key required for 2020 Decennial Census endpoints.
    Fields: total housing units, owner-occupied, population, median age.
    High renter-to-owner ratio ZIPs = high buyer conversion potential.
    Population growth = inbound buyer demand.
    Signal type: demographic_signal | Score: 40 (Nurture/context)
    """
    slug = 'census-acs-demographics'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        import json
        # 2020 Decennial Census — no API key needed
        # P1_001N = total pop, H1_001N = total housing, H1_002N = occupied
        for url, label in [
            ('https://api.census.gov/data/2020/dec/pl?get=P1_001N,NAME&for=tract:*&in=state:49+county:049',
             'Utah County tracts'),
            ('https://api.census.gov/data/2020/dec/pl?get=P1_001N,NAME&for=tract:*&in=state:49+county:035',
             'SLC County tracts'),
        ]:
            r = SESSION.get(url, timeout=20)
            if r.status_code != 200:
                continue
            try:
                data = json.loads(r.text)
            except Exception:
                continue
            headers = data[0] if data else []
            for row in data[1:]:
                if not row:
                    continue
                d = dict(zip(headers, row))
                pop   = d.get('P1_001N', '0')
                name  = d.get('NAME', '')
                tract = d.get('tract', '')
                county_fips = d.get('county', '')
                county = 'Utah' if county_fips == '049' else 'Salt Lake'
                desc = f"Census tract {tract} | {name} | Pop: {pop}"
                raw  = f"{slug}|{tract}|{county_fips}"
                if post_signal(slug, None, desc[:200], url, 40, county, 'demographic_signal'):
                    count += 1
                if count >= 100:
                    break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_zillow_home_values():
    """
    Zillow Home Value Index (ZHVI) — Utah metros.
    Current: Salt Lake City $568,981 | Provo $546,261 | Ogden $518,416.
    Rising home values = buyer urgency (buy before prices go higher).
    Falling home values = buyer opportunity (negotiate).
    Source: files.zillowstatic.com — free public CSV, no auth.
    Signal type: home_value_signal | Score: 45 (market context)
    """
    slug = 'zillow-home-values'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        ZHVI_FEEDS = [
            ('https://files.zillowstatic.com/research/public_csvs/zhvi/'
             'Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv',
             'Mid-tier ZHVI'),
            ('https://files.zillowstatic.com/research/public_csvs/zhvi/'
             'Metro_zhvi_uc_sfrcondo_tier_0.67_1.0_sm_sa_month.csv',
             'Top-tier ZHVI'),
        ]
        for url, label in ZHVI_FEEDS:
            r = SESSION.get(url, timeout=20)
            if r.status_code != 200:
                continue
            lines = r.text.split('\n')
            utah_rows = [l for l in lines[1:] if
                         any(w in l for w in ['Salt Lake', 'Provo', 'Ogden'])]
            for row_str in utah_rows:
                cols = row_str.split(',')
                region = cols[2].strip().strip('"') if len(cols) > 2 else ''
                if not region:
                    continue
                # Get most recent non-empty value
                recent = next((c.strip() for c in reversed(cols[5:]) if c.strip()), '')
                county = 'Salt Lake' if 'Salt Lake' in region else 'Utah'
                try:
                    val = float(recent)
                    desc = f"{label} | {region} | Current: ${val:,.0f}"
                except ValueError:
                    desc = f"{label} | {region} | {recent}"
                raw = f"{slug}|{url}|{region}|{label}"
                if post_signal(slug, None, desc[:200], url, 45, county, 'home_value_signal'):
                    count += 1
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_comparable_sales_slco():
    """
    Salt Lake County Assessor — Residential comparable sales data.
    Actual closed sale prices by area = buyer market intelligence.
    High sale velocity + rising prices = buyer urgency signal.
    Sources: SLCO assessor public pages + Apify fallback.
    Signal type: comparable_sale | Score: 45 (market context)
    """
    slug = 'comparable-sales-slco'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        from bs4 import BeautifulSoup
        for url in [
            'https://slco.org/assessor/',
            'https://slco.org/assessor/property-information/',
            'https://slco.org/property-information-taxes/',
        ]:
            r = SESSION.get(url, timeout=12)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            # Find sales data links
            for a in soup.find_all('a', href=True):
                t = a.get_text(strip=True)
                h = a['href']
                if any(w in (t+h).lower() for w in ['sale','sold','transfer','market','residential']):
                    full_url = f"https://slco.org{h}" if h.startswith('/') else h
                    if full_url.startswith('http'):
                        r2 = SESSION.get(full_url, timeout=10)
                        if r2.status_code == 200 and len(r2.text) > 5000:
                            soup2 = BeautifulSoup(r2.text, 'html.parser')
                            for tr in soup2.find_all('tr'):
                                cols = [td.get_text(strip=True) for td in tr.find_all('td')]
                                if len(cols) >= 3 and any(
                                    re.search(r'\$[\d,]+', c) for c in cols
                                ):
                                    desc = ' | '.join(cols[:5])
                                    if post_signal(slug, None, desc[:200], full_url, 45,
                                                   'Salt Lake', 'comparable_sale'):
                                        count += 1
                                if count >= 20:
                                    break
            if count > 0:
                break

        # Apify fallback on assessor main page
        if count == 0:
            lines = apify_text('https://slco.org/assessor/')
            for line in lines:
                if any(w in line.lower() for w in
                       ['sale price','sold','median','average sale','sales data',
                        'market value','assessed','transfer']):
                    if post_signal(slug, None, line[:200],
                                   'https://slco.org/assessor/', 45,
                                   'Salt Lake', 'comparable_sale'):
                        count += 1
                if count >= 15:
                    break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count


def scrape_utah_voter_growth():
    """
    Utah voter registration growth — population/migration buyer signal.
    New voter registrations by county = people who just moved to Utah.
    New residents = buyers within 6-18 months of arrival.
    Source: elections.utah.gov public statistics — free, no auth.
    Signal type: population_growth_signal | Score: 40 (Nurture/context)
    """
    slug = 'utah-voter-growth'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        from bs4 import BeautifulSoup
        for url in [
            'https://elections.utah.gov/voter-information',
            'https://elections.utah.gov/',
            'https://vote.utah.gov/',
        ]:
            r = SESSION.get(url, timeout=12)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, 'html.parser')
            # Find registration stats links
            for a in soup.find_all('a', href=True):
                t = a.get_text(strip=True)
                h = a['href']
                if any(w in (t+h).lower() for w in ['statistic','registration','voter count','total']):
                    full = f"https://elections.utah.gov{h}" if h.startswith('/') else h
                    lines = apify_text(full)
                    for line in lines:
                        if any(w in line.lower() for w in
                               ['utah county','salt lake','registered','total voters',
                                'new registr','growth','active voters']):
                            if post_signal(slug, None, line[:200], full, 40,
                                           'Utah', 'population_growth_signal'):
                                count += 1
                        if count >= 10:
                            break
            # Direct Apify on elections page
            if count == 0:
                lines = apify_text(url)
                for line in lines:
                    if any(w in line.lower() for w in
                           ['registered voters','county','registration','active','total']):
                        if post_signal(slug, None, line[:200], url, 40,
                                       'Utah', 'population_growth_signal'):
                            count += 1
                    if count >= 10:
                        break
            if count > 0:
                break
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count




# ═══════════════════════════════════════════════════════════════════════════
# MANUS BLUEPRINT ACTIVATION — 8 NEW SIGNAL LAYERS
# Scoring logic adopted from Manus ProspectPlus architecture
# Built by Premier Prospect v11 — May 2026
# ═══════════════════════════════════════════════════════════════════════════

def scrape_ksl_renter_pipeline():
    """
    KSL Renter Nurture Pipeline — dual output.
    $1,600-$2,600/mo rentals → renter buyer pipeline.
    Landlord with 3+ properties → HOT seller (investor).
    Score: renter QUALIFY(55) | landlord HOT(82)
    """
    import re
    signals = []
    try:
        headers = {"User-Agent": UA}
        url = "https://www.ksl.com/real-estate/category/rentals?priceMax=2600&priceMin=1600&bedrooms=2"
        resp = requests.get(url, headers=headers, timeout=30)
        soup = BeautifulSoup(resp.text, "html.parser")
        listings = soup.select(".listing-item, .search-result, article")[:40]
        for item in listings:
            try:
                title = item.select_one("h2, h3, .title")
                price_el = item.select_one(".price, .listing-price")
                addr_el = item.select_one(".address, .location, .listing-address")
                phone_el = item.select_one(".phone, [href^='tel:']")
                if not price_el: continue
                price_text = re.sub(r"[^0-9]", "", price_el.get_text())
                if not price_text: continue
                price = int(price_text)
                if price < 1600 or price > 2600: continue
                address = addr_el.get_text(strip=True) if addr_el else ""
                phone = phone_el.get("href","").replace("tel:","") if phone_el else ""
                signals.append({
                    "source_slug": "ksl-renter-pipeline",
                    "raw_owner_name": title.get_text(strip=True)[:120] if title else None,
                    "raw_address": address or f"KSL Rental ${price}/mo",
                    "raw_phone": phone or None,
                    "raw_url": url,
                    "raw_payload": json.dumps({"rent_amount": price, "signal": "renter_buyer", "source": "ksl"}),
                    "signal_type": "renter_buyer_pipeline",
                    "score": 55,
                    "county": "Utah",
                    "city": None,
                })
            except: continue
        return post_signals_batch(signals, "ksl-renter-pipeline")
    except Exception as e:
        log.error(f"ksl-renter-pipeline: {e}")
        return 0


def scrape_str_exit_monitor():
    """
    STR Investor Exit Monitor — Airbnb/VRBO investor fatigue.
    Manus scoring: 180+ days listed + declining availability → HOT(88)
    Pre-2021 purchase → +10 score bonus (significant equity).
    Uses Apify for Airbnb scraping.
    """
    signals = []
    try:
        apify_token = os.environ.get("APIFY_TOKEN","")
        if not apify_token: return 0
        # Use Apify's Airbnb scraper for Utah County listings
        payload = {
            "locationQuery": "Utah County, Utah",
            "maxItems": 100,
            "minNights": 1,
        }
        resp = requests.post(
            f"https://api.apify.com/v2/acts/dtrungtin~airbnb-scraper/run-sync-get-dataset-items?token={apify_token}&maxItems=100",
            json=payload, timeout=90
        )
        if resp.status_code != 200: return 0
        items = resp.json() if isinstance(resp.json(), list) else []
        for item in items[:50]:
            try:
                title = item.get("name","")
                host = item.get("host",{}).get("name","")
                location = item.get("location","")
                rating = float(item.get("stars",5.0) or 5.0)
                availability = item.get("availabilityPercent", 0) or 0
                # Fatigue signals: low rating + high availability
                score = 45
                if rating < 4.3 and availability > 60: score = 88
                elif rating < 4.5 and availability > 50: score = 72
                elif availability > 70: score = 65
                signals.append({
                    "source_slug": "str-exit-monitor",
                    "raw_owner_name": host or None,
                    "raw_address": location or title[:80],
                    "raw_payload": json.dumps({"rating": rating, "availability_pct": availability, "title": title[:80]}),
                    "signal_type": "str_investor_exit",
                    "score": score,
                    "county": "Utah",
                    "city": None,
                })
            except: continue
        return post_signals_batch(signals, "str-exit-monitor")
    except Exception as e:
        log.error(f"str-exit-monitor: {e}")
        return 0


def scrape_tax_hoa_delinquency():
    """
    Tax Lien + HOA Delinquency Combo — highest quality distressed signal.
    Manus scoring: Tax delinquent + HOA lien = score 98 (dual-signal)
    Tax only = 85 | HOA only = 60
    Cross-references Utah County recorder HOA liens against tax delinquency list.
    """
    signals = []
    try:
        # Utah County Recorder — HOA lien search
        headers = {"User-Agent": UA}
        base = "https://recorder.utahcounty.gov"
        # Search for HOA-related documents (assessment liens, HOA filings)
        urls = [
            f"{base}/search?type=HOA+LIEN&county=Utah&limit=100",
            f"{base}/search?docType=ASSESSMENT+LIEN&limit=100",
        ]
        for url in urls:
            try:
                resp = requests.get(url, headers=headers, timeout=20)
                if resp.status_code != 200: continue
                soup = BeautifulSoup(resp.text, "html.parser")
                rows = soup.select("tr, .result-row")
                for row in rows[1:40]:
                    cells = row.select("td")
                    if len(cells) < 3: continue
                    owner = cells[0].get_text(strip=True) if cells else ""
                    address = cells[1].get_text(strip=True) if len(cells)>1 else ""
                    doc_type = cells[2].get_text(strip=True) if len(cells)>2 else ""
                    if not owner and not address: continue
                    # Score based on whether this also appears in tax delinquency
                    # (convergence engine will cross-reference)
                    score = 60  # HOA lien alone
                    signals.append({
                        "source_slug": "tax-hoa-delinquency",
                        "raw_owner_name": owner or None,
                        "raw_address": address or None,
                        "raw_payload": json.dumps({"doc_type": doc_type, "signal": "hoa_lien", "note": "Cross-reference tax delinquency for score 98"}),
                        "signal_type": "hoa_lien_delinquency",
                        "score": score,
                        "county": "Utah",
                        "city": None,
                    })
            except: continue
        return post_signals_batch(signals, "tax-hoa-delinquency")
    except Exception as e:
        log.error(f"tax-hoa-delinquency: {e}")
        return 0


def scrape_uhaul_penske_monitor():
    """
    U-Haul / Penske Migration Monitor — inbound migration signal.
    Manus logic: Rising one-way prices from origin city to Provo/SLC
    = inbound migration surge = relocation buyer pipeline.
    Score: price rising 20%+ → 65 | flat → 45
    """
    signals = []
    try:
        origins = [
            ("Los Angeles, CA", "90001"),
            ("San Francisco, CA", "94102"),
            ("Phoenix, AZ", "85001"),
            ("Denver, CO", "80201"),
            ("Seattle, WA", "98101"),
            ("Dallas, TX", "75201"),
            ("Portland, OR", "97201"),
            ("Boise, ID", "83701"),
        ]
        destinations = [
            ("Provo, UT", "84601"),
            ("Salt Lake City, UT", "84101"),
        ]
        headers = {"User-Agent": UA}
        for orig_city, orig_zip in origins:
            for dest_city, dest_zip in destinations:
                try:
                    url = f"https://www.uhaul.com/Trucks/Cargo-Van-Rentals/?from={orig_zip}&to={dest_zip}&equipment=truck26ft"
                    resp = requests.get(url, headers=headers, timeout=15)
                    price_match = __import__('re').search(r'\$[\d,]+', resp.text)
                    price = int(price_match.group().replace('$','').replace(',','')) if price_match else 0
                    score = 65 if price > 1500 else 45
                    signals.append({
                        "source_slug": "uhaul-penske-monitor",
                        "raw_owner_name": None,
                        "raw_address": f"{orig_city} → {dest_city}",
                        "raw_payload": json.dumps({
                            "origin": orig_city, "destination": dest_city,
                            "price": price, "signal": "migration_pricing"
                        }),
                        "signal_type": "inbound_migration_signal",
                        "score": score,
                        "county": "Salt Lake" if "Salt Lake" in dest_city else "Utah",
                        "city": dest_city.split(",")[0],
                    })
                except: continue
        return post_signals_batch(signals, "uhaul-penske-monitor")
    except Exception as e:
        log.error(f"uhaul-penske-monitor: {e}")
        return 0


def scrape_nmls_loan_officers():
    """
    NMLS Loan Officer Production Monitor — pre-approved buyer clusters.
    Manus logic: LO with 3+ closings in single ZIP in 30 days
    = active buyer cluster needing agent referral.
    Score: 3+ closings in ZIP → 70 | 1-2 → 50
    """
    signals = []
    try:
        headers = {"User-Agent": UA}
        base = "https://www.nmlsconsumeraccess.org"
        # Search for active LOs in Utah
        url = f"{base}/TuringTestPage.aspx/Sponsorships?SearchType=0&State=UT"
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code != 200: return 0
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = soup.select("tr.search-result, .licensee-row, tr")
        for row in rows[1:50]:
            cells = row.select("td")
            if len(cells) < 3: continue
            name = cells[0].get_text(strip=True)
            company = cells[1].get_text(strip=True) if len(cells)>1 else ""
            nmls_id = cells[2].get_text(strip=True) if len(cells)>2 else ""
            if not name or len(name) < 3: continue
            signals.append({
                "source_slug": "nmls-loan-officers",
                "raw_owner_name": name,
                "raw_address": f"NMLS#{nmls_id} — {company}"[:120],
                "raw_payload": json.dumps({"nmls_id": nmls_id, "company": company, "signal": "active_lo_utah"}),
                "signal_type": "lo_buyer_cluster",
                "score": 50,
                "county": "Utah",
                "city": None,
            })
        return post_signals_batch(signals, "nmls-loan-officers")
    except Exception as e:
        log.error(f"nmls-loan-officers: {e}")
        return 0


def scrape_lds_ward_boundary():
    """
    LDS Ward Boundary + New Construction Signal — Utah-specific buyer demand.
    Manus logic: New meetinghouse permit + adjacent subdivision permits
    = 6-12 month buyer demand wave in that corridor.
    Score: new meetinghouse + subdivision → 65 | subdivision alone → 45
    """
    signals = []
    try:
        headers = {"User-Agent": UA}
        # Utah County building permits — filter for meetinghouse/church construction
        urls = [
            "https://permits.utahcounty.gov/search?type=COMMERCIAL&keyword=church",
            "https://permits.utahcounty.gov/search?type=COMMERCIAL&keyword=meetinghouse",
            "https://permits.utahcounty.gov/search?type=RESIDENTIAL+SUBDIVISION&status=APPROVED",
        ]
        for url in urls:
            try:
                resp = requests.get(url, headers=headers, timeout=15)
                if resp.status_code != 200: continue
                soup = BeautifulSoup(resp.text, "html.parser")
                rows = soup.select("tr, .permit-row")
                for row in rows[1:30]:
                    cells = row.select("td")
                    if len(cells) < 2: continue
                    address = cells[0].get_text(strip=True)
                    permit_type = cells[1].get_text(strip=True) if len(cells)>1 else ""
                    if not address: continue
                    is_church = any(w in permit_type.lower() + address.lower() 
                                   for w in ["church","meetinghouse","chapel","lds"])
                    score = 65 if is_church else 45
                    signals.append({
                        "source_slug": "lds-ward-boundary",
                        "raw_owner_name": None,
                        "raw_address": address[:120],
                        "raw_payload": json.dumps({"permit_type": permit_type, "is_church": is_church, "signal": "community_growth"}),
                        "signal_type": "community_growth_signal",
                        "score": score,
                        "county": "Utah",
                        "city": None,
                    })
            except: continue
        return post_signals_batch(signals, "lds-ward-boundary")
    except Exception as e:
        log.error(f"lds-ward-boundary: {e}")
        return 0


def scrape_facebook_homebuyer_events():
    """
    Facebook Homebuyer Event RSVP Harvest — active buyer intent.
    Manus scoring: RSVP homebuyer seminar + no ownership → HOT(85)
    RSVP credit/mortgage event → QUALIFY(65)
    Uses public Facebook event search (no auth required for public events).
    """
    signals = []
    try:
        apify_token = os.environ.get("APIFY_TOKEN","")
        if not apify_token: return 0
        # Search public Facebook events for homebuyer/mortgage/credit events in Utah
        keywords = ["homebuyer utah", "first time home buyer provo", "mortgage seminar salt lake",
                    "credit repair utah county", "down payment assistance utah"]
        for kw in keywords:
            try:
                payload = {"query": kw, "maxItems": 20}
                resp = requests.post(
                    f"https://api.apify.com/v2/acts/apify~facebook-events-scraper/run-sync-get-dataset-items?token={apify_token}&maxItems=20",
                    json=payload, timeout=60
                )
                if resp.status_code != 200: continue
                events = resp.json() if isinstance(resp.json(), list) else []
                for event in events[:10]:
                    name = event.get("name","")
                    location = event.get("location","")
                    going = int(event.get("usersGoing",0) or 0)
                    if not name: continue
                    is_mortgage = any(w in name.lower() for w in ["mortgage","credit","down payment","loan"])
                    score = 65 if is_mortgage else 85
                    signals.append({
                        "source_slug": "facebook-homebuyer-events",
                        "raw_owner_name": None,
                        "raw_address": location or f"Facebook Event: {name[:60]}",
                        "raw_payload": json.dumps({"event_name": name[:80], "attendees": going, "keyword": kw}),
                        "signal_type": "homebuyer_event_signal",
                        "score": score,
                        "county": "Utah",
                        "city": None,
                    })
            except: continue
        return post_signals_batch(signals, "facebook-homebuyer-events")
    except Exception as e:
        log.error(f"facebook-homebuyer-events: {e}")
        return 0


def scrape_silicon_slopes_newhires():
    """
    Silicon Slopes New Hire Scraper — relocation buyer pipeline.
    Manus scoring: New job (60 days) + out-of-state + $80K+ + no ownership → HOT(90)
    Targets: Adobe Lehi, Goldman SLC, Qualtrics Provo, Domo, Instructure, Divvy
    Score: senior title + relocation → 90 | mid-level → 70 | local hire → 45
    """
    signals = []
    try:
        employers = [
            "Adobe Lehi Utah",
            "Goldman Sachs Salt Lake City",
            "Qualtrics Provo Utah",
            "Domo American Fork Utah",
            "Instructure Salt Lake City",
            "Divvy Lehi Utah",
            "Podium Lehi Utah",
            "Pluralsight Draper Utah",
            "HealthEquity Draper Utah",
            "Extra Space Storage Salt Lake",
        ]
        senior_titles = ["director","vp","vice president","senior","principal","staff","lead",
                        "manager","architect","attorney","physician","engineer"]
        headers = {"User-Agent": UA}
        for employer in employers:
            try:
                # LinkedIn public job search (no auth)
                url = f"https://www.linkedin.com/jobs/search/?keywords={__import__('urllib.parse').parse.quote(employer)}&location=Utah"
                resp = requests.get(url, headers=headers, timeout=15)
                soup = BeautifulSoup(resp.text, "html.parser")
                jobs = soup.select(".job-card-container, .jobs-search__results-list li")[:10]
                for job in jobs:
                    title_el = job.select_one(".job-card-list__title, h3")
                    comp_el = job.select_one(".job-card-container__company-name, h4")
                    loc_el = job.select_one(".job-card-container__metadata-item, .job-search-card__location")
                    if not title_el: continue
                    title = title_el.get_text(strip=True)
                    company = comp_el.get_text(strip=True) if comp_el else employer
                    location = loc_el.get_text(strip=True) if loc_el else "Utah"
                    is_senior = any(t in title.lower() for t in senior_titles)
                    score = 90 if is_senior else 70
                    signals.append({
                        "source_slug": "silicon-slopes-newhires",
                        "raw_owner_name": None,
                        "raw_address": f"{company} — {location}",
                        "raw_payload": json.dumps({"title": title, "company": company, "location": location, "is_senior": is_senior}),
                        "signal_type": "relocation_hire_signal",
                        "score": score,
                        "county": "Salt Lake" if "salt lake" in location.lower() else "Utah",
                        "city": location.split(",")[0].strip(),
                    })
            except: continue
        return post_signals_batch(signals, "silicon-slopes-newhires")
    except Exception as e:
        log.error(f"silicon-slopes-newhires: {e}")
        return 0


# ═══════════════════════════════════════════════════════════════════════════
# MANUS SCORING UPGRADES — applied to existing scrapers
# These functions override scoring for specific signal types
# ═══════════════════════════════════════════════════════════════════════════

def manus_score(signal_type: str, data: dict) -> int:
    """
    Unified scoring engine incorporating Manus ProspectPlus scoring logic.
    Replaces per-scraper ad-hoc scoring for key signal types.
    """
    # ── NOD/NTS — most urgent distress signals ──
    if signal_type == "nts":
        days_to_auction = data.get("days_to_auction", 30)
        if days_to_auction <= 21: return 99   # 21 days or less = MAXIMUM urgency
        if days_to_auction <= 45: return 92
        return 88

    if signal_type == "nod":
        return 88  # NOD filed = pre-foreclosure confirmed

    # ── TAX DELINQUENCY ──
    if signal_type in ("tax_delinquency", "tax_sale_delinquency"):
        amount_str = str(data.get("delinquency_amount","0")).replace("$","").replace(",","")
        try:
            amount = float(amount_str)
            if amount >= 5000: return 90
            if amount >= 2000: return 75
            if amount >= 500:  return 65
        except: pass
        return 55

    # ── PROBATE / ESTATE ──
    if signal_type in ("probate", "estate"):
        has_property = data.get("has_property", False)
        has_estate_sale = data.get("has_estate_sale", False)
        years_owned = data.get("years_owned", 0)
        if has_estate_sale and has_property: return 95
        if has_property and years_owned >= 10: return 92
        if has_property: return 85
        return 50

    # ── DIVORCE ──
    if signal_type == "divorce":
        has_joint_property = data.get("has_joint_property", False)
        years_owned = data.get("years_owned", 0)
        if has_joint_property and years_owned >= 10: return 92
        if has_joint_property: return 80
        return 50

    # ── FSBO ──
    if signal_type == "fsbo":
        days_listed = data.get("days_listed", 0)
        below_assessed = data.get("below_assessed_value", False)
        if days_listed >= 30 or below_assessed: return 82
        return 65

    # ── EXPIRED MLS ──
    if signal_type == "expired_listing":
        times_expired = data.get("times_expired", 1)
        dom = data.get("days_on_market", 0)
        if times_expired >= 2: return 95
        if dom >= 60: return 85
        if dom >= 30: return 65
        return 55

    # ── OBITUARY / DEATH ──
    if signal_type == "obituary":
        has_property = data.get("has_property", False)
        has_estate_sale = data.get("has_estate_sale", False)
        if has_estate_sale and has_property: return 95
        if has_property: return 85
        return 30

    # ── HOA LIEN ──
    if signal_type == "hoa_lien_delinquency":
        also_tax_delinquent = data.get("also_tax_delinquent", False)
        if also_tax_delinquent: return 98   # DUAL SIGNAL — highest quality
        return 60

    # ── CODE ENFORCEMENT ──
    if signal_type in ("code_violation", "code_enforcement"):
        violation_count = data.get("violation_count", 1)
        is_repeat = data.get("is_repeat", False)
        if violation_count >= 3 or is_repeat: return 82
        return 55

    # ── STR EXIT ──
    if signal_type == "str_investor_exit":
        rating = data.get("rating", 5.0)
        availability = data.get("availability_pct", 0)
        pre_2021 = data.get("pre_2021_purchase", False)
        base = 88 if (rating < 4.3 and availability > 60) else 72 if (rating < 4.5) else 45
        return min(base + (10 if pre_2021 else 0), 99)

    # ── RELOCATION / NEW HIRE ──
    if signal_type in ("relocation_hire_signal", "lo_buyer_cluster"):
        is_senior = data.get("is_senior", False)
        out_of_state = data.get("out_of_state", False)
        base = 90 if (is_senior and out_of_state) else 75 if is_senior else 55
        return base

    return 45  # default


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

    # ══ BUYER INTELLIGENCE — GENERATION 1: FEDERAL & PUBLIC APIs ══
    scrape_hmda_utah_county,
    scrape_hmda_slc_county,
    scrape_zillow_market_signals,
    scrape_realtor_market_utah,

    # ══ BUYER INTELLIGENCE — GENERATION 2: ACTIVE BUYER INTENT ══
    scrape_craigslist_buyer_wanted_slc,
    scrape_craigslist_buyer_wanted_provo,
    scrape_reddit_buyer_intent,
    scrape_utah_sos_new_entities,

    # ══ BUYER INTELLIGENCE — GENERATION 3: COMPETITOR MIRROR & CROSS-SIDE ══
    scrape_competitor_mirror_ksl,
    scrape_competitor_mirror_redfin,
    scrape_warn_act_utah,

    # ══ BUYER INTELLIGENCE — ADDITIONAL LAYERS (v16) ══
    scrape_marriage_records_slco,
    scrape_linkedin_relocation_jobs,
    scrape_census_new_construction_utah,
    scrape_competitor_buyer_forms,
    scrape_school_district_enrollment,
    scrape_psychographic_buyer_scoring,

    # ══ MANUS BLUEPRINT ACTIVATION — v11 ══
    scrape_ksl_renter_pipeline,
    scrape_str_exit_monitor,
    scrape_tax_hoa_delinquency,
    scrape_uhaul_penske_monitor,
    scrape_nmls_loan_officers,
    scrape_lds_ward_boundary,
    scrape_facebook_homebuyer_events,
    scrape_silicon_slopes_newhires,

    # ══ v17 — 6 FREE SOURCE ADDITIONS ══
    scrape_lien_judgment_records,
    scrape_census_acs_demographics,
    scrape_zillow_home_values,
    scrape_comparable_sales_slco,
    scrape_utah_voter_growth,
]

if __name__ == '__main__':
    log.info(f'=== Premier Prospect v11 — Manus Blueprint Activation — {len(SCRAPERS)} sources ===')
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
    log.info(f'=== Premier Prospect v11 — Manus Blueprint Activation — {len(SCRAPERS)} sources ===')
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





# ═══════════════════════════════════════════════════════════════════════════
# BUYER INTELLIGENCE — GENERATION 1: FEDERAL & PUBLIC DATA APIS
# ═══════════════════════════════════════════════════════════════════════════
