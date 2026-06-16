#!/usr/bin/env python3
"""
Premier Prospect™ — MLS Expired Listings
Source:  utahrealestate.com /search/perform/ → /member/{listno}
Signal:  expired_listing — Score 95 (HOT)
Auth:    ure_session.py autonomous session manager (shakel/Ronnal13=)

Strategy: Search for Expired status listings, parse listing IDs from results HTML,
          fetch each listing's full detail page for address/price/DOM/agent fields.
          No RESO/OData API exists — all data is HTML-only.
"""
import os, json, logging, hashlib, requests, re
from datetime import datetime

log = logging.getLogger(__name__)

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
SOURCE_SLUG  = 'mls-expired-listings'
SIGNAL_TYPE  = 'expired_listing'
SCORE_BASE   = 95
COUNTIES     = {'Salt Lake', 'Utah', 'Weber', 'Davis'}

# Expired listing search checksum — precomputed for "StandardStatus=Expired, Utah"
# If this changes, re-capture from Kelvin's authenticated session
EXPIRED_CHECKSUM = os.environ.get('URE_EXPIRED_CHECKSUM', 'd751713988987e9331980363e24189ce')

HEADERS = {
    'apikey':        SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type':  'application/json',
    'Prefer':        'return=minimal',
}

def run() -> int:
    from ure_session import get_session, parse_search_listnos, parse_listing

    log.info(f'[{SOURCE_SLUG}] starting')
    sess = get_session()
    if not sess.ensure_alive():
        log.warning(f'[{SOURCE_SLUG}] session unavailable — will try web scrape anyway')

    signals = []
    seen_listnos = set()

    # Fetch up to 5 pages of expired listings
    for page in range(1, 6):
        html = sess.search_perform(checksum=EXPIRED_CHECKSUM, page=page)
        if not html:
            log.info(f'[{SOURCE_SLUG}] no results on page {page}')
            break

        listnos = parse_search_listnos(html)
        log.info(f'[{SOURCE_SLUG}] page {page}: {len(listnos)} listings')
        if not listnos:
            break

        for listno in listnos:
            if listno in seen_listnos:
                continue
            seen_listnos.add(listno)

            detail_html = sess.get_listing(listno)
            listing = parse_listing(detail_html, listno)
            if not listing:
                continue

            county = listing.get('county', '').replace(' County', '').strip()
            if county and county not in COUNTIES:
                continue

            address = listing.get('address', '') or f'MLS #{listno}'
            dom = int(re.sub(r'[^0-9]', '', listing.get('days_on_market', '0')) or 0)
            price = listing.get('price', 0) or 0

            # Score boost for longer DOM and higher price
            score = min(SCORE_BASE + (5 if dom > 180 else 0) + (3 if price >= 500000 else 0), 100)

            dedup_key = hashlib.sha256(f'{SOURCE_SLUG}:{listno}'.encode()).hexdigest()
            signals.append({
                'source_slug':           SOURCE_SLUG,
                'signal_type':           SIGNAL_TYPE,
                'raw_address':           address,
                'raw_owner_name':        listing.get('agent_id') or None,
                'city':                  listing.get('city') or None,
                'county':                county or None,
                'score':                 score,
                'primed_stage':          2,
                'motivation_probability': 88,
                'outreach_routing':      'direct_agent',
                'dedupe_hash':           dedup_key,
                'raw_payload': json.dumps({
                    'listno':         listno,
                    'price':          price,
                    'days_on_market': dom,
                    'year_built':     listing.get('year_built'),
                    'beds':           listing.get('beds'),
                    'baths':          listing.get('baths'),
                    'sqft':           listing.get('sqft'),
                    'status':         listing.get('status'),
                    'photo_count':    listing.get('photo_count', 0),
                    'listing_url':    f'https://www.utahrealestate.com/member/{listno}',
                    'source':         'utahrealestate.com',
                }),
            })

    if not signals:
        log.info(f'[{SOURCE_SLUG}] 0 signals (session may need URE_SESSION_COOKIE secret)')
        return 0

    # Insert to Supabase
    inserted = 0
    for i in range(0, len(signals), 100):
        batch = signals[i:i+100]
        r = requests.post(
            f'{SUPABASE_URL}/rest/v1/pp_scraper_signals',
            headers=HEADERS, json=batch, timeout=30
        )
        if r.status_code in [200, 201]:
            inserted += len(batch)
        else:
            log.warning(f'[{SOURCE_SLUG}] insert {r.status_code}: {r.text[:100]}')

    log.info(f'[{SOURCE_SLUG}] done — {inserted} signals inserted')
    return inserted


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    run()
