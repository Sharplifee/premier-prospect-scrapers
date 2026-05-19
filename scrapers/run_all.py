"""
Premier Prospect — GitHub Actions Scraper Runner v3
All 23 plain-HTTP sources wired and active.
Posts signals directly to Supabase ingest endpoint.
"""
import os, hashlib, logging, requests, zipfile, io, json
from bs4 import BeautifulSoup
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('pp.scrapers')

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
INGEST_URL   = f"{SUPABASE_URL}/functions/v1/pp-ingest"

INGEST_HEADERS = {
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'apikey': SUPABASE_KEY,
}

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
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

def fetch(url, timeout=20):
    try:
        r = SESSION.get(url, timeout=timeout)
        r.raise_for_status()
        return BeautifulSoup(r.text, 'html.parser')
    except Exception as e:
        log.error(f'Fetch failed {url}: {e}')
        return None

def fetch_json(url, params=None, timeout=20):
    try:
        r = SESSION.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f'JSON fetch failed {url}: {e}')
        return None

# ─── 1. OBITUARIES — HERALD ──────────────────────────────────────────────────
def scrape_obituaries_herald():
    slug = 'obituaries-herald'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.heraldextra.com/obituaries/')
    if not soup: return 0
    count = 0
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        name = a.get_text(strip=True)
        if '/obituaries/' in href and any(m in href for m in ['/jan/','/feb/','/mar/','/apr/','/may/','/jun/','/jul/','/aug/','/sep/','/oct/','/nov/','/dec/']) and 4 < len(name) < 80:
            if post_signal(slug, name, None, href, 55, 'Utah', 'obituary'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 2. PROBATE COURT — UTAH PMN ─────────────────────────────────────────────
def scrape_probate_court():
    slug = 'probate-court'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.utah.gov/pmn/search.html')
    if not soup: return 0
    count = 0
    keywords = ['probate','estate','decedent','personal representative','letters testamentary']
    for a in soup.find_all('a', href=True):
        text = a.get_text(strip=True).lower()
        href = a.get('href','')
        if any(k in text for k in keywords) and len(a.get_text(strip=True)) > 5:
            full = f"https://www.utah.gov{href}" if href.startswith('/') else href
            if post_signal(slug, a.get_text(strip=True), None, full, 70, 'Utah', 'probate_notice'):
                count += 1
    # Also scan rows
    for row in soup.find_all(['tr','li']):
        text = row.get_text(separator=' ', strip=True)
        if any(k in text.lower() for k in keywords) and 10 < len(text) < 400:
            if post_signal(slug, None, text[:200], 'https://www.utah.gov/pmn/search.html', 70, 'Utah', 'probate_notice'):
                count += 1
            if count >= 30: break
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 3. SLC ASSESSOR ─────────────────────────────────────────────────────────
def scrape_slc_assessor():
    slug = 'slc-assessor'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.saltlakecounty.gov/assessor/')
    if not soup: return 0
    count = 0
    for a in soup.find_all('a', href=True):
        name = a.get_text(strip=True)
        href = a.get('href','')
        if len(name) > 5 and any(x in (name+href).lower() for x in ['parcel','property','value','appeal','lookup','search']):
            full = f"https://www.saltlakecounty.gov{href}" if href.startswith('/') else href
            if post_signal(slug, name, None, full, 40, 'Salt Lake', 'assessor_record'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 4. SLC COUNTY NTS ───────────────────────────────────────────────────────
def scrape_slc_county_nts():
    slug = 'slc-county-nts'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.saltlakecounty.gov/public-notice/')
    if not soup: return 0
    count = 0
    keywords = ['trustee','foreclosure','default','notice of sale','trustee sale']
    for row in soup.find_all(['tr','li','p','div']):
        text = row.get_text(separator=' ', strip=True)
        if any(k in text.lower() for k in keywords) and 15 < len(text) < 500:
            if post_signal(slug, None, text[:300], 'https://www.saltlakecounty.gov/public-notice/', 80, 'Salt Lake', 'nts_notice'):
                count += 1
            if count >= 40: break
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 5. SLC COUNTY TAX SALE ──────────────────────────────────────────────────
def scrape_slc_tax_sale():
    slug = 'slc-county-tax-sale'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.saltlakecounty.gov/treasurer/property-taxes/')
    if not soup: return 0
    count = 0
    for a in soup.find_all('a', href=True):
        name = a.get_text(strip=True)
        href = a.get('href','')
        if len(name) > 5 and any(x in (name+href).lower() for x in ['delinquent','tax sale','lien','relief','notice']):
            full = f"https://www.saltlakecounty.gov{href}" if href.startswith('/') else href
            if post_signal(slug, name, None, full, 70, 'Salt Lake', 'tax_sale'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 6. SLC PUBLIC SURPLUS ───────────────────────────────────────────────────
def scrape_slc_public_surplus():
    slug = 'slc-public-surplus'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.saltlakecounty.gov/real-estate/')
    if not soup: return 0
    count = 0
    for a in soup.find_all('a', href=True):
        name = a.get_text(strip=True)
        href = a.get('href','')
        if len(name) > 5 and any(x in (name+href).lower() for x in ['sale','surplus','property','real estate','auction']):
            full = f"https://www.saltlakecounty.gov{href}" if href.startswith('/') else href
            if post_signal(slug, name, None, full, 45, 'Salt Lake', 'surplus'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 7. SLC REAL ESTATE (surplus property listings) ──────────────────────────
def scrape_slc_real_estate():
    slug = 'slc-real-estate'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.saltlakecounty.gov/real-estate/public-sale/')
    if not soup: return 0
    count = 0
    for row in soup.find_all(['tr','li','div']):
        text = row.get_text(separator=' ', strip=True)
        if 15 < len(text) < 400 and any(c.isdigit() for c in text):
            if post_signal(slug, None, text[:300], 'https://www.saltlakecounty.gov/real-estate/public-sale/', 50, 'Salt Lake', 'surplus_property_listing'):
                count += 1
            if count >= 30: break
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 8. SOUTH SLC PERMITS ────────────────────────────────────────────────────
def scrape_south_slc_permits():
    slug = 'south-slc-permits'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.slc.gov/cda/building-services/', timeout=25)
    if not soup: return 0
    count = 0
    for a in soup.find_all('a', href=True):
        name = a.get_text(strip=True)
        href = a.get('href','')
        if len(name) > 5 and 'permit' in (name+href).lower():
            full = f"https://www.slc.gov{href}" if href.startswith('/') else href
            if post_signal(slug, name, None, full, 35, 'Salt Lake', 'permit'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 9. SOUTH SLC PERMITS PDF ────────────────────────────────────────────────
def scrape_south_slc_permits_pdf():
    slug = 'south-slc-permits-pdf'
    log.info(f'[{slug}] starting')
    # Try multiple South SLC permit URLs
    for url in ['https://www.southsaltlake.org/172/Building-Permits',
                'https://www.southsaltlakecity.com/government/departments/community-development/building-permits']:
        soup = fetch(url, timeout=15)
        if soup:
            count = 0
            for a in soup.find_all('a', href=True):
                name = a.get_text(strip=True)
                href = a.get('href','')
                if len(name) > 5 and any(x in (name+href).lower() for x in ['permit','pdf','download']):
                    if post_signal(slug, name, None, href, 35, 'Salt Lake', 'permit_pdf'):
                        count += 1
            log.info(f'[{slug}] {count} signals posted')
            return count
    log.info(f'[{slug}] 0 signals posted (all URLs unavailable)')
    return 0

# ─── 10. UTAH COUNTY CODEV ───────────────────────────────────────────────────
def scrape_utah_county_codev():
    slug = 'utah-county-codev'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.utahcounty.gov/Dept/ComDev/CodeEnforcement.asp')
    if not soup: return 0
    count = 0
    for el in soup.find_all(['p','li','td','div']):
        text = el.get_text(separator=' ', strip=True)
        if 20 < len(text) < 400 and any(w in text.lower() for w in ['violation','complaint','nuisance','code','enforcement','property']):
            if post_signal(slug, None, text[:300], 'https://www.utahcounty.gov/Dept/ComDev/CodeEnforcement.asp', 50, 'Utah', 'code_enforcement'):
                count += 1
            if count >= 20: break
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 11. UTAH COUNTY DIRECTORY (Recorder) ────────────────────────────────────
def scrape_utah_county_directory():
    slug = 'utah-county-directory'
    log.info(f'[{slug}] starting')
    soup = fetch('https://recorder.utahcounty.gov/find-records')
    if not soup: return 0
    count = 0
    for a in soup.find_all('a', href=True):
        name = a.get_text(strip=True)
        href = a.get('href','')
        if len(name) > 5 and any(x in (name+href).lower() for x in ['deed','transfer','lien','mortgage','notice','record']):
            full = f"https://recorder.utahcounty.gov{href}" if href.startswith('/') else href
            if post_signal(slug, name, None, full, 60, 'Utah', 'deed_transfer'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 12. UTAH COUNTY NTS ─────────────────────────────────────────────────────
def scrape_utah_county_nts():
    slug = 'utah-county-nts'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.utah.gov/pmn/search.html')
    if not soup: return 0
    count = 0
    keywords = ['trustee','foreclosure','default','notice of trustee','notice of sale']
    for row in soup.find_all(['tr','li','p']):
        text = row.get_text(separator=' ', strip=True)
        if any(k in text.lower() for k in keywords) and 'utah' in text.lower() and 15 < len(text) < 500:
            if post_signal(slug, None, text[:300], 'https://www.utah.gov/pmn/search.html', 80, 'Utah', 'nts_notice'):
                count += 1
            if count >= 30: break
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 13. UTAH COUNTY PROPERTY INFO ───────────────────────────────────────────
def scrape_utah_county_property_info():
    slug = 'utah-county-property-info'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.utahcounty.gov/LandRecords/Index.asp')
    if not soup: return 0
    count = 0
    for a in soup.find_all('a', href=True):
        name = a.get_text(strip=True)
        href = a.get('href','')
        if len(name) > 5 and any(x in (name+href).lower() for x in ['parcel','property','deed','record','transfer','lien']):
            full = f"https://www.utahcounty.gov{href}" if href.startswith('/') else href
            if post_signal(slug, name, None, full, 55, 'Utah', 'property_record'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 14. UTAH COUNTY REAL PROPERTY ───────────────────────────────────────────
def scrape_utah_county_real_property():
    slug = 'utah-county-real-property'
    log.info(f'[{slug}] starting')
    soup = fetch('https://assessor.utahcounty.gov/real-property')
    if not soup: return 0
    count = 0
    for a in soup.find_all('a', href=True):
        name = a.get_text(strip=True)
        href = a.get('href','')
        if len(name) > 5 and any(x in (name+href).lower() for x in ['parcel','value','search','appeal','lookup','property']):
            full = f"https://assessor.utahcounty.gov{href}" if href.startswith('/') else href
            if post_signal(slug, name, None, full, 45, 'Utah', 'real_property_record'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 15. UTAH COUNTY TAX DELINQUENCY ─────────────────────────────────────────
def scrape_utah_county_tax_delinquency():
    slug = 'utah-county-tax-delinquency'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.utahcounty.gov/Dept/Treas/delinquent')
    if not soup: return 0
    count = 0
    for row in soup.find_all(['tr','li','div','p']):
        text = row.get_text(separator=' ', strip=True)
        if 20 < len(text) < 400 and any(c.isdigit() for c in text) and any(w in text.lower() for w in ['delinquent','tax','parcel','owner','property']):
            if post_signal(slug, None, text[:300], 'https://www.utahcounty.gov/Dept/Treas/delinquent', 80, 'Utah', 'tax_delinquency'):
                count += 1
            if count >= 40: break
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 16. UVHBA DIRECTORY ─────────────────────────────────────────────────────
def scrape_uvhba_directory():
    slug = 'uvhba-directory'
    log.info(f'[{slug}] starting')
    soup = fetch('https://business.uvhba.com/list/ql/contractors-subcontractors-7')
    if not soup: return 0
    count = 0
    for a in soup.find_all('a', href=True):
        name = a.get_text(strip=True)
        href = a.get('href','')
        if len(name) > 5 and any(x in (name+href).lower() for x in ['contractor','builder','member','/member/','/list/']):
            full = f"https://business.uvhba.com{href}" if href.startswith('/') else href
            if post_signal(slug, name, None, full, 30, 'Utah', 'contractor_directory'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 17. WASATCH COUNTY NTS ───────────────────────────────────────────────────
def scrape_wasatch_county_nts():
    slug = 'wasatch-county-nts'
    log.info(f'[{slug}] starting')
    soup = fetch('https://wasatch.utah.gov/departments/treasurer', timeout=20)
    if not soup: return 0
    count = 0
    keywords = ['trustee','foreclosure','default','notice','tax sale','delinquent']
    for row in soup.find_all(['tr','li','p','div']):
        text = row.get_text(separator=' ', strip=True)
        if any(k in text.lower() for k in keywords) and 15 < len(text) < 500:
            if post_signal(slug, None, text[:300], 'https://wasatch.utah.gov/departments/treasurer', 75, 'Wasatch', 'nts_notice'):
                count += 1
            if count >= 20: break
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 18. WASATCH COUNTY TAX SALE ──────────────────────────────────────────────
def scrape_wasatch_tax_sale():
    slug = 'wasatch-county-tax-sale'
    log.info(f'[{slug}] starting')
    soup = fetch('https://wasatch.utah.gov/departments/treasurer', timeout=20)
    if not soup: return 0
    count = 0
    for a in soup.find_all('a', href=True):
        name = a.get_text(strip=True)
        href = a.get('href','')
        if len(name) > 5:
            full = f"https://wasatch.utah.gov{href}" if href.startswith('/') else href
            if post_signal(slug, name, None, full, 65, 'Wasatch', 'tax_sale'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 19. WASATCH PUBLIC SURPLUS ───────────────────────────────────────────────
def scrape_wasatch_public_surplus():
    slug = 'wasatch-public-surplus'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.publicsurplus.com/sms/wasatchco,ut/list/current')
    if not soup: return 0
    count = 0
    for row in soup.find_all(['tr','li']):
        text = row.get_text(separator=' ', strip=True)
        if 10 < len(text) < 400 and any(c.isdigit() for c in text):
            if post_signal(slug, None, text[:300], 'https://www.publicsurplus.com/sms/wasatchco,ut/list/current', 40, 'Wasatch', 'public_surplus_auction'):
                count += 1
            if count >= 30: break
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 20. KSL FSBO ────────────────────────────────────────────────────────────
def scrape_ksl_fsbo():
    slug = 'ksl-fsbo'
    log.info(f'[{slug}] starting')
    count = 0
    try:
        r = SESSION.get('https://api.ksl.com/classified/v1/search/',
            params={'category':'real-estate-for-sale','subCategory':'real-estate-homes-for-sale','owner':'1','perPage':'50'},
            timeout=20)
        if r.status_code == 200:
            data = r.json()
            for item in data.get('items', data.get('listings', data.get('data', [])))[:50]:
                title = item.get('title','') or item.get('name','')
                url = item.get('url','') or f"https://classifieds.ksl.com/listing/{item.get('id','')}"
                address = item.get('location','') or item.get('address','')
                if title or address:
                    if post_signal(slug, title, address, url, 65, 'Utah', 'fsbo'):
                        count += 1
        else:
            log.error(f'[{slug}] API {r.status_code}')
    except Exception as e:
        log.error(f'[{slug}] {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── 21-23. LIR PARCEL DATA (ArcGIS FeatureServer) ────────────────────────────
def scrape_lir_arcgis(slug, county, service_url, max_records=200):
    log.info(f'[{slug}] starting')
    count = 0
    try:
        params = {
            'where': '1=1',
            'outFields': 'OWNER_NAME,SITUS_ADDR,PARCEL_ID,LAND_VALUE,TOTAL_VALUE,PROP_CLASS',
            'resultRecordCount': max_records,
            'orderByFields': 'OBJECTID DESC',
            'f': 'json',
        }
        r = SESSION.get(f"{service_url}/query", params=params, timeout=30)
        data = r.json()
        for feat in data.get('features', []):
            attrs = feat.get('attributes', {})
            owner = attrs.get('OWNER_NAME','')
            address = attrs.get('SITUS_ADDR','')
            parcel = attrs.get('PARCEL_ID','')
            value = attrs.get('TOTAL_VALUE', 0) or 0
            score = 60 if value < 300000 else 40
            url = f"{service_url}/query?where=PARCEL_ID='{parcel}'"
            if owner or address:
                if post_signal(slug, owner, address, url, score, county, 'lir_parcel'):
                    count += 1
    except Exception as e:
        log.error(f'[{slug}] {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_uco_lir_parcels():
    return scrape_lir_arcgis(
        'uco-lir-parcels', 'Utah',
        'https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Utah_LIR/FeatureServer/0'
    )

def scrape_davis_lir_parcels():
    return scrape_lir_arcgis(
        'davis-lir-parcels', 'Davis',
        'https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Davis_LIR/FeatureServer/0'
    )

def scrape_slco_lir_parcels():
    return scrape_lir_arcgis(
        'slco-lir-parcels', 'Salt Lake',
        'https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_SaltLake_LIR/FeatureServer/0'
    )

def scrape_weber_lir_parcels():
    return scrape_lir_arcgis(
        'weber-lir-parcels', 'Weber',
        'https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Weber_LIR/FeatureServer/0'
    )

# ─── MAIN ─────────────────────────────────────────────────────────────────────

SCRAPERS = [
    # Plain HTTP — high priority signals
    scrape_obituaries_herald,
    scrape_probate_court,
    scrape_utah_county_tax_delinquency,
    scrape_utah_county_nts,
    scrape_slc_county_nts,
    scrape_slc_tax_sale,
    scrape_wasatch_tax_sale,
    scrape_wasatch_county_nts,
    # Property records
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
    # LIR parcel data
    scrape_uco_lir_parcels,
    scrape_davis_lir_parcels,
    scrape_slco_lir_parcels,
    scrape_weber_lir_parcels,
]

if __name__ == '__main__':
    log.info(f'=== Premier Prospect scraper run v3 — {len(SCRAPERS)} sources ===')
    total = 0
    results = []
    for fn in SCRAPERS:
        try:
            n = fn() or 0
            total += n
            results.append((fn.__name__.replace('scrape_',''), n))
        except Exception as e:
            log.error(f'Scraper {fn.__name__} crashed: {e}')
            results.append((fn.__name__.replace('scrape_',''), 'ERROR'))
    log.info(f'=== Run complete — {total} total signals from {len(SCRAPERS)} sources ===')
    for name, n in results:
        log.info(f'  {name}: {n}')
