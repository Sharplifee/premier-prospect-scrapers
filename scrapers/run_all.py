"""
Premier Prospect — GitHub Actions Scraper Runner v6
Apify fetch + text-based parsing (no HTML/soup needed).
24 active sources. Fully tested parsers.
"""
import os, hashlib, logging, requests, re, json, time
from bs4 import BeautifulSoup

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
UA = 'PremierProspect/6.0'  # alias used by v11 scrapers

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

def post_signals_batch(records, source_slug_override=None):
    """Batch insert signals. Handles deduplication and schema validation."""
    if not records:
        return 0
    import hashlib as _hl

    # Allowed schema columns — must match pp_scraper_signals exactly
    ALLOWED = {'source_slug','raw_address','raw_owner_name','raw_phone','raw_url',
               'raw_payload','signal_type','score','county','city','captured_at','dedupe_hash'}

    seen = set()
    unique = []
    for rec in records:
        # Override source_slug if provided
        if source_slug_override:
            rec['source_slug'] = source_slug_override
        # Compute dedupe hash
        h = _hl.md5(
            f"{rec.get('source_slug','')}|{rec.get('raw_url','')}|{rec.get('raw_owner_name','')}|{rec.get('raw_address','')}".encode()
        ).hexdigest()
        rec['dedupe_hash'] = h
        if h not in seen:
            seen.add(h)
            # Strip any keys not in schema to prevent PGRST204
            clean = {k: v for k, v in rec.items() if k in ALLOWED}
            unique.append(clean)

    count = 0
    CHUNK = 200
    for i in range(0, len(unique), CHUNK):
        chunk = unique[i:i+CHUNK]
        for attempt in range(3):
            try:
                r = SESSION.post(
                    TABLE_URL, json=chunk,
                    headers={**TABLE_HEADERS, 'Prefer': 'return=minimal,resolution=ignore-duplicates'},
                    timeout=45
                )
                if r.status_code in (200, 201, 204, 409):
                    count += len(chunk)
                    break
                elif r.status_code == 400 and 'PGRST204' in r.text:
                    log.error(f'Schema mismatch — extra columns stripped, retrying')
                    # Already stripped above, log and break
                    break
                else:
                    log.error(f'Batch POST failed: {r.status_code} {r.text[:80]}')
                    break
            except Exception as e:
                log.error(f'Batch POST error: {e}')
                if attempt < 2:
                    time.sleep(5)
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
    """Daily Herald obituaries — RSS feed. Owner death = estate property signal."""
    slug = 'obituaries-herald'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        lines = apify_text('https://www.heraldextra.com/obituaries/')
        kw = ['born', 'passed', 'survived', 'memorial', 'funeral', 'services', 'resided', 'home']
        current = []
        for line in lines:
            line = line.strip()
            if not line: continue
            if any(k in line.lower() for k in kw) and len(line) > 20:
                current.append(line)
            if len(current) >= 3:
                full_text = ' '.join(current)
                addr_match = re.search(r'\d+\s+\w+\s+(?:St|Ave|Dr|Rd|Blvd|Way|Ln|Ct|Circle)', full_text, re.IGNORECASE)
                name_match = re.search(r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})', current[0])
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': name_match.group(1) if name_match else current[0][:60],
                    'raw_address': addr_match.group() if addr_match else 'Utah Valley',
                    'raw_payload': json.dumps({'text': full_text[:300], 'source': 'herald_obituaries'}),
                    'signal_type': 'obituary',
                    'score': 85,
                    'county': 'Utah',
                    'city': None,
                })
                current = []
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



def scrape_uvhba_directory():
    """UVHBA — Utah Valley Home Builders Association member directory."""
    slug = 'uvhba-directory'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        from bs4 import BeautifulSoup
        for url in ['https://uvhba.com/members/', 'https://uvhba.com/member-directory/']:
            r = SESSION.get(url, timeout=20)
            if r.status_code != 200: continue
            soup = BeautifulSoup(r.text, 'html.parser')
            members = soup.select('.member, .member-card, .directory-item, article, .entry')
            if not members:
                members = soup.select('h2, h3, h4')
            for member in members[:100]:
                name = member.get_text(strip=True)[:80]
                if not name or len(name) < 4: continue
                link = member.select_one('a')
                url_ref = link['href'] if link and link.get('href') else url
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': name,
                    'raw_address': 'Utah Valley',
                    'raw_url': url_ref,
                    'raw_payload': json.dumps({'member_name': name, 'source': 'uvhba'}),
                    'signal_type': 'contractor_directory',
                    'score': 35,
                    'county': 'Utah',
                    'city': None,
                })
            if signals: break
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_probate_court():
    """Probate Court — Utah Courts Xchange public probate case search."""
    slug = 'probate-court'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        from bs4 import BeautifulSoup
        # Utah County = 40, Salt Lake County = 49
        for county_id, county_name in [('40', 'Utah'), ('49', 'Salt Lake')]:
            r = SESSION.get(
                f'https://www.utcourts.gov/xchange/?caseType=PR&countyId={county_id}&dateRange=30',
                timeout=20
            )
            soup = BeautifulSoup(r.text, 'html.parser')
            rows = soup.select('table tr')[1:]
            for row in rows:
                cells = row.select('td')
                if len(cells) < 3: continue
                case_num = cells[0].get_text(strip=True)
                case_name = cells[1].get_text(strip=True) if len(cells) > 1 else ''
                filed_date = cells[2].get_text(strip=True) if len(cells) > 2 else ''
                if not case_num: continue
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': case_name or None,
                    'raw_address': f'Probate Case #{case_num}',
                    'raw_payload': json.dumps({'case_number': case_num, 'case_name': case_name, 'filed_date': filed_date, 'county': county_name}),
                    'signal_type': 'probate',
                    'score': 85,
                    'county': county_name,
                    'city': None,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_utah_county_tax_delinquency():
    """Utah County tax delinquency — live search via state property tax portal."""
    slug = 'utah-county-tax-delinquency'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        lines = apify_text('https://propertytax.utah.gov/county/utah/delinquent')
        for line in lines:
            if any(k in line.lower() for k in ['delinquent', 'tax sale', 'unpaid', 'lien', 'parcel', 'overdue']):
                owner = re.search(r'^([A-Z][A-Z\s,&]+)(?=\s)', line)
                addr = re.search(r'\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln|Ct)', line, re.IGNORECASE)
                amount = re.search(r'\$[\d,]+\.?\d*', line)
                if len(line) > 5:
                    signals.append({
                        'source_slug': slug,
                        'raw_owner_name': owner.group(1)[:80] if owner else None,
                        'raw_address': addr.group()[:120] if addr else line[:80],
                        'raw_payload': json.dumps({'line': line[:200], 'amount': amount.group() if amount else ''}),
                        'signal_type': 'tax_delinquency',
                        'score': 65,
                        'county': 'Utah',
                        'city': None,
                    })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



