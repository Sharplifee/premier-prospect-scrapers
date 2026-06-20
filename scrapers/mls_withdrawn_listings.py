#!/usr/bin/env python3
"""
Premier Prospect™ — MLS Withdrawn Listings
Source:  utahrealestate.com (session-cookie auth, reverse-engineered)
Signal:  withdrawn_listing — Score 82 (HOT, direct_agent)
Auth:    ure_session.py — shakel/Ronnal13= member 88098
"""
import os, json, logging, hashlib, requests, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

log = logging.getLogger(__name__)
SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
SOURCE_SLUG  = 'mls-withdrawn-listings'
SIGNAL_TYPE  = 'withdrawn_listing'
SCORE        = 82
COUNTIES     = {'Salt Lake', 'Utah', 'Weber', 'Davis'}
CHECKSUM     = os.environ.get('URE_WITHDRAWN_CHECKSUM', 'd751713988987e9331980363e24189ce')
HDR = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}',
       'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

# Use quad-redundancy auth engine when available
try:
    from ure_auth_engine import get_authenticated_session as _get_session
    from ure_session import parse_search_listnos, parse_listing
    def _make_session():
        return _get_session()
except ImportError:
    try:
        from scrapers.ure_session import get_session as _raw_sess, parse_search_listnos, parse_listing
    except ImportError:
        from ure_session import get_session as _raw_sess, parse_search_listnos, parse_listing
    def _make_session():
        return _raw_sess()

def run() -> int:
    # auth engine loaded at module level

    log.info(f'[{SOURCE_SLUG}] starting')
    sess = _make_session()
    signals, seen = [], set()

    for page in range(1, 4):
        html = search_perform(sess, cookie, checksum=CHECKSUM, page=page)
        listnos = parse_search_listnos(html) if html else []
        log.info(f'[{SOURCE_SLUG}] page {page}: {len(listnos)} listings')
        if not listnos:
            break
        for listno in listnos:
            if listno in seen:
                continue
            seen.add(listno)
            listing = parse_listing(get_listing_html(sess, cookie, listno), listno)
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
                'primed_stage': 2, 'motivation_probability': 78,
                'outreach_routing': 'direct_agent', 'dedupe_hash': dedup,
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
