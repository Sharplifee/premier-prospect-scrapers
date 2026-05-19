"""
Premier Prospect — GitHub Actions Scraper Runner v5
Uses Apify website-content-crawler to bypass bot detection on all sources.
24 active sources. Posts signals to Supabase ingest endpoint.
"""
import os, hashlib, logging, requests, json, re
from datetime import datetime, timezone
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('pp.scrapers')

SUPABASE_URL  = os.environ['SUPABASE_URL']
SUPABASE_KEY  = os.environ['SUPABASE_SERVICE_KEY']
APIFY_TOKEN   = os.environ.get('APIFY_TOKEN', '')
INGEST_URL    = f"{SUPABASE_URL}/functions/v1/pp-ingest"

INGEST_HEADERS = {
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'apikey': SUPABASE_KEY,
}

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
})

def dh(*parts):
    return hashlib.sha256('|'.join(str(p or '') for p in parts).encode()).hexdigest()

def post_signal(slug, owner, address, url, score, county, signal_type):
    payload = {
        'event_type': 'scraper_signal',
        'source_slug': slug,
        'raw_owner_name': owner or '',
        'raw_address': address or '',
        'raw_url': url or '',
        'score': score,
        'county': county,
        'signal_type': signal_type,
        'dedupe_hash': dh(slug, url, owner, address),
        'captured_at': datetime.now(timezone.utc).isoformat(),
    }
    try:
        r = requests.post(INGEST_URL, json=payload, headers=INGEST_HEADERS, timeout=15)
        return r.status_code in (200, 201, 409)
    except Exception as e:
        log.error(f'POST failed for {slug}: {e}')
        return False

def apify_fetch(url, max_pages=1):
    """Fetch via Apify crawler — bypasses bot detection, returns text content."""
    if not APIFY_TOKEN:
        log.warning('No APIFY_TOKEN set')
        return None
    try:
        r = SESSION.post(
            f'https://api.apify.com/v2/acts/apify~website-content-crawler/run-sync-get-dataset-items?token={APIFY_TOKEN}&timeout=60',
            json={'startUrls': [{'url': url}], 'maxCrawlPages': max_pages, 'crawlerType': 'cheerio'},
            timeout=90
        )
        if r.status_code in (200, 201):
            data = r.json()
            if isinstance(data, list) and data:
                return data
        log.error(f'Apify fetch failed {url}: {r.status_code}')
        return None
    except Exception as e:
        log.error(f'Apify fetch error {url}: {e}')
        return None

def fetch_text(url):
    """Get page text via Apify, returns (text, soup) or (None, None)."""
    results = apify_fetch(url)
    if not results:
        return None, None
    text = results[0].get('text', '') or results[0].get('markdown', '') or ''
    # Also get HTML if available for link extraction
    html = results[0].get('html', '') or ''
    soup = BeautifulSoup(html, 'html.parser') if html else BeautifulSoup(f'<pre>{text}</pre>', 'html.parser')
    return text, soup

def fetch_json_api(url, params=None):
    """Direct JSON API call — no bot detection on ArcGIS/government APIs."""
    try:
        r = SESSION.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f'JSON API failed {url}: {e}')
        return None

# ─── SCRAPERS ────────────────────────────────────────────────────────────────

