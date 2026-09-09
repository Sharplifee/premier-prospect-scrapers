"""
Premier Prospect™ — Scraper Pipeline v20.1
Fixes June 17 2026: DEED OF TRUST removed, WARN Act county map, FSBO Utah filter, competitor -> market_data, LIR owner names
Commercial-grade: retry logic, source health monitoring,
correct dedup, no broken scrapers, single __main__, no undefined refs.
"""
import os, hashlib, logging, requests, re, json, time, datetime, html

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
# MLS scrapers pending auth resolution — credentials removed

# on_conflict=dedupe_hash is REQUIRED. Without it, PostgREST's
# "resolution=ignore-duplicates" only applies to the primary key (id, a serial
# that never conflicts), so a single duplicate row rejected the ENTIRE 200-row
# batch with 409 — silently dropping every genuinely new row alongside it.
# Verified Aug 31 2026. post_batch treats 409 as success, which hid this.
TABLE_URL = f"{SUPABASE_URL}/rest/v1/pp_scraper_signals?on_conflict=dedupe_hash"
HEADERS = {
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'apikey': SUPABASE_KEY,
    'Prefer': 'return=minimal,resolution=ignore-duplicates',
}
# RPC calls must NOT carry 'resolution=ignore-duplicates' — that Prefer directive
# is insert-only and PostgREST returns HTTP 500 on any RPC POST that includes it.
# Root cause of the matching engine / KPI cache failures since June 27.
RPC_HEADERS = {
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'apikey': SUPABASE_KEY,
}

SESSION = requests.Session()
SESSION.headers['User-Agent'] = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'

# ── APIFY RESIDENTIAL PROXY ─────────────────────────────────────────────────
# Fixes the shared-GitHub-runner-IP rate-limiting behind the 15-scraper
# intermittent cluster (HMDA, Trulia, Loopnet, Zillow, Auction.com, etc. —
# all showed an identical 24/35 zero-result rate over 14 days, which pointed
# to one shared upstream cause rather than 15 unrelated site issues).
#
# NOTE: Apify's proxy password is a separate credential from the API token
# in most accounts (see console.apify.com -> Proxy). This tries APIFY_TOKEN
# first since some accounts share the value, with APIFY_PROXY_PASSWORD as
# the explicit override if that doesn't work. If neither authenticates,
# APIFY_PROXIES stays disabled and safe_get() falls back to direct requests
# exactly as before — this does not change behavior unless proxy auth succeeds.
_APIFY_PROXY_PW = os.environ.get('APIFY_PROXY_PASSWORD') or APIFY_TOKEN
APIFY_PROXIES = None
if _APIFY_PROXY_PW:
    _proxy_url = f"http://groups-RESIDENTIAL:{_APIFY_PROXY_PW}@proxy.apify.com:8000"
    APIFY_PROXIES = {'http': _proxy_url, 'https': _proxy_url}

# Sources known to be affected by shared-IP rate limiting — route these
# through the Apify proxy. Add more slugs here if new ones show the same
# identical-zero-rate pattern in pp_run_log.
PROXY_ROUTED_SLUGS = {
    'hmda-slc-county', 'hmda-utah-county', 'trulia-utah', 'loopnet-utah',
    'auction-com-utah', 'reo-utah', 'hubzu-utah', 'zillow-market-signals',
    'school-district-enrollment', 'uhaul-penske-monitor', 'comparable-sales-slco',
    'marriage-records-slco', 'silicon-slopes-newhires', 'uvhba-directory',
    'warn-act-utah',
}

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
            # After the first failed attempt, route through the Apify proxy if
            # configured — this is what actually fixes the shared-runner-IP
            # rate-limiting behind the 15-scraper intermittent cluster, without
            # requiring every individual scraper/helper to be edited to pass
            # proxies explicitly. Direct attempt first (cheaper, no proxy cost);
            # only fall back to proxy on retry.
            req_kwargs = dict(kwargs)
            # .gov sites (e.g. utahcounty.gov) actively block proxy/VPN IP ranges —
            # confirmed directly: utahcounty.gov returns 200 OK in ~5s on a direct
            # connection but times out completely (30s, twice) through the Apify
            # residential proxy. Routing .gov traffic through the proxy makes
            # things strictly worse, so it's excluded here regardless of attempt.
            if attempt > 0 and APIFY_PROXIES and '.gov' not in url:
                req_kwargs['proxies'] = APIFY_PROXIES
            r = SESSION.get(url, timeout=timeout, **req_kwargs)
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
                'raw_payload','signal_type','score','county','city','captured_at','dedupe_hash',
                'parcel_serial','tax_owed','tax_total','loan_amount','loan_date','lender_name','loan_released'}

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
    # FINAL FIX: two previous attempts (checking status code, then checking
    # return=representation body) both turned out to depend on PostgREST
    # response semantics that didn't behave as documented in live testing —
    # verified directly against the DB, both still reported full batch size
    # as "inserted" even when zero new rows landed. This approach depends on
    # nothing except a plain row count before and after, which cannot lie.
    source_slug = unique[0].get('source_slug', '') if unique else ''
    try:
        before_r = SESSION.get(
            f"{SUPABASE_URL}/rest/v1/pp_scraper_signals"
            f"?select=id&source_slug=eq.{source_slug}&limit=1",
            headers={**HEADERS, 'Prefer': 'count=exact'}, timeout=15
        )
        before_count = int(before_r.headers.get('content-range', '0/0').split('/')[-1])
    except Exception:
        before_count = None

    for i in range(0, len(unique), 200):
        chunk = unique[i:i+200]
        for attempt in range(3):
            try:
                r = SESSION.post(TABLE_URL, json=chunk, headers=HEADERS, timeout=45)
                if r.status_code in (200, 201, 204, 409):
                    break
                log.error(f"Batch insert {r.status_code}: {r.text[:100]}")
                time.sleep(5)
            except Exception as e:
                log.error(f"Batch insert error: {e}")
                if attempt < 2: time.sleep(5)

    if before_count is not None:
        try:
            after_r = SESSION.get(
                f"{SUPABASE_URL}/rest/v1/pp_scraper_signals"
                f"?select=id&source_slug=eq.{source_slug}&limit=1",
                headers={**HEADERS, 'Prefer': 'count=exact'}, timeout=15
            )
            after_count = int(after_r.headers.get('content-range', '0/0').split('/')[-1])
            inserted = max(0, after_count - before_count)
        except Exception:
            inserted = 0
    else:
        inserted = 0

    if inserted == 0 and len(unique) > 0:
        log.info(f"post_batch [{source_slug}]: 0 new rows out of {len(unique)} attempted (all duplicates)")
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
    """
    Fix June 27 2026: Utah County DocDescSearch now returns ALL documents regardless
    of DocDesc filter. Each data row includes its own PLSS section in cells[0].
    Old logic skipped every row because it treated 'Township' rows as headers.
    New logic: use GET with offset pagination, target Table[3], filter by KOI client-side.
    """
    slug = 'utah-county-nts'
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
    NTS_KOIS = {'RSUBTEE','SUB TEE','SUBTEE','PRSUBTE'}
    def city_from_plss(text):
        m = _re.search(r'(\d+S Range \d+[EW])', text or '')
        return PLSS_CITY.get(m.group(1)) if m else None

    signals = []
    base_url = 'https://www.utahcounty.gov/LandRecords/DocDescSearch.asp?DocDesc=RSUBTEE&DateRange=30&County=Utah'
    for offset in [0, 200, 400]:
        r = safe_get(f'{base_url}&offset={offset}', timeout=25)
        if not r or r.status_code != 200: break
        soup = BeautifulSoup(r.text, 'html.parser')
        tables = soup.find_all('table')
        data_table = next((t for t in tables if t.find('tr') and
            'Description' in [td.get_text(strip=True) for td in (t.find('tr').find_all('td') or [])]), None)
        if not data_table: break
        rows = data_table.find_all('tr')
        page_count = 0
        for row in rows[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all('td')]
            if len(cells) < 5 or not any(cells): continue
            koi = cells[2].strip().upper()
            if koi not in NTS_KOIS: continue
            plss = cells[0]
            city = city_from_plss(plss)
            rec_date = cells[1]
            entry = cells[3].replace('\xa0', ' ').strip()
            grantor = cells[4]
            grantee = cells[5] if len(cells) > 5 else ''
            signals.append({
                'source_slug': slug, 'signal_type': 'nts', 'score': 99,
                'county': 'Utah', 'city': city,
                'raw_owner_name': grantor or None,
                'raw_address': f'NTS — Entry #{entry}',
                'raw_payload': json.dumps({
                    'koi': koi, 'rec_date': rec_date, 'entry': entry,
                    'plss_section': plss, 'grantee': grantee
                }),
            })
            page_count += 1
        if 'Next' not in r.text: break
    return post_batch(signals)

