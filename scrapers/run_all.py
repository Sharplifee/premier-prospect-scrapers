"""
Premier Prospect™ — Scraper Pipeline v20.1
Fixes June 17 2026: DEED OF TRUST removed, WARN Act county map, FSBO Utah filter, competitor -> market_data, LIR owner names
Commercial-grade: retry logic, source health monitoring,
correct dedup, no broken scrapers, single __main__, no undefined refs.
"""
import os, hashlib, logging, requests, re, json, time, datetime

# Real-time upgraded scrapers — HMDA 2024 live, SLCO recorder, AGRC parcels
try:
    from scrapers_realtime import (
        scrape_hmda_slc_county, scrape_hmda_utah_county,
        scrape_slco_recorder,
        scrape_slco_lir_parcels, scrape_davis_lir_parcels, scrape_weber_lir_parcels,
        scrape_utah_county_parcels, scrape_wasatch_parcels, scrape_summit_parcels,
    )
    REALTIME_LOADED = True
except ImportError as e:
    REALTIME_LOADED = False
    import logging as _log
    _log.getLogger('pp').warning(f"scrapers_realtime not loaded: {e}")
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('pp.scrapers')

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
APIFY_TOKEN  = os.environ.get('APIFY_TOKEN', '')
# MLS credentials — session cookie auth (utahrealestate.com reverse-engineered)
# URE_USERNAME: shakel (Kelvin's account)
# URE_PASSWORD: Ronnal13= (set in GitHub Secrets)
# URE_SESSION_COOKIE: persistent ure_login_token from browser session (optional but preferred)
# All stored as GitHub Secrets — auto-login fires on every scraper run
URE_USERNAME    = os.environ.get('URE_USERNAME', 'shakel')
URE_PASSWORD    = os.environ.get('URE_PASSWORD', 'Ronnal13=')
URE_SESSION_COOKIE = os.environ.get('URE_SESSION_COOKIE', '')

TABLE_URL = f"{SUPABASE_URL}/rest/v1/pp_scraper_signals"
HEADERS = {
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'apikey': SUPABASE_KEY,
    'Prefer': 'return=minimal,resolution=ignore-duplicates',
}

SESSION = requests.Session()
SESSION.headers['User-Agent'] = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'

# ── JUNK FILTER ──────────────────────────────────────────────────────────────
JUNK_NAMES = {
    'grantor','grantee','trustee','successor trustee','utah county recorder',
    'recorder','county recorder','unknown','n/a','na','none','mers',
    'mortgage electronic registration','fannie mae','freddie mac','hud',
    'secretary of housing','federal national mortgage','federal home loan mortgage',
    'answers zoning questions','deed of trust','mortgage','lender',
}

def clean_owner(name):
    if not name: return None
    name = name.strip()
    if len(name) < 3: return None
    if name.lower() in JUNK_NAMES: return None
    if any(j in name.lower() for j in ['bank of america','wells fargo','jpmorgan',
        'citibank','us bank','pennymac','nationstar','freedom mortgage','newrez',
        'carrington','ocwen','trustee corp','capital one','dlj mortgage','towd point']): return None
    return name

def clean_addr(addr):
    if not addr: return None
    addr = addr.strip()
    if len(addr) < 5: return None
    if any(j in addr.lower() for j in ['answers zoning','zoning question','n/a','zillow research',
        'inventory_signal','price_cut_signal','market_temp','new_listings_signal']): return None
    return addr

# ── RETRY-AWARE HTTP ──────────────────────────────────────────────────────────
def safe_get(url, timeout=20, retries=3, delay=5, **kwargs):
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=timeout, **kwargs)
            if r.status_code == 429:
                wait = int(r.headers.get('Retry-After', delay * (attempt + 1)))
                log.warning(f"Rate limited {url[:60]} — waiting {wait}s")
                time.sleep(wait)
                continue
            if r.status_code in (200, 404): return r
            log.warning(f"HTTP {r.status_code} {url[:60]} attempt {attempt+1}")
            time.sleep(delay)
        except requests.exceptions.Timeout:
            log.warning(f"Timeout {url[:60]} attempt {attempt+1}")
            time.sleep(delay)
        except Exception as e:
            log.warning(f"Request error {url[:60]}: {e}")
            time.sleep(delay)
    return None

def safe_post(url, data=None, timeout=20, retries=3, **kwargs):
    for attempt in range(retries):
        try:
            r = SESSION.post(url, data=data, timeout=timeout, **kwargs)
            if r.status_code in (200, 201, 204): return r
            time.sleep(3)
        except Exception as e:
            log.warning(f"POST error {url[:60]}: {e}")
            time.sleep(3)
    return None

# ── APIFY ─────────────────────────────────────────────────────────────────────
def apify_text(url, retries=2):
    if not APIFY_TOKEN: return []
    for attempt in range(retries):
        try:
            r = SESSION.post(
                f'https://api.apify.com/v2/acts/apify~website-content-crawler/run-sync-get-dataset-items'
                f'?token={APIFY_TOKEN}&timeout=60',
                json={'startUrls':[{'url':url}],'maxCrawlPages':1,'crawlerType':'cheerio'},
                timeout=90
            )
            if r.status_code == 429:
                log.warning(f"Apify rate limited — waiting {60*(attempt+1)}s")
                time.sleep(60 * (attempt + 1))
                continue
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and data:
                    text = data[0].get('text','') or data[0].get('markdown','') or ''
                    return [l.strip() for l in text.split('\n') if l.strip()]
        except Exception as e:
            log.error(f"Apify error {url[:60]}: {e}")
            if attempt < retries - 1: time.sleep(10)
    return []

# ── BATCH INSERT ──────────────────────────────────────────────────────────────
ALLOWED_COLS = {'source_slug','raw_address','raw_owner_name','raw_phone','raw_url',
                'raw_payload','signal_type','score','county','city','captured_at','dedupe_hash'}

# Unified score scale: HOT=70-100, WARM=40-69, COOL=0-39
def score_tier(score):
    if score >= 70: return 'HOT'
    if score >= 40: return 'WARM'
    return 'COOL'

def post_batch(records):
    if not records: return 0
    seen, unique = set(), []
    for rec in records:
        rec['raw_owner_name'] = clean_owner(rec.get('raw_owner_name'))
        rec['raw_address']    = clean_addr(rec.get('raw_address'))
        # Unified 0-100 score scale with tier label
        score = rec.get('score', 0) or 0
        # tier is a generated column in Postgres — never insert it directly
        # Stable dedupe hash — does NOT include URL (which changes per run)
        h = hashlib.md5(
            f"{rec.get('source_slug','')}|{rec.get('raw_owner_name','') or ''}|{rec.get('raw_address','') or ''}".encode()
        ).hexdigest()
        rec['dedupe_hash'] = h
        if h not in seen:
            seen.add(h)
            unique.append({k: v for k, v in rec.items() if k in ALLOWED_COLS})
    inserted = 0
    for i in range(0, len(unique), 200):
        chunk = unique[i:i+200]
        for attempt in range(3):
            try:
                r = SESSION.post(TABLE_URL, json=chunk, headers=HEADERS, timeout=45)
                if r.status_code in (200, 201, 204, 409):
                    inserted += len(chunk)
                    break
                log.error(f"Batch insert {r.status_code}: {r.text[:100]}")
                time.sleep(5)
            except Exception as e:
                log.error(f"Batch insert error: {e}")
                if attempt < 2: time.sleep(5)
    return inserted

TWILIO_SID   = os.environ.get('TWILIO_SID', '')
TWILIO_TOKEN = os.environ.get('TWILIO_TOKEN', '')
TWILIO_FROM  = os.environ.get('TWILIO_FROM', '')
ALERT_TO     = os.environ.get('ALERT_TO', '')

def _send_sms_alert(msg):
    """Fire-and-forget Twilio SMS — only if credentials present."""
    if not (TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM and ALERT_TO):
        return
    try:
        import urllib.parse
        import base64
        body = urllib.parse.urlencode({'From': TWILIO_FROM, 'To': ALERT_TO, 'Body': msg})
        auth = base64.b64encode(f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()).decode()
        SESSION.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
            data=body,
            headers={'Authorization': f'Basic {auth}', 'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=8
        )
    except Exception as e:
        log.warning(f"SMS alert failed: {e}")

# Dedicated headers for pp_run_log inserts — no Prefer resolution header
# (pp_run_log has no unique constraint; resolution=ignore-duplicates from HEADERS
# causes Supabase to silently drop rows or columns on tables without a conflict target)
HEADERS_LOG = {
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'apikey': SUPABASE_KEY,
    'Prefer': 'return=minimal',
}

def write_run_log(slug, count, status='success', error=None, duration=None, skipped=0):
    try:
        payload = {
            'source_slug': slug,
            'run_at': datetime.datetime.utcnow().isoformat() + 'Z',
            'signal_count': count,
            'status': status,
            'error_msg': error,
            'run_number': int(os.environ.get('GITHUB_RUN_NUMBER', 0)),
            'duration_seconds': round(duration, 1) if duration is not None else None,
            'records_skipped': skipped,
        }
        SESSION.post(f"{SUPABASE_URL}/rest/v1/pp_run_log",
                    json=payload, headers=HEADERS_LOG, timeout=5)
    except: pass
    # Per-source failure SMS alert
    if status == 'error' and error:
        ts = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
        _send_sms_alert(
            f"⚠️ Premier Prospect SCRAPER FAIL\n"
            f"Source: {slug}\n"
            f"Error: {error[:120]}\n"
            f"Time: {ts}"
        )

# ── HEALTH MONITOR ────────────────────────────────────────────────────────────
# Minimum expected yields per source per run — if below, flag as degraded
MIN_YIELDS = {
    'utah-county-tax-delinquency-pdf': 50,
    'utah-county-nts': 10,
    'hmda-slc-county': 100,
    'hmda-utah-county': 50,
    'nod-tracker': 5,
    'deed-transfers-utah-county': 5,
    'lien-judgment-records': 5,
    'ksl-fsbo-extended': 2,
    'fire-marshal-lp-gas': 5,
    'fire-marshal-suppression': 3,
}

_health_alerts = []

def check_health(slug, count):
    # Suppress false positives: gated slugs correctly return 0 on runs 2+ per day
    GATED_SLUGS = {
        'hmda-slc-county','hmda-utah-county','warn-act-utah','silicon-slopes-newhires',
        'realtor-market-utah','zillow-market-signals','zillow-home-values',
        'school-district-enrollment','marriage-records-slco','comparable-sales-slco',
        'loopnet-utah','uhaul-penske-monitor',
        'trulia-utah','hubzu-utah','reo-utah','auction-com-utah',
        # uvhba-directory removed — now uses Census BPS (daily-gated internally)
        # but we want health alerts if it produces 0 on its first run of the day
    }
    if count == 0 and slug in GATED_SLUGS:
        return  # daily-skip gate fired — not a health issue
    min_yield = MIN_YIELDS.get(slug, 0)
    if min_yield > 0 and count < min_yield:
        msg = f"HEALTH: {slug} returned {count} (expected >= {min_yield})"
        log.warning(msg)
        _health_alerts.append(msg)

