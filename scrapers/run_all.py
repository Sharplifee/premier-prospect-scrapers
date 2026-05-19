"""
Premier Prospect — GitHub Actions Scraper Runner v6
Apify fetch + text-based parsing (no HTML/soup needed).
24 active sources. Fully tested parsers.
"""
import os, hashlib, loggingquests

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
    slug = 'wasatch-county-tax-sale'
    log.info(f'[{slug}] starting')
    lines = apify_text('https://wasatch.utah.gov/departments/treasurer')
    count = 0
    for line in lines:
        if any(w in line.lower() for w in ['tax','property','treasurer','delinquent','sale','notice']) and 10 < len(line) < 300:
            if post_signal(slug, None, line[:200], 'https://wasatch.utah.gov/departments/treasurer', 65, 'Wasatch', 'tax_sale'):
                count += 1
            if count >= 20: break
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
    lines = apify_text('https://classifieds.ksl.com/search/?category=real-estate-for-sale&subCategory=real-estate-homes-for-sale&owner=1')
    count = 0
    utah_cities = ['provo','orem','lehi','springville','payson','spanish fork','pleasant grove','american fork','mapleton','eagle mountain','saratoga springs','elk ridge','santaquin','nephi','manti','richfield','price','helper','moab']
    for line in lines:
        if any(c in line.lower() for c in utah_cities) or any(w in line.lower() for w in ['bedroom','bath','sq ft','sqft','acre']):
            if post_signal(slug, None, line[:200], 'https://classifieds.ksl.com/search/?category=real-estate-for-sale&owner=1', 65, 'Utah', 'fsbo'):
                count += 1
            if count >= 30: break
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
    return scrape_lir('uco-lir-parcels','Utah','https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Utah_LIR/FeatureServer/0')
def scrape_davis_lir_parcels():
    return scrape_lir('davis-lir-parcels','Davis','https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Davis_LIR/FeatureServer/0')
def scrape_slco_lir_parcels():
    return scrape_lir('slco-lir-parcels','Salt Lake','https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_SaltLake_LIR/FeatureServer/0')
def scrape_weber_lir_parcels():
    return scrape_lir('weber-lir-parcels','Weber','https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Weber_LIR/FeatureServer/0')

# ─── MAIN ─────────────────────────────────────────────────────────────────────
SCRAPERS = [
    scrape_obituaries_herald, scrape_probate_court, scrape_utah_county_tax_delinquency,
    scrape_utah_county_nts, scrape_slc_county_nts, scrape_slc_tax_sale,
    scrape_wasatch_tax_sale, scrape_wasatch_county_nts, scrape_slc_assessor,
    scrape_slc_real_estate, scrape_slc_public_surplus, scrape_wasatch_public_surplus,
    scrape_south_slc_permits, scrape_south_slc_permits_pdf, scrape_utah_county_codev,
    scrape_utah_county_directory, scrape_utah_county_property_info, scrape_utah_county_real_property,
    scrape_uvhba_directory, scrape_ksl_fsbo,
    scrape_uco_lir_parcels, scrape_davis_lir_parcels, scrape_slco_lir_parcels, scrape_weber_lir_parcels,
]

if __name__ == '__main__':
    log.info(f'=== Premier Prospect v6 — {len(SCRAPERS)} sources ===')
    total = 0
    for fn in SCRAPERS:
        try:
            total += fn() or 0
        except Exception as e:
            log.error(f'{fn.__name__} crashed: {e}')
    log.info(f'=== Done — {total} total signals ===')