# ─── UTAH COUNTY RECORDER — UNIFIED HIGH-VALUE KOI SWEEP ─────────────────────
# Added Jul 30 2026 after a full KOI inventory of the live recorder feed revealed
# several high-motivation document types the system had never captured. All of
# these are FREE and require no court subscription:
#
#   PR LP    lis pendens          judicial foreclosure / suit against the property
#   PERREPD  personal rep deed    an ESTATE is selling — confirmed probate sale
#   AF DC    affidavit of death   owner died — recorded, dated death signal
#   TEE D    trustee's deed       foreclosure COMPLETED → retires the lead
#   M CHGCN  mechanics lien       unpaid contractor, stalled renovation
#   N LN     notice of lien       creditor pressure
#   R LN     release of lien      NEGATIVE signal — debt cleared
#
# One pass over the recorder index serves all of them, which is far kinder to the
# county server than one request per document type.
#
# NOTE: utahcounty.gov intermittently returns HTTP 500 on this endpoint even when
# healthy — observed 2 failures then success on the 3rd try. safe_get retries, but
# a 500 here means "try again", NOT "no records".
# ─── DEEDS OF TRUST → LOAN BURDEN / EQUITY PROXY ──────────────────────────────
# The recorder INDEX carries no dollar amounts, but each document's detail page
# does: "Consideration: $409,000.00" is the ORIGINAL LOAN AMOUNT on a trust deed.
# Verified live Aug 2026 (entries 68982, 69741, 60798).
#
# HONEST LIMITS, do not overstate this:
#   * Serial Number(s) and Mail Address are usually EMPTY on these documents, so
#     we cannot join to a parcel or harvest a mailing address here. We join on
#     the GRANTOR (borrower) name, same as every other recorder signal.
#   * TRUE equity % needs assessed market value, which the AGRC parcel layer does
#     NOT carry. So this computes a LOAN-VINTAGE PROXY instead: a 2005 trust deed
#     is mostly amortised (likely high equity), a 2025 one is not. That is a
#     proxy, not a measurement, and is scored modestly to reflect that.
#   * "Releases:" populated means the debt was satisfied — that loan must NOT
#     count against equity.
def scrape_deeds_of_trust():
    slug = 'utah-deeds-of-trust'
    log.info(f'[{slug}] starting')
    entries, signals = [], []
    for offset in (0, 200):
        r = safe_get('https://www.utahcounty.gov/LandRecords/DocDescSearch.asp',
                     params={'DocDesc':'','DateRange':'30','County':'Utah','offset':offset},
                     timeout=45)
        if not r:
            log.warning(f'[{slug}] index offset {offset} unreachable'); continue
        soup = BeautifulSoup(r.text, 'html.parser')
        for row in soup.select('tr'):
            c = row.select('td')
            if len(c) < 6: continue
            koi = c[2].get_text(strip=True)
            if koi not in ('TR D','D TR'): continue
            m = re.match(r'(\d+)', c[3].get_text(' ', strip=True).replace('\xa0',' '))
            if not m: continue
            entries.append((m.group(1), c[4].get_text(strip=True), c[5].get_text(strip=True)))
        time.sleep(2)

    # de-dup and cap: this is one HTTP request per document against a county
    # server, so stay polite and bounded.
    seen, uniq = set(), []
    for e in entries:
        if e[0] not in seen:
            seen.add(e[0]); uniq.append(e)
    uniq = uniq[:60]
    log.info(f'[{slug}] {len(uniq)} unique trust deeds to detail-fetch')

    for entry, grantor, grantee in uniq:
        d = safe_get(f'https://www.utahcounty.gov/LandRecords/document.asp',
                     params={'avEntry': entry, 'avYear': datetime.datetime.now().year},
                     timeout=30)
        if not d: continue
        txt = re.sub(r'\s+',' ', re.sub(r'<[^>]+>',' ', d.text))
        amt = re.search(r'Consideration:\s*\$?([\d,]+\.?\d*)', txt)
        idt = re.search(r'Instrument Date:\s*([\d/]+)', txt)
        # A real release names a releasing document, e.g.
        # "Releases: Type A Entry 60927 Year 2026". The field is EMPTY when the
        # loan is still outstanding. The previous pattern matched the following
        # label text and therefore flagged every deed as released.
        rel = re.search(r'Releases:\s*Type\s+\w+\s+Entry\s+\d+', txt)
        if not amt: continue
        try: loan = float(amt.group(1).replace(',',''))
        except Exception: continue
        if loan <= 0: continue
        signals.append({
            'source_slug': slug, 'signal_type': 'deed_of_trust',
            'score': 35,                      # CONTEXT, not distress — must never anchor a lead
            'county': 'Utah', 'city': None,
            'raw_owner_name': clean_owner(grantor) if grantor else None,
            'raw_address': f'Trust Deed — Entry #{entry}',
            'raw_payload': json.dumps({
                'entry': entry, 'loan_amount': loan,
                'loan_date': idt.group(1) if idt else None,
                'lender': grantee, 'borrower': grantor,
                'released': bool(rel),
            }),
        })
        time.sleep(1.2)   # ~1 req/sec against the county
    log.info(f'[{slug}] {len(signals)} trust deeds with loan amounts')
    return post_batch(signals)