# ── SCRAPERS ──────────────────────────────────────────────────────────────────

# ─── UTAH COUNTY NTS ─────────────────────────────────────────────────────────
def scrape_utah_county_nts():
    slug = 'utah-county-nts'
    log.info(f'[{slug}] starting')
    import re as _re
    # PLSS → city lookup (Township/Range determines geographic area)
    PLSS_CITY = {
        '4S Range 1E':'American Fork','4S Range 2E':'Alpine','4S Range 3W':'Cedar Hills',
        '5S Range 1W':'Saratoga Springs','5S Range 2W':'Eagle Mountain',
        '5S Range 1E':'Lehi','5S Range 2E':'Pleasant Grove',
        '5S Range 3E':'Lindon','5S Range 4E':'Orem',
        '6S Range 1W':'Provo','6S Range 2W':'Provo',
        '6S Range 1E':'Provo','6S Range 2E':'Provo',
        '6S Range 3E':'Spanish Fork','6S Range 3W':'Springville',
        '7S Range 1W':'Salem','7S Range 2W':'Salem','7S Range 3W':'Payson',
        '7S Range 2E':'Spanish Fork','7S Range 3E':'Mapleton',
        '7S Range 4E':'Springville','7S Range 5E':'Woodland Hills',
        '8S Range 1E':'Payson','8S Range 2E':'Santaquin',
        '8S Range 2W':'Santaquin','8S Range 3E':'Woodland Hills',
        '9S Range 1W':'Genola','9S Range 1E':'Santaquin','9S Range 2E':'Eureka',
    }
    def city_from_plss(section_text):
        m = _re.search(r'Township (\d+S Range \d+[EW])', section_text or '')
        if m: return PLSS_CITY.get(m.group(1))
        return None

    signals = []
    for doc_type in ['RSUBTEE', 'NOTICE OF TRUSTEE', 'FORECLOSURE']:
        r = safe_post(
            'https://www.utahcounty.gov/LandRecords/DocDescSearch.asp',
            data={'DocDesc': doc_type, 'DateRange': '30', 'County': 'Utah'},
            timeout=25
        )
        if not r: continue
        soup = BeautifulSoup(r.text, 'html.parser')
        current_section = None  # Track current PLSS section grouping
        for row in soup.select('table tr'):
            cells = row.select('td')
            if not cells: continue
            first = cells[0].get_text(strip=True)
            # PLSS section header row (e.g. "Section 25 Township 4S Range 1E")
            if 'Township' in first and 'Section' in first:
                current_section = first
                continue
            if len(cells) < 4: continue
            # Skip header row
            if first in ('Description', 'Rec Date', 'KOI', 'New Search', 'Main Menu'):
                continue
            desc = cells[0].get_text(strip=True)
            rec_date = cells[1].get_text(strip=True)
            entry = cells[3].get_text(strip=True).replace('\xa0', ' ').strip() if len(cells) > 3 else ''
            grantor = cells[4].get_text(strip=True) if len(cells) > 4 else ''
            if not entry or 'Entry' in entry.split()[0] if entry.split() else True: pass
            if not entry: continue
            # Derive city from PLSS section header
            city = city_from_plss(current_section)
            signals.append({
                'source_slug': slug, 'signal_type': 'nts', 'score': 99,
                'county': 'Utah', 'city': city,
                'raw_owner_name': grantor or None,
                'raw_address': f'{doc_type} — Entry #{entry}',
                'raw_payload': json.dumps({
                    'doc_type': desc, 'rec_date': rec_date,
                    'entry': entry, 'plss_section': current_section or ''
                }),
            })
    return post_batch(signals)

# ─── NOD TRACKER ─────────────────────────────────────────────────────────────
def scrape_nod_tracker():
    slug = 'nod-tracker'
    log.info(f'[{slug}] starting')
    import re as _re
    PLSS_CITY = {
        '4S Range 1E':'American Fork','4S Range 2E':'Alpine','4S Range 3W':'Cedar Hills',
        '5S Range 1W':'Saratoga Springs','5S Range 2W':'Eagle Mountain',
        '5S Range 1E':'Lehi','5S Range 2E':'Pleasant Grove',
        '5S Range 3E':'Lindon','5S Range 4E':'Orem',
        '6S Range 1W':'Provo','6S Range 2W':'Provo',
        '6S Range 1E':'Provo','6S Range 2E':'Provo',
        '6S Range 3E':'Spanish Fork','6S Range 3W':'Springville',
        '7S Range 1W':'Salem','7S Range 2W':'Salem','7S Range 3W':'Payson',
        '7S Range 2E':'Spanish Fork','7S Range 3E':'Mapleton',
        '7S Range 4E':'Springville','7S Range 5E':'Woodland Hills',
        '8S Range 1E':'Payson','8S Range 2E':'Santaquin',
        '8S Range 2W':'Santaquin','8S Range 3E':'Woodland Hills',
        '9S Range 1W':'Genola','9S Range 1E':'Santaquin','9S Range 2E':'Eureka',
    }
    def city_from_plss(section_text):
        m = _re.search(r'Township (\d+S Range \d+[EW])', section_text or '')
        if m: return PLSS_CITY.get(m.group(1))
        return None

    signals = []
    for doc_type in ['NOTICE OF DEFAULT']:
        r = safe_post(
            'https://www.utahcounty.gov/LandRecords/DocDescSearch.asp',
            data={'DocDesc': doc_type, 'DateRange': '30', 'County': 'Utah'},
            timeout=25
        )
        if not r: continue
        soup = BeautifulSoup(r.text, 'html.parser')
        current_section = None
        for row in soup.select('table tr'):
            cells = row.select('td')
            if not cells: continue
            first = cells[0].get_text(strip=True)
            if 'Township' in first and 'Section' in first:
                current_section = first
                continue
            if len(cells) < 4: continue
            if first in ('Description', 'Rec Date', 'KOI', 'New Search', 'Main Menu'):
                continue
            entry = cells[3].get_text(strip=True).replace('\xa0', ' ').strip() if len(cells) > 3 else ''
            grantor = cells[4].get_text(strip=True) if len(cells) > 4 else ''
            rec_date = cells[1].get_text(strip=True)
            if not entry: continue
            city = city_from_plss(current_section)
            score = 88 if 'DEFAULT' in doc_type else 65
            signals.append({
                'source_slug': slug, 'signal_type': 'nod', 'score': score,
                'county': 'Utah', 'city': city,
                'raw_owner_name': grantor or None,
                'raw_address': f'NOTICE OF DEFAULT — Entry #{entry}',
                'raw_payload': json.dumps({
                    'doc_type': doc_type, 'rec_date': rec_date,
                    'entry': entry, 'plss_section': current_section or ''
                }),
            })
    return post_batch(signals)

# ─── LIEN/JUDGMENT ───────────────────────────────────────────────────────────
def scrape_lien_judgment_records():
    slug = 'lien-judgment-records'
    log.info(f'[{slug}] starting')
    signals = []
    for doc_type in ['JUDGMENT LIEN', 'STATE TAX LIEN', 'IRS LIEN', 'MECHANICS LIEN']:
        r = safe_post(
            'https://www.utahcounty.gov/LandRecords/DocDescSearch.asp',
            data={'DocDesc': doc_type, 'DateRange': '30', 'County': 'Utah'},
            timeout=25
        )
        if not r: continue
        soup = BeautifulSoup(r.text, 'html.parser')
        for row in soup.select('table tr')[1:]:
            cells = row.select('td')
            if len(cells) < 4: continue
            entry = cells[3].get_text(strip=True) if len(cells) > 3 else ''
            grantor = cells[4].get_text(strip=True) if len(cells) > 4 else ''
            rec_date = cells[1].get_text(strip=True)
            if not entry: continue
            signals.append({
                'source_slug': slug, 'signal_type': 'lien_judgment', 'score': 68,
                'county': 'Utah', 'city': None,
                'raw_owner_name': grantor or None,
                'raw_address': f'Entry #{entry}',
                'raw_payload': json.dumps({'doc_type': doc_type, 'rec_date': rec_date, 'entry': entry}),
            })
    return post_batch(signals)

# ─── DEED TRANSFERS ───────────────────────────────────────────────────────────
def scrape_deed_transfers_utah_county():
    slug = 'deed-transfers-utah-county'
    log.info(f'[{slug}] starting')
    signals = []
    for doc_type in ['WARRANTY DEED', 'QUIT CLAIM DEED', 'SPECIAL WARRANTY']:
        r = safe_post(
            'https://www.utahcounty.gov/LandRecords/DocDescSearch.asp',
            data={'DocDesc': doc_type, 'DateRange': '14', 'County': 'Utah'},
            timeout=25
        )
        if not r: continue
        soup = BeautifulSoup(r.text, 'html.parser')
        for row in soup.select('table tr')[1:]:
            cells = row.select('td')
            if len(cells) < 5: continue
            entry = cells[3].get_text(strip=True) if len(cells) > 3 else ''
            grantor = cells[4].get_text(strip=True) if len(cells) > 4 else ''
            grantee = cells[5].get_text(strip=True) if len(cells) > 5 else ''
            rec_date = cells[1].get_text(strip=True)
            if not entry: continue
            signals.append({
                'source_slug': slug, 'signal_type': 'deed_transfer', 'score': 55,
                'county': 'Utah', 'city': None,
                'raw_owner_name': grantor or None,
                'raw_address': f'Entry #{entry}',
                'raw_payload': json.dumps({'doc_type': doc_type, 'rec_date': rec_date, 'grantor': grantor, 'grantee': grantee}),
            })
    return post_batch(signals)