def scrape_utah_county_nts():
    """Utah County NTS — via Land Records trustee/foreclosure document search."""
    slug = 'utah-county-nts'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        # Search land records for TRUSTEE documents filed in last 30 days
        for doc_type in ['RSUBTEE', 'NOTICE OF TRUSTEE', 'TRUSTEE SALE', 'FORECLOSURE']:
            r = SESSION.post(
                'https://www.utahcounty.gov/LandRecords/DocDescSearch.asp',
                data={'DocDesc': doc_type, 'DateRange': '30', 'County': 'Utah'},
                timeout=20
            )
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, 'html.parser')
            rows = soup.select('table tr')[1:]  # skip header
            for row in rows:
                cells = row.select('td')
                if len(cells) < 4: continue
                description = cells[0].get_text(strip=True)
                rec_date = cells[1].get_text(strip=True)
                entry = cells[3].get_text(strip=True) if len(cells) > 3 else ''
                grantor = cells[4].get_text(strip=True) if len(cells) > 4 else ''
                if not entry: continue
                # Manus scoring: NTS = score 99
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': grantor or None,
                    'raw_address': f'{doc_type} — Entry #{entry}',
                    'raw_payload': json.dumps({'doc_type': description, 'rec_date': rec_date, 'entry': entry, 'grantor': grantor}),
                    'signal_type': 'nts',
                    'score': 99,
                    'county': 'Utah',
                    'city': None,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_slc_county_nts():
    """SLC County NTS — Salt Lake Tribune legal notices for foreclosure/trustee filings."""
    slug = 'slc-county-nts'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        from bs4 import BeautifulSoup
        r = SESSION.get('https://www.sltrib.com/legal-notices/', timeout=20)
        soup = BeautifulSoup(r.text, 'html.parser')
        notices = soup.select('article, .notice, .legal-notice, .classifieds-item, p')
        nts_keywords = ['trustee', 'foreclosure', 'notice of sale', 'default', 'auction', 'delinquent']
        for notice in notices:
            text = notice.get_text(strip=True)
            if len(text) < 20: continue
            if not any(kw in text.lower() for kw in nts_keywords): continue
            # Extract address if present
            import re
            addr_match = re.search(r'\d+\s+[A-Z]\w+\s+(?:St|Ave|Dr|Rd|Blvd|Way|Ln|Ct|Circle|Cir)', text, re.IGNORECASE)
            address = addr_match.group() if addr_match else 'See legal notice'
            signals.append({
                'source_slug': slug,
                'raw_owner_name': None,
                'raw_address': address,
                'raw_payload': json.dumps({'notice_text': text[:300], 'source': 'sltrib_legal'}),
                'signal_type': 'nts',
                'score': 99,
                'county': 'Salt Lake',
                'city': 'Salt Lake City',
            })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


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
    """Wasatch County NTS — treasurer delinquent taxes and legal notices."""
    slug = 'wasatch-county-nts'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        for url in ['https://wasatch.utah.gov/departments/treasurer/delinquent-taxes',
                    'https://wasatch.utah.gov/departments/clerk/legal-notices']:
            lines = apify_text(url)
            for line in lines:
                if any(k in line.lower() for k in ['trustee', 'foreclosure', 'delinquent', 'notice', 'sale', 'lien', 'tax']):
                    if len(line) > 10:
                        owner = re.search(r'^([A-Z][A-Z\s,]+)(?=\s+\d)', line)
                        addr = re.search(r'\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way)', line, re.IGNORECASE)
                        signals.append({
                            'source_slug': slug,
                            'raw_owner_name': owner.group(1)[:80] if owner else None,
                            'raw_address': addr.group()[:120] if addr else line[:80],
                            'raw_payload': json.dumps({'line': line[:200], 'url': url}),
                            'signal_type': 'nts',
                            'score': 99,
                            'county': 'Wasatch',
                            'city': None,
                        })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



def scrape_slc_assessor():
    """SLC County Assessor — recent property sales and assessments."""
    slug = 'slc-assessor'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        lines = apify_text('https://slco.org/assessor/new-search/search.html')
        for line in lines:
            addr = re.search(r'\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln|Ct)', line, re.IGNORECASE)
            price = re.search(r'\$[\d,]+', line)
            if addr and price:
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': None,
                    'raw_address': addr.group()[:120],
                    'raw_payload': json.dumps({'line': line[:200], 'price': price.group()}),
                    'signal_type': 'comparable_sale',
                    'score': 45,
                    'county': 'Salt Lake',
                    'city': None,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



def scrape_slc_real_estate():
    """SLC County public real estate sales — surplus and foreclosure properties."""
    slug = 'slc-real-estate'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        for url in ['https://www.saltlakecounty.gov/real-estate/public-sale/',
                    'https://www.saltlakecounty.gov/real-estate/']:
            lines = apify_text(url)
            for line in lines:
                if any(k in line.lower() for k in ['property', 'parcel', 'sale', 'auction', 'bid', 'address', 'surplus']):
                    addr = re.search(r'\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln|Ct|N|S|E|W)', line, re.IGNORECASE)
                    if addr and len(line) > 10:
                        signals.append({
                            'source_slug': slug,
                            'raw_owner_name': None,
                            'raw_address': addr.group()[:120],
                            'raw_payload': json.dumps({'line': line[:200]}),
                            'signal_type': 'public_sale',
                            'score': 65,
                            'county': 'Salt Lake',
                            'city': 'Salt Lake City',
                        })
            if signals: break
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



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
    """South Salt Lake building permits."""
    slug = 'south-slc-permits'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        for url in ['https://www.southsaltlake.org/government/departments/community-development/building-permits',
                    'https://www.southsaltlake.org/government/departments/community-development']:
            lines = apify_text(url)
            for line in lines:
                if any(k in line.lower() for k in ['permit', 'issued', 'address', 'contractor', 'residential']):
                    addr = re.search(r'\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way)', line, re.IGNORECASE)
                    if len(line) > 10:
                        signals.append({
                            'source_slug': slug,
                            'raw_owner_name': None,
                            'raw_address': addr.group()[:120] if addr else line[:80],
                            'raw_payload': json.dumps({'line': line[:200]}),
                            'signal_type': 'building_permit',
                            'score': 45,
                            'county': 'Salt Lake',
                            'city': 'South Salt Lake',
                        })
            if signals: break
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



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
    """Utah County Code Enforcement — ArcGIS REST API for violations."""
    slug = 'utah-county-codev'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        # ArcGIS REST endpoint for Utah County code enforcement
        arcgis_urls = [
            'https://gis.utahcounty.gov/server/rest/services/PublicView/Planning/MapServer/0/query?where=1%3D1&outFields=ADDRESS,DESCRIPTION,STATUS&f=json&resultRecordCount=100',
            'https://gis.utahcounty.gov/arcgis/rest/services/PublicView/MapServer/0/query?where=1%3D1&outFields=*&f=json&resultRecordCount=100',
        ]
        # json already imported at top level
        for url in arcgis_urls:
            r = SESSION.get(url, timeout=20)
            if r.status_code != 200: continue
            data = r.json()
            features = data.get('features', [])
            if not features: continue
            for feat in features[:100]:
                attrs = feat.get('attributes', {})
                address = attrs.get('ADDRESS', attrs.get('SITE_ADDRESS', attrs.get('address', '')))
                owner = attrs.get('OWNER', attrs.get('OWNER_NAME', ''))
                violation = attrs.get('VIOLATION_TYPE', attrs.get('DESCRIPTION', 'Code Violation'))
                if not address: continue
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': owner or None,
                    'raw_address': str(address)[:120],
                    'raw_payload': _json.dumps({'violation': violation, 'attrs': {k: str(v)[:50] for k,v in attrs.items() if v}}),
                    'signal_type': 'code_violation',
                    'score': 82,
                    'county': 'Utah',
                    'city': None,
                })
            break
        # Apify fallback if ArcGIS is blocked
        if not signals:
            lines = apify_text('https://www.utahcounty.gov/dept/bldgservices/publicreports/')
            for line in lines:
                if any(w in line.lower() for w in ['violation', 'citation', 'notice', 'complaint']):
                    signals.append({
                        'source_slug': slug,
                        'raw_owner_name': None,
                        'raw_address': line[:120],
                        'raw_payload': json.dumps({'line': line[:200], 'source': 'apify_fallback'}),
                        'signal_type': 'code_violation',
                        'score': 82,
                        'county': 'Utah',
                        'city': None,
                    })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_utah_county_directory():
    """Utah County Recorder — recent document filings via land records."""
    slug = 'utah-county-directory'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        lines = apify_text('https://www.utahcounty.gov/LandRecords/PartyNameForm.asp')
        for line in lines:
            if any(k in line.lower() for k in ['deed', 'trust', 'lien', 'notice', 'mortgage', 'record']):
                if len(line) > 8:
                    signals.append({
                        'source_slug': slug,
                        'raw_owner_name': None,
                        'raw_address': line[:120],
                        'raw_payload': json.dumps({'line': line[:200]}),
                        'signal_type': 'recorder_document',
                        'score': 35,
                        'county': 'Utah',
                        'city': None,
                    })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



def scrape_utah_county_property_info():
    """Utah County property search — recent transactions and ownership."""
    slug = 'utah-county-property-info'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        lines = apify_text('https://www.utahcounty.gov/LandRecords/Index.asp')
        for line in lines:
            addr = re.search(r'\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln|Ct|N|S|E|W)', line, re.IGNORECASE)
            if addr and len(line) > 10:
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': None,
                    'raw_address': addr.group()[:120],
                    'raw_payload': json.dumps({'line': line[:200]}),
                    'signal_type': 'property_record',
                    'score': 35,
                    'county': 'Utah',
                    'city': None,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



