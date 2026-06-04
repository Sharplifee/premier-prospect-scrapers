#!/usr/bin/env python3
"""
Premier Prospect™ — MLS Expired Listings Scraper
Source: UtahRealEstate.com RESO Web API
Signal: expired_listing — Score 95
Status: READY — swap MLS_BEARER_TOKEN when DocuSign complete

Bearer token location: vendor.utahrealestate.com → Account Summary
"""
import os, json, logging, hashlib, requests
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
MLS_BEARER_TOKEN = os.environ.get("MLS_BEARER_TOKEN", "PENDING_DOCUSIGN")
MLS_BASE = "https://resoapi.utahrealestate.com/reso/odata"

SCORE = 95
SIGNAL_TYPE = "expired_listing"
SOURCE_SLUG = "mls-expired-listings"
COUNTIES = ["Utah", "Salt Lake", "Weber"]

def fetch_expired_listings():
    if MLS_BEARER_TOKEN == "PENDING_DOCUSIGN":
        log.warning("MLS_BEARER_TOKEN not set — skipping expired listings scraper")
        return []

    headers = {
        "Authorization": f"Bearer {MLS_BEARER_TOKEN}",
        "Accept": "application/json"
    }

    # Expired listings — StandardStatus eq Expired, last 90 days
    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%dT00:00:00Z")
    params = {
        "$filter": f"StandardStatus eq 'Expired' and ModificationTimestamp ge {cutoff}",
        "$select": "ListingKey,UnparsedAddress,City,CountyOrParish,ListPrice,OriginalListPrice,DaysOnMarket,ListAgentFullName,PublicRemarks,CloseDate,ModificationTimestamp",
        "$top": 500,
        "$orderby": "ModificationTimestamp desc"
    }

    all_records = []
    url = f"{MLS_BASE}/Property"
    while url:
        resp = requests.get(url, headers=headers, params=params if url == f"{MLS_BASE}/Property" else None, timeout=30)
        if resp.status_code == 401:
            log.error("MLS token invalid or expired — check vendor.utahrealestate.com")
            break
        if not resp.ok:
            log.error(f"MLS API error: {resp.status_code} {resp.text[:200]}")
            break
        data = resp.json()
        records = data.get("value", [])
        all_records.extend(records)
        url = data.get("@odata.nextLink")
        params = None
        if len(all_records) >= 2000:
            break

    log.info(f"Fetched {len(all_records)} expired listings from MLS")
    return all_records

def build_signals(records):
    signals = []
    for r in records:
        county = r.get("CountyOrParish", "")
        if not any(c.lower() in county.lower() for c in COUNTIES):
            continue
        address = r.get("UnparsedAddress", "")
        if not address:
            continue
        dedup_key = hashlib.sha256(f"{SOURCE_SLUG}:{address}".encode()).hexdigest()
        dom = r.get("DaysOnMarket", 0) or 0
        list_price = r.get("ListPrice", 0) or 0
        orig_price = r.get("OriginalListPrice", 0) or 0
        price_drop_pct = round((orig_price - list_price) / orig_price * 100, 1) if orig_price > 0 else 0
        signals.append({
            "source_slug": SOURCE_SLUG,
            "signal_type": SIGNAL_TYPE,
            "raw_address": address,
            "raw_owner_name": r.get("ListAgentFullName"),
            "city": r.get("City"),
            "county": county.replace(" County", "").strip(),
            "score": min(SCORE + (5 if dom > 180 else 0) + (3 if price_drop_pct > 10 else 0), 100),
            "primed_stage": 2,
            "motivation_probability": 84,
            "outreach_routing": "direct_agent",
            "dedup_hash": dedup_key,
            "raw_payload": json.dumps({
                "listing_key": r.get("ListingKey"),
                "list_price": list_price,
                "original_price": orig_price,
                "price_drop_pct": price_drop_pct,
                "days_on_market": dom,
                "mls_source": "utahrealestate.com"
            })
        })
    return signals

def post_signals(signals):
    if not signals:
        return 0
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal,resolution=ignore-duplicates"
    }
    inserted = 0
    for i in range(0, len(signals), 200):
        batch = signals[i:i+200]
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/pp_scraper_signals",
            headers=headers, json=batch, timeout=30
        )
        if resp.status_code in [200, 201]:
            inserted += len(batch)
    return inserted

def run():
    log.info(f"[{SOURCE_SLUG}] Starting")
    records = fetch_expired_listings()
    if not records:
        log.info(f"[{SOURCE_SLUG}] No records — token pending or no results")
        return 0
    signals = build_signals(records)
    inserted = post_signals(signals)
    log.info(f"[{SOURCE_SLUG}] Done — {inserted} inserted")
    return inserted

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