def scrape_obituaries_herald():
    slug = 'obituaries-herald'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://www.heraldextra.com/obituaries/')
    if not text: return 0
    count = 0
    # Parse names from text — pattern: Name\nDate line
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    months_short = ['January','February','March','April','May','June','July','August','September','October','November','December']
    for i, line in enumerate(lines):
        # A name line is followed by a month/date line
        if i+1 < len(lines) and any(m in lines[i+1] for m in months_short):
            name = line
            if 4 < len(name) < 60 and not any(c.isdigit() for c in name[:5]):
                url = f"https://www.heraldextra.com/obituaries/"
                if post_signal(slug, name, None, url, 55, 'Utah', 'obituary'):
                    count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_probate_court():
    slug = 'probate-court'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://www.utah.gov/pmn/search.html')
    if not text: return 0
    count = 0
    keywords = ['probate','estate','decedent','personal representative']
    lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip()) > 10]
    for line in lines:
        if any(k in line.lower() for k in keywords):
            if post_signal(slug, None, line[:200], 'https://www.utah.gov/pmn/search.html', 70, 'Utah', 'probate_notice'):
                count += 1
            if count >= 30: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_utah_county_tax_delinquency():
    slug = 'utah-county-tax-delinquency'
    log.info(f'[{slug}] starting')
    # Use propertytax.utah.gov which confirmed 200 and has data
    text, soup = fetch_text('https://propertytax.utah.gov/')
    if not text: return 0
    count = 0
    for a in (soup.find_all('a', href=True) if soup else []):
        name = a.get_text(strip=True)
        href = a.get('href','')
        if any(w in (name+href).lower() for w in ['delinquent','tax sale','lien','notice']):
            full = href if href.startswith('http') else f"https://propertytax.utah.gov{href}"
            if post_signal(slug, name, None, full, 80, 'Utah', 'tax_delinquency'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_utah_county_nts():
    slug = 'utah-county-nts'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://www.utah.gov/pmn/search.html')
    if not text: return 0
    count = 0
    keywords = ['trustee','foreclosure','notice of trustee','notice of sale','default']
    lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip()) > 15]
    for line in lines:
        if any(k in line.lower() for k in keywords):
            if post_signal(slug, None, line[:300], 'https://www.utah.gov/pmn/search.html', 80, 'Utah', 'nts_notice'):
                count += 1
            if count >= 30: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_slc_county_nts():
    slug = 'slc-county-nts'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://www.saltlakecounty.gov/public-notice/')
    if not text: return 0
    count = 0
    keywords = ['trustee','foreclosure','default','notice of sale']
    lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip()) > 15]
    for line in lines:
        if any(k in line.lower() for k in keywords):
            if post_signal(slug, None, line[:300], 'https://www.saltlakecounty.gov/public-notice/', 80, 'Salt Lake', 'nts_notice'):
                count += 1
            if count >= 30: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_slc_tax_sale():
    slug = 'slc-county-tax-sale'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://www.saltlakecounty.gov/treasurer/property-taxes/')
    if not text: return 0
    count = 0
    if soup:
        for a in soup.find_all('a', href=True):
            name = a.get_text(strip=True)
            href = a.get('href','')
            if len(name) > 5 and any(x in (name+href).lower() for x in ['delinquent','tax sale','lien','relief','notice']):
                full = f"https://www.saltlakecounty.gov{href}" if href.startswith('/') else href
                if post_signal(slug, name, None, full, 70, 'Salt Lake', 'tax_sale'):
                    count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_wasatch_tax_sale():
    slug = 'wasatch-county-tax-sale'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://wasatch.utah.gov/departments/treasurer')
    if not text: return 0
    count = 0
    lines = [l.strip() for l in text.split('\n') if 10 < len(l.strip()) < 300]
    for line in lines[:50]:
        if any(w in line.lower() for w in ['tax','property','treasurer','delinquent','sale','notice']):
            if post_signal(slug, None, line[:200], 'https://wasatch.utah.gov/departments/treasurer', 65, 'Wasatch', 'tax_sale'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_wasatch_county_nts():
    slug = 'wasatch-county-nts'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://wasatch.utah.gov/departments/treasurer')
    if not text: return 0
    count = 0
    keywords = ['trustee','foreclosure','default','notice','delinquent']
    lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip()) > 15]
    for line in lines:
        if any(k in line.lower() for k in keywords):
            if post_signal(slug, None, line[:300], 'https://wasatch.utah.gov/departments/treasurer', 75, 'Wasatch', 'nts_notice'):
                count += 1
            if count >= 20: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_slc_assessor():
    slug = 'slc-assessor'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://www.saltlakecounty.gov/assessor/')
    if not text: return 0
    count = 0
    if soup:
        for a in soup.find_all('a', href=True):
            name = a.get_text(strip=True)
            href = a.get('href','')
            if len(name) > 5 and any(x in (name+href).lower() for x in ['parcel','property','value','appeal','lookup','search','data']):
                full = f"https://www.saltlakecounty.gov{href}" if href.startswith('/') else href
                if post_signal(slug, name, None, full, 40, 'Salt Lake', 'assessor_record'):
                    count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_slc_real_estate():
    slug = 'slc-real-estate'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://www.saltlakecounty.gov/real-estate/public-sale/')
    if not text: return 0
    count = 0
    lines = [l.strip() for l in text.split('\n') if 15 < len(l.strip()) < 400 and any(c.isdigit() for c in l)]
    for line in lines[:30]:
        if post_signal(slug, None, line[:300], 'https://www.saltlakecounty.gov/real-estate/public-sale/', 50, 'Salt Lake', 'surplus_property'):
            count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_slc_public_surplus():
    slug = 'slc-public-surplus'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://www.saltlakecounty.gov/real-estate/')
    if not text: return 0
    count = 0
    if soup:
        for a in soup.find_all('a', href=True):
            name = a.get_text(strip=True)
            href = a.get('href','')
            if len(name) > 5 and any(x in (name+href).lower() for x in ['sale','surplus','property','real estate','auction']):
                full = f"https://www.saltlakecounty.gov{href}" if href.startswith('/') else href
                if post_signal(slug, name, None, full, 45, 'Salt Lake', 'surplus'):
                    count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_wasatch_public_surplus():
    slug = 'wasatch-public-surplus'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://www.publicsurplus.com/sms/wasatchco,ut/list/current')
    if not text: return 0
    count = 0
    lines = [l.strip() for l in text.split('\n') if 10 < len(l.strip()) < 400 and any(c.isdigit() for c in l)]
    for line in lines[:30]:
        if post_signal(slug, None, line[:300], 'https://www.publicsurplus.com/sms/wasatchco,ut/list/current', 40, 'Wasatch', 'surplus_auction'):
            count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_south_slc_permits():
    slug = 'south-slc-permits'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://www.slcgov.com/cda/building-services')
    if not text:
        text, soup = fetch_text('https://www.slc.gov/building-services/')
    if not text: return 0
    count = 0
    if soup:
        for a in soup.find_all('a', href=True):
            name = a.get_text(strip=True)
            href = a.get('href','')
            if len(name) > 5 and 'permit' in (name+href).lower():
                if post_signal(slug, name, None, href, 35, 'Salt Lake', 'permit'):
                    count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_south_slc_permits_pdf():
    slug = 'south-slc-permits-pdf'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://www.southsaltlake.org/172/Building-Permits')
    if not text: return 0
    count = 0
    lines = [l.strip() for l in text.split('\n') if len(l.strip()) > 10]
    for line in lines[:30]:
        if any(w in line.lower() for w in ['permit','building','construction','inspection']):
            if post_signal(slug, None, line[:200], 'https://www.southsaltlake.org/172/Building-Permits', 35, 'Salt Lake', 'permit'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_utah_county_codev():
    slug = 'utah-county-codev'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://www.utahcounty.gov/Dept/ComDev/CodeEnforcement.asp')
    if not text: return 0
    count = 0
    lines = [l.strip() for l in text.split('\n') if 20 < len(l.strip()) < 400]
    for line in lines:
        if any(w in line.lower() for w in ['violation','complaint','nuisance','code enforcement','property']):
            if post_signal(slug, None, line[:300], 'https://www.utahcounty.gov/Dept/ComDev/CodeEnforcement.asp', 50, 'Utah', 'code_enforcement'):
                count += 1
            if count >= 20: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_utah_county_directory():
    slug = 'utah-county-directory'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://recorder.utahcounty.gov/find-records')
    if not text: return 0
    count = 0
    if soup:
        for a in soup.find_all('a', href=True):
            name = a.get_text(strip=True)
            href = a.get('href','')
            if len(name) > 5 and any(x in (name+href).lower() for x in ['deed','transfer','lien','mortgage','notice','record','document']):
                full = f"https://recorder.utahcounty.gov{href}" if href.startswith('/') else href
                if post_signal(slug, name, None, full, 60, 'Utah', 'deed_transfer'):
                    count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_utah_county_property_info():
    slug = 'utah-county-property-info'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://www.utahcounty.gov/LandRecords/Index.asp')
    if not text: return 0
    count = 0
    if soup:
        for a in soup.find_all('a', href=True):
            name = a.get_text(strip=True)
            href = a.get('href','')
            if len(name) > 5 and any(x in (name+href).lower() for x in ['parcel','property','deed','record','transfer','lien','search']):
                full = f"https://www.utahcounty.gov{href}" if href.startswith('/') else href
                if post_signal(slug, name, None, full, 55, 'Utah', 'property_record'):
                    count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_utah_county_real_property():
    slug = 'utah-county-real-property'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://assessor.utahcounty.gov/real-property')
    if not text: return 0
    count = 0
    if soup:
        for a in soup.find_all('a', href=True):
            name = a.get_text(strip=True)
            href = a.get('href','')
            if len(name) > 5 and any(x in (name+href).lower() for x in ['parcel','value','search','appeal','lookup','property','assessment']):
                full = f"https://assessor.utahcounty.gov{href}" if href.startswith('/') else href
                if post_signal(slug, name, None, full, 45, 'Utah', 'real_property_record'):
                    count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_uvhba_directory():
    slug = 'uvhba-directory'
    log.info(f'[{slug}] starting')
    text, soup = fetch_text('https://business.uvhba.com/list/ql/contractors-subcontractors-7')
    if not text: return 0
    count = 0
    if soup:
        for a in soup.find_all('a', href=True):
            name = a.get_text(strip=True)
            href = a.get('href','')
            if '/list/member/' in href and len(name) > 3 and name not in ['Map','Website','Email']:
                if post_signal(slug, name, None, href, 30, 'Utah', 'contractor_directory'):
                    count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_ksl_fsbo():
    slug = 'ksl-fsbo'
    log.info(f'[{slug}] starting')
    # KSL FSBO via Apify
    text, soup = fetch_text('https://classifieds.ksl.com/search/?category=real-estate-for-sale&subCategory=real-estate-homes-for-sale&owner=1')
    if not text: return 0
    count = 0
    lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip()) > 10]
    for line in lines[:60]:
        if any(w in line.lower() for w in ['bedroom','bath','sq ft','sqft','home','house','provo','orem','lehi','springville','spanish fork','payson']):
            if post_signal(slug, None, line[:200], 'https://classifieds.ksl.com/search/?category=real-estate-for-sale&owner=1', 65, 'Utah', 'fsbo'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── LIR PARCELS via ArcGIS JSON API (no bot detection) ─────────────────────
def scrape_lir_arcgis(slug, county, service_url, max_records=200):
    log.info(f'[{slug}] starting')
    count = 0
    data = fetch_json_api(f"{service_url}/query", params={
        'where': '1=1',
        'outFields': 'PARCEL_ID,PARCEL_ADD,PARCEL_CITY,TOTAL_MKT_VALUE,PROP_CLASS,PRIMARY_RES',
        'resultRecordCount': max_records,
        'orderByFields': 'OBJECTID DESC',
        'f': 'json',
    })
    if not data: return 0
    for feat in data.get('features', []):
        attrs = feat.get('attributes', {})
        address = attrs.get('PARCEL_ADD', '')
        city = attrs.get('PARCEL_CITY', '')
        parcel = attrs.get('PARCEL_ID', '')
        value = attrs.get('TOTAL_MKT_VALUE') or 0
        full_address = f"{address}, {city}".strip(', ') if city else address
        score = 65 if value and value < 400000 else 45
        if address:
            if post_signal(slug, None, full_address, f"{service_url}/query?where=PARCEL_ID='{parcel}'", score, county, 'lir_parcel'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_uco_lir_parcels():
    return scrape_lir_arcgis('uco-lir-parcels', 'Utah',
        'https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Utah_LIR/FeatureServer/0')

def scrape_davis_lir_parcels():
    return scrape_lir_arcgis('davis-lir-parcels', 'Davis',
        'https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Davis_LIR/FeatureServer/0')

def scrape_slco_lir_parcels():
    return scrape_lir_arcgis('slco-lir-parcels', 'Salt Lake',
        'https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_SaltLake_LIR/FeatureServer/0')

def scrape_weber_lir_parcels():
    return scrape_lir_arcgis('weber-lir-parcels', 'Weber',
        'https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Weber_LIR/FeatureServer/0')

# ─── MAIN ────────────────────────────────────────────────────────────────────

SCRAPERS = [
    scrape_obituaries_herald,
    scrape_probate_court,
    scrape_utah_county_tax_delinquency,
    scrape_utah_county_nts,
    scrape_slc_county_nts,
    scrape_slc_tax_sale,
    scrape_wasatch_tax_sale,
    scrape_wasatch_county_nts,
    scrape_slc_assessor,
    scrape_slc_real_estate,
    scrape_slc_public_surplus,
    scrape_wasatch_public_surplus,
    scrape_south_slc_permits,
    scrape_south_slc_permits_pdf,
    scrape_utah_county_codev,
    scrape_utah_county_directory,
    scrape_utah_county_property_info,
    scrape_utah_county_real_property,
    scrape_uvhba_directory,
    scrape_ksl_fsbo,
    scrape_uco_lir_parcels,
    scrape_davis_lir_parcels,
    scrape_slco_lir_parcels,
    scrape_weber_lir_parcels,
]

if __name__ == '__main__':
    log.info(f'=== Premier Prospect v5 — {len(SCRAPERS)} sources ===')
    total = 0
    for fn in SCRAPERS:
        try:
            n = fn() or 0
            total += n
        except Exception as e:
            log.error(f'{fn.__name__} crashed: {e}')
    log.info(f'=== Done — {total} signals from {len(SCRAPERS)} sources ===')