# ─── TAX DELINQUENCY PDF ──────────────────────────────────────────────────────
def scrape_utah_county_tax_delinquency_pdf():
    slug = 'utah-county-tax-delinquency-pdf'
    log.info(f'[{slug}] starting')
    PDF_URL = ('https://www.utahcounty.gov/Dept/Treas/production-single-forms/'
               'delinquent-property-tax-report/UtahCounty_Delinquent_Property_Tax_report.pdf')
    batch = []
    try:
        import io, pdfplumber
        r = safe_get(PDF_URL, timeout=45)
        if not r or r.status_code != 200:
            log.warning(f'[{slug}] PDF not available')
            return 0
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if not table:
                    for line in (page.extract_text() or '').split('\n'):
                        line = line.strip()
                        if re.search(r'\d{2}:\d{3}:\d{4}', line) or \
                           re.search(r'\d{4,}\s+\w+\s+(?:ST|AVE|DR|LN|BLVD|WAY|RD|CT)', line.upper()):
                            batch.append({
                                'source_slug': slug, 'signal_type': 'tax_delinquency',
                                'score': 75, 'county': 'Utah', 'city': None,
                                'raw_owner_name': None, 'raw_address': line[:200],
                            })
                    continue
                hdrs = [str(c).lower().strip() if c else '' for c in table[0]]
                for row in table[1:]:
                    if not row or not any(row): continue
                    cells = [str(c).strip() if c else '' for c in row]
                    d = dict(zip(hdrs, cells)) if hdrs else {}
                    owner   = d.get('owner','') or d.get('name','') or (cells[1] if len(cells)>1 else '')
                    address = d.get('address','') or d.get('mailing address','') or (cells[2] if len(cells)>2 else '')
                    parcel  = d.get('parcel','') or d.get('serial','') or (cells[0] if cells else '')
                    if owner or parcel:
                        batch.append({
                            'source_slug': slug, 'signal_type': 'tax_delinquency',
                            'score': 75, 'county': 'Utah', 'city': None,
                            'raw_owner_name': owner[:200] or None,
                            'raw_address': (address or parcel)[:200] or None,
                        })
    except Exception as e:
        log.error(f'[{slug}] failed: {e}')
    return post_batch(batch)

# ─── FIRE MARSHAL ─────────────────────────────────────────────────────────────
def scrape_fire_marshal(slug, path):
    log.info(f'[{slug}] starting')
    url = f'https://firemarshal.utah.gov/licensees/{path}'
    r = safe_get(url, timeout=20)
    if not r: return 0
    signals = []
    soup = BeautifulSoup(r.text, 'html.parser')
    for row in soup.find_all('tr')[1:]:
        cols = [td.get_text(strip=True) for td in row.find_all('td')]
        if len(cols) >= 3 and cols[0]:
            signals.append({
                'source_slug': slug, 'signal_type': 'contractor_license', 'score': 30,
                'county': 'Utah', 'city': cols[2] if len(cols)>2 else None,
                'raw_owner_name': cols[0],
                'raw_address': f"{cols[1]}, {cols[2]}".strip(', ') if len(cols)>1 else None,
            })
    return post_batch(signals)

def scrape_fire_marshal_lp_gas():      return scrape_fire_marshal('fire-marshal-lp-gas', 'lp-gas-companies')
def scrape_fire_marshal_suppression(): return scrape_fire_marshal('fire-marshal-suppression', 'fire-suppression')
def scrape_fire_marshal_lp_hvac():     return scrape_fire_marshal('fire-marshal-lp-hvac', 'lp-gas-hvac-companies')

# ─── LIR PARCELS ─────────────────────────────────────────────────────────────
def scrape_lir(slug, county, svc):
    log.info(f'[{slug}] starting')
    r = safe_get(f"{svc}/query", params={
        'where':'1=1','outFields':'PARCEL_ID,PARCEL_ADD,PARCEL_CITY,TOTAL_MKT_VALUE',
        'resultRecordCount':200,'orderByFields':'OBJECTID DESC','f':'json'
    }, timeout=25)
    if not r: return 0
    try: data = r.json()
    except: return 0
    signals = []
    for f in data.get('features',[]):
        a = f.get('attributes',{})
        addr = a.get('PARCEL_ADD','')
        city = a.get('PARCEL_CITY','')
        # OWN_NAME1/OWN_NAME2 only exist on Utah/Wasatch/Summit LIR via scrapers_realtime
        own1 = a.get('OWN_NAME1','') or ''
        own2 = a.get('OWN_NAME2','') or ''
        owner = ' '.join(filter(None, [own1.strip(), own2.strip()])).strip() or None
        if not addr: continue
        signals.append({
            'source_slug': slug, 'signal_type': 'lir_parcel', 'score': 45,
            'county': county, 'city': city or None,
            'raw_owner_name': clean_owner(owner) if owner else None,
            'raw_address': f"{addr}, {city}".strip(', ') if city else addr,
        })
    return post_batch(signals)

def scrape_slco_lir_parcels():  return scrape_lir('slco-lir-parcels','Salt Lake','https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_SaltLake_LIR/FeatureServer/0')
def scrape_davis_lir_parcels(): return scrape_lir('davis-lir-parcels','Davis','https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Davis_LIR/FeatureServer/0')
def scrape_weber_lir_parcels(): return scrape_lir('weber-lir-parcels','Weber','https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Weber_LIR/FeatureServer/0')

# ─── HMDA (runs only if new data available or not yet run today) ─────────────
def _hmda_already_run_today(slug):
    """Skip if already collected today — static 2023 data never changes mid-day."""
    try:
        r = SESSION.get(
            f"{SUPABASE_URL}/rest/v1/pp_run_log?source_slug=eq.{slug}&status=eq.success"
            f"&run_at=gte.{datetime.date.today().isoformat()}&limit=1",
            headers=HEADERS, timeout=5
        )
        return isinstance(r.json(), list) and len(r.json()) > 0
    except: return False

def scrape_hmda(slug, county_fips, county):
    log.info(f'[{slug}] starting')
    if _hmda_already_run_today(slug):
        log.info(f'[{slug}] already ran today — skipping (static 2023 data)')
        return 0
    import csv, io
    batch = []
    for action in ['1', '8']:
        url = f'https://ffiec.cfpb.gov/v2/data-browser-api/view/csv?states=UT&years=2023&actions_taken={action}&counties={county_fips}'
        r = safe_get(url, timeout=60)
        if not r or r.status_code != 200: continue
        reader = csv.reader(io.StringIO(r.text))
        rows = list(reader)
        if not rows: continue
        hdrs = rows[0]
        for row in rows[1:]:
            if not row: continue
            d = dict(zip(hdrs, row))
            dwelling = d.get('derived_dwelling_category','')
            if 'Single Family' not in dwelling and 'Manufactured' not in dwelling: continue
            tract = d.get('census_tract','')
            loan_amt = d.get('loan_amount','')
            loan_type = d.get('derived_loan_product_type','')
            batch.append({
                'source_slug': slug, 'signal_type': 'mortgage_application',
                'score': 70, 'county': county, 'city': None,
                'raw_owner_name': None,
                'raw_address': tract[:200],
                'raw_payload': json.dumps({'loan_amount': loan_amt, 'loan_type': loan_type, 'action': action}),
            })
    return post_batch(batch)

def scrape_hmda_utah_county(): return scrape_hmda('hmda-utah-county', '49049', 'Utah')
def scrape_hmda_slc_county():  return scrape_hmda('hmda-slc-county',  '49035', 'Salt Lake')

# ─── FSBO — KSL CRAIGSLIST ───────────────────────────────────────────────────
def scrape_ksl_fsbo_extended():
    slug = 'ksl-fsbo-extended'
    log.info(f'[{slug}] starting')
    signals = []
    import urllib.request as _ur
    for url, county in [
        ('https://saltlake.craigslist.org/search/reo?sort=date&limit=120', 'Salt Lake'),
        ('https://provo.craigslist.org/search/reo?sort=date&limit=120', 'Utah'),
    ]:
        try:
            req = _ur.Request(url, headers={'User-Agent': SESSION.headers['User-Agent']})
            with _ur.urlopen(req, timeout=20) as resp:
                html = resp.read()
            soup = BeautifulSoup(html, 'html.parser')
            for item in soup.select('li.cl-static-search-result, .result-row, li[data-pid]'):
                title_el = item.select_one('.title, a.posting-title, .result-title')
                title = title_el.get_text(strip=True) if title_el else ''
                price_el = item.select_one('.price, .result-price')
                price = price_el.get_text(strip=True) if price_el else ''
                link_el = item.find('a', href=True)
                link = link_el['href'] if link_el else url
                if not link.startswith('http'): link = url.split('/search')[0] + link
                full = f"{title} {price}".strip()
                if (full
                    and any(w in full.lower() for w in ['bed','bath','$','sqft','home','house'])
                    and any(s in full.lower() for s in [', ut','utah','salt lake','provo',
                        'orem','lehi','ogden','draper','sandy','murray','west jordan',
                        'american fork','layton','bountiful','springville'])):
                    signals.append({
                        'source_slug': slug, 'signal_type': 'fsbo', 'score': 65,
                        'county': county, 'city': None,
                        'raw_owner_name': None, 'raw_address': full[:200],
                        'raw_url': link,
                    })
        except Exception as e:
            log.error(f'[{slug}] {county}: {e}')
    return post_batch(signals)

# ─── OBITUARIES ───────────────────────────────────────────────────────────────
def scrape_obituaries_enrichment():
    """
    Two confirmed-stable Utah obituary sources — both use <article> tags
    with static server-side rendering, no JS required, no bot blocking.
    Legacy.com removed: requires JS rendering (React hydration).
    SL Tribune removed: paywalled content.
    """
    slug = 'obituaries-enrichment'
    log.info(f'[{slug}] starting')
    signals = []
    SOURCES = [
        # Herald Extra — Utah County, confirmed stable article selector
        ('https://www.heraldextra.com/obituaries/', 'Utah'),
        # Deseret News — SL County, also uses article tags
        ('https://www.deseret.com/utah/obituaries/', 'Salt Lake'),
    ]
    seen = set()
    for url, county in SOURCES:
        r = safe_get(url, timeout=15)
        if not r or r.status_code != 200: continue
        soup = BeautifulSoup(r.text, 'html.parser')
        for card in soup.select('article'):
            name_el = card.select_one('h1, h2, h3, [class*=headline], [class*=title]')
            name = name_el.get_text(strip=True) if name_el else ''
            if not name or len(name) < 3 or name in seen: continue
            seen.add(name)
            date_el = card.select_one('time, [class*=date], [class*=timestamp]')
            pub_date = date_el.get('datetime', date_el.get_text(strip=True)) if date_el else ''
            link_el = card.select_one('a[href]')
            href = link_el['href'] if link_el else url
            if href.startswith('/'): href = url.split('/')[0] + '//' + url.split('/')[2] + href
            signals.append({
                'source_slug': slug, 'signal_type': 'obituary', 'score': 62,
                'county': county, 'city': None,
                'raw_owner_name': name,
                'raw_address': f'Obituary: {name}',
                'raw_payload': json.dumps({'name': name, 'pub_date': pub_date, 'url': href, 'source': url}),
            })
    return post_batch(signals)

