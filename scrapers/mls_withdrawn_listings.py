#!/usr/bin/env python3
"""
Premier Prospect™ — MLS withdrawn
Score: 82 | signal_type: withdrawn_listing
"""
import os, json, logging, hashlib, re, requests
log = logging.getLogger(__name__)

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
SOURCE_SLUG  = 'mls-withdrawn-listings'
SIGNAL_TYPE  = 'withdrawn_listing'
SCORE        = 82
COUNTIES     = {'Salt Lake', 'Utah', 'Weber', 'Davis'}
CHECKSUM     = os.environ.get('URE_WITHDRAWN_CHECKSUM', 'd751713988987e9331980363e24189ce')
HDR = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}',
       'Content-Type': 'application/json', 'Prefer': 'return=minimal,resolution=ignore-duplicates'}

_URE_BASE = 'https://www.utahrealestate.com'

def _ure_headers(cookie, referer=''):
    return {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Cookie': cookie,
        'x-requested-with': 'XMLHttpRequest',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Referer': referer or _URE_BASE + '/',
        'cache-control': 'no-cache',
    }

def _search_perform(sess, cookie, checksum, page=1, per_page=25):
    start = (page - 1) * per_page
    url = f'{_URE_BASE}/search/perform/format/json/type/1/count/{per_page}/start/{start}/checksum/{checksum}'
    try:
        r = sess.get(url, headers=_ure_headers(cookie, _URE_BASE + '/search/form/type/1/'), timeout=20)
        return r.content if r.status_code == 200 else b''
    except Exception as e:
        log.warning(f'_search_perform p{page}: {e}')
        return b''

def _get_listing_html(sess, cookie, listno):
    try:
        r = sess.get(f'{_URE_BASE}/member/{listno}',
            headers=_ure_headers(cookie, _URE_BASE + '/search/results/'), timeout=15)
        return r.content if r.status_code == 200 else b''
    except Exception as e:
        log.warning(f'_get_listing_html {listno}: {e}')
        return b''

def _parse_listnos(raw):
    """Extract listing numbers from search result JSON."""
    try:
        d = json.loads(raw)
        if isinstance(d, dict):
            return d.get('listnos', d.get('listing_ids', []))
        return []
    except Exception:
        nums = re.findall(r'"(\d{6,8})"', raw.decode('utf-8', errors='ignore'))
        return list(dict.fromkeys(nums))

def _parse_listing(html, listno):
    """Extract address, city, county, price from listing HTML."""
    text = html.decode('utf-8', errors='ignore') if isinstance(html, bytes) else html
    price_m  = re.search(r'\$([\d,]+)', text)
    city_m   = re.search(r'"city"\s*:\s*"([^"]+)"', text)
    county_m = re.search(r'"county"\s*:\s*"([^"]+)"', text)
    addr_m   = re.search(r'"address"\s*:\s*"([^"]+)"', text)
    if not addr_m:
        addr_m = re.search(r'<h1[^>]*>([^<]{10,80})</h1>', text)
    return {
        'address': addr_m.group(1).strip() if addr_m else f'MLS #{listno}',
        'city':    city_m.group(1).strip()   if city_m   else None,
        'county':  county_m.group(1).replace(' County','').strip() if county_m else None,
        'price':   int(price_m.group(1).replace(',','')) if price_m else 0,
    }

def run() -> int:
    log.info(f'[{SOURCE_SLUG}] starting')
    cookie = os.environ.get('URE_SESSION_COOKIE', '')
    if not cookie:
        log.warning(f'[{SOURCE_SLUG}] no URE_SESSION_COOKIE — skipping')
        return 0

    sess = requests.Session()
    sess.headers.update({'User-Agent': 'Mozilla/5.0'})

    signals, seen = [], set()
    for page in range(1, 6):
        raw = _search_perform(sess, cookie, CHECKSUM, page)
        listnos = _parse_listnos(raw)
        log.info(f'[{SOURCE_SLUG}] page {page}: {len(listnos)} listings')
        if not listnos:
            break
        for listno in listnos:
            if listno in seen:
                continue
            seen.add(listno)
            html    = _get_listing_html(sess, cookie, listno)
            listing = _parse_listing(html, listno)
            county  = listing.get('county', '')
            if county and county not in COUNTIES:
                continue
            dedup = hashlib.sha256(f'{SOURCE_SLUG}:{listno}'.encode()).hexdigest()
            signals.append({
                'source_slug': SOURCE_SLUG,
                'signal_type': SIGNAL_TYPE,
                'score':       SCORE,
                'raw_address': listing['address'],
                'city':        listing.get('city'),
                'county':      county or None,
                'dedupe_hash': dedup,
                'raw_payload': json.dumps({
                    'listno': listno,
                    'price':  listing.get('price', 0),
                    'url':    f'{_URE_BASE}/member/{listno}',
                }),
            })

    if not signals:
        log.info(f'[{SOURCE_SLUG}] 0 signals')
        return 0

    # Batch insert
    inserted = 0
    for i in range(0, len(signals), 100):
        chunk = signals[i:i+100]
        r = requests.post(f'{SUPABASE_URL}/rest/v1/pp_scraper_signals',
            json=chunk, headers=HDR, timeout=30)
        if r.status_code in (200, 201, 204):
            inserted += len(chunk)
        else:
            log.error(f'[{SOURCE_SLUG}] insert {r.status_code}: {r.text[:80]}')
    log.info(f'[{SOURCE_SLUG}] {inserted} signals written')
    return inserted
