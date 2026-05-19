"""
Premier Prospect — GitHub Actions Scraper Runner
Runs all live sources and POSTs signals to Supabase ingest endpoint.
No database needed — everything goes through pp-ingest Edge Function.
"""
import os, hashlib, logging, requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('pp.scrapers')

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
INGEST_URL   = f"{SUPABASE_URL}/functions/v1/pp-ingest"

HEADERS = {
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'apikey': SUPABASE_KEY,
}

SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'PremierProspect/2.0 (+https://github.com/williamscorealty/premier-prospect)'})

def dh(*parts):
    return hashlib.sha256('|'.join(str(p or '') for p in parts).encode()).hexdigest()

def post_signal(slug, owner, address, url, score, county, signal_type, extra=None):
    payload = {
        'event_type': 'scraper_signal',
        'source_slug': slug,
        'raw_owner_name': owner,
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
        r = requests.post(INGEST_URL, json=payload, headers=HEADERS, timeout=15)
        return r.status_code in (200, 201, 409)  # 409 = duplicate, that's fine
    except Exception as e:
        log.error(f'POST failed for {slug}: {e}')
        return False

def fetch(url, timeout=30):
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
    if not soup:
        return 0
    count = 0
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        name = a.get_text(strip=True)
        if '/obituaries/' in href and len(name) > 4 and len(name) < 80:
            if post_signal(slug, name, None, href, 55, 'Utah', 'obituary'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_slc_tax_sale():
    """Salt Lake County tax sale — distressed property signals"""
    slug = 'slc-county-tax-sale'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.saltlakecounty.gov/assessor/tax-sale/')
    if not soup:
        return 0
    count = 0
    for row in soup.find_all(['tr', 'li']):
        text = row.get_text(separator=' ', strip=True)
        if len(text) > 10 and any(c.isdigit() for c in text):
            if post_signal(slug, None, text[:200], None, 75, 'Salt Lake', 'tax_sale'):
                count += 1
            if count >= 50:
                break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_wasatch_tax_sale():
    """Wasatch County treasurer — tax delinquency signals"""
    slug = 'wasatch-county-tax-sale'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.wasatchcounty.gov/departments/treasurer')
    if not soup:
        return 0
    count = 0
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        name = a.get_text(strip=True)
        if len(name) > 4 and ('tax' in href.lower() or 'sale' in href.lower() or 'delinq' in href.lower()):
            if post_signal(slug, name, None, href, 65, 'Wasatch', 'tax_sale'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_slc_public_surplus():
    """SLC surplus property — motivated seller signals"""
    slug = 'slc-public-surplus'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.saltlakecounty.gov/real-estate/public-sale/')
    if not soup:
        return 0
    count = 0
    for row in soup.find_all(['tr', 'li', 'div']):
        text = row.get_text(separator=' ', strip=True)
        if 15 < len(text) < 300 and any(c.isdigit() for c in text):
            if post_signal(slug, None, text[:200], None, 45, 'Salt Lake', 'surplus'):
                count += 1
            if count >= 30:
                break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_utah_county_tax_delinquency():
    """Utah County tax delinquency list — highest priority distressed signals"""
    slug = 'utah-county-tax-delinquency'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.utahcounty.gov/Dept/Treas/TaxSale.asp')
    if not soup:
        # Try alternate URL
        soup = fetch('https://www.utahcounty.gov/treasurer/tax-sale')
    if not soup:
        return 0
    count = 0
    for row in soup.find_all(['tr', 'li']):
        cells = row.find_all(['td', 'li'])
        text = row.get_text(separator=' ', strip=True)
        if len(text) > 10 and any(c.isdigit() for c in text):
            if post_signal(slug, None, text[:200], None, 80, 'Utah', 'tax_delinquency'):
                count += 1
            if count >= 50:
                break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_south_slc_permits():
    """South SLC building permits — renovation/flip signals"""
    slug = 'south-slc-permits'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.southsaltlakecity.com/172/Building-Permits')
    if not soup:
        return 0
    count = 0
    for a in soup.find_all('a', href=True):
        name = a.get_text(strip=True)
        href = a.get('href', '')
        if len(name) > 4 and ('permit' in name.lower() or 'permit' in href.lower()):
            if post_signal(slug, name, None, href, 35, 'Salt Lake', 'permit'):
                count += 1
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_utah_county_codev():
    """Utah County Code Enforcement — distressed property signals"""
    slug = 'utah-county-codev'
    log.info(f'[{slug}] starting')
    soup = fetch('https://www.utahcounty.gov/Dept/ComDev/CodeEnforcement.asp')
    if not soup:
        return 0
    count = 0
    for row in soup.find_all(['tr', 'li', 'p']):
        text = row.get_text(separator=' ', strip=True)
        if 10 < len(text) < 300:
            if post_signal(slug, None, text[:200], None, 50, 'Utah', 'code_enforcement'):
                count += 1
            if count >= 30:
                break
    log.info(f'[{slug}] {count} signals posted')
    return count

def scrape_ksl_fsbo():
    """KSL FSBO listings — unrepresented seller signals"""
    slug = 'ksl-fsbo'
    log.info(f'[{slug}] starting')
    # KSL is JS-rendered; use their search API directly
    try:
        r = SESSION.get(
            'https://classifieds.ksl.com/search/',
            params={'category': 'real-estate-for-sale', 'subCategory': 'real-estate-homes-for-sale', 'owner': '1'},
            timeout=30
        )
        soup = BeautifulSoup(r.text, 'html.parser')
        count = 0
        for a in soup.find_all('a', href=True):
            name = a.get_text(strip=True)
            href = a.get('href', '')
            if '/listing/' in href and len(name) > 3:
                full_url = f"https://classifieds.ksl.com{href}" if href.startswith('/') else href
                if post_signal(slug, name, None, full_url, 65, 'Utah', 'fsbo'):
                    count += 1
        log.info(f'[{slug}] {count} signals posted')
        return count
    except Exception as e:
        log.error(f'[{slug}] {e}')
        return 0

# ─── MAIN ─────────────────────────────────────────────────────────────────────

SCRAPERS = [
    scrape_obituaries_herald,
    scrape_utah_county_tax_delinquency,
    scrape_slc_tax_sale,
    scrape_wasatch_tax_sale,
    scrape_slc_public_surplus,
    scrape_south_slc_permits,
    scrape_utah_county_codev,
    scrape_ksl_fsbo,
]

if __name__ == '__main__':
    log.info('=== Premier Prospect scraper run start ===')
    total = 0
    for fn in SCRAPERS:
        try:
            total += fn() or 0
        except Exception as e:
            log.error(f'Scraper {fn.__name__} crashed: {e}')
    log.info(f'=== Run complete — {total} total signals posted ===')