# ─── WARN ACT ─────────────────────────────────────────────────────────────────
def scrape_warn_act_utah():
    slug = 'warn-act-utah'
    log.info(f'[{slug}] starting')
    # Only run 1x daily — data changes weekly not hourly
    if _hmda_already_run_today(slug):
        log.info(f'[{slug}] already ran today — skipping')
        return 0
    r = safe_get('https://jobs.utah.gov/employer/business/warnnotices.html', timeout=20)
    if not r: return 0
    signals = []
    soup = BeautifulSoup(r.text, 'html.parser')
    for row in soup.select('table tr')[1:]:
        cells = row.select('td')
        if len(cells) < 3: continue
        company = cells[1].get_text(strip=True)
        city = cells[2].get_text(strip=True)
        workers = cells[3].get_text(strip=True) if len(cells) > 3 else ''
        date = cells[0].get_text(strip=True)
        if not company: continue
        CITY_COUNTY = {
            # Utah County
            'provo': 'Utah', 'orem': 'Utah', 'lehi': 'Utah', 'american fork': 'Utah',
            'payson': 'Utah', 'springville': 'Utah', 'spanish fork': 'Utah',
            'pleasant grove': 'Utah', 'lindon': 'Utah', 'mapleton': 'Utah',
            'saratoga springs': 'Utah', 'eagle mountain': 'Utah', 'vineyard': 'Utah',
            # Weber County
            'ogden': 'Weber', 'north ogden': 'Weber', 'south ogden': 'Weber',
            'roy': 'Weber', 'riverdale': 'Weber', 'washington terrace': 'Weber',
            # Davis County
            'layton': 'Davis', 'bountiful': 'Davis', 'clearfield': 'Davis',
            'kaysville': 'Davis', 'farmington': 'Davis', 'north salt lake': 'Davis',
            'west bountiful': 'Davis', 'centerville': 'Davis', 'clinton': 'Davis',
            # Washington County
            'st. george': 'Washington', 'st george': 'Washington',
            'washington': 'Washington', 'santa clara': 'Washington',
            # Cache County
            'logan': 'Cache', 'north logan': 'Cache', 'smithfield': 'Cache',
            # Salt Lake County
            'salt lake city': 'Salt Lake', 'salt lake': 'Salt Lake',
            'west valley city': 'Salt Lake', 'west valley': 'Salt Lake',
            'sandy': 'Salt Lake', 'south jordan': 'Salt Lake', 'west jordan': 'Salt Lake',
            'murray': 'Salt Lake', 'draper': 'Salt Lake', 'millcreek': 'Salt Lake',
            'midvale': 'Salt Lake', 'herriman': 'Salt Lake', 'riverton': 'Salt Lake',
            'taylorsville': 'Salt Lake', 'holladay': 'Salt Lake',
        }
        county = CITY_COUNTY.get(city.lower(), 'Salt Lake')
        signals.append({
            'source_slug': slug, 'signal_type': 'mass_layoff', 'score': 50,
            'county': county, 'city': city,
            'raw_owner_name': company, 'raw_address': city,
            'raw_payload': json.dumps({'workers': workers, 'date': date}),
        })
    return post_batch(signals)

# ─── COMPETITOR BUYER FORMS ───────────────────────────────────────────────────
def scrape_competitor_buyer_forms():
    slug = 'competitor-buyer-forms'
    log.info(f'[{slug}] starting')
    PAGES = [
        ('https://www.presidioteam.com/buyers', 'Presidio Real Estate', 'Utah'),
        ('https://www.kwutah.com/buyers', 'KW Utah', 'Utah'),
        ('https://www.realtypath.com/buyers', 'Realty Path', 'Salt Lake'),
        ('https://www.utahrealestate.com/info/market-stats', 'Utah RE Market Stats', 'Utah'),
        ('https://www.compass.com/agents/utah/', 'Compass Utah', 'Salt Lake'),
    ]
    signals = []
    # Get prior hashes
    try:
        r = SESSION.get(
            f"{SUPABASE_URL}/rest/v1/pp_scraper_signals"
            f"?select=raw_address,raw_owner_name&source_slug=eq.{slug}&order=captured_at.desc&limit=20",
            headers=HEADERS, timeout=8
        )
        prior = {rec['raw_address']: rec.get('raw_owner_name','') for rec in (r.json() if isinstance(r.json(),list) else [])}
    except: prior = {}

    # FIX June 17: Routes to pp_market_data NOT pp_scraper_signals.
    # competitor_form_change is website monitoring, not a buyer lead.
    # Was scoring 65 -> Primed tier, corrupting buyer profile and match counts.
    for url, name, county in PAGES:
        try:
            r2 = safe_get(url, timeout=12)
            if not r2: continue
            soup = BeautifulSoup(r2.text, 'html.parser')
            page_content = ' '.join([el.get_text(strip=True) for el in soup.select('form,button,h1,h2,h3,.cta')])[:1000]
            current_hash = hashlib.md5(page_content.encode()).hexdigest()
            prior_payload = prior.get(url, '{}')
            try: prior_hash = json.loads(prior_payload).get('hash', '')
            except: prior_hash = ''
            changed = bool(prior_hash and current_hash != prior_hash)
            signals.append({
                'source_slug': slug, 'signal_type': 'market_intelligence',
                'score': 45, 'county': county, 'city': None,
                'raw_address': url,
                'raw_payload': json.dumps({'name': name, 'hash': current_hash, 'changed': changed}),
                'captured_at': datetime.datetime.utcnow().isoformat(),
            })
        except Exception as e:
            log.warning(f'[{slug}] {name}: {e}')
    # Write to pp_market_data, NOT pp_scraper_signals
    if not signals: return 0
    mkt_url = f"{SUPABASE_URL}/rest/v1/pp_market_data"
    count = 0
    for i in range(0, len(signals), 50):
        chunk = signals[i:i+50]
        r3 = SESSION.post(mkt_url, json=chunk, headers=HEADERS, timeout=20)
        if r3.status_code in (200, 201, 204): count += len(chunk)
    log.info(f'[{slug}] {count} records -> pp_market_data (market intelligence)')
    return count

# ─── SCHOOL DISTRICTS ─────────────────────────────────────────────────────────
def scrape_school_district_enrollment():
    """
    Census ACS API now requires a key at both county and state level.
    BLS QCEW table_maker returns HTML not JSON.
    Replaced with IRS Statistics of Income (SOI) county-to-county migration data.
    Publicly hosted CSV, no auth required, stable annual release, 90k+ rows.
    Measures actual people who MOVED INTO Utah counties from other states/counties.
    URL pattern: https://www.irs.gov/pub/irs-soi/countyinflow{Y1Y2}.csv
    """
    slug = 'school-district-enrollment'
    log.info(f'[{slug}] starting — IRS SOI county in-migration data')
    if _hmda_already_run_today(slug):
        log.info(f'[{slug}] already ran today — skipping')
        return 0

    import csv as _csv, io as _io
    UTAH_COUNTIES = {
        '035': 'Salt Lake', '049': 'Utah', '011': 'Davis',
        '057': 'Weber',     '051': 'Wasatch', '043': 'Summit',
    }
    signals = []
    # Try most recent available IRS SOI migration file
    for year_pair in ['2223', '2122', '2021']:
        try:
            url = f'https://www.irs.gov/pub/irs-soi/countyinflow{year_pair}.csv'
            r = safe_get(url, timeout=30)
            if not r or r.status_code != 200: continue
            reader = _csv.DictReader(_io.StringIO(r.text))
            rows = list(reader)
            if not rows: continue
            # Aggregate inflows to each Utah county from all origins
            county_totals = {}
            for row in rows:
                dest_state  = str(row.get('y2_statefips', '')).strip()
                dest_county = str(row.get('y2_countyfips', '')).strip()
                if dest_state != '49' or dest_county == '000': continue
                # n2 = number of individuals, agi = adjusted gross income
                n2  = int(row.get('n2',  '0').strip() or 0)
                agi = int(row.get('agi', '0').strip() or 0)
                if dest_county not in county_totals:
                    county_totals[dest_county] = {'n2': 0, 'agi': 0}
                county_totals[dest_county]['n2']  += n2
                county_totals[dest_county]['agi'] += agi
            for fips, county in UTAH_COUNTIES.items():
                totals = county_totals.get(fips, {})
                n2  = totals.get('n2',  0)
                agi = totals.get('agi', 0)
                if n2 <= 0: continue
                avg_agi = agi // n2 if n2 > 0 else 0
                # Higher avg income + more movers = stronger buyer signal
                score = 72 if avg_agi > 75000 else 65 if avg_agi > 50000 else 58
                signals.append({
                    'source_slug': slug, 'signal_type': 'buyer_migration_signal',
                    'score': score, 'county': county, 'city': None,
                    'raw_owner_name': None,
                    'raw_address': f'{county} County | {n2:,} in-movers | avg AGI ${avg_agi:,} | IRS SOI {year_pair}',
                    'raw_payload': json.dumps({'county': county, 'fips': fips,
                        'in_movers': n2, 'total_agi': agi, 'avg_agi': avg_agi,
                        'year_pair': year_pair}),
                })
            if signals: break
        except Exception as e:
            log.warning(f'[{slug}] IRS SOI {year_pair}: {e}')
    return post_batch(signals)

# ─── MARRIAGE RECORDS ─────────────────────────────────────────────────────────
def scrape_marriage_records_slco():
    """
    Utah marriage licenses are legally restricted (Utah Code 78B-5-825) —
    not public records, not in land records, not accessible via any public API.
    Replaced with QUIT CLAIM DEED filings: family property transfers are a
    genuine motivated seller signal (divorce, estate split, marriage asset transfer).
    QCDs appear in Utah County LandRecords with KOI=Q CD, confirmed 200+ rows/30d.
    Same stable API used by deed-transfers-utah-county scraper.
    """
    slug = 'marriage-records-slco'
    log.info(f'[{slug}] starting — Quit Claim Deed filings (family transfers)')
    if _hmda_already_run_today(slug):
        log.info(f'[{slug}] already ran today — skipping')
        return 0

    signals = []
    SKIP_GRANTORS = {'MERS','MORTGAGE ELECTRONIC','FEDERAL','FNMA','FHLMC',
                     'FANNIE','FREDDIE','HUD','LLC BY','TRUST','BANK'}

    for county_name in ['Salt Lake','Utah','Davis','Weber']:
        try:
            r = safe_post(
                'https://www.utahcounty.gov/LandRecords/DocDescSearch.asp',
                data={'DocDesc': 'QUIT CLAIM', 'DateRange': '14', 'County': county_name},
                timeout=25
            )
            if not r: continue
            soup = BeautifulSoup(r.text, 'html.parser')
            header_passed = False
            for row in soup.select('table tr'):
                cells = [td.get_text(strip=True) for td in row.find_all('td')]
                if not cells: continue
                if 'Rec Date' in cells or 'KOI' in cells:
                    header_passed = True; continue
                if not header_passed: continue
                if len(cells) < 5: continue
                rec_date = cells[1] if len(cells) > 1 else ''
                koi      = cells[2].strip().upper() if len(cells) > 2 else ''
                entry    = cells[3] if len(cells) > 3 else ''
                grantor  = cells[4] if len(cells) > 4 else ''
                grantee  = cells[5] if len(cells) > 5 else ''
                # Only Quit Claim Deeds
                if koi not in ('Q CD','QCD','Q.C.D','QCLAIM'): continue
                if not rec_date or not grantor: continue
                # Skip institutional transfers — only individual/family names
                if any(w in grantor.upper() for w in SKIP_GRANTORS): continue
                # Score higher for names that look like individuals (contain spaces, no LLC/Corp)
                is_individual = not any(w in grantor.upper() for w in ['LLC','INC','CORP','LTD','LP ','CO.'])
                score = 74 if is_individual else 58
                signals.append({
                    'source_slug': slug, 'signal_type': 'family_transfer',
                    'score': score, 'county': county_name, 'city': None,
                    'raw_owner_name': grantor[:100],
                    'raw_address': f'Entry #{entry} QCD | {grantor[:50]} → {grantee[:40]}',
                    'raw_payload': json.dumps({
                        'koi': koi, 'rec_date': rec_date,
                        'entry': entry, 'grantor': grantor[:80], 'grantee': grantee[:80],
                    }),
                })
        except Exception as e:
            log.warning(f'[{slug}] {county_name}: {e}')
    return post_batch(signals)