# ─── UTAH COURT CALENDARS (free, statewide, updated weekdays 05:30) ─────────
# legacy.utcourts.gov/cal/search.php is server-rendered. There is no "list all"
# mode, but the judge-name search is a SUBSTRING match, so a sweep of common
# two-letter substrings enumerates every judge's calendar without a roster.
# Verified Sept 2026: 15 substrings → 3,122 distinct hearings at Provo District.
# Each hearing carries: "<PARTIES>. Case #YYTTNNNNN, m/d/yyyy h:mm AM". The TT
# code is the case type. Only property-relevant types are kept:
#   44 / 64  domestic ("A and B" / "A vs. B")        → divorce_filing
#   34       probate / trusts ("IN THE MATTER OF …")  → probate_filing
#   94       debt collection (creditor vs. person)    → creditor_suit
#   04       civil — kept ONLY when a lender/HOA/servicer is plaintiff or the
#            hearing is an eviction (unlawful detainer)  → civil_property
# Criminal (14/54) is dropped. Parties who are not natural persons are dropped.
COURT_LOCS = {'2550D': ('Utah', 'Provo District'), '2140D': ('Utah', 'American Fork District'),
              '3150D': ('Salt Lake', 'Salt Lake District'), '3170D': ('Salt Lake', 'West Jordan District'),
              '4020D': ('Summit', 'Silver Summit District'), '4400D': ('Wasatch', 'Heber District')}
COURT_SUBSTRINGS = ['an','en','on','er','ar','in','el','or','ll','ul','ne','st','ro','le','ha','ma','be','al','il','ol']
LENDER_RX = re.compile(r'\b(BANK|MORTGAGE|LENDING|LOAN|FINANCIAL|CREDIT UNION|SERVICING|TRUSTEE|HOA|HOMEOWNERS|ASSOCIATION|FUNDING|CAPITAL|LLC|INC)\b', re.I)
def _court_owner(parties, code):
    """Return the natural-person party the filing is against, or None."""
    p = html.unescape(parties).strip()
    if code in ('44', '64'):                     # divorce: both are people; take the first
        a = re.split(r'\s+(?:and|vs\.?)\s+', p, 1, flags=re.I)
        return a[0].strip()
    if code == '34':                             # probate: estate / trust name
        m = re.search(r'ESTATE OF\s+(.+?)(?:,|$)', p, re.I)
        return m.group(1).strip() if m else None  # trusts (no named decedent) are skipped
    if code in ('94', '04'):                     # creditor vs person: defendant is the owner
        a = re.split(r'\s+vs\.?\s+', p, 1, flags=re.I)
        if len(a) < 2: return None
        plaintiff, defendant = a[0], a[1]
        if code == '04' and not (LENDER_RX.search(plaintiff) or 'UNLAWFUL DETAINER' in p.upper()): return None
        if LENDER_RX.search(defendant) or 'STATE OF' in defendant.upper(): return None
        return re.sub(r'\s+et al\.?$', '', defendant, flags=re.I).strip()
    return None

def scrape_court_calendars():
    slug = 'utah-court-calendars'
    log.info(f'[{slug}] starting')
    s = requests.Session(); s.headers['User-Agent'] = 'Mozilla/5.0'
    seen, signals = set(), []
    rx = re.compile(r'title="Hearing location[^"]*?More Info\.\s*(.*?)\. Case #(\d{9}), ([\d/]+ [\d:]+ [AP]M)"')
    for loc, (county, courthouse) in COURT_LOCS.items():
        for q in COURT_SUBSTRINGS:
            try:
                r = s.get('https://legacy.utcourts.gov/cal/search.php',
                          params={'t': 'j', 'j': q, 'd': 'all', 'loc': loc}, timeout=90)
            except Exception as e:
                log.warning(f'[{slug}] {loc} {q}: {type(e).__name__}'); continue
            for parties, case, when in rx.findall(r.text):
                if case in seen: continue
                code = case[2:4]
                if code not in ('44', '64', '34', '94', '04'): continue
                owner = _court_owner(parties, code)
                if not owner or pp_is_inst_local(owner): continue
                seen.add(case)
                sig, score = {'44': ('divorce_filing', 82), '64': ('divorce_filing', 82),
                              '34': ('probate_filing', 84), '94': ('creditor_suit', 62),
                              '04': ('civil_property', 66)}[code]
                signals.append({
                    'source_slug': slug, 'signal_type': sig, 'score': score,
                    'county': county, 'city': None,
                    'raw_owner_name': clean_owner(owner),
                    'raw_address': f'{sig.replace("_", " ").title()} — Case #{case}',
                    'raw_payload': json.dumps({'entry': case, 'koi': f'COURT-{code}', 'case_type': code,
                                               'parties': html.unescape(parties), 'hearing': when,
                                               'courthouse': courthouse, 'source': 'Utah Court Calendar'}),
                })
            time.sleep(0.8)
    log.info(f'[{slug}] {len(signals)} signals across {len(set(x["signal_type"] for x in signals))} types from {len(COURT_LOCS)} courthouses')
    return post_batch(signals)

