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
SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
SOURCE_SLUG  = 'mls-expired-listings'
SIGNAL_TYPE  = 'expired_listing'
SCORE_BASE   = 95
COUNTIES     = {'Salt Lake', 'Utah', 'Weber', 'Davis'}
CHECKSUM     = os.environ.get('URE_EXPIRED_CHECKSUM', 'd751713988987e9331980363e24189ce')
HDR = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}',
       'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

def run() -> int:
    try:
        from ure_session import get_session, parse_search_listnos, parse_listing
    except ImportError:
        from scrapers.ure_session import get_session, parse_search_listnos, parse_listing

    log.info(f'[{SOURCE_SLUG}] starting')
    sess = get_session()
    if not sess.ensure_alive():
        log.warning(f'[{SOURCE_SLUG}] session could not authenticate — attempting anyway')

    signals, seen = [], set()

    for page in range(1, 6):
        html = sess.search_perform(checksum=CHECKSUM, page=page)
        listnos = parse_search_listnos(html) if html else []
        log.info(f'[{SOURCE_SLUG}] page {page}: {len(listnos)} listing IDs found')
        if not listnos:
            break
        for listno in listnos:
            if listno in seen:
                continue
            seen.add(listno)
            detail_html = sess.get_listing(listno)
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