def scrape_utah_county_real_property():
    """Utah County Assessor real property search."""
    slug = 'utah-county-real-property'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        lines = apify_text('https://assessor.utahcounty.gov/real-property')
        for line in lines:
            addr = re.search(r'\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln)', line, re.IGNORECASE)
            owner = re.search(r'^([A-Z][A-Z\s,]+)(?=\s+\d)', line)
            if (addr or owner) and len(line) > 8:
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': owner.group(1)[:80] if owner else None,
                    'raw_address': addr.group()[:120] if addr else line[:80],
                    'raw_payload': json.dumps({'line': line[:200]}),
                    'signal_type': 'property_record',
                    'score': 35,
                    'county': 'Utah',
                    'city': None,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



def scrape_ksl_fsbo():
    """KSL FSBO — For Sale By Owner listings on KSL Classifieds."""
    slug = 'ksl-fsbo'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        from bs4 import BeautifulSoup
        import re
        for page in range(1, 4):
            lines = apify_text(f'https://kslclassifieds.com/real-estate/homes-for-sale/?page={page}')
            r_text = '\n'.join(lines)
            class _FakeR:
                text = r_text
                status_code = 200
            r = _FakeR()
            soup = BeautifulSoup(r.text, 'html.parser')
            listings = soup.select('.listing, .classified-item, article, .ad-item, .result')
            if not listings:
                listings = soup.select('li[class*="listing"], div[class*="listing"]')
            for item in listings:
                title = item.select_one('h2, h3, .title, .ad-title')
                price_el = item.select_one('.price, [class*=price]')
                addr_el = item.select_one('.location, .address, [class*=location]')
                if not title: continue
                title_text = title.get_text(strip=True)
                if not any(w in title_text.lower() for w in ['sale', 'home', 'house', 'bed', 'bath', 'sqft', '$']): continue
                price = price_el.get_text(strip=True) if price_el else ''
                address = addr_el.get_text(strip=True) if addr_el else title_text[:60]
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': None,
                    'raw_address': address[:120],
                    'raw_payload': json.dumps({'title': title_text[:80], 'price': price, 'source': 'kslclassifieds'}),
                    'signal_type': 'fsbo',
                    'score': 65,
                    'county': 'Utah',
                    'city': None,
                })
            if not listings: break
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


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
    """Utah County LIR parcels — ArcGIS parcel ownership data."""
    slug = 'uco-lir-parcels'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        import json as _json
        # Try multiple ArcGIS endpoints for Utah County parcels
        arcgis_urls = [
            'https://services1.arcgis.com/99lidPhWCzftIe9K/arcgis/rest/services/UtahCountyParcels/FeatureServer/0/query?where=1%3D1&outFields=OWNER,SITUS_ADDR,COUNTY,PARCEL_ID&resultRecordCount=200&f=json',
            'https://maps.utahcounty.gov/arcgis/rest/services/Parcels/MapServer/0/query?where=SITUS_STATE%3D%27UT%27&outFields=OWNER,SITUS_ADDR,PARCEL_ID&resultRecordCount=200&f=json',
        ]
        for url in arcgis_urls:
            try:
                r = SESSION.get(url, timeout=25)
                if r.status_code != 200: continue
                data = r.json()
                features = data.get('features', [])
                if not features: continue
                for feat in features:
                    attrs = feat.get('attributes', {})
                    owner = attrs.get('OWNER', attrs.get('OWNERNAME', ''))
                    address = attrs.get('SITUS_ADDR', attrs.get('SITE_ADDR', ''))
                    parcel = attrs.get('PARCEL_ID', attrs.get('PARCELID', ''))
                    if not address: continue
                    signals.append({
                        'source_slug': slug,
                        'raw_owner_name': str(owner)[:80] if owner else None,
                        'raw_address': str(address)[:120],
                        'raw_payload': json.dumps({'parcel_id': str(parcel), 'owner': str(owner)[:60]}),
                        'signal_type': 'parcel_ownership',
                        'score': 45,
                        'county': 'Utah',
                        'city': None,
                    })
                if signals: break
            except Exception as inner_e:
                log.warning(f'[{slug}] ArcGIS endpoint failed: {inner_e}')
                continue
        # Apify fallback
        if not signals:
            lines = apify_text('https://www.utahcounty.gov/LandRecords/PartyNameForm.asp')
            for line in lines:
                if len(line) > 10 and any(c.isdigit() for c in line):
                    signals.append({
                        'source_slug': slug,
                        'raw_owner_name': None,
                        'raw_address': line[:120],
                        'raw_payload': json.dumps({'line': line[:200], 'fallback': True}),
                        'signal_type': 'parcel_ownership',
                        'score': 45,
                        'county': 'Utah',
                        'city': None,
                    })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



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
    """Orem City building permits — new construction and major reno signals."""
    slug = 'orem-building-permits'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        lines = apify_text('https://www.orem.org/buildingpermits/')
        permit_kw = ['permit', 'issued', 'address', 'contractor', 'value', 'type', 'residential', 'commercial']
        for line in lines:
            if any(k in line.lower() for k in permit_kw) and len(line) > 10:
                addr = re.search(r'\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln|Ct|N|S|E|W)', line, re.IGNORECASE)
                if addr:
                    signals.append({
                        'source_slug': slug,
                        'raw_owner_name': None,
                        'raw_address': addr.group()[:120],
                        'raw_payload': json.dumps({'line': line[:200], 'source': 'orem_permits'}),
                        'signal_type': 'building_permit',
                        'score': 45,
                        'county': 'Utah',
                        'city': 'Orem',
                    })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



def scrape_utah_county_building_permit():
    """Utah County building permits — codev.utahcounty.gov."""
    slug = 'utah-county-building-permit'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        lines = apify_text('https://codev.utahcounty.gov/building')
        for line in lines:
            if any(k in line.lower() for k in ['permit', 'address', 'issued', 'contractor', 'applicant', 'valuation']):
                addr = re.search(r'\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln|Ct|N|S|E|W)', line, re.IGNORECASE)
                if addr:
                    signals.append({
                        'source_slug': slug,
                        'raw_owner_name': None,
                        'raw_address': addr.group()[:120],
                        'raw_payload': json.dumps({'line': line[:200]}),
                        'signal_type': 'building_permit',
                        'score': 45,
                        'county': 'Utah',
                        'city': None,
                    })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



def scrape_slc_city_real_estate():
    """SLC City real estate listings — city-owned surplus properties."""
    slug = 'slc-city-real-estate'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        lines = apify_text('https://www.slc.gov/rem/realestate/')
        for line in lines:
            if any(k in line.lower() for k in ['property', 'parcel', 'sale', 'surplus', 'bid', 'acre', 'sqft']):
                addr = re.search(r'\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way)', line, re.IGNORECASE)
                if len(line) > 10:
                    signals.append({
                        'source_slug': slug,
                        'raw_owner_name': None,
                        'raw_address': addr.group()[:120] if addr else line[:80],
                        'raw_payload': json.dumps({'line': line[:200]}),
                        'signal_type': 'public_sale',
                        'score': 65,
                        'county': 'Salt Lake',
                        'city': 'Salt Lake City',
                    })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