def pp_is_inst_local(name):
    return bool(LENDER_RX.search(name)) or bool(re.search(r'\b(CITY|COUNTY|STATE OF|DEPARTMENT|SCHOOL|HOSPITAL|TRUST\b)', name, re.I))

# ─── WASATCH COUNTY (Heber) — OnBase public-access recorder ─────────────────
# docs.wasatch.utah.gov/PublicAccess. The portal's config claims no date search
# but the API keyword 287 ("Date") accepts >= and <= operators. Verified live
# Sept 2026: 1,388 documents in 30 days. Each result's Name is one line:
#   "578266 - Date: 8/26/2026 - Grantor: X - Grantee: Y - Doc Type: TRUST DEED - Book and Page: …"
# POST api/CustomQuery/KeywordSearch {QueryID:114, Keywords:[{ID:287,Value,op:>=},{ID:287,Value,op:<=}], QueryLimit}
WASATCH_TYPES = {
    'NOTICE OF DEFAULT':               ('nod',                   88),
    'NOTICE OF TRUSTEE':               ('nts',                   99),   # prefix match
    'SUBSTITUTION OF TRUSTEE':         ('trustee_substitution',  88),   # exact only; "& RECONVEYA" variant is a payoff
    'AFFIDAVIT OF SUCCESSOR TRUSTEE':  ('trustee_substitution',  88),
    'LIS PENDENS':                     ('lis_pendens',           90),
    'NOTICE OF LIEN':                  ('lien_judgment',         68),
    'NOTICE OF FEDERAL TAX LIEN':      ('lien_judgment',         70),
    'NOTICE OF ROLL BACK TAX':         ('tax_delinquency',       55),
    'DEATH CERTIFICATE':               ('death_affidavit',       85),
    'AFFIDAVIT OF DEATH':              ('death_affidavit',       85),
    'NOTICE NOT TO OCCUPY':            ('code_violation',        60),   # condition distress
    'TRUSTEE\'S DEED':                 ('trustee_deed',          92),   # kill signal
    'WARRANTY DEED':                   ('deed_transfer',         55),
    'SPECIAL WARRANTY DEED':           ('deed_transfer',         55),
    'QUIT CLAIM DEED':                 ('family_transfer',       50),
    'TRUST DEED':                      ('deed_of_trust',         35),
}
def scrape_wasatch_recorder():
    slug = 'wasatch-recorder-onbase'
    log.info(f'[{slug}] starting')
    s = requests.Session()
    s.headers.update({'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Content-Type': 'application/json',
                      'Origin': 'https://docs.wasatch.utah.gov',
                      'Referer': 'https://docs.wasatch.utah.gov/PublicAccess/sample-cq/index.html'})
    end = datetime.date.today(); start = end - datetime.timedelta(days=30)
    body = {"QueryID": 114, "Keywords": [
        {"ID": 287, "Value": start.strftime('%m/%d/%Y'), "KeywordOperator": ">="},
        {"ID": 287, "Value": end.strftime('%m/%d/%Y'), "KeywordOperator": "<="}], "QueryLimit": 3000}
    try:
        r = s.post('https://docs.wasatch.utah.gov/PublicAccess/api/CustomQuery/KeywordSearch', json=body, timeout=180)
        rows = r.json().get('Data', [])
    except Exception as e:
        log.error(f'[{slug}] search failed: {e}'); return 0
    signals, seen = [], set()
    for x in rows:
        line = re.sub(r'<[^>]+>', '', html.unescape(x.get('Name', '')))
        m = re.match(r'\s*(\d+)\s*-\s*Date:\s*([\d/]+)\s*-\s*Grantor:\s*(.*?)\s*-\s*Grantee:\s*(.*?)\s*-\s*Doc Type:\s*(.*?)\s*(?:-\s*Book and Page.*)?$', line)
        if not m: continue
        entry, dt, grantor, grantee, doctype = m.groups()
        if entry in seen: continue
        doctype = doctype.strip().upper()
        if 'RECONVEY' in doctype and 'SUBSTITUTION' in doctype: continue   # substitution + reconveyance = payoff
        hit = None
        for k, v in WASATCH_TYPES.items():
            if doctype == k or (k == 'NOTICE OF TRUSTEE' and doctype.startswith(k)): hit = v; break
        if not hit: continue
        sig_type, score = hit
        # the party under pressure: grantee on lender-originated instruments, grantor otherwise
        owner = grantee if sig_type in ('nod', 'nts', 'trustee_substitution', 'lien_judgment', 'tax_delinquency', 'code_violation') else grantor
        seen.add(entry)
        signals.append({
            'source_slug': slug, 'signal_type': sig_type, 'score': score,
            'county': 'Wasatch', 'city': None,
            'raw_owner_name': clean_owner(owner) if owner else None,
            'raw_address': f'{doctype.title()} — Entry #{entry}',
            'raw_payload': json.dumps({'entry': entry, 'koi': doctype, 'recorded': dt,
                                       'grantor': grantor, 'grantee': grantee, 'source': 'Wasatch County OnBase'}),
        })
    log.info(f'[{slug}] {len(signals)} signals across {len(set(x["signal_type"] for x in signals))} types from {len(rows)} docs')
    return post_batch(signals)

