#!/usr/bin/env python3
"""
Premier Prospect™ — MLS Price Reduction Scraper
Source: UtahRealEstate.com RESO Web API
Signal: price_reduction — Score 78
Status: READY — swap MLS_BEARER_TOKEN when DocuSign complete
"""
import os, json, logging, hashlib, requests
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
MLS_BEARER_TOKEN = os.environ.get("MLS_BEARER_TOKEN", "PENDING_DOCUSIGN")
MLS_BASE = "https://resoapi.utahrealestate.com/reso/odata"

SCORE = 78
SIGNAL_TYPE = "price_reduction"
SOURCE_SLUG = "mls-price-reductions"
COUNTIES = ["Utah", "Salt Lake", "Weber"]
MIN_DROP_PCT = 3.0  # Only surface reductions >= 3%

def fetch_price_reductions():
    if MLS_BEARER_TOKEN == "PENDING_DOCUSIGN":
        log.warning("MLS_BEARER_TOKEN not set — skipping price reduction scraper")
        return []

    headers = {"Authorization": f"Bearer {MLS_BEARER_TOKEN}", "Accept": "application/json"}
    cutoff = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%dT00:00:00Z")

    params = {
        "$filter": f"StandardStatus eq 'Active' and ListPrice lt OriginalListPrice and ModificationTimestamp ge {cutoff}",
        "$select": "ListingKey,UnparsedAddress,City,CountyOrParish,ListPrice,OriginalListPrice,DaysOnMarket,ListAgentFullName,ModificationTimestamp,PriceChangeTimestamp",
        "$top": 500,
        "$orderby": "PriceChangeTimestamp desc"
    }

    all_records = []
    url = f"{MLS_BASE}/Property"
    while url:
        resp = requests.get(url, headers=headers, params=params if url==f"{MLS_BASE}/Property" else None, timeout=30)
        if not resp.ok:
            log.error(f"MLS API: {resp.status_code}")
            break
        data = resp.json()
        all_records.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
        params = None
        if len(all_records) >= 2000: break

    return all_records

def build_signals(records):
    signals = []
    for r in records:
        county = r.get("CountyOrParish", "")
        if not any(c.lower() in county.lower() for c in COUNTIES):
            continue
        address = r.get("UnparsedAddress", "")
        if not address: continue
        list_price = r.get("ListPrice") or 0
        orig_price = r.get("OriginalListPrice") or 0
        if orig_price <= 0: continue
        drop_pct = (orig_price - list_price) / orig_price * 100
        if drop_pct < MIN_DROP_PCT: continue

        dedup_key = hashlib.sha256(f"{SOURCE_SLUG}:{address}".encode()).hexdigest()
        score = min(SCORE + (5 if drop_pct >= 10 else 0) + (3 if drop_pct >= 15 else 0), 100)

        signals.append({
            "source_slug": SOURCE_SLUG,
            "signal_type": SIGNAL_TYPE,
            "raw_address": address,
            "raw_owner_name": r.get("ListAgentFullName"),
            "city": r.get("City"),
            "county": county.replace(" County", "").strip(),
            "score": score,
            "primed_stage": 2 if score >= 88 else 1,
            "motivation_probability": 75 if drop_pct >= 10 else 68,
            "outreach_routing": "direct_agent" if score >= 88 else "agent_first",
            "dedup_hash": dedup_key,
            "raw_payload": json.dumps({
                "list_price": list_price,
                "original_price": orig_price,
                "drop_pct": round(drop_pct, 1),
                "days_on_market": r.get("DaysOnMarket", 0),
                "price_change_date": r.get("PriceChangeTimestamp", "")[:10]
            })
        })
    return signals

def post_signals(signals):
    if not signals: return 0
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "return=minimal,resolution=ignore-duplicates"}
    inserted = 0
    for i in range(0, len(signals), 200):
        r = requests.post(f"{SUPABASE_URL}/rest/v1/pp_scraper_signals", headers=headers, json=signals[i:i+200], timeout=30)
        if r.status_code in [200,201]: inserted += 200
    return inserted

def run():
    log.info(f"[{SOURCE_SLUG}] Starting")
    records = fetch_price_reductions()
    if not records: return 0
    signals = build_signals(records)
    inserted = post_signals(signals)
    log.info(f"[{SOURCE_SLUG}] Done — {inserted} inserted")
    return inserted

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