def scrape_utah_county_codev_browser():
    """Utah County code enforcement — browser-rendered violations."""
    slug = 'utah-county-codev-browser'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        lines = apify_text('https://codev.utahcounty.gov/')
        for line in lines:
            if any(k in line.lower() for k in ['violation', 'citation', 'notice', 'complaint', 'code', 'abatement']):
                addr = re.search(r'\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln|Ct|N|S|E|W)', line, re.IGNORECASE)
                if len(line) > 10:
                    signals.append({
                        'source_slug': slug,
                        'raw_owner_name': None,
                        'raw_address': addr.group()[:120] if addr else line[:80],
                        'raw_payload': json.dumps({'line': line[:200]}),
                        'signal_type': 'code_violation',
                        'score': 82,
                        'county': 'Utah',
                        'city': None,
                    })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



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
    """NOD Tracker — Utah County Land Records for deed-of-trust filings."""
    slug = 'nod-tracker'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        from bs4 import BeautifulSoup
        for doc_type in ['NOTICE OF DEFAULT', 'DEED OF TRUST', 'RECONVEYANCE']:
            r = SESSION.post(
                'https://www.utahcounty.gov/LandRecords/DocDescSearch.asp',
                data={'DocDesc': doc_type, 'DateRange': '30', 'County': 'Utah'},
                timeout=20
            )
            soup = BeautifulSoup(r.text, 'html.parser')
            rows = soup.select('table tr')[1:]
            for row in rows:
                cells = row.select('td')
                if len(cells) < 4: continue
                description = cells[0].get_text(strip=True)
                rec_date = cells[1].get_text(strip=True)
                entry = cells[3].get_text(strip=True) if len(cells) > 3 else ''
                grantor = cells[4].get_text(strip=True) if len(cells) > 4 else ''
                if not entry: continue
                score = 88 if 'DEFAULT' in doc_type else 65
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': grantor or None,
                    'raw_address': f'{doc_type} — Entry #{entry}',
                    'raw_payload': json.dumps({'doc_type': description, 'rec_date': rec_date, 'entry': entry}),
                    'signal_type': 'nod',
                    'score': score,
                    'county': 'Utah',
                    'city': None,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_probate_court_xchange():
    """Utah Courts Xchange — probate case filings via Apify."""
    slug = 'probate-court-xchange'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        for county_id, county_name in [('40','Utah'), ('49','Salt Lake')]:
            url = f'https://www.utcourts.gov/xchange/?caseType=PR&countyId={county_id}'
            lines = apify_text(url)
            for line in lines:
                if any(k in line.lower() for k in ['estate', 'probate', 'decedent', 'intestate', 'testamentary', 'pr-']):
                    if len(line) > 5:
                        case_match = re.search(r'(\d{4}-\d+)', line)
                        signals.append({
                            'source_slug': slug,
                            'raw_owner_name': line[:80] if len(line) < 80 else None,
                            'raw_address': f'Probate {county_name} Co. — {case_match.group(1) if case_match else line[:30]}',
                            'raw_payload': json.dumps({'line': line[:200], 'county': county_name}),
                            'signal_type': 'probate',
                            'score': 85,
                            'county': county_name,
                            'city': None,
                        })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



def scrape_divorce_court():
    """Divorce court — Utah Courts Xchange domestic cases with property."""
    slug = 'divorce-court'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        # Utah Courts Xchange — Domestic Relations cases (type DR)
        for county_id, county_name in [('40','Utah'), ('49','Salt Lake')]:
            lines = apify_text(f'https://www.utcourts.gov/xchange/?caseType=DR&countyId={county_id}')
            for line in lines:
                if any(k in line.lower() for k in ['divorce', 'dissolution', 'property', 'dr-', 'petition']):
                    case_match = re.search(r'(\d{4}-\d+)', line)
                    if len(line) > 5:
                        signals.append({
                            'source_slug': slug,
                            'raw_owner_name': line[:60] if len(line) < 60 else None,
                            'raw_address': f'Divorce Case {county_name} — {case_match.group(1) if case_match else line[:30]}',
                            'raw_payload': json.dumps({'line': line[:200], 'county': county_name}),
                            'signal_type': 'divorce',
                            'score': 80,
                            'county': county_name,
                            'city': None,
                        })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



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
    """Deed Transfers — Utah County Land Records warranty/quit claim deeds."""
    slug = 'deed-transfers-utah-county'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        from bs4 import BeautifulSoup
        for doc_type in ['WARRANTY DEED', 'QUIT CLAIM DEED', 'SPECIAL WARRANTY']:
            r = SESSION.post(
                'https://www.utahcounty.gov/LandRecords/DocDescSearch.asp',
                data={'DocDesc': doc_type, 'DateRange': '14', 'County': 'Utah'},
                timeout=20
            )
            soup = BeautifulSoup(r.text, 'html.parser')
            rows = soup.select('table tr')[1:]
            for row in rows:
                cells = row.select('td')
                if len(cells) < 4: continue
                rec_date = cells[1].get_text(strip=True)
                entry = cells[3].get_text(strip=True) if len(cells) > 3 else ''
                grantor = cells[4].get_text(strip=True) if len(cells) > 4 else ''
                grantee = cells[5].get_text(strip=True) if len(cells) > 5 else ''
                if not entry: continue
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': grantor or None,
                    'raw_address': f'{doc_type} — Entry #{entry}',
                    'raw_payload': json.dumps({'doc_type': doc_type, 'rec_date': rec_date, 'grantor': grantor, 'grantee': grantee, 'entry': entry}),
                    'signal_type': 'deed_transfer',
                    'score': 55,
                    'county': 'Utah',
                    'city': None,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


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
    """Craigslist SLC — buyers explicitly posting want-to-buy ads."""
    slug = 'craigslist-buyer-wanted-slc'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        search_terms = ['pre-approved', 'looking to buy', 'cash buyer', 'want to buy', 'first home', 'relocating']
        for term in search_terms[:3]:
            lines = apify_text(f'https://saltlake.craigslist.org/search/rea?sort=date&query={term.replace(" ","+")}')
            for line in lines:
                if any(k in line.lower() for k in ['$', 'bed', 'bath', 'sqft', 'approve', 'cash', 'buy', 'looking']):
                    if len(line) > 15:
                        addr = re.search(r'\d+\s+\w+\s+(?:St|Ave|Dr|Rd)', line, re.IGNORECASE)
                        signals.append({
                            'source_slug': slug,
                            'raw_owner_name': None,
                            'raw_address': addr.group() if addr else f'CL SLC: {term}',
                            'raw_payload': json.dumps({'text': line[:200], 'search_term': term}),
                            'signal_type': 'buyer_wanted_post',
                            'score': 65,
                            'county': 'Salt Lake',
                            'city': 'Salt Lake City',
                        })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



def scrape_craigslist_buyer_wanted_provo():
    """Craigslist Provo — buyers explicitly posting want-to-buy ads."""
    slug = 'craigslist-buyer-wanted-provo'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        search_terms = ['pre-approved', 'looking to buy', 'cash buyer', 'want to buy', 'first home']
        for term in search_terms[:3]:
            lines = apify_text(f'https://provo.craigslist.org/search/rea?sort=date&query={term.replace(" ","+")}')
            for line in lines:
                if any(k in line.lower() for k in ['$', 'bed', 'bath', 'approve', 'cash', 'buy', 'looking']):
                    if len(line) > 15:
                        signals.append({
                            'source_slug': slug,
                            'raw_owner_name': None,
                            'raw_address': f'CL Provo: {line[:60]}',
                            'raw_payload': json.dumps({'text': line[:200], 'search_term': term}),
                            'signal_type': 'buyer_wanted_post',
                            'score': 65,
                            'county': 'Utah',
                            'city': 'Provo',
                        })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



def scrape_reddit_buyer_intent():
    """Reddit buyer intent — r/utahhousing, r/SaltLakeCity via Apify actor."""
    slug = 'reddit-buyer-intent'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        # Reddit blocks direct access — use Apify web scraper
        for subreddit in ['utahhousing', 'SaltLakeCity', 'Provo']:
            lines = apify_text(f'https://www.reddit.com/r/{subreddit}/new/')
            buy_kw = ['looking to buy', 'want to buy', 'relocating', 'moving to', 'first home',
                      'pre-approved', 'cash buyer', 'agent recommendation', 'neighborhood advice']
            for line in lines:
                if any(k in line.lower() for k in buy_kw) and len(line) > 15:
                    signals.append({
                        'source_slug': slug,
                        'raw_owner_name': None,
                        'raw_address': f'Reddit r/{subreddit}: {line[:80]}',
                        'raw_payload': json.dumps({'subreddit': subreddit, 'text': line[:200]}),
                        'signal_type': 'social_buyer_intent',
                        'score': 55,
                        'county': 'Utah',
                        'city': None,
                    })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