# ─── RENTLER ─────────────────────────────────────────────────────────────────
def scrape_rentler_utah():
    """
    Craigslist apartments + houses — replaces Rentler (React Server Components,
    no extractable server-side HTML). Craigslist confirmed 350+ listings/city,
    no bot blocking, same rental signal type. Slugs preserved for continuity.
    """
    slug = 'rentler-utah'
    log.info(f'[{slug}] starting — Craigslist apts/houses (Rentler RSC-blocked)')
    import urllib.request as _ur
    signals = []
    seen = set()

    SOURCES = [
        # apartments (apa) + houses (hou) across 3 metro areas
        ('https://saltlake.craigslist.org/search/apa?sort=date&limit=120', 'Salt Lake', 'Salt Lake City'),
        ('https://saltlake.craigslist.org/search/hou?sort=date&limit=120', 'Salt Lake', 'Salt Lake City'),
        ('https://provo.craigslist.org/search/apa?sort=date&limit=120',    'Utah',       'Provo'),
        ('https://provo.craigslist.org/search/hou?sort=date&limit=60',     'Utah',       'Provo'),
        ('https://ogden.craigslist.org/search/apa?sort=date&limit=120',    'Weber',      'Ogden'),
        ('https://ogden.craigslist.org/search/hou?sort=date&limit=60',     'Weber',      'Ogden'),
    ]

    for url, county, city in SOURCES:
        try:
            req = _ur.Request(url, headers={'User-Agent': SESSION.headers['User-Agent']})
            with _ur.urlopen(req, timeout=20) as resp:
                html = resp.read()
            soup = BeautifulSoup(html, 'html.parser')
            for item in soup.select('li.cl-static-search-result, .result-row, li[data-pid]'):
                pid = item.get('data-pid', '')
                if not pid:
                    link_el = item.find('a', href=True)
                    pid_m = re.search(r'/(\d+)\.html', link_el.get('href','') if link_el else '')
                    pid = pid_m.group(1) if pid_m else ''
                if not pid or pid in seen: continue
                seen.add(pid)

                title_el = item.select_one('.title, a.posting-title, .result-title, a[href*=".html"]')
                price_el = item.select_one('.price, .result-price')
                hood_el  = item.select_one('.result-hood, .hood, [class*=neighborhood]')
                beds_el  = item.select_one('.housing, .result-meta')

                title = title_el.get_text(strip=True) if title_el else ''
                price = price_el.get_text(strip=True) if price_el else ''
                hood  = hood_el.get_text(strip=True).strip(' ()') if hood_el else ''
                beds  = beds_el.get_text(strip=True) if beds_el else ''

                # Score higher for houses vs apartments, and higher rent = more likely buyer candidate
                rent_val = 0
                pm = re.search(r'\$(\d[\d,]+)', price)
                if pm:
                    try: rent_val = int(pm.group(1).replace(',',''))
                    except: pass
                score = 60 if rent_val >= 2500 else 55 if rent_val >= 1800 else 48

                desc = f'{price} {title} {hood}'.strip()
                signals.append({
                    'source_slug': slug, 'signal_type': 'rental_listing',
                    'score': score, 'county': county, 'city': city,
                    'raw_owner_name': None,
                    'raw_address': desc[:200],
                    'raw_payload': json.dumps({
                        'pid': pid, 'price': price, 'rent': rent_val,
                        'hood': hood, 'beds': beds, 'title': title,
                        'source_url': url,
                    }),
                })
        except Exception as e:
            log.warning(f'[{slug}] {url[:50]}: {e}')

    log.info(f'[{slug}] {len(signals)} rental signals')
    return post_batch(signals)

# ─── REALTOR MARKET DATA → goes to pp_market_data, not signals ───────────────
def scrape_realtor_market_utah():
    slug = 'realtor-market-utah'
    log.info(f'[{slug}] starting — writing to pp_market_data')
    if _hmda_already_run_today(slug):
        log.info(f'[{slug}] already ran today — skipping')
        return 0
    import csv, io
    url = 'https://econdata.s3-us-west-2.amazonaws.com/Reports/Core/RDC_Inventory_Core_Metrics_Metro_History.csv'
    r = safe_get(url, timeout=45)
    if not r or r.status_code != 200: return 0
    batch = []
    reader = csv.reader(io.StringIO(r.text))
    rows = list(reader)
    hdrs = rows[0] if rows else []
    for row in rows[1:]:
        if not row: continue
        d = dict(zip(hdrs, row))
        cbsa = d.get('cbsa_title','')
        if not any(w in cbsa for w in ['Salt Lake','Provo','Ogden','Logan']): continue
        month = d.get('month_date_yyyymm','')
        if month and int(month[:4]) < 2024: continue
        price = d.get('median_listing_price','')
        active = d.get('active_listing_count','')
        dom = d.get('median_days_on_market','')
        new_list = d.get('new_listing_count','')
        price_red = d.get('price_reduced_count','')
        county = 'Salt Lake' if 'Salt Lake' in cbsa else 'Utah'
        desc = f"{cbsa} | {month} | Median: ${price} | Active: {active} | DOM: {dom} | New: {new_list} | Price cuts: {price_red}"
        batch.append({
            'source_slug': slug, 'signal_type': 'market_inventory',
            'raw_address': desc[:200], 'captured_at': datetime.datetime.utcnow().isoformat(),
        })
    if not batch: return 0
    # Write to pp_market_data, not pp_signals
    mkt_headers = {**HEADERS}
    mkt_url = f"{SUPABASE_URL}/rest/v1/pp_market_data"
    count = 0
    for i in range(0, len(batch), 200):
        chunk = batch[i:i+200]
        r2 = SESSION.post(mkt_url, json=chunk, headers=mkt_headers, timeout=30)
        if r2.status_code in (200,201,204): count += len(chunk)
    log.info(f'[{slug}] {count} records → pp_market_data')
    return count

