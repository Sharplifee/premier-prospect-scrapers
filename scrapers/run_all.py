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


