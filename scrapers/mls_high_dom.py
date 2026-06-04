#!/usr/bin/env python3
"""
Premier Prospect™ — MLS High Days-on-Market Scraper
Source: UtahRealEstate.com RESO Web API
Signal: high_dom — Score 75
Status: READY — swap MLS_BEARER_TOKEN when DocuSign complete
Threshold: 60+ days on market = motivated seller signal
"""
import os, json, logging, hashlib, requests
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
MLS_BEARER_TOKEN = os.environ.get("MLS_BEARER_TOKEN", "PENDING_DOCUSIGN")
MLS_BASE = "https://resoapi.utahrealestate.com/reso/odata"

SOURCE_SLUG = "mls-high-dom"
SIGNAL_TYPE = "high_dom"
COUNTIES = ["Utah", "Salt Lake", "Weber"]
DOM_THRESHOLD = 60

def fetch_high_dom():
    if MLS_BEARER_TOKEN == "PENDING_DOCUSIGN":
        log.warning("MLS_BEARER_TOKEN not set — skipping high DOM scraper")
        return []

    headers = {"Authorization": f"Bearer {MLS_BEARER_TOKEN}", "Accept": "application/json"}
    params = {
        "$filter": f"StandardStatus eq 'Active' and DaysOnMarket ge {DOM_THRESHOLD}",
        "$select": "ListingKey,UnparsedAddress,City,CountyOrParish,ListPrice,OriginalListPrice,DaysOnMarket,ListAgentFullName,ModificationTimestamp",
        "$top": 500,
        "$orderby": "DaysOnMarket desc"
    }

    all_records = []
    url = f"{MLS_BASE}/Property"
    while url:
        resp = requests.get(url, headers=headers, params=params if url==f"{MLS_BASE}/Property" else None, timeout=30)
        if not resp.ok: log.error(f"MLS: {resp.status_code}"); break
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
        if not any(c.lower() in county.lower() for c in COUNTIES): continue
        address = r.get("UnparsedAddress", "")
        if not address: continue
        dom = r.get("DaysOnMarket", 0) or 0
        score = 75 + (5 if dom >= 90 else 0) + (5 if dom >= 120 else 0) + (5 if dom >= 180 else 0)
        score = min(score, 95)
        dedup_key = hashlib.sha256(f"{SOURCE_SLUG}:{address}".encode()).hexdigest()
        signals.append({
            "source_slug": SOURCE_SLUG,
            "signal_type": SIGNAL_TYPE,
            "raw_address": address,
            "raw_owner_name": r.get("ListAgentFullName"),
            "city": r.get("City"),
            "county": county.replace(" County", "").strip(),
            "score": score,
            "primed_stage": 2 if score >= 88 else 1,
            "motivation_probability": 72 if dom >= 120 else 65,
            "outreach_routing": "direct_agent" if score >= 88 else "agent_first",
            "dedup_hash": dedup_key,
            "raw_payload": json.dumps({
                "days_on_market": dom,
                "list_price": r.get("ListPrice"),
                "original_price": r.get("OriginalListPrice"),
                "listing_key": r.get("ListingKey")
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
    records = fetch_high_dom()
    if not records: return 0
    signals = build_signals(records)
    inserted = post_signals(signals)
    log.info(f"[{SOURCE_SLUG}] Done — {inserted} inserted")
    return inserted

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