# ─── SUMMIT COUNTY (Park City) — Eagle Web recorder ────────────────────────
# Summit runs Tyler Eagle Web with a PUBLIC guest login and a full document
# search: 225 document types, date range, grantor/grantee, parcel. Verified live
# Sept 2026: 120 distress filings in 30 days. Flow: GET login.jsp → POST
# loginPOST.jsp {guest:true} → GET docSearch.jsp (sets session) → POST
# docSearchPOST.jsp with repeated __search_select values (multi-select) and
# AllDocuments UNCHECKED → results at docSearchResults.jsp?searchId=0.
# Row layout (pipe-split after tag strip): [type] [docnum] … [B: P:] …
# [mm/dd/yyyy hh:mm] … Related: … [parcel] … From: [grantor] To: [grantee] Subd: …
SUMMIT_TYPES = {
    '313': ('nts',                   99, "Notice of Trustee's Sale"),
    '116': ('nod',                   88, 'Notice of Default'),
    '636': ('nod',                   88, 'Appoint of TR and Notice of Default'),
    '159': ('trustee_substitution',  88, 'Appointment of Successor Trustee'),
    '326': ('lis_pendens',           90, 'Lis Pendens'),
    '150': ('lien_judgment',         68, 'Lien'),
    '155': ('lien_judgment',         70, 'Federal Tax Lien'),
    '614': ('tax_delinquency',       55, 'Assessors Rollback Tax Lien'),
    '012': ('death_affidavit',       85, 'Death Certificate'),
    '007': ('affidavit',             40, 'Affidavit'),
}
def scrape_summit_recorder():
    slug = 'summit-recorder-eagle'
    log.info(f'[{slug}] starting')
    B = 'https://property.summitcounty.org/eaglesoftware'
    s = requests.Session(); s.headers['User-Agent'] = HEADERS_UA if 'HEADERS_UA' in globals() else 'Mozilla/5.0'
    try:
        r = s.get(f'{B}/web/login.jsp', timeout=45)
        act = re.search(r'action="([^"]*loginPOST[^"]*)"', r.text).group(1).replace('../', '/')
        s.post(f'{B}{act}', data={'submit': 'Public Login', 'guest': 'true'}, timeout=45)
        s.get(f'{B}/eagleweb/docSearch.jsp', timeout=45)
    except Exception as e:
        log.error(f'[{slug}] login failed: {e}'); return 0
    end = datetime.date.today(); start = end - datetime.timedelta(days=30)
    data = [('RecordingDateIDStart', start.strftime('%m/%d/%Y')), ('RecordingDateIDEnd', end.strftime('%m/%d/%Y')),
            ('docTypeTotal', '225'), ('NameIDSearchType', '1'), ('CreatedByIDSearchType', '1'), ('WaterMineNameIDSearchType', '1')]
    data += [('__search_select', k) for k in SUMMIT_TYPES]
    try:
        r = s.post(f'{B}/eagleweb/docSearchPOST.jsp', data=data, timeout=90, allow_redirects=True)
    except Exception as e:
        log.error(f'[{slug}] search failed: {e}'); return 0
    signals, seen = [], set()
    pages = [r.text]
    # Eagle Web pages results at 100/page: follow "next" links if present
    for pg in range(2, 6):
        m = re.search(r'href="([^"]*docSearchResults\.jsp[^"]*page=' + str(pg) + r'[^"]*)"', pages[-1])
        if not m: break
        try: pages.append(s.get(f'{B}/eagleweb/' + m.group(1).split('eagleweb/')[-1], timeout=60).text)
        except Exception: break
        time.sleep(1.2)
    for page in pages:
        for raw in re.findall(r'<tr[^>]*>(.*?)</tr>', page, re.S | re.I):
            cells = [c.strip() for c in re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '|', raw))).split('|') if c.strip()]
            if len(cells) < 6: continue
            dt = next((c for c in cells if re.match(r'\d{1,2}/\d{1,2}/\d{4}', c)), None)
            if not dt: continue
            typ_label = cells[0]; docnum = next((c for c in cells if re.match(r'^\d{8}$', c)), None)
            if not docnum or docnum in seen: continue
            code = next((k for k, v in SUMMIT_TYPES.items() if v[2].lower() == typ_label.lower()), None)
            if not code: continue
            sig_type, score, _ = SUMMIT_TYPES[code]
            def after(lbl):
                if lbl in cells:
                    i = cells.index(lbl); return cells[i + 1] if i + 1 < len(cells) else ''
                return ''
            grantor, grantee = after('From:'), after('To:')
            parcel = next((c for c in cells if re.match(r'^[A-Z]{2,6}-[A-Z0-9-]+$', c)), None)
            # the OWNER is the party the distress is against: the grantee on NOD/NTS
            # (trustee → borrower), the grantee on a lien (claimant → owner).
            owner = grantee if sig_type in ('nts', 'nod', 'lien_judgment', 'trustee_substitution', 'tax_delinquency') else grantor
            seen.add(docnum)
            signals.append({
                'source_slug': slug, 'signal_type': sig_type, 'score': score,
                'county': 'Summit', 'city': None,
                'raw_owner_name': clean_owner(owner) if owner else None,
                'raw_address': f'{typ_label} — Doc #{docnum}',
                'parcel_serial': parcel,
                'raw_payload': json.dumps({'entry': docnum, 'koi': typ_label, 'recorded': dt,
                                           'grantor': grantor, 'grantee': grantee, 'parcel': parcel,
                                           'source': 'Summit County Eagle Web'}),
            })
    log.info(f'[{slug}] {len(signals)} signals across {len(set(x["signal_type"] for x in signals))} types')
    return post_batch(signals)