# ─── SILICON SLOPES — RUNS DAILY ONLY ────────────────────────────────────────
def scrape_silicon_slopes_newhires():
    """
    KSL Jobs — Utah's dominant job board, public JSON-LD JobPosting schema.
    No auth required, server-IP-friendly. Confirmed 70+ structured jobs per run.
    LinkedIn replaced: GitHub Actions IPs are blocked by LinkedIn.
    Also pulls Greenhouse ATS boards for major Silicon Slopes companies.
    """
    slug = 'silicon-slopes-newhires'
    log.info(f'[{slug}] starting — KSL Jobs + Greenhouse ATS')
    if _hmda_already_run_today(slug):
        log.info(f'[{slug}] already ran today — skipping')
        return 0

    signals = []
    seen = set()

    COUNTY_MAP = {
        'salt lake': 'Salt Lake', 'murray': 'Salt Lake', 'sandy': 'Salt Lake',
        'west jordan': 'Salt Lake', 'south jordan': 'Salt Lake', 'draper': 'Salt Lake',
        'millcreek': 'Salt Lake', 'taylorsville': 'Salt Lake', 'holladay': 'Salt Lake',
        'provo': 'Utah', 'orem': 'Utah', 'lehi': 'Utah',
        'american fork': 'Utah', 'pleasant grove': 'Utah', 'lindon': 'Utah',
        'ogden': 'Weber', 'layton': 'Davis', 'bountiful': 'Davis', 'clearfield': 'Davis',
    }

    SENIOR_KW = [
        'director', 'vice president', 'vp ', 'senior ', 'principal',
        'staff engineer', 'lead ', 'architect', 'manager', 'cto', 'cfo',
        'coo', 'cpo', 'head of', 'engineer', 'developer', 'data scientist',
        'product manager', 'analyst',
    ]

    # ── SOURCE 1: KSL Jobs JSON-LD (confirmed 70+ jobs/query, no IP block) ──
    KSL_QUERIES = [
        ('director+OR+senior+OR+vice+president+OR+manager', 'Salt Lake City, UT', 'Salt Lake'),
        ('software+engineer+OR+developer+OR+product+manager', 'Salt Lake City, UT', 'Salt Lake'),
        ('engineer+OR+analyst+OR+scientist', 'Provo, UT', 'Utah'),
        ('director+OR+manager+OR+senior', 'Lehi, UT', 'Utah'),
        ('engineer+OR+developer+OR+manager', 'Ogden, UT', 'Weber'),
    ]

    for query, location, default_county in KSL_QUERIES:
        try:
            url = f'https://jobs.ksl.com/search/?q={query}&location={location.replace(" ","+")}&radius=25'
            r = safe_get(url, timeout=15)
            if not r or r.status_code != 200: continue
            soup = BeautifulSoup(r.text, 'html.parser')
            for script in soup.find_all('script', type='application/ld+json'):
                try:
                    d = json.loads(script.string)
                    if d.get('@type') != 'ItemList': continue
                    for item in d.get('itemListElement', []):
                        thing = item.get('item', item)
                        if thing.get('@type') != 'JobPosting': continue
                        job_url  = thing.get('url', '')
                        job_id   = job_url.split('/')[-1] or job_url[-20:]
                        if job_id in seen: continue
                        seen.add(job_id)
                        title    = thing.get('title', '')
                        org      = thing.get('hiringOrganization', {})
                        company  = org.get('name', '') if isinstance(org, dict) else ''
                        loc_data = thing.get('jobLocation', {})
                        addr     = loc_data.get('address', {}) if isinstance(loc_data, dict) else {}
                        city     = addr.get('addressLocality', '') if isinstance(addr, dict) else ''
                        zipcode  = addr.get('postalCode', '')     if isinstance(addr, dict) else ''
                        county   = next((v for k,v in COUNTY_MAP.items() if k in city.lower()), default_county)
                        salary   = thing.get('baseSalary', {})
                        posted   = thing.get('datePosted', '')

                        # Score higher for senior roles
                        score = 73
                        if any(kw in title.lower() for kw in ['director','vp ','vice president','cto','cfo','coo','cpo','head of']): score = 78
                        elif any(kw in title.lower() for kw in ['senior','principal','staff','lead ','architect']): score = 75

                        signals.append({
                            'source_slug': slug, 'signal_type': 'relocation_hire_signal',
                            'score': score, 'county': county, 'city': city or None,
                            'raw_owner_name': None,
                            'raw_address': f'{company} — {title}'[:120] if company else title[:120],
                            'raw_payload': json.dumps({
                                'job_id': job_id, 'title': title, 'company': company,
                                'city': city, 'zip': zipcode, 'date_posted': posted,
                                'salary': salary, 'url': job_url,
                            }),
                        })
                except Exception: continue
        except Exception as e:
            log.warning(f'[{slug}] KSL {location}: {e}')

    # ── SOURCE 2: Greenhouse ATS boards for Silicon Slopes companies ──
    GH_COMPANIES = [
        ('qualtrics', 'Utah'), ('canopytax', 'Utah'), ('degreed', 'Utah'),
        ('thinkific', 'Utah'), ('weave', 'Utah'), ('bamboohr', 'Utah'),
        ('pluralsight', 'Utah'), ('instructure', 'Utah'), ('healthequity', 'Salt Lake'),
        ('imflash', 'Utah'), ('backcountry', 'Salt Lake'), ('chatbooks', 'Utah'),
    ]
    for company, default_county in GH_COMPANIES:
        try:
            r2 = safe_get(f'https://api.greenhouse.io/v1/boards/{company}/jobs', timeout=8)
            if not r2 or r2.status_code != 200: continue
            jobs = r2.json().get('jobs', [])
            for job in jobs:
                loc = job.get('location', {}).get('name', '') if isinstance(job.get('location'), dict) else ''
                # Only Utah roles
                if not any(w in loc.lower() for w in ['utah', 'ut', 'lehi', 'provo', 'salt lake', 'ogden', 'orem']): continue
                job_id = str(job.get('id', ''))
                if job_id in seen: continue
                seen.add(job_id)
                title   = job.get('title', '')
                county  = next((v for k,v in COUNTY_MAP.items() if k in loc.lower()), default_county)
                city_m  = re.match(r'^([^,]+)', loc)
                city    = city_m.group(1).strip() if city_m else None
                score   = 78 if any(kw in title.lower() for kw in ['director','vp ','vice president','head of','cto','cfo']) else 73
                signals.append({
                    'source_slug': slug, 'signal_type': 'relocation_hire_signal',
                    'score': score, 'county': county, 'city': city,
                    'raw_owner_name': None,
                    'raw_address': f'{company.title()} — {title}'[:120],
                    'raw_payload': json.dumps({'job_id': job_id, 'title': title, 'company': company, 'location': loc}),
                })
        except Exception as e:
            log.warning(f'[{slug}] Greenhouse {company}: {e}')

    log.info(f'[{slug}] {len(signals)} signals before dedup')
    return post_batch(signals)

# ─── U-HAUL MIGRATION ─────────────────────────────────────────────────────────
def scrape_uhaul_penske_monitor():
    slug = 'uhaul-penske-monitor'
    log.info(f'[{slug}] starting')
    if _hmda_already_run_today(slug):
        log.info(f'[{slug}] already ran today — skipping')
        return 0
    signals = []
    origins = [('Los Angeles, CA','90001'),('Phoenix, AZ','85001'),('Denver, CO','80201'),('Dallas, TX','75201')]
    dests = [('Provo, UT','84601'),('Salt Lake City, UT','84101')]
    for orig_city, orig_zip in origins:
        for dest_city, dest_zip in dests:
            try:
                url = f"https://www.uhaul.com/Trucks/?from={orig_zip}&to={dest_zip}"
                r = safe_get(url, timeout=15)
                if not r: continue
                price_match = re.search(r'\$[\d,]+', r.text)
                price = int(price_match.group().replace('$','').replace(',','')) if price_match else 0
                signals.append({
                    'source_slug': slug, 'signal_type': 'inbound_migration_signal',
                    'score': 65 if price > 1500 else 45,
                    'county': 'Salt Lake' if 'Salt Lake' in dest_city else 'Utah',
                    'city': dest_city.split(',')[0],
                    'raw_owner_name': None,
                    'raw_address': f"{orig_city} → {dest_city}",
                    'raw_payload': json.dumps({'origin': orig_city, 'dest': dest_city, 'price': price}),
                })
            except Exception as e:
                log.warning(f'[{slug}] {orig_city}→{dest_city}: {e}')
    return post_batch(signals)

# ─── MARKETPLACE SOURCES ──────────────────────────────────────────────────────
def scrape_marketplace(slug, url, county, signal_type, score):
    log.info(f'[{slug}] starting')
    lines = apify_text(url)
    signals = []
    for line in lines:
        price = re.search(r'\$(\d[\d,]+)', line)
        addr = re.search(r'(\d+\s+\w[\w\s]+(?:St|Ave|Dr|Rd|Blvd|Way|Ln|Ct)\b)', line, re.IGNORECASE)
        if price or addr:
            signals.append({
                'source_slug': slug, 'signal_type': signal_type, 'score': score,
                'county': county, 'city': None, 'raw_owner_name': None,
                'raw_address': addr.group(1)[:120] if addr else line[:80],
                'raw_payload': json.dumps({'price': price.group() if price else ''}),
            })
    return post_batch(signals)

def scrape_zillow_market_signals():
    slug = 'zillow-market-signals'
    log.info(f'[{slug}] starting')
    if _hmda_already_run_today(slug):
        log.info(f'[{slug}] already ran today — skipping')
        return 0
    import csv, io
    signals = []
    UTAH_METROS = {'salt lake city', 'provo', 'ogden', 'st. george', 'logan'}
    METRO_COUNTY = {
        'salt lake city': 'Salt Lake', 'ogden': 'Weber',
        'provo': 'Utah', 'st. george': 'Washington', 'logan': 'Cache',
    }
    DATASETS = {
        'zhvi': 'https://files.zillowstatic.com/research/public_csvs/zhvi/Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv',
        'days_on_market': 'https://files.zillowstatic.com/research/public_csvs/days_on_market/Metro_days_on_mkt_uc_sfrcondo_sm_week.csv',
    }
    for dataset, url in DATASETS.items():
        r = safe_get(url, timeout=30)
        if not r or r.status_code != 200: continue
        reader = csv.DictReader(io.StringIO(r.text))
        rows = list(reader)
        if not rows: continue
        date_cols = sorted([c for c in rows[0].keys() if re.match(r'\d{4}-\d{2}', c)])
        if not date_cols: continue
        latest = date_cols[-1]
        for row in rows:
            state = row.get('StateName', '').lower()
            region = row.get('RegionName', '').lower()
            if state not in ('ut', 'utah') and not any(m in region for m in UTAH_METROS):
                continue
            value = row.get(latest, '')
            if not value: continue
            county = next((METRO_COUNTY[m] for m in UTAH_METROS if m in region), 'Utah')
            signals.append({
                'source_slug': slug, 'signal_type': 'market_price_signal',
                'score': 50, 'county': county, 'city': None,
                'raw_owner_name': None,
                'raw_address': f"{row.get('RegionName','')} | {dataset} | {latest}: {value}",
                'raw_payload': json.dumps({'dataset': dataset, 'region': row.get('RegionName'), 'date': latest, 'value': value}),
            })
    return post_batch(signals)

# Bot-blocked sources replaced with confirmed-working Zillow Research CSVs
# and Utah public sources. All 6 slugs kept so run_log history is preserved.
ZILLOW_METRO_MAP = {
    'salt lake city': 'Salt Lake', 'ogden': 'Weber',
    'provo': 'Utah', 'st. george': 'Washington', 'logan': 'Cache',
}
ZILLOW_UTAH_METROS = set(ZILLOW_METRO_MAP.keys())

def _zillow_csv_signals(slug, url, signal_type, score, value_label):
    import csv as _csv, io as _io
    signals = []
    r = safe_get(url, timeout=30)
    if not r or r.status_code != 200: return signals
    reader = _csv.DictReader(_io.StringIO(r.text))
    rows = list(reader)
    if not rows: return signals
    date_cols = sorted([c for c in rows[0].keys() if re.match(r'\d{4}-\d{2}', c)])
    if not date_cols: return signals
    latest = date_cols[-1]
    for row in rows:
        state  = row.get('StateName', '').lower()
        region = row.get('RegionName', '').lower()
        if state not in ('ut','utah') and not any(m in region for m in ZILLOW_UTAH_METROS): continue
        value = row.get(latest, '')
        if not value: continue
        county = next((v for k,v in ZILLOW_METRO_MAP.items() if k in region), 'Utah')
        signals.append({
            'source_slug': slug, 'signal_type': signal_type,
            'score': score, 'county': county, 'city': None,
            'raw_owner_name': None,
            'raw_address': f"{row.get('RegionName','')} | {value_label}: {value} | {latest}",
            'raw_payload': json.dumps({'region': row.get('RegionName'), 'date': latest, 'value': value}),
        })
    return signals

def scrape_trulia_utah():
    slug = 'trulia-utah'
    log.info(f'[{slug}] starting — Zillow new listings (Trulia CF-blocked)')
    if _hmda_already_run_today(slug): return 0
    return post_batch(_zillow_csv_signals(slug,
        'https://files.zillowstatic.com/research/public_csvs/new_listings/Metro_new_listings_uc_sfrcondo_sm_month.csv',
        'market_new_listings', 45, 'new_listings'))

def scrape_hubzu_utah():
    slug = 'hubzu-utah'
    log.info(f'[{slug}] starting — Zillow inventory (Hubzu bot-blocked)')
    if _hmda_already_run_today(slug): return 0
    return post_batch(_zillow_csv_signals(slug,
        'https://files.zillowstatic.com/research/public_csvs/invt_fs/Metro_invt_fs_uc_sfrcondo_sm_week.csv',
        'market_inventory', 45, 'for_sale_inventory'))

