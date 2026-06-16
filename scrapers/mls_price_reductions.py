#!/usr/bin/env python3
"""
Premier Prospect™ — MLS Price Reductions
Signal: price_reduction — Score 78 (HOT)
Behavioral distress — seller reduced price, motivation escalating.
"""
import os, json, logging, hashlib, requests, re
log = logging.getLogger(__name__)
SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
SOURCE_SLUG  = 'mls-price-reductions'
SIGNAL_TYPE  = 'price_reduction'
SCORE_BASE   = 78
COUNTIES     = {'Salt Lake', 'Utah', 'Weber', 'Davis'}
PRICE_RED_CHECKSUM = os.environ.get('URE_PRICE_RED_CHECKSUM', 'd751713988987e9331980363e24189ce')
HEADERS = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}',
           'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

def run() -> int:
    from ure_session import get_session, parse_search_listnos, parse_listing
    log.info(f'[{SOURCE_SLUG}] starting')
    sess = get_session()
    sess.ensure_alive()
    signals = []
    seen = set()
    for page in range(1, 4):
        html = sess.search_perform(checksum=PRICE_RED_CHECKSUM, page=page)
        listnos = parse_search_listnos(html) if html else []
        log.info(f'[{SOURCE_SLUG}] page {page}: {len(listnos)} listings')
        if not listnos: break
        for listno in listnos:
            if listno in seen: continue
            seen.add(listno)
            listing = parse_listing(sess.get_listing(listno), listno)
            if not listing: continue
            county = listing.get('county','').replace(' County','').strip()
            if county and county not in COUNTIES: continue
            address = listing.get('address','') or f'MLS #{listno}'
            price = listing.get('price', 0) or 0
            dedup = hashlib.sha256(f'{SOURCE_SLUG}:{listno}'.encode()).hexdigest()
            signals.append({
                'source_slug': SOURCE_SLUG, 'signal_type': SIGNAL_TYPE,
                'raw_address': address, 'city': listing.get('city'),
                'county': county or None, 'score': SCORE_BASE,
                'primed_stage': 1, 'motivation_probability': 72,
                'outreach_routing': 'agent_first', 'dedupe_hash': dedup,
                'raw_payload': json.dumps({'listno': listno, 'price': price,
                    'listing_url': f'https://www.utahrealestate.com/member/{listno}'})
            })
    if not signals:
        log.info(f'[{SOURCE_SLUG}] 0 signals'); return 0
    r = requests.post(f'{SUPABASE_URL}/rest/v1/pp_scraper_signals',
        headers=HEADERS, json=signals, timeout=30)
    inserted = len(signals) if r.status_code in [200,201] else 0
    log.info(f'[{SOURCE_SLUG}] done — {inserted} inserted'); return inserted

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO); run()
