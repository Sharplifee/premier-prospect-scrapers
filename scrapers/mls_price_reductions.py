#!/usr/bin/env python3
"""
Premier Prospect™ — MLS Price Reductions
Source:  utahrealestate.com (session-cookie auth, reverse-engineered)
Signal:  price_reduction — Score 78 (HOT, agent_first)
Auth:    ure_session.py — shakel/Ronnal13= member 88098
"""
import os, json, logging, hashlib, requests, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

log = logging.getLogger(__name__)
SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
SOURCE_SLUG  = 'mls-price-reductions'
SIGNAL_TYPE  = 'price_reduction'
SCORE        = 78
COUNTIES     = {'Salt Lake', 'Utah', 'Weber', 'Davis'}
CHECKSUM     = os.environ.get('URE_PRICE_RED_CHECKSUM', 'd751713988987e9331980363e24189ce')
HDR = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}',
       'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

def run() -> int:
    try:
        from ure_session import get_session, parse_search_listnos, parse_listing
    except ImportError:
        from scrapers.ure_session import get_session, parse_search_listnos, parse_listing

    log.info(f'[{SOURCE_SLUG}] starting')
    sess = get_session()
    sess.ensure_alive()
    signals, seen = [], set()

    for page in range(1, 4):
        html = sess.search_perform(checksum=CHECKSUM, page=page)
        listnos = parse_search_listnos(html) if html else []
        log.info(f'[{SOURCE_SLUG}] page {page}: {len(listnos)} listings')
        if not listnos:
            break
        for listno in listnos:
            if listno in seen:
                continue
            seen.add(listno)
            listing = parse_listing(sess.get_listing(listno), listno)
            if not listing:
                continue
            county = listing.get('county', '').replace(' County', '').strip()
            if county and county not in COUNTIES:
                continue
            address = listing.get('address') or f'MLS #{listno}'
            dedup = hashlib.sha256(f'{SOURCE_SLUG}:{listno}'.encode()).hexdigest()
            signals.append({
                'source_slug': SOURCE_SLUG, 'signal_type': SIGNAL_TYPE,
                'raw_address': address, 'city': listing.get('city'),
                'county': county or None, 'score': SCORE,
                'primed_stage': 1, 'motivation_probability': 72,
                'outreach_routing': 'agent_first', 'dedupe_hash': dedup,
                'raw_payload': json.dumps({
                    'listno': listno, 'price': listing.get('price', 0),
                    'listing_url': f'https://www.utahrealestate.com/member/{listno}'
                })
            })

    if not signals:
        log.info(f'[{SOURCE_SLUG}] 0 signals')
        return 0
    r = requests.post(f'{SUPABASE_URL}/rest/v1/pp_scraper_signals', headers=HDR, json=signals, timeout=30)
    inserted = len(signals) if r.status_code in [200, 201] else 0
    log.info(f'[{SOURCE_SLUG}] done — {inserted} inserted')
    return inserted

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run()
