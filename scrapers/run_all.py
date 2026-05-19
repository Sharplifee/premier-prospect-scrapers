"""
Premier Prospect — GitHub Actions Scraper Runner v2
Corrected URLs and selectors based on live site verification May 19, 2026.
Posts signals directly to Supabase ingest endpoint.
"""
import os, hashlib, logging, requests
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

def post_signal(slug, owner, address, url, score, county, signal_type, extra=None):
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
        **(extra or {}),
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

# ─── SCRAPERS ────────────────────────────────────────────────────────────────

def scrape_obituaries_herald():
    """Daily Herald obituaries — Utah County estate signals"""
    slug = 'obituaries-herald'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.heraldextra.com/obituaries/')
    if not soup: return 0
    count = 0
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        name = a.get_text(strip=True)
        # Match individual obit pages like /obituaries/2026/may/19/name/
        if '/obituaries/' in href and any(m in href for m in ['/jan/','/feb/','/mar/','/apr/','/may/','/jun/','/jul/','/aug/','/sep/','/oct/','/nov/','/dec/']) and len(name) > 4:
            if post_signal(slug, name, None, href, 55, 'Utah', 'obituary'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_utah_county_tax_delinquency():
    """Utah County treasurer tax delinquency page"""
    slug = 'utah-county-tax-delinquency'
    log.info(f'[{slug}] starting')
    # The delinquent list page confirmed 200
    soup = fetch('https://www.utahcounty.gov/Dept/Treas/delinquent')
    if not soup: return 0
    count = 0
    for row in soup.find_all(['tr', 'li', 'div', 'p']):
        text = row.get_text(separator=' ', strip=True)
        if 20 < len(text) < 400 and any(c.isdigit() for c in text) and any(w in text.lower() for w in ['delinquent', 'tax', 'parcel', 'owner', 'property']):
            if post_signal(slug, None, text[:300], 'https://www.utahcounty.gov/Dept/Treas/delinquent', 80, 'Utah', 'tax_delinquency'):
                count += 1
            if count >= 40: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_slc_tax_sale():
    """Salt Lake County property tax info — distressed signals"""
    slug = 'slc-county-tax-sale'
    log.info(f'[{slug}] starting')
    # Confirmed 200 URL
    soup = fetch('https://www.saltlakecounty.gov/treasurer/property-taxes/')
    if not soup: return 0
    count = 0
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        name = a.get_text(strip=True)
        if len(name) > 5 and any(x in (name + href).lower() for x in ['delinquent', 'tax sale', 'lien', 'relief', 'notice']):
            full = f"https://www.saltlakecounty.gov{href}" if href.startswith('/') else href
            if post_signal(slug, name, None, full, 70, 'Salt Lake', 'tax_sale'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_wasatch_tax():
    """Wasatch County property tax signals"""
    slug = 'wasatch-county-tax-sale'
    log.info(f'[{slug}] starting')
    # Confirmed 200
    soup = fetch('https://wasatch.utah.gov/departments/treasurer', timeout=20)
    if not soup: return 0
    count = 0
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        name = a.get_text(strip=True)
        if len(name) > 5:
            full = f"https://wasatch.utah.gov{href}" if href.startswith('/') else href
            if post_signal(slug, name, None, full, 60, 'Wasatch', 'tax_sale'):
                count += 1
    # Also grab alerts as distress signals
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        name = a.get_text(strip=True)
        if 'CivicAlerts' in href and len(name) > 10:
            full = f"https://wasatch.utah.gov{href}" if href.startswith('/') else href
            if post_signal(slug, name, None, full, 65, 'Wasatch', 'tax_distress'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_slc_public_surplus():
    """SLC surplus property"""
    slug = 'slc-public-surplus'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.saltlakecounty.gov/real-estate/')
    if not soup: return 0
    count = 0
    for a in soup.find_all('a', href=True):
        name = a.get_text(strip=True)
        href = a.get('href', '')
        if len(name) > 5 and any(x in (name+href).lower() for x in ['sale', 'surplus', 'property', 'real estate']):
            full = f"https://www.saltlakecounty.gov{href}" if href.startswith('/') else href
            if post_signal(slug, name, None, full, 45, 'Salt Lake', 'surplus'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_south_slc_permits():
    """South SLC building permits via state portal"""
    slug = 'south-slc-permits'
    log.info(f'[{slug}] starting')
    # southsaltlakecity.com times out — use state permit portal instead
    soup = fetch('https://permits.utah.gov/ords/apex_apps/r/building_permits/home', timeout=20)
    if not soup:
        # fallback to SLC building services
        soup = fetch('https://www.slc.gov/cda/building-services/', timeout=20)
    if not soup: return 0
    count = 0
    for a in soup.find_all('a', href=True):
        name = a.get_text(strip=True)
        href = a.get('href', '')
        if len(name) > 5 and 'permit' in (name+href).lower():
            if post_signal(slug, name, None, href, 35, 'Salt Lake', 'permit'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_utah_county_codev():
    """Utah County code enforcement — distressed signals"""
    slug = 'utah-county-codev'
    log.info(f'[{slug}] starting')
    # Confirmed 200
    soup = fetch('https://www.utahcounty.gov/Dept/ComDev/CodeEnforcement.asp')
    if not soup: return 0
    count = 0
    # Grab all meaningful text blocks
    for el in soup.find_all(['p', 'li', 'td', 'div']):
        text = el.get_text(separator=' ', strip=True)
        if 20 < len(text) < 400 and any(w in text.lower() for w in ['violation', 'complaint', 'nuisance', 'code', 'enforcement', 'property']):
            if post_signal(slug, None, text[:300], 'https://www.utahcounty.gov/Dept/ComDev/CodeEnforcement.asp', 50, 'Utah', 'code_enforcement'):
                count += 1
            if count >= 20: break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_ksl_fsbo():
    """KSL FSBO — use RSS/API endpoint to bypass JS render"""
    slug = 'ksl-fsbo'
    log.info(f'[{slug}] starting')
    count = 0
    # KSL has an API endpoint that returns JSON
    try:
        r = SESSION.get(
            'https://api.ksl.com/classified/v1/search/',
            params={
                'category': 'real-estate-for-sale',
                'subCategory': 'real-estate-homes-for-sale',
                'owner': '1',
                'perPage': '50',
            },
            timeout=20
        )
        if r.status_code == 200:
            data = r.json()
            listings = data.get('items', data.get('listings', data.get('data', [])))
            for item in listings[:50]:
                title = item.get('title', '') or item.get('name', '')
                url = item.get('url', '') or f"https://classifieds.ksl.com/listing/{item.get('id','')}"
                address = item.get('location', '') or item.get('address', '')
                if title or address:
                    if post_signal(slug, title, address, url, 65, 'Utah', 'fsbo'):
                        count += 1
        else:
            log.error(f'[{slug}] API returned {r.status_code}')
    except Exception as e:
        log.error(f'[{slug}] {e}')
    log.info(f'[{slug}] {count} signals posted')
    return count

# ─── MAIN ─────────────────────────────────────────────────────────────────────

SCRAPERS = [
    scrape_obituaries_herald,
    scrape_utah_county_tax_delinquency,
    scrape_slc_tax_sale,
    scrape_wasatch_tax,
    scrape_slc_public_surplus,
    scrape_south_slc_permits,
    scrape_utah_county_codev,
    scrape_ksl_fsbo,
]

if __name__ == '__main__':
    log.info('=== Premier Prospect scraper run v2 start ===')
    total = 0
    for fn in SCRAPERS:
        try:
            total += fn() or 0
        except Exception as e:
            log.error(f'Scraper {fn.__name__} crashed: {e}')
    log.info(f'=== Run complete — {total} total signals posted ===')