def scrape_utah_sos_new_entities():
    """Utah SOS — new real estate/investment LLCs forming = investor buyer signal."""
    slug = 'utah-sos-new-entities'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        from bs4 import BeautifulSoup
        real_estate_keywords = ['properties', 'holdings', 'realty', 'investments', 'capital', 'homes', 'estates', 'ventures', 'assets']
        for kw in real_estate_keywords[:5]:
            r = SESSION.get(
                f'https://secure.utah.gov/bes/results.html?search={kw}&type=name&status=Active&entity=LLC',
                timeout=20
            )
            soup = BeautifulSoup(r.text, 'html.parser')
            rows = soup.select('table tr, .entity-result, .result-row')[1:]
            for row in rows[:20]:
                cells = row.select('td')
                if len(cells) < 2: continue
                name = cells[0].get_text(strip=True)
                status = cells[1].get_text(strip=True) if len(cells) > 1 else ''
                reg_date = cells[2].get_text(strip=True) if len(cells) > 2 else ''
                if not name or len(name) < 3: continue
                if not any(k in name.lower() for k in real_estate_keywords): continue
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': name,
                    'raw_address': f'Utah LLC — {name}',
                    'raw_payload': json.dumps({'entity_name': name, 'status': status, 'reg_date': reg_date, 'keyword': kw}),
                    'signal_type': 'new_investor_entity',
                    'score': 50,
                    'county': 'Utah',
                    'city': None,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_competitor_mirror_ksl():
    """KSL Homes — mirror competitor listings for market intelligence."""
    slug = 'competitor-mirror-ksl'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        for county in ['Utah', 'SaltLake']:
            lines = apify_text(f'https://homes.ksl.com/for-sale/?sort=newest&county={county}&limit=25')
            for line in lines:
                price = re.search(r'\$[\d,]+', line)
                addr = re.search(r'\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln|Ct)', line, re.IGNORECASE)
                if price and (addr or len(line) > 15):
                    signals.append({
                        'source_slug': slug,
                        'raw_owner_name': None,
                        'raw_address': addr.group()[:120] if addr else f'KSL {county}: {line[:60]}',
                        'raw_payload': json.dumps({'price': price.group() if price else '', 'county': county, 'line': line[:200]}),
                        'signal_type': 'competitor_listing',
                        'score': 40,
                        'county': 'Salt Lake' if county == 'SaltLake' else 'Utah',
                        'city': None,
                    })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



def scrape_competitor_mirror_redfin():
    """Redfin market intelligence — via Apify (direct access 403 blocked)."""
    slug = 'competitor-mirror-redfin'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        # Redfin blocks direct — use Apify residential proxy
        for city_url, city, county in [
            ('https://www.redfin.com/city/14971/UT/Provo', 'Provo', 'Utah'),
            ('https://www.redfin.com/city/17312/UT/Salt-Lake-City', 'Salt Lake City', 'Salt Lake'),
        ]:
            lines = apify_text(city_url)
            for line in lines:
                price = re.search(r'\$[\d,]+', line)
                addr = re.search(r'\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way)\b', line, re.IGNORECASE)
                if price and len(line) > 10:
                    signals.append({
                        'source_slug': slug,
                        'raw_owner_name': None,
                        'raw_address': addr.group()[:120] if addr else f'Redfin {city}: {line[:60]}',
                        'raw_payload': json.dumps({'price': price.group() if price else '', 'city': city, 'line': line[:200]}),
                        'signal_type': 'competitor_listing',
                        'score': 40,
                        'county': county,
                        'city': city,
                    })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



def scrape_warn_act_utah():
    """WARN Act — Utah DWS layoff filings. Displaced workers = buyer/relocation signal."""
    slug = 'warn-act-utah'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        from bs4 import BeautifulSoup
        r = SESSION.get('https://jobs.utah.gov/employer/business/warnnotices.html', timeout=20)
        soup = BeautifulSoup(r.text, 'html.parser')
        rows = soup.select('table tr')[1:]
        for row in rows:
            cells = row.select('td')
            if len(cells) < 3: continue
            date = cells[0].get_text(strip=True)
            company = cells[1].get_text(strip=True)
            city = cells[2].get_text(strip=True)
            workers = cells[3].get_text(strip=True) if len(cells) > 3 else ''
            if not company: continue
            county = 'Utah' if city.lower() in ['provo','orem','lehi','american fork','spanish fork','payson','springville','mapleton','salem'] else 'Salt Lake'
            signals.append({
                'source_slug': slug,
                'raw_owner_name': company,
                'raw_address': city,
                'raw_payload': json.dumps({'company': company, 'city': city, 'workers': workers, 'date': date}),
                'signal_type': 'mass_layoff',
                'score': 50,
                'county': county,
                'city': city,
            })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


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
    """Census Bureau building permit survey — new construction by county."""
    slug = 'census-new-construction-utah'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        lines = apify_text('https://www.census.gov/construction/bps/')
        for line in lines:
            if any(k in line.lower() for k in ['utah', 'permit', 'unit', 'housing', 'residential', 'construction']):
                if len(line) > 8:
                    signals.append({
                        'source_slug': slug,
                        'raw_owner_name': None,
                        'raw_address': f'Census Construction: {line[:80]}',
                        'raw_payload': json.dumps({'line': line[:200], 'source': 'census_bps'}),
                        'signal_type': 'new_construction',
                        'score': 45,
                        'county': 'Utah',
                        'city': None,
                    })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



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
    import time  # fix: was missing
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
    """Lien & Judgment Records — SLCO recorder document search."""
    slug = 'lien-judgment-records'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        from bs4 import BeautifulSoup
        # Use Utah County Land Records for lien-type documents
        for doc_type in ['JUDGMENT LIEN', 'STATE TAX LIEN', 'IRS LIEN', 'MECHANICS LIEN']:
            r = SESSION.post(
                'https://www.utahcounty.gov/LandRecords/DocDescSearch.asp',
                data={'DocDesc': doc_type, 'DateRange': '30', 'County': 'Utah'},
                timeout=20
            )
            soup = BeautifulSoup(r.text, 'html.parser')
            rows = soup.select('table tr')[1:]
            for row in rows:
                cells = row.select('td')
                if len(cells) < 4: continue
                description = cells[0].get_text(strip=True)
                rec_date = cells[1].get_text(strip=True)
                entry = cells[3].get_text(strip=True) if len(cells) > 3 else ''
                grantor = cells[4].get_text(strip=True) if len(cells) > 4 else ''
                if not entry: continue
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': grantor or None,
                    'raw_address': f'{doc_type} — Entry #{entry}',
                    'raw_payload': json.dumps({'doc_type': description, 'rec_date': rec_date, 'entry': entry, 'grantor': grantor}),
                    'signal_type': 'lien_judgment',
                    'score': 65,
                    'county': 'Utah',
                    'city': None,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_census_acs_demographics():
    census_key = os.environ.get("CENSUS_API_KEY","")
    if not census_key:
        log.warning("census-acs-demographics: CENSUS_API_KEY not set — skipping (get free key at api.census.gov)")
        return 0
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
    """Utah voter registration growth — population migration signal."""
    slug = 'utah-voter-growth'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        # elections.utah.gov voter stats
        for url in ['https://elections.utah.gov/election-resources/voter-statistics',
                    'https://elections.utah.gov/voter-information/voter-registration']:
            lines = apify_text(url)
            for line in lines:
                if any(k in line.lower() for k in ['registered', 'county', 'total', 'growth', 'utah', 'salt lake', 'voter']):
                    if any(c.isdigit() for c in line) and len(line) > 5:
                        signals.append({
                            'source_slug': slug,
                            'raw_owner_name': None,
                            'raw_address': f'Voter Registration Signal: {line[:80]}',
                            'raw_payload': json.dumps({'line': line[:200], 'source': 'utah_elections'}),
                            'signal_type': 'population_growth',
                            'score': 35,
                            'county': 'Utah',
                            'city': None,
                        })
            if signals: break
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



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