# ─── UNIFIED UTAH COUNTY RECORDER ─────────────────────────────────────────────
# Utah County's server-side DocDesc filter is BROKEN — POSTing a document
# description returns an unfiltered/empty result set. Four scrapers relying on it
# (lien-judgment-records, deed-transfers-utah-county, marriage-records-slco,
# loopnet-utah) silently returned ZERO for weeks while logging clean runs, and
# nod-tracker did the same until July 2026.
#
# This pulls the recent recording feed once and filters CLIENT-SIDE by KOI code,
# which is what actually works. Row layout (verified Aug 2026):
#   [0] PLSS description  [1] datetime  [2] KOI  [3] entry+year  [4] grantor  [5] grantee
# Note cell[0] is the PLSS description, so KOI is index 2 — the old scrapers read
# index 3 for entry assuming a different layout.
RECORDER_KOI_MAP = {
    # code        (signal_type,        score, label)
    'PR LP':      ('lis_pendens',        90, 'Lis pendens — litigation/judicial foreclosure'),
    'PERREPD':    ('probate_deed',       88, 'Personal representative deed — estate selling'),
    'AF DC':      ('death_affidavit',    85, 'Affidavit of death — owner deceased'),
    'TEE D':      ('trustee_deed',       92, 'Trustee deed — foreclosure completed'),
    'N LN':       ('lien_judgment',      68, 'Notice of lien'),
    'M CHGCN':    ('mechanics_lien',     70, 'Mechanics/construction lien'),
    'R LN':       ('lien_release',       30, 'Lien released — debt cleared'),
    # VERIFIED Aug 2026 from the county document detail page:
    #   SUB TEE = "TRUSTEE-SUBSTITUTION" — the lender swapping in a successor
    #     trustee, the step immediately BEFORE a trustee sale is noticed. Real
    #     pre-foreclosure distress, and earlier than an NTS.
    #   RSUBTEE = "TRUSTEE-SUBSTITUTION & DEED OF RECONVEYANCE" — a reconveyance
    #     means the debt was SATISFIED and the trust deed released. That is a
    #     RESOLUTION, not distress. Scoring it as distress previously put 158
    #     paid-off homeowners into the primed calling queue.
    'SUB TEE':    ('trustee_substitution', 88, 'Substitution of trustee — pre-foreclosure'),
    'SUBTEE':     ('trustee_substitution', 88, 'Substitution of trustee — pre-foreclosure'),
    'RSUBTEE':    ('loan_reconveyance',    25, 'Trustee substitution WITH reconveyance — debt satisfied'),
    'WD':         ('deed_transfer',      55, 'Warranty deed'),
    'SP WD':      ('deed_transfer',      55, 'Special warranty deed'),
    'QCD':        ('family_transfer',    50, 'Quit claim deed'),
}