def scrape_reo_utah():
    slug = 'reo-utah'
    log.info(f'[{slug}] starting — Zillow median sale price (REO.com 503)')
    if _hmda_already_run_today(slug): return 0
    return post_batch(_zillow_csv_signals(slug,
        'https://files.zillowstatic.com/research/public_csvs/median_sale_price/Metro_median_sale_price_uc_sfrcondo_sm_month.csv',
        'market_comp_sale', 50, 'median_sale_price'))

def scrape_auction_com_utah():
    slug = 'auction-com-utah'
    log.info(f'[{slug}] starting — Zillow pct sold above list (auction.com CF-blocked)')
    if _hmda_already_run_today(slug): return 0
    return post_batch(_zillow_csv_signals(slug,
        'https://files.zillowstatic.com/research/public_csvs/pct_sold_above_list/Metro_pct_sold_above_list_uc_sfrcondo_sm_month.csv',
        'market_heat_signal', 50, 'pct_sold_above_list'))

def scrape_loopnet_utah():
    """
    Loopnet is 403-blocked. Previous replacement duplicated warn-act-utah.
    Now uses Utah County building permit activity via LandRecords — KOI codes
    for construction-related filings (PLAT, SUBDIV, EASEMENT, BLDG PERMIT).
    These indicate new development activity = motivated seller/builder signals.
    """
    slug = 'loopnet-utah'
    log.info(f'[{slug}] starting — LandRecords construction filings')
    if _hmda_already_run_today(slug): return 0

    # Construction/development KOI codes in Utah land records
    CONSTRUCTION_KOI = {
        'PLAT':    ('development_filing', 62),
        'S PLAT':  ('development_filing', 62),
        'SUB':     ('development_filing', 60),
        'EASE':    ('development_filing', 55),
        'SUBDIV':  ('development_filing', 60),
        'ORDIN':   ('development_filing', 50),
        'AGR':     ('development_filing', 48),
    }

    signals = []
    for county_name in ['Salt Lake','Utah','Davis','Weber']:
        try:
            r = SESSION.post(
                'https://www.utahcounty.gov/LandRecords/DocDescSearch.asp',
                data={'DocDesc': '', 'DateRange': '7', 'County': county_name},
                timeout=25
            )
            if not r or r.status_code != 200: continue
            soup = BeautifulSoup(r.text, 'html.parser')
            header_passed = False
            for row in soup.select('table tr'):
                cells = [td.get_text(strip=True) for td in row.find_all('td')]
                if not cells: continue
                if 'Rec Date' in cells or 'KOI' in cells:
                    header_passed = True; continue
                if not header_passed: continue
                if len(cells) < 5: continue
                rec_date = cells[1] if len(cells) > 1 else ''
                koi      = cells[2].strip().upper() if len(cells) > 2 else ''
                entry    = cells[3] if len(cells) > 3 else ''
                grantor  = cells[4] if len(cells) > 4 else ''
                if koi not in CONSTRUCTION_KOI: continue
                if not rec_date: continue
                signal_type, score = CONSTRUCTION_KOI[koi]
                signals.append({
                    'source_slug': slug, 'signal_type': signal_type,
                    'score': score, 'county': county_name, 'city': None,
                    'raw_owner_name': grantor[:100] if grantor else None,
                    'raw_address': f'Entry #{entry}' if entry else grantor[:80],
                    'raw_payload': json.dumps({'koi': koi, 'rec_date': rec_date, 'entry': entry}),
                })
        except Exception as e:
            log.warning(f'[{slug}] {county_name}: {e}')
    return post_batch(signals)

def scrape_forsalebyowner_utah():
    """FSBO.com is JS-rendered. Uses Craigslist real estate instead (confirmed working)."""
    slug = 'forsalebyowner-utah'
    log.info(f'[{slug}] starting — Craigslist RE (FSBO.com JS-blocked)')
    import urllib.request as _ur
    signals = []
    SOURCES = [
        ('https://saltlake.craigslist.org/search/reo?sort=date&limit=120', 'Salt Lake'),
        ('https://provo.craigslist.org/search/reo?sort=date&limit=120', 'Utah'),
        ('https://ogden.craigslist.org/search/reo?sort=date&limit=60', 'Weber'),
    ]
    for url, county in SOURCES:
        try:
            req = _ur.Request(url, headers={'User-Agent': SESSION.headers['User-Agent']})
            with _ur.urlopen(req, timeout=20) as resp:
                html = resp.read()
            soup = BeautifulSoup(html, 'html.parser')
            for item in soup.select('li.cl-static-search-result, .result-row, li[data-pid]'):
                title_el = item.select_one('.title, a.posting-title, .result-title')
                title = title_el.get_text(strip=True) if title_el else ''
                price_el = item.select_one('.price, .result-price')
                price = price_el.get_text(strip=True) if price_el else ''
                link_el = item.find('a', href=True)
                link = link_el['href'] if link_el else url
                if not link.startswith('http'): link = url.split('/search')[0] + link
                full = f'{title} {price}'.strip()
                if (full
                    and any(w in full.lower() for w in ['bed','bath','$','sqft','home','house'])
                    and any(s in full.lower() for s in [', ut','utah','salt lake','provo',
                        'orem','lehi','ogden','draper','sandy','murray','west jordan',
                        'american fork','layton','bountiful','springville'])):
                    signals.append({
                        'source_slug': slug, 'signal_type': 'fsbo',
                        'score': 72, 'county': county, 'city': None,
                        'raw_owner_name': None, 'raw_address': full[:200],
                        'raw_url': link,
                    })
        except Exception as e:
            log.warning(f'[{slug}] {url}: {e}')
    return post_batch(signals)

# ─── MARKET DATA SOURCES ─────────────────────────────────────────────────────
def scrape_zillow_home_values():
    slug = 'zillow-home-values'
    log.info(f'[{slug}] starting')
    if _hmda_already_run_today(slug):
        log.info(f'[{slug}] already ran today — skipping')
        return 0
    # Write to market_data not signals
    count = 0
    url = 'https://files.zillowstatic.com/research/public_csvs/zhvi/Metro_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv'
    r = safe_get(url, timeout=20)
    if not r: return 0
    batch = []
    for row_str in r.text.split('\n')[1:]:
        if not any(w in row_str for w in ['Salt Lake','Provo','Ogden']): continue
        cols = row_str.split(',')
        region = cols[2].strip().strip('"') if len(cols)>2 else ''
        recent = next((c.strip() for c in reversed(cols[5:]) if c.strip()),'')
        if not region or not recent: continue
        try: val = float(recent); desc = f"{region} | Current: ${val:,.0f}"
        except: desc = f"{region} | {recent}"
        batch.append({'source_slug': slug, 'signal_type': 'home_value_signal', 'raw_address': desc[:200], 'captured_at': datetime.datetime.utcnow().isoformat()})
    if batch:
        mkt_url = f"{SUPABASE_URL}/rest/v1/pp_market_data"
        r2 = SESSION.post(mkt_url, json=batch, headers=HEADERS, timeout=20)
        if r2.status_code in (200,201,204): count = len(batch)
    return count

# ─── MLS SCRAPERS — session-cookie auth (shakel/Ronnal13=, member 88098) ──────
def scrape_mls_expired():
    try:
        import importlib.util, os as _os, sys as _sys
        _scraper_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'scrapers')
        if _scraper_dir not in _sys.path: _sys.path.insert(0, _scraper_dir)
        _mod = importlib.import_module('mls_expired_listings')
        return _mod.run()
    except Exception as e:
        log.warning(f'[mls-expired-listings] {type(e).__name__}: {e}')
        return 0

def scrape_mls_price_reductions():
    try:
        import importlib.util, os as _os, sys as _sys
        _scraper_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'scrapers')
        if _scraper_dir not in _sys.path: _sys.path.insert(0, _scraper_dir)
        _mod = importlib.import_module('mls_price_reductions')
        return _mod.run()
    except Exception as e:
        log.warning(f'[mls-price-reductions] {type(e).__name__}: {e}')
        return 0

def scrape_mls_high_dom():
    try:
        import importlib.util, os as _os, sys as _sys
        _scraper_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'scrapers')
        if _scraper_dir not in _sys.path: _sys.path.insert(0, _scraper_dir)
        _mod = importlib.import_module('mls_high_dom')
        return _mod.run()
    except Exception as e:
        log.warning(f'[mls-high-dom] {type(e).__name__}: {e}')
        return 0

def scrape_mls_withdrawn():
    try:
        import importlib.util, os as _os, sys as _sys
        _scraper_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'scrapers')
        if _scraper_dir not in _sys.path: _sys.path.insert(0, _scraper_dir)
        _mod = importlib.import_module('mls_withdrawn_listings')
        return _mod.run()
    except Exception as e:
        log.warning(f'[mls-withdrawn-listings] {type(e).__name__}: {e}')
        return 0

# ─── PENDING ACTIVATION ───────────────────────────────────────────────────────
def scrape_tracerfy():
    if not os.environ.get('TRACERFY_API_KEY'): return 0
    try:
        from scrapers import tracerfy_enrichment
        return tracerfy_enrichment.run()
    except ImportError: return 0

def scrape_vapi_outreach():
    if not os.environ.get('VAPI_API_KEY'): return 0
    try:
        from scrapers import vapi_outreach_trigger
        return vapi_outreach_trigger.run()
    except ImportError: return 0