def scrape_wasatch_county_tax_sale():
    """Wasatch County tax sale — delinquent property auctions."""
    slug = 'wasatch-county-tax-sale'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        lines = apify_text('https://wasatch.utah.gov/departments/treasurer')
        for line in lines:
            if any(k in line.lower() for k in ['tax sale', 'delinquent', 'auction', 'lien', 'notice', 'property']):
                if len(line) > 5:
                    owner = re.search(r'^([A-Z][A-Z\s,&]+)', line)
                    signals.append({
                        'source_slug': slug,
                        'raw_owner_name': owner.group(1)[:80] if owner else None,
                        'raw_address': line[:120],
                        'raw_payload': json.dumps({'line': line[:200]}),
                        'signal_type': 'tax_sale_delinquency',
                        'score': 75,
                        'county': 'Wasatch',
                        'city': None,
                    })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_slc_county_tax_sale():
    """SLC County tax sale — delinquent property auctions."""
    slug = 'slc-county-tax-sale'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        lines = apify_text('https://slco.org/treasurer/tax-sale/')
        for line in lines:
            if any(k in line.lower() for k in ['tax sale', 'delinquent', 'auction', 'parcel', 'property', 'lien']):
                owner = re.search(r'^([A-Z][A-Z\s,&]+)(?=\s)', line)
                addr = re.search(r'\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way)', line, re.IGNORECASE)
                if len(line) > 5:
                    signals.append({
                        'source_slug': slug,
                        'raw_owner_name': owner.group(1)[:80] if owner else None,
                        'raw_address': addr.group()[:120] if addr else line[:80],
                        'raw_payload': json.dumps({'line': line[:200]}),
                        'signal_type': 'tax_sale_delinquency',
                        'score': 75,
                        'county': 'Salt Lake',
                        'city': None,
                    })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)



# ═══════════════════════════════════════════════════════════════════════════
# UTAH MARKETPLACE INTELLIGENCE — v16
# Full competitor + marketplace sweep across all major Utah RE platforms
# Signals: competitor_listing, rental_listing, distressed_sale, fsbo, new_construction
# ═══════════════════════════════════════════════════════════════════════════