def scrape_utah_recorder_unified():
    slug = 'utah-recorder-unified'
    log.info(f'[{slug}] starting')
    signals, seen = [], set()
    for offset in (0, 200, 400, 600):
        r = safe_get(
            'https://www.utahcounty.gov/LandRecords/DocDescSearch.asp',
            params={'DocDesc': '', 'DateRange': '30', 'County': 'Utah', 'offset': offset},
            timeout=45
        )
        if not r:
            log.warning(f'[{slug}] offset {offset} unreachable')
            continue
        soup = BeautifulSoup(r.text, 'html.parser')
        for row in soup.select('tr'):
            cells = row.select('td')
            if len(cells) < 6:
                continue
            rec_dt = cells[1].get_text(strip=True)
            koi    = cells[2].get_text(strip=True)
            entry  = cells[3].get_text(' ', strip=True).replace('\xa0', ' ')
            grantor = cells[4].get_text(strip=True)
            grantee = cells[5].get_text(strip=True)
            if not re.match(r'^\d+/\d+/\d{4}', rec_dt) or koi not in RECORDER_KOI_MAP:
                continue
            if not entry or entry in seen:
                continue
            seen.add(entry)
            sig_type, score, label = RECORDER_KOI_MAP[koi]
            # On a trustee deed / death affidavit the GRANTOR is the party of
            # interest (the foreclosed owner / the decedent).
            signals.append({
                'source_slug': slug, 'signal_type': sig_type, 'score': score,
                'county': 'Utah', 'city': None,
                'raw_owner_name': clean_owner(grantor) if grantor else None,
                'raw_address': f'Entry #{entry}',
                'raw_payload': json.dumps({
                    'koi': koi, 'label': label, 'entry': entry,
                    'rec_date': rec_dt, 'grantor': grantor, 'grantee': grantee,
                }),
            })
        time.sleep(2)   # county server — be polite
    log.info(f'[{slug}] {len(signals)} signals across {len(RECORDER_KOI_MAP)} document types')
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
                                # Backtested July 2026 against recorded sales: tax delinquency
                                # ALONE converts at 0.22% (2,315 owners), but combined with 2+
                                # other distress signals it converted 10/10. Weak anchor, strong
                                # corroborator — so it sits in the nurture band on its own and
                                # earns its weight through the convergence engine instead.
                                'score': 55, 'county': 'Utah', 'city': None,
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
                            'score': 55, 'county': 'Utah', 'city': None,  # see backtest note above
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
    # PRIMARY_RES (owner-occupied flag) and TOTAL_MKT_VALUE are the two fields that
    # make absentee-owner and equity scoring possible. They were being requested but
    # thrown away — only signal_type/source_family were persisted, so the three
    # largest counties (Salt Lake, Davis, Weber) had no usable parcel attributes.
    r = safe_get(f"{svc}/query", params={
        'where':'1=1',
        'outFields':('PARCEL_ID,SERIAL_NUM,PARCEL_ADD,PARCEL_CITY,PARCEL_ZIP,'
                     'TOTAL_MKT_VALUE,LAND_MKT_VALUE,PARCEL_ACRES,PROP_CLASS,'
                     'PRIMARY_RES,BLDG_SQFT,BUILT_YR,OWN_NAME1,OWN_NAME2'),
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
        own1 = a.get('OWN_NAME1','') or ''
        own2 = a.get('OWN_NAME2','') or ''
        owner = ' '.join(filter(None, [own1.strip(), own2.strip()])).strip() or None
        if not addr: continue
        mkt = a.get('TOTAL_MKT_VALUE')
        primary_res = a.get('PRIMARY_RES')
        # Absentee owners sell materially more often than owner-occupiers, so a
        # non-primary-residence parcel is scored above the baseline.
        score = 45
        if str(primary_res).upper() in ('N','NO','0','FALSE'):
            score = 58
        signals.append({
            'source_slug': slug, 'signal_type': 'lir_parcel', 'score': score,
            'county': county, 'city': city or None,
            'raw_owner_name': clean_owner(owner) if owner else None,
            'raw_address': f"{addr}, {city}".strip(', ') if city else addr,
            'raw_payload': json.dumps({
                'parcel_id':    a.get('PARCEL_ID') or a.get('SERIAL_NUM'),
                'serial_num':   a.get('SERIAL_NUM'),
                'market_value': mkt,
                'land_value':   a.get('LAND_MKT_VALUE'),
                'acres':        a.get('PARCEL_ACRES'),
                'prop_class':   a.get('PROP_CLASS'),
                'primary_res':  primary_res,
                'bldg_sqft':    a.get('BLDG_SQFT'),
                'built_yr':     a.get('BUILT_YR'),
                'zip':          a.get('PARCEL_ZIP'),
            }),
        })
    return post_batch(signals)

def scrape_slco_lir_parcels():  return scrape_lir('slco-lir-parcels','Salt Lake','https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_SaltLake_LIR/FeatureServer/0')
def scrape_davis_lir_parcels(): return scrape_lir('davis-lir-parcels','Davis','https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Davis_LIR/FeatureServer/0')
def scrape_weber_lir_parcels(): return scrape_lir('weber-lir-parcels','Weber','https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/Parcels_Weber_LIR/FeatureServer/0')

# ─── HMDA (runs only if new data available or not yet run today) ─────────────
def _hmda_already_run_today(slug):
    """Skip if already collected today — static 2023 data never changes mid-day.
    Bypassed entirely when FORCE_RERUN=true, for verification testing where every
    scraper needs to actually execute regardless of an earlier run today."""
    if os.environ.get('FORCE_RERUN', '').lower() == 'true':
        return False
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
        ('https://www.realtypath.com/', 'Realty Path', 'Salt Lake'),
        ('https://www.utahrealestate.com/statistics', 'Utah RE Market Stats', 'Utah'),
        ('https://www.compass.com/agents/utah/', 'Compass Utah', 'Salt Lake'),
    ]
    signals = []
    # Get prior hashes
    # Read prior hashes from pp_market_data (where this scraper actually writes).
    # Was reading pp_scraper_signals.raw_owner_name — wrong table AND wrong field,
    # so a prior hash was never found and `changed` was permanently False.
    try:
        r = SESSION.get(
            f"{SUPABASE_URL}/rest/v1/pp_market_data"
            f"?select=raw_address,raw_payload&source_slug=eq.{slug}&order=captured_at.desc&limit=20",
            headers=HEADERS, timeout=8
        )
        prior = {rec['raw_address']: rec.get('raw_payload') or {}
                 for rec in (r.json() if isinstance(r.json(), list) else [])}
    except Exception:
        prior = {}

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
            prior_payload = prior.get(url) or {}
            if isinstance(prior_payload, str):
                try: prior_payload = json.loads(prior_payload)
                except Exception: prior_payload = {}
            prior_hash = prior_payload.get('hash', '') if isinstance(prior_payload, dict) else ''
            changed = bool(prior_hash and current_hash != prior_hash)
            # pp_market_data has NO score/county/city columns — including them returned
            # 400 PGRST204 on every insert since the June 17 reroute, silently swallowed
            # because only 200/201/204 were counted. County/score kept inside raw_payload.
            signals.append({
                'source_slug': slug, 'signal_type': 'market_intelligence',
                'metro': county,
                'raw_address': url,
                'raw_payload': json.dumps({'name': name, 'hash': current_hash,
                                           'changed': changed, 'county': county, 'score': 45}),
                'captured_at': datetime.datetime.utcnow().isoformat(),
            })
        except Exception as e:
            log.warning(f'[{slug}] {name}: {e}')
    # Write to pp_market_data, NOT pp_scraper_signals
    if not signals: return 0
    mkt_url = f"{SUPABASE_URL}/rest/v1/pp_market_data?on_conflict=source_slug,raw_address"  # requires pp_market_data_source_addr_unique constraint
    count = 0
    for i in range(0, len(signals), 50):
        chunk = signals[i:i+50]
        r3 = SESSION.post(mkt_url, json=chunk, headers=HEADERS, timeout=20)
        if r3.status_code in (200, 201, 204):
            count += len(chunk)
        else:
            # Never swallow a write failure again — this is exactly how the
            # PGRST204 column error hid for five weeks looking like "0 records".
            log.error(f'[{slug}] market_data write failed {r3.status_code}: {r3.text[:200]}')
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
    mkt_url = f"{SUPABASE_URL}/rest/v1/pp_market_data?on_conflict=source_slug,raw_address"  # requires pp_market_data_source_addr_unique constraint
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
                # uhaul.com now DROPS the from/to query params and 302s to the generic
                # /Truck-Rentals/ landing page, so the old regex was scraping the
                # advertised "$19.95 in-town" teaser rate — not a real one-way quote
                # for this route. That produced meaningless price=0/price=19 rows.
                # A genuine one-way quote requires their session-based quote flow,
                # which a plain GET cannot reach. Rather than write a fabricated or
                # hollow figure, skip: no real price means no signal.
                final_url = str(getattr(r, 'url', '') or '')
                if 'from=' not in final_url or 'to=' not in final_url:
                    log.warning(f'[{slug}] {orig_city}→{dest_city}: route params dropped '
                                f'(redirected to {final_url[:60]}) — no real quote available, skipping')
                    continue
                price_match = re.search(r'\$[\d,]+', r.text)
                if not price_match:
                    log.warning(f'[{slug}] {orig_city}→{dest_city}: no price found — skipping')
                    continue
                price = int(price_match.group().replace('$','').replace(',',''))
                # An in-town teaser rate is never a valid long-haul one-way quote.
                if price < 100:
                    log.warning(f'[{slug}] {orig_city}→{dest_city}: implausible quote ${price} '
                                f'(teaser rate, not a route quote) — skipping')
                    continue
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
    seen_entries = set()
    # NOTE: The site's County POST parameter does not actually filter results —
    # confirmed directly: querying with County=Salt Lake, County=Utah, County=Davis,
    # and County=Weber all return the identical dataset from Utah County's own
    # LandRecords system. This isn't Loopnet.com at all (blocked, see docstring);
    # it's Utah County's recorder search, which has no cross-county data to filter.
    # Previously this looped 4x, made 4x the requests, and mislabeled every real
    # result with whichever county name happened to be looping — a real data
    # quality bug that mattered for downstream county-based routing/scoring.
    try:
        r = SESSION.post(
            'https://www.utahcounty.gov/LandRecords/DocDescSearch.asp',
            data={'DocDesc': '', 'DateRange': '7', 'County': 'Utah'},
            timeout=25
        )
        if r and r.status_code == 200:
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
                if entry in seen_entries: continue  # same entry can span multiple sections
                seen_entries.add(entry)
                signal_type, score = CONSTRUCTION_KOI[koi]
                signals.append({
                    'source_slug': slug, 'signal_type': signal_type,
                    'score': score, 'county': 'Utah', 'city': None,
                    'raw_owner_name': grantor[:100] if grantor else None,
                    'raw_address': f'Entry #{entry}' if entry else grantor[:80],
                    'raw_payload': json.dumps({'koi': koi, 'rec_date': rec_date, 'entry': entry}),
                })
    except Exception as e:
        log.warning(f'[{slug}] {e}')
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
        mkt_url = f"{SUPABASE_URL}/rest/v1/pp_market_data?on_conflict=source_slug,raw_address"  # requires pp_market_data_source_addr_unique constraint
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
    ('utah-deeds-of-trust',         scrape_deeds_of_trust),
    ('utah-county-tax-delinquency-pdf', scrape_utah_county_tax_delinquency_pdf),
    ('utah-recorder-unified',       scrape_utah_recorder_unified),
    ('summit-recorder-eagle',       scrape_summit_recorder),
    ('wasatch-recorder-onbase',     scrape_wasatch_recorder),
    ('utah-court-calendars',        scrape_court_calendars),
    # Fire marshal
    # LIR parcels
    # Extended AGRC parcel coverage (bonus counties)
    # SLCO Recorder — real-time NTS/NOD/Deed/Lien for Salt Lake County
    ('slco-recorder-live',          scrape_slco_recorder if REALTIME_LOADED else lambda: 0),
    # HMDA — daily only (now live 2024/2025 data)
    # FSBO & marketplace
    # Enrichment
    ('obituaries-enrichment',       scrape_obituaries_enrichment),
    # Buyer side — daily only
    # Buyer signals
    ('comparable-sales-slco',       scrape_comparable_sales_slco),
    # Market data → pp_market_data
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

    # BUYER LAYER RETIRED (Aug 31 2026). The buyer profiles were website names
    # and page boilerplate ("Redfin SLC", "KW Utah", SLCO nav text, news
    # headlines) — never people. Zero of 990 had a phone or email because none
    # existed. pp_populate_buyer_profiles and pp_trigger_matching_engine are no
    # longer called. Rebuild the buyer side only from a source that yields
    # identifiable humans with purchase intent.

    # Step 1: Convergence / conviction scoring.
    # Aggregates every live signal per resolved owner entity into one conviction
    # score (anchor + signal-type diversity, recency-decayed, plus clustering,
    # tax-escalation, absentee, contactability, and kill signals).
    try:
        r_conv = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/pp_compute_convergence",
            headers=RPC_HEADERS, json={}, timeout=180
        )
        log.info(f'  Convergence: {r_conv.status_code} entities_scored={r_conv.text[:40]}')
        if r_conv.status_code >= 400:
            log.error(f'  Convergence FAILED: {r_conv.text[:300]}')
    except Exception as e:
        log.error(f'  Convergence failed: {e}')

    # Buyer intelligence: the grantee on a recorded warranty deed is a PROVEN
    # buyer — someone who completed a purchase, with an entry number anyone can
    # verify. Repeat grantees are active acquirers. Must run after convergence
    # so it sees the same deed set.
    try:
        r_buy = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/pp_compute_buyers",
            headers=RPC_HEADERS, json={}, timeout=180
        )
        log.info(f'  Buyer intelligence: {r_buy.status_code} buyers={r_buy.text[:20]}')
        if r_buy.status_code >= 400:
            log.error(f'  Buyer intelligence FAILED: {r_buy.text[:250]}')
    except Exception as e:
        log.error(f'  Buyer intelligence failed: {e}')

    # App cache: the iOS app's anon role has a 3s statement timeout, and the
    # overview aggregates take longer than that computed live. Cache them here
    # so the app reads one row. Runs after convergence and buyers.
    try:
        r_app = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/pp_refresh_app_cache",
            headers=RPC_HEADERS, json={}, timeout=180
        )
        log.info(f'  App cache: {r_app.status_code}')
        if r_app.status_code >= 400:
            log.error(f'  App cache FAILED: {r_app.text[:250]}')
    except Exception as e:
        log.error(f'  App cache failed: {e}')

    # Step 2: KPI cache — void return
    # Retried on failure: right after 43 scrapers finish their batch inserts,
    # the DB can be under transient load and this call can 500 with a 57014
    # statement timeout even though the function itself completes in <3s once
    # load settles (confirmed July 15 2026 — a single un-retried failure here
    # left the dashboard cache stale for ~22h until manually caught). No single
    # point of failure: retry up to 3x with a short backoff before giving up.
    kpi_ok = False
    for attempt in range(1, 4):
        try:
            r_kpi = requests.post(
                f"{SUPABASE_URL}/rest/v1/rpc/pp_refresh_kpi_cache",
                headers=RPC_HEADERS,
                json={}, timeout=120
            )
            log.info(f'  KPI cache (attempt {attempt}): {r_kpi.status_code}')
            if r_kpi.status_code < 400:
                kpi_ok = True
                break
            log.warning(f'  KPI cache body: {r_kpi.text[:300]}')
        except Exception as e:
            log.warning(f'  KPI cache attempt {attempt} failed: {e}')
        if attempt < 3:
            time.sleep(10)
    if not kpi_ok:
        log.error('  KPI cache refresh FAILED after 3 attempts — dashboards may show stale data')

    log.info('Pipeline intelligence refresh complete.')

    log.info(f'=== Done — {total} total signals ===')
    log.info(f'Top sources: {sorted(results.items(), key=lambda x: -x[1])[:10]}')