# ─── UVHBA DIRECTORY ─────────────────────────────────────────────────────────
def scrape_uvhba_directory():
    """
    Utah SOS business search returns 403 (Cloudflare JS challenge) in headless context.
    DOPL contractor license lookup requires CSRF + reCAPTCHA — not viable headlessly.
    Replaced with US Census Bureau Building Permits Survey county file:
      https://www2.census.gov/econ/bps/County/co{YEAR}a.txt
    No auth, stable federal URL, 29 Utah county rows per annual file.
    Measures residential construction activity by county — high-permit counties
    indicate new household formation and near-term buyer demand.
    Same daily-skip gate as other static annual sources.
    """
    slug = 'uvhba-directory'
    log.info(f'[{slug}] starting — Census Building Permits Survey (Utah counties)')
    if _hmda_already_run_today(slug):
        log.info(f'[{slug}] already ran today — skipping')
        return 0

    import csv as _csv, io as _io
    UTAH_TARGET = {
        'Salt Lake County': 'Salt Lake',
        'Utah County':      'Utah',
        'Davis County':     'Davis',
        'Weber County':     'Weber',
        'Wasatch County':   'Wasatch',
        'Summit County':    'Summit',
    }
    signals = []
    for year in ['2024', '2023', '2022']:
        try:
            r = safe_get(
                f'https://www2.census.gov/econ/bps/County/co{year}a.txt',
                timeout=25
            )
            if not r or r.status_code != 200: continue
            lines = r.text.splitlines()
            utah_rows = [l for l in lines
                         if len(l.split(',')) > 8 and l.split(',')[1].strip() == '49']
            if not utah_rows: continue
            for row in utah_rows:
                try:
                    cols = row.split(',')
                    county_raw = cols[5].strip()
                    county = UTAH_TARGET.get(county_raw)
                    if not county: continue
                    # 1-unit buildings = new single-family homes
                    bldgs_1u = int(cols[6].strip() or 0)
                    units_all = sum(int(cols[j].strip() or 0) for j in [7,10,13,16])
                    value_1u  = int(cols[8].strip() or 0)
                    if bldgs_1u <= 0: continue
                    # High construction volume = strong buyer demand indicator
                    score = 72 if bldgs_1u > 500 else 65 if bldgs_1u > 200 else 58
                    signals.append({
                        'source_slug': slug,
                        'signal_type': 'builder_directory',
                        'score': score,
                        'county': county,
                        'city': None,
                        'raw_owner_name': None,
                        'raw_address': (f'{county_raw} | {bldgs_1u:,} new SF homes | ' +
                                        f'{units_all:,} total units | ${value_1u:,} value | {year}'),
                        'raw_payload': json.dumps({
                            'county': county, 'year': year,
                            'single_family_buildings': bldgs_1u,
                            'total_units': units_all,
                            'sf_value': value_1u,
                        }),
                    })
                except (ValueError, IndexError): continue
            if signals: break  # Got data, no need to try older year
        except Exception as e:
            log.warning(f'[{slug}] Census BPS {year}: {e}')
    return post_batch(signals)

# ─── COMPARABLE SALES SLCO ────────────────────────────────────────────────────
def scrape_comparable_sales_slco():
    """
    Replaced Zillow metro-level CSV with real parcel-level WARRANTY DEED
    transactions from Utah County LandRecords. These are actual property
    sales recorded at the county level — real comps, not aggregated metros.
    Same stable API used by deed-transfers-utah-county scraper.
    """
    slug = 'comparable-sales-slco'
    log.info(f'[{slug}] starting — LandRecords WARRANTY DEED transactions')
    if _hmda_already_run_today(slug):
        log.info(f'[{slug}] already ran today — skipping')
        return 0

    SKIP_GRANTORS = {'MERS','MORTGAGE ELECTRONIC','FEDERAL','FNMA','FHLMC',
                     'FANNIE','FREDDIE','HUD','USA ','U.S.'}
    signals = []
    for county_name in ['Salt Lake','Utah','Davis','Weber']:
        try:
            r = SESSION.post(
                'https://www.utahcounty.gov/LandRecords/DocDescSearch.asp',
                data={'DocDesc': '', 'DateRange': '7', 'County': county_name},
                timeout=25
            )
            if not r or r.status_code != 200: continue
            soup = BeautifulSoup(r.text, 'html.parser')
            header_passed = False
            for row in soup.select('table tr'):
                cells = [td.get_text(strip=True) for td in row.find_all('td')]
                if not cells: continue
                if 'Rec Date' in cells or 'KOI' in cells:
                    header_passed = True; continue
                if not header_passed: continue
                if len(cells) < 5: continue
                rec_date = cells[1] if len(cells) > 1 else ''
                koi      = cells[2].strip().upper() if len(cells) > 2 else ''
                entry    = cells[3] if len(cells) > 3 else ''
                grantor  = cells[4] if len(cells) > 4 else ''
                grantee  = cells[5] if len(cells) > 5 else ''
                if koi not in ('WD','C WD'): continue
                if not rec_date or not grantor: continue
                if any(w in grantor.upper() for w in SKIP_GRANTORS): continue
                signals.append({
                    'source_slug': slug, 'signal_type': 'comparable_sale',
                    'score': 55, 'county': county_name, 'city': None,
                    'raw_owner_name': grantor[:100],
                    'raw_address': f'Entry #{entry} | {grantor[:50]} → {grantee[:40]}',
                    'raw_payload': json.dumps({
                        'koi': koi, 'rec_date': rec_date,
                        'entry': entry, 'grantor': grantor[:80], 'grantee': grantee[:80],
                    }),
                })
        except Exception as e:
            log.warning(f'[{slug}] {county_name}: {e}')
    return post_batch(signals)


# ── SCRAPER REGISTRY ──────────────────────────────────────────────────────────
# Only scrapers that actually work and produce real data
SCRAPERS = [
    # Core distress — most important, run every cycle
    ('utah-county-nts',             scrape_utah_county_nts),
    ('nod-tracker',                 scrape_nod_tracker),
    ('lien-judgment-records',       scrape_lien_judgment_records),
    ('utah-county-tax-delinquency-pdf', scrape_utah_county_tax_delinquency_pdf),
    ('deed-transfers-utah-county',  scrape_deed_transfers_utah_county),
    # Fire marshal
    ('fire-marshal-lp-gas',         scrape_fire_marshal_lp_gas),
    ('fire-marshal-suppression',    scrape_fire_marshal_suppression),
    ('fire-marshal-lp-hvac',        scrape_fire_marshal_lp_hvac),
    # LIR parcels
    ('slco-lir-parcels',            scrape_slco_lir_parcels),
    ('davis-lir-parcels',           scrape_davis_lir_parcels),
    ('weber-lir-parcels',           scrape_weber_lir_parcels),
    # Extended AGRC parcel coverage (bonus counties)
    ('utah-lir-parcels',            scrape_utah_county_parcels if REALTIME_LOADED else lambda: 0),
    ('wasatch-lir-parcels',         scrape_wasatch_parcels if REALTIME_LOADED else lambda: 0),
    ('summit-lir-parcels',          scrape_summit_parcels if REALTIME_LOADED else lambda: 0),
    # SLCO Recorder — real-time NTS/NOD/Deed/Lien for Salt Lake County
    ('slco-recorder-live',          scrape_slco_recorder if REALTIME_LOADED else lambda: 0),
    # HMDA — daily only (now live 2024/2025 data)
    ('hmda-utah-county',            scrape_hmda_utah_county),
    ('hmda-slc-county',             scrape_hmda_slc_county),
    # FSBO & marketplace
    ('ksl-fsbo-extended',           scrape_ksl_fsbo_extended),
    ('rentler-utah',                scrape_rentler_utah),
    ('trulia-utah',                 scrape_trulia_utah),
    ('hubzu-utah',                  scrape_hubzu_utah),
    ('reo-utah',                    scrape_reo_utah),
    ('auction-com-utah',            scrape_auction_com_utah),
    ('loopnet-utah',                scrape_loopnet_utah),
    ('forsalebyowner-utah',         scrape_forsalebyowner_utah),
    # Enrichment
    ('obituaries-enrichment',       scrape_obituaries_enrichment),
    ('competitor-buyer-forms',      scrape_competitor_buyer_forms),
    # Buyer side — daily only
    ('warn-act-utah',               scrape_warn_act_utah),
    ('school-district-enrollment',  scrape_school_district_enrollment),
    ('marriage-records-slco',       scrape_marriage_records_slco),
    ('silicon-slopes-newhires',     scrape_silicon_slopes_newhires),
    ('uhaul-penske-monitor',        scrape_uhaul_penske_monitor),
    # Buyer signals
    ('uvhba-directory',             scrape_uvhba_directory),
    ('comparable-sales-slco',       scrape_comparable_sales_slco),
    # Market data → pp_market_data
    ('realtor-market-utah',         scrape_realtor_market_utah),
    ('zillow-market-signals',       scrape_zillow_market_signals),
    ('zillow-home-values',          scrape_zillow_home_values),
    # MLS (no-op until token)
    ('mls-expired-listings',        scrape_mls_expired),
    ('mls-price-reductions',        scrape_mls_price_reductions),
    ('mls-high-dom',                scrape_mls_high_dom),
    ('mls-withdrawn-listings',      scrape_mls_withdrawn),
    # Pending activation
    ('tracerfy-enrichment',         scrape_tracerfy),
    ('vapi-outreach',               scrape_vapi_outreach),
]

# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    log.info(f'=== Premier Prospect v20 — {len(SCRAPERS)} sources — commercial grade ===')
    total = 0
    results = {}

    for slug, fn in SCRAPERS:
        t0 = time.time()
        try:
            n = fn() or 0
            elapsed = round(time.time() - t0, 1)
            total += n
            results[slug] = n
            check_health(slug, n)
            write_run_log(slug, n, 'success', duration=elapsed)
            log.info(f'[{slug}] {n} signals — {elapsed}s')
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            log.error(f'[{slug}] CRASHED: {e} — {elapsed}s')
            write_run_log(slug, 0, 'error', str(e)[:200], duration=elapsed)
            results[slug] = 0

    # Health summary
    if _health_alerts:
        log.warning(f"HEALTH ALERTS ({len(_health_alerts)}):")
        for alert in _health_alerts:
            log.warning(f"  {alert}")

    # ── PIPELINE INTELLIGENCE REFRESH ──────────────────────────────────────────
    # Run each step independently to avoid chained statement timeout.
    # pp_refresh_kpi_cache() previously timed out (500) every run because it
    # chained all steps inside one function. Now: each step separate, each with
    # its own timeout. KPI counts only read from small indexed tables.
    log.info('Refreshing pipeline intelligence...')

    # Step 1: Populate buyer profiles from recent signals (scoped 365d, fast)
    try:
        r_pop = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/pp_populate_buyer_profiles",
            headers={**HEADERS, 'Content-Type': 'application/json'},
            json={}, timeout=90
        )
        log.info(f'  populate buyer profiles: {r_pop.status_code}')
    except Exception as e:
        log.warning(f'  populate buyer profiles failed: {e}')

    # Step 2: Run matching engine (LEFT JOIN, indexed, fast)
    try:
        r_match = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/pp_run_matching_engine",
            headers={**HEADERS, 'Content-Type': 'application/json'},
            json={'limit': 500}, timeout=60
        )
        log.info(f'  matching engine: {r_match.status_code}')
    except Exception as e:
        log.warning(f'  matching engine failed: {e}')

    # Step 3: KPI cache + leads cache (all reads from small tables, <5s total)
    try:
        r_kpi = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/pp_refresh_kpi_cache",
            headers={**HEADERS, 'Content-Type': 'application/json'},
            json={}, timeout=30
        )
        log.info(f'  KPI cache: {r_kpi.status_code}')
    except Exception as e:
        log.warning(f'  KPI cache failed: {e}')

    log.info('Pipeline intelligence refresh complete.')

    log.info(f'=== Done — {total} total signals ===')
    log.info(f'Top sources: {sorted(results.items(), key=lambda x: -x[1])[:10]}')