def scrape_trulia_utah():
    """Trulia Utah — competitor listing monitor. Price cuts + days on market signal."""
    slug = 'trulia-utah'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        for url in [
            'https://www.trulia.com/UT/',
            'https://www.trulia.com/for_sale/Utah_state/price_reduced/',
            'https://www.trulia.com/for_sale/Utah_state/FSBO_lt/',
        ]:
            r = SESSION.get(url, timeout=20)
            soup = BeautifulSoup(r.text, 'html.parser')
            # Trulia renders listing cards in HTML
            cards = soup.select('[data-testid="property-card"], .PropertyCard, [class*="PropertyCard"]')
            if not cards:
                cards = soup.select('li[class*="search"], div[class*="listing"]')
            for card in cards[:50]:
                text = card.get_text(separator=' ', strip=True)
                price = re.search(r'\$([\d,]+)', text)
                addr = re.search(r'(\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln|Ct|N|S|E|W))', text, re.IGNORECASE)
                beds = re.search(r'(\d+)\s*bd', text, re.IGNORECASE)
                dom = re.search(r'(\d+)\s*days?\s*on', text, re.IGNORECASE)
                if not (price or addr): continue
                # Score by days on market — longer = more motivated seller
                days = int(dom.group(1)) if dom else 0
                score = 82 if days >= 60 else 65 if days >= 30 else 45
                county = 'Utah' if any(c in text for c in ['Provo','Orem','Lehi','American Fork','Spanish Fork']) else 'Salt Lake'
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': None,
                    'raw_address': addr.group(1)[:120] if addr else f'Trulia Utah listing',
                    'raw_payload': json.dumps({
                        'price': price.group() if price else '',
                        'beds': beds.group(1) if beds else '',
                        'days_on_market': days,
                        'source': 'trulia'
                    }),
                    'signal_type': 'competitor_listing',
                    'score': score,
                    'county': county,
                    'city': None,
                })
            if signals: break
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_rentler_utah():
    """Rentler — Utah's largest rental platform. Landlords listing = investor seller signal."""
    slug = 'rentler-utah'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        for url in [
            'https://rentler.com/search?location=Salt+Lake+City%2C+UT&propertyType=house',
            'https://rentler.com/search?location=Provo%2C+UT&propertyType=house',
            'https://rentler.com/search?location=Ogden%2C+UT&propertyType=house',
            'https://rentler.com/search?location=Utah+County%2C+UT',
        ]:
            r = SESSION.get(url, timeout=20)
            soup = BeautifulSoup(r.text, 'html.parser')
            cards = soup.select('.listing-card, .property-card, [class*="listing"], article')
            if not cards:
                lines = apify_text(url)
                for line in lines:
                    price = re.search(r'\$([\d,]+)/mo', line, re.IGNORECASE)
                    addr = re.search(r'(\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln))', line, re.IGNORECASE)
                    if price and addr:
                        rent = int(price.group(1).replace(',',''))
                        # High-rent single family = investor landlord = potential seller
                        score = 72 if rent >= 2000 else 55
                        signals.append({
                            'source_slug': slug,
                            'raw_owner_name': None,
                            'raw_address': addr.group(1)[:120],
                            'raw_payload': json.dumps({'rent': rent, 'source': 'rentler', 'url': url}),
                            'signal_type': 'rental_listing',
                            'score': score,
                            'county': 'Utah' if 'Provo' in url or 'Utah' in url else 'Salt Lake',
                            'city': None,
                        })
                continue
            for card in cards[:40]:
                text = card.get_text(separator=' ', strip=True)
                price = re.search(r'\$([\d,]+)', text)
                addr = re.search(r'(\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln))', text, re.IGNORECASE)
                if not price: continue
                rent = int(price.group(1).replace(',','')) if price else 0
                score = 72 if rent >= 2000 else 55
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': None,
                    'raw_address': addr.group(1)[:120] if addr else f'Rentler: {text[:60]}',
                    'raw_payload': json.dumps({'rent': rent, 'source': 'rentler'}),
                    'signal_type': 'rental_listing',
                    'score': score,
                    'county': 'Utah' if 'Provo' in url or 'Utah+County' in url else 'Salt Lake',
                    'city': None,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_ksl_homes_market():
    """KSL Homes — Utah's dominant local listing platform. Price cuts + DOM signals."""
    slug = 'ksl-homes-market'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        for url in [
            'https://homes.ksl.com/for-sale/?county=Utah&sort=newest',
            'https://homes.ksl.com/for-sale/?county=Salt+Lake&sort=newest',
            'https://homes.ksl.com/for-sale/?county=Weber&sort=newest',
            'https://homes.ksl.com/for-sale-by-owner/',
        ]:
            lines = apify_text(url)
            for line in lines:
                price = re.search(r'\$([\d,]+)', line)
                addr = re.search(r'(\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln|Ct|N|S|E|W))', line, re.IGNORECASE)
                dom = re.search(r'(\d+)\s*days?', line, re.IGNORECASE)
                if not (price or addr): continue
                days = int(dom.group(1)) if dom else 0
                is_fsbo = 'by owner' in url.lower() or 'fsbo' in line.lower()
                score = 82 if is_fsbo else (75 if days >= 60 else 55 if days >= 30 else 40)
                county = 'Weber' if 'Weber' in url else ('Salt Lake' if 'Salt+Lake' in url else 'Utah')
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': None,
                    'raw_address': addr.group(1)[:120] if addr else line[:80],
                    'raw_payload': json.dumps({
                        'price': price.group() if price else '',
                        'days_on_market': days,
                        'is_fsbo': is_fsbo,
                        'source': 'ksl_homes'
                    }),
                    'signal_type': 'fsbo' if is_fsbo else 'competitor_listing',
                    'score': score,
                    'county': county,
                    'city': None,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_zillow_utah():
    """Zillow Utah — via Apify residential proxy. Price cuts + FSBO + Zestimates."""
    slug = 'zillow-utah-market'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        for url in [
            'https://www.zillow.com/provo-ut/',
            'https://www.zillow.com/salt-lake-city-ut/',
            'https://www.zillow.com/ogden-ut/',
            'https://www.zillow.com/lehi-ut/',
            'https://www.zillow.com/orem-ut/',
        ]:
            lines = apify_text(url)
            city = url.split('/')[3].split('-ut')[0].replace('-',' ').title()
            for line in lines:
                price = re.search(r'\$([\d,]+)', line)
                addr = re.search(r'(\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln|Ct))', line, re.IGNORECASE)
                dom = re.search(r'(\d+)\s*days?\s*on', line, re.IGNORECASE)
                cut = re.search(r'price\s+cut|reduced|price\s+drop', line, re.IGNORECASE)
                if not (price or addr): continue
                days = int(dom.group(1)) if dom else 0
                score = 75 if cut else (65 if days >= 45 else 45)
                county = 'Salt Lake' if 'salt-lake' in url or 'sandy' in url else 'Utah'
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': None,
                    'raw_address': addr.group(1)[:120] if addr else f'{city}: {line[:60]}',
                    'raw_payload': json.dumps({
                        'price': price.group() if price else '',
                        'days_on_market': days,
                        'price_cut': bool(cut),
                        'city': city,
                        'source': 'zillow'
                    }),
                    'signal_type': 'competitor_listing',
                    'score': score,
                    'county': county,
                    'city': city,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_realtor_com_utah():
    """Realtor.com Utah — via Apify. Days on market + price reduction signals."""
    slug = 'realtor-com-utah'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        for url in [
            'https://www.realtor.com/realestateandhomes-search/Provo_UT',
            'https://www.realtor.com/realestateandhomes-search/Salt-Lake-City_UT',
            'https://www.realtor.com/realestateandhomes-search/Orem_UT',
            'https://www.realtor.com/realestateandhomes-search/Lehi_UT',
        ]:
            lines = apify_text(url)
            city = url.split('/')[-1].split('_UT')[0].replace('-',' ')
            for line in lines:
                price = re.search(r'\$([\d,]+)', line)
                addr = re.search(r'(\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln|Ct))', line, re.IGNORECASE)
                dom = re.search(r'(\d+)\s*days?', line, re.IGNORECASE)
                cut = 'reduced' in line.lower() or 'price cut' in line.lower() or 'price drop' in line.lower()
                if not (price or addr): continue
                days = int(dom.group(1)) if dom else 0
                score = 75 if cut else (65 if days >= 45 else 45)
                county = 'Salt Lake' if 'Salt-Lake' in url else 'Utah'
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': None,
                    'raw_address': addr.group(1)[:120] if addr else f'{city}: {line[:60]}',
                    'raw_payload': json.dumps({
                        'price': price.group() if price else '',
                        'days_on_market': days,
                        'price_cut': cut,
                        'city': city,
                        'source': 'realtor_com'
                    }),
                    'signal_type': 'competitor_listing',
                    'score': score,
                    'county': county,
                    'city': city,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_redfin_utah():
    """Redfin Utah — via Apify. Hot homes + price drops + days on market."""
    slug = 'redfin-utah'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        for url in [
            'https://www.redfin.com/city/14971/UT/Provo',
            'https://www.redfin.com/city/17312/UT/Salt-Lake-City',
            'https://www.redfin.com/city/12867/UT/Ogden',
            'https://www.redfin.com/city/10069/UT/Lehi',
        ]:
            lines = apify_text(url)
            city = url.split('/UT/')[-1].replace('-',' ')
            for line in lines:
                price = re.search(r'\$([\d,]+)', line)
                addr = re.search(r'(\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln|Ct))', line, re.IGNORECASE)
                dom = re.search(r'(\d+)\s*days?', line, re.IGNORECASE)
                cut = any(w in line.lower() for w in ['reduced', 'price drop', 'price cut', 'back on market'])
                hot = 'hot home' in line.lower() or 'hot listing' in line.lower()
                if not (price or addr): continue
                days = int(dom.group(1)) if dom else 0
                score = 72 if cut else (80 if hot else (60 if days >= 45 else 40))
                county = 'Salt Lake' if 'Salt-Lake' in url or 'Ogden' in url else 'Utah'
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': None,
                    'raw_address': addr.group(1)[:120] if addr else f'{city}: {line[:60]}',
                    'raw_payload': json.dumps({
                        'price': price.group() if price else '',
                        'days_on_market': days,
                        'price_cut': cut,
                        'hot_home': hot,
                        'source': 'redfin'
                    }),
                    'signal_type': 'competitor_listing',
                    'score': score,
                    'county': county,
                    'city': city,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_apartments_com_utah():
    """Apartments.com Utah — rental market intelligence. High-rent SFR = investor seller signal."""
    slug = 'apartments-com-utah'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        for url in [
            'https://www.apartments.com/provo-ut/',
            'https://www.apartments.com/salt-lake-city-ut/',
            'https://www.apartments.com/ogden-ut/',
            'https://www.apartments.com/orem-ut/',
        ]:
            lines = apify_text(url)
            city = url.split('/')[-2].split('-ut')[0].replace('-',' ').title()
            for line in lines:
                price = re.search(r'\$([\d,]+)', line)
                addr = re.search(r'(\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln))', line, re.IGNORECASE)
                if not price: continue
                rent = int(price.group(1).replace(',',''))
                if rent < 800 or rent > 15000: continue
                score = 72 if rent >= 2500 else 55
                county = 'Salt Lake' if city in ['Salt Lake City','Ogden'] else 'Utah'
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': None,
                    'raw_address': addr.group(1)[:120] if addr else f'{city} rental: {line[:60]}',
                    'raw_payload': json.dumps({'rent': rent, 'city': city, 'source': 'apartments_com'}),
                    'signal_type': 'rental_listing',
                    'score': score,
                    'county': county,
                    'city': city,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_craigslist_housing_utah():
    """Craigslist housing Utah — FSBO + rental + housing wanted posts."""
    slug = 'craigslist-housing-utah'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        searches = [
            ('https://saltlake.craigslist.org/search/reo?sort=date', 'Salt Lake', 'real_estate_by_owner'),
            ('https://provo.craigslist.org/search/reo?sort=date', 'Utah', 'real_estate_by_owner'),
            ('https://saltlake.craigslist.org/search/apa?sort=date&max_price=3000', 'Salt Lake', 'rental_listing'),
            ('https://provo.craigslist.org/search/apa?sort=date&max_price=2500', 'Utah', 'rental_listing'),
        ]
        for url, county, sig_type in searches:
            lines = apify_text(url)
            for line in lines:
                price = re.search(r'\$([\d,]+)', line)
                addr = re.search(r'(\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln))', line, re.IGNORECASE)
                if not (price or len(line) > 15): continue
                score = 75 if sig_type == 'real_estate_by_owner' else 55
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': None,
                    'raw_address': addr.group(1)[:120] if addr else line[:80],
                    'raw_payload': json.dumps({'price': price.group() if price else '', 'source': 'craigslist', 'type': sig_type}),
                    'signal_type': sig_type,
                    'score': score,
                    'county': county,
                    'city': None,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_loopnet_utah():
    """LoopNet Utah — commercial and investment property listings."""
    slug = 'loopnet-utah'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        for url in [
            'https://www.loopnet.com/search/commercial-real-estate/utah/for-sale/',
            'https://www.loopnet.com/search/multifamily-apartment-buildings/utah/for-sale/',
        ]:
            lines = apify_text(url)
            for line in lines:
                price = re.search(r'\$([\d,.]+[MK]?)', line, re.IGNORECASE)
                addr = re.search(r'(\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way))', line, re.IGNORECASE)
                prop_type = re.search(r'(multifamily|apartment|industrial|office|retail|land)', line, re.IGNORECASE)
                if not (price or addr): continue
                score = 65  # Commercial = investor seller
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': None,
                    'raw_address': addr.group(1)[:120] if addr else line[:80],
                    'raw_payload': json.dumps({
                        'price': price.group() if price else '',
                        'property_type': prop_type.group(1) if prop_type else 'commercial',
                        'source': 'loopnet'
                    }),
                    'signal_type': 'competitor_listing',
                    'score': score,
                    'county': 'Utah',
                    'city': None,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_hubzu_utah():
    """Hubzu Utah — bank-owned and foreclosure auction properties."""
    slug = 'hubzu-utah'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        lines = apify_text('https://www.hubzu.com/search?stateId=UT')
        for line in lines:
            price = re.search(r'\$([\d,]+)', line)
            addr = re.search(r'(\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln))', line, re.IGNORECASE)
            auction = any(w in line.lower() for w in ['auction', 'bid', 'bank-owned', 'reo', 'foreclosure'])
            if not (price or addr): continue
            score = 85 if auction else 65  # Bank-owned = highly motivated
            signals.append({
                'source_slug': slug,
                'raw_owner_name': None,
                'raw_address': addr.group(1)[:120] if addr else line[:80],
                'raw_payload': json.dumps({
                    'price': price.group() if price else '',
                    'is_auction': auction,
                    'source': 'hubzu'
                }),
                'signal_type': 'distressed_sale',
                'score': score,
                'county': 'Utah',
                'city': None,
            })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_reo_utah():
    """REO.com Utah — bank-owned REO properties. Motivated institutional seller."""
    slug = 'reo-utah'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        lines = apify_text('https://www.reo.com/listings/utah')
        for line in lines:
            price = re.search(r'\$([\d,]+)', line)
            addr = re.search(r'(\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln))', line, re.IGNORECASE)
            if not (price or addr): continue
            signals.append({
                'source_slug': slug,
                'raw_owner_name': None,
                'raw_address': addr.group(1)[:120] if addr else line[:80],
                'raw_payload': json.dumps({'price': price.group() if price else '', 'source': 'reo_com'}),
                'signal_type': 'distressed_sale',
                'score': 82,  # REO = bank-owned = must sell
                'county': 'Utah',
                'city': None,
            })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_homesnap_utah():
    """Homesnap Utah — MLS-connected listing data. Agent-listed properties."""
    slug = 'homesnap-utah'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        r = SESSION.get('https://www.homesnap.com/ut', timeout=20)
        soup = BeautifulSoup(r.text, 'html.parser')
        cards = soup.select('[class*="listing"], [class*="property"], article, .card')
        if not cards:
            lines = apify_text('https://www.homesnap.com/ut')
            for line in lines:
                price = re.search(r'\$([\d,]+)', line)
                addr = re.search(r'(\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln))', line, re.IGNORECASE)
                dom = re.search(r'(\d+)\s*days?', line, re.IGNORECASE)
                if not (price or addr): continue
                days = int(dom.group(1)) if dom else 0
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': None,
                    'raw_address': addr.group(1)[:120] if addr else line[:80],
                    'raw_payload': json.dumps({'price': price.group() if price else '', 'days_on_market': days}),
                    'signal_type': 'competitor_listing',
                    'score': 65 if days >= 30 else 40,
                    'county': 'Utah',
                    'city': None,
                })
        else:
            for card in cards[:40]:
                text = card.get_text(separator=' ', strip=True)
                price = re.search(r'\$([\d,]+)', text)
                addr = re.search(r'(\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln))', text, re.IGNORECASE)
                if not (price or addr): continue
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': None,
                    'raw_address': addr.group(1)[:120] if addr else text[:80],
                    'raw_payload': json.dumps({'price': price.group() if price else '', 'source': 'homesnap'}),
                    'signal_type': 'competitor_listing',
                    'score': 40,
                    'county': 'Utah',
                    'city': None,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_forsalebyowner_utah():
    """ForSaleByOwner.com Utah — confirmed FSBO listings. No agent = opportunity."""
    slug = 'forsalebyowner-utah'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        for url in [
            'https://www.forsalebyowner.com/real-estate/utah/',
            'https://www.forsalebyowner.com/real-estate/utah/salt-lake-county/',
            'https://www.forsalebyowner.com/real-estate/utah/utah-county/',
        ]:
            lines = apify_text(url)
            for line in lines:
                price = re.search(r'\$([\d,]+)', line)
                addr = re.search(r'(\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln|Ct))', line, re.IGNORECASE)
                if not (price or addr): continue
                county = 'Salt Lake' if 'salt-lake' in url else 'Utah'
                signals.append({
                    'source_slug': slug,
                    'raw_owner_name': None,
                    'raw_address': addr.group(1)[:120] if addr else line[:80],
                    'raw_payload': json.dumps({'price': price.group() if price else '', 'source': 'fsbo_com', 'county': county}),
                    'signal_type': 'fsbo',
                    'score': 82,  # Confirmed FSBO = agent opportunity
                    'county': county,
                    'city': None,
                })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


