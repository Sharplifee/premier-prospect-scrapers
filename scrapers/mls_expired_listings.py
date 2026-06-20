#!/usr/bin/env python3
"""
Premier Prospect™ — MLS Expired Listings
Source:  utahrealestate.com (session-cookie auth, reverse-engineered)
Signal:  expired_listing — Score 95 (HOT, direct_agent)
Auth:    ure_session.py — shakel/Ronnal13= member 88098
Strategy: search_perform → parse listing IDs → get_listing per ID → extract fields
"""
import os, json, logging, hashlib, requests, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

log = logging.getLogger(__name__)
# ── Self-contained MLS helpers (no ure_session import dependency) ───────────
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

def __search_perform(sess, cookie, checksum, page=1, per_page=25):
    start = (page - 1) * per_page
    url = f'{_URE_BASE}/search/perform/format/json/type/1/count/{per_page}/start/{start}/checksum/{checksum}'
    try:
        r = sess.get(url, headers=_ure_headers(cookie, _URE_BASE + '/search/form/type/1/'), timeout=20)
        return r.content if r.status_code == 200 else b''
    except Exception as e:
        log.warning(f'_search_perform p{page}: {e}')
        return b''

def __get_listing_html(sess, cookie, listno):
    try:
        r = sess.get(f'{_URE_BASE}/member/{listno}',
            headers=_ure_headers(cookie, _URE_BASE + '/search/results/'), timeout=15)
        return r.content if r.status_code == 200 else b''
    except Exception as e:
        log.warning(f'_get_listing_html {listno}: {e}')
        return b''

def _get_ure_session():
    """Get authenticated session using URE_SESSION_COOKIE env var directly."""
    cookie = os.environ.get('URE_SESSION_COOKIE', '')
    sess = requests.Session()
    sess.headers.update({'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'})
    return sess, cookie

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
SOURCE_SLUG  = 'mls-expired-listings'
SIGNAL_TYPE  = 'expired_listing'
SCORE_BASE   = 95
COUNTIES     = {'Salt Lake', 'Utah', 'Weber', 'Davis'}
CHECKSUM     = os.environ.get('URE_EXPIRED_CHECKSUM', 'd751713988987e9331980363e24189ce')
HDR = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}',
       'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

# Use quad-redundancy auth engine when available
try:
    from ure_auth_engine import get_authenticated_session as _get_session, _current_cookie
    from ure_session import parse_search_listnos, parse_listing, search_perform, get_listing_html
    _HAS_AUTH_ENGINE = True
except ImportError:
    _HAS_AUTH_ENGINE = False
    try:
        from scrapers.ure_session import parse_search_listnos, parse_listing, search_perform, get_listing_html
    except ImportError:
        from ure_session import parse_search_listnos, parse_listing, search_perform, get_listing_html

URE_BASE = 'https://www.utahrealestate.com'

def _get_auth_session():
    """Returns (session, cookie) — works with auth engine or direct cookie env var."""
    cookie = os.environ.get('URE_SESSION_COOKIE', '')
    if _HAS_AUTH_ENGINE:
        try:
            sess = _get_session()
            from ure_auth_engine import _current_cookie as cc
            if cc:
                return sess, cc
        except Exception:
            pass
    # Fallback: build session from URE_SESSION_COOKIE env var directly
    sess = requests.Session()
    sess.headers.update({
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Cookie': cookie,
        'x-requested-with': 'XMLHttpRequest',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Referer': f'{URE_BASE}/search/form/type/1/name/quick',
    })
    return sess, cookie

def run() -> int:
    log.info(f'[{SOURCE_SLUG}] starting')
    sess, cookie = _get_ure_session()
    if not cookie:
        log.warning(f'[{SOURCE_SLUG}] no URE session cookie — skipping')
        return 0

    signals, seen = [], set()

    for page in range(1, 6):
        html = _search_perform(sess, cookie, checksum=CHECKSUM, page=page)
        listnos = parse_search_listnos(html) if html else []
        log.info(f'[{SOURCE_SLUG}] page {page}: {len(listnos)} listing IDs found')
        if not listnos:
            break
        for listno in listnos:
            if listno in seen:
                continue
            seen.add(listno)
            detail_html = _get_listing_html(sess, cookie, listno)
            listing = parse_listing(detail_html, listno)
            if not listing:
                continue
            county = listing.get('county', '').replace(' County', '').strip()
            if county and county not in COUNTIES:
                continue
            address = listing.get('address') or f'MLS #{listno}'
            dom_raw = re.sub(r'[^0-9]', '', str(listing.get('days_on_market', '0')))
            dom = int(dom_raw or 0)
            price = listing.get('price', 0) or 0
            score = min(SCORE_BASE + (5 if dom > 180 else 0) + (3 if price >= 500000 else 0), 100)
            dedup = hashlib.sha256(f'{SOURCE_SLUG}:{listno}'.encode()).hexdigest()
            signals.append({
                'source_slug': SOURCE_SLUG, 'signal_type': SIGNAL_TYPE,
                'raw_address': address, 'raw_owner_name': listing.get('agent_id') or None,
                'city': listing.get('city'), 'county': county or None,
                'score': score, 'primed_stage': 2, 'motivation_probability': 88,
                'outreach_routing': 'direct_agent', 'dedupe_hash': dedup,
                'raw_payload': json.dumps({
                    'listno': listno, 'price': price, 'days_on_market': dom,
                    'year_built': listing.get('year_built'),
                    'beds': listing.get('beds'), 'baths': listing.get('baths'),
                    'listing_url': f'https://www.utahrealestate.com/member/{listno}',
                    'source': 'utahrealestate.com'
                })
            })

    if not signals:
        log.info(f'[{SOURCE_SLUG}] 0 signals (session may need URE_SESSION_COOKIE)')
        return 0

    inserted = 0
    for i in range(0, len(signals), 100):
        r = requests.post(f'{SUPABASE_URL}/rest/v1/pp_scraper_signals',
                          headers=HDR, json=signals[i:i+100], timeout=30)
        if r.status_code in [200, 201]:
            inserted += len(signals[i:i+100])

    log.info(f'[{SOURCE_SLUG}] done — {inserted} inserted')
    return inserted

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run()