def scrape_auction_com_utah():
    """Auction.com Utah — foreclosure auctions. Distressed motivated seller."""
    slug = 'auction-com-utah'
    log.info(f'[{slug}] starting')
    signals = []
    try:
        lines = apify_text('https://www.auction.com/residential/?state=UT')
        for line in lines:
            price = re.search(r'\$([\d,]+)', line)
            addr = re.search(r'(\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln))', line, re.IGNORECASE)
            auction_date = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+', line)
            if not (price or addr): continue
            # Close auction date = higher urgency
            score = 92 if auction_date else 78
            signals.append({
                'source_slug': slug,
                'raw_owner_name': None,
                'raw_address': addr.group(1)[:120] if addr else line[:80],
                'raw_payload': json.dumps({
                    'price': price.group() if price else '',
                    'auction_date': auction_date.group() if auction_date else '',
                    'source': 'auction_com'
                }),
                'signal_type': 'distressed_sale',
                'score': score,
                'county': 'Utah',
                'city': None,
            })
    except Exception as e:
        log.error(f'[{slug}] {e}')
    return post_signals_batch(signals)


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

    scrape_slc_county_tax_sale,
    scrape_wasatch_county_tax_sale,
    # ══ UTAH MARKETPLACE INTELLIGENCE — v16 ══
    scrape_rentler_utah,
    scrape_ksl_homes_market,
    scrape_zillow_utah,
    scrape_realtor_com_utah,
    scrape_redfin_utah,
    scrape_apartments_com_utah,
    scrape_craigslist_housing_utah,
    scrape_loopnet_utah,
    scrape_hubzu_utah,
    scrape_reo_utah,
    scrape_homesnap_utah,
    scrape_forsalebyowner_utah,
    scrape_auction_com_utah,
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
    log.info(f'=== Premier Prospect v17 — KPI cache refresh, instant dashboard load — {len(SCRAPERS)} sources ===')
    total = 0
    for fn in SCRAPERS:
        try:
            n = fn() or 0
            total += n
            write_run_log(fn.__name__.replace('scrape_',''), n, 'success')
        except Exception as e:
            log.error(f'{fn.__name__} crashed: {e}')
            write_run_log(fn.__name__.replace('scrape_',''), 0, 'error', str(e)[:200])
    # Run convergence engines after all scrapers complete
    conv = 0
    try:
        conv = run_convergence()
        total += conv
        log.info(f'Seller convergence: {conv} marks')
    except Exception as e:
        log.error(f'convergence crashed: {e}')
    try:
        cross = run_cross_side_convergence()
        total += cross
        log.info(f'Cross-side convergence: {cross} marks')
    except Exception as e:
        log.error(f'cross-side convergence crashed: {e}')
    # Refresh KPI cache — dashboard reads from cache, not live table
    try:
        cache_resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/pp_refresh_kpi_cache",
            headers={**HEADERS, 'Content-Type': 'application/json'},
            json={}, timeout=120
        )
        log.info(f'KPI cache refreshed — {cache_resp.status_code}')
    except Exception as e:
        log.warning(f'Cache refresh failed (non-critical): {e}')

    log.info(f'=== Done — {total} total signals (incl. {conv} convergence) ===')



if __name__ == '__main__':
    log.info(f'=== Premier Prospect v17 — KPI cache refresh, instant dashboard load — {len(SCRAPERS)} sources ===')
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
    # Refresh KPI cache — dashboard reads from cache, not live table
    try:
        cache_resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/pp_refresh_kpi_cache",
            headers={**HEADERS, 'Content-Type': 'application/json'},
            json={}, timeout=120
        )
        log.info(f'KPI cache refreshed — {cache_resp.status_code}')
    except Exception as e:
        log.warning(f'Cache refresh failed (non-critical): {e}')

    log.info(f'=== Done — {total} total signals (incl. {conv} convergence) ===')





# ═══════════════════════════════════════════════════════════════════════════
# BUYER INTELLIGENCE — GENERATION 1: FEDERAL & PUBLIC DATA APIS
# ═══════════════════════════════════════════════════════════════════════════
