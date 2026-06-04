#!/usr/bin/env python3
"""
Premier Prospect™ — MLS Withdrawn Listings Scraper
Source: UtahRealEstate.com RESO Web API
Signal: withdrawn_listing — Score 82
Status: READY — swap MLS_BEARER_TOKEN when DocuSign complete
Withdrawn = seller tried, pulled back — highest re-list conversion rate
"""
import os, json, logging, hashlib, requests
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
MLS_BEARER_TOKEN = os.environ.get("MLS_BEARER_TOKEN", "PENDING_DOCUSIGN")
MLS_BASE = "https://resoapi.utahrealestate.com/reso/odata"

SOURCE_SLUG = "mls-withdrawn-listings"
SIGNAL_TYPE = "withdrawn_listing"
COUNTIES = ["Utah", "Salt Lake", "Weber"]

def fetch_withdrawn():
    if MLS_BEARER_TOKEN == "PENDING_DOCUSIGN":
        log.warning("MLS_BEARER_TOKEN not set — skipping withdrawn listings scraper")
        return []

    headers = {"Authorization": f"Bearer {MLS_BEARER_TOKEN}", "Accept": "application/json"}
    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%dT00:00:00Z")
    params = {
        "$filter": f"StandardStatus eq 'Withdrawn' and ModificationTimestamp ge {cutoff}",
        "$select": "ListingKey,UnparsedAddress,City,CountyOrParish,ListPrice,OriginalListPrice,DaysOnMarket,ListAgentFullName,ModificationTimestamp,WithdrawalDate",
        "$top": 500,
        "$orderby": "ModificationTimestamp desc"
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
        dedup_key = hashlib.sha256(f"{SOURCE_SLUG}:{address}".encode()).hexdigest()
        dom = r.get("DaysOnMarket", 0) or 0
        score = 82 + (5 if dom > 60 else 0)
        score = min(score, 95)
        signals.append({
            "source_slug": SOURCE_SLUG,
            "signal_type": SIGNAL_TYPE,
            "raw_address": address,
            "raw_owner_name": r.get("ListAgentFullName"),
            "city": r.get("City"),
            "county": county.replace(" County", "").strip(),
            "score": score,
            "primed_stage": 2 if score >= 88 else 1,
            "motivation_probability": 80 if dom > 60 else 74,
            "outreach_routing": "direct_agent" if score >= 88 else "agent_first",
            "dedup_hash": dedup_key,
            "raw_payload": json.dumps({
                "list_price": r.get("ListPrice"),
                "original_price": r.get("OriginalListPrice"),
                "days_on_market": dom,
                "withdrawal_date": r.get("WithdrawalDate", "")[:10] if r.get("WithdrawalDate") else "",
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
    records = fetch_withdrawn()
    if not records: return 0
    signals = build_signals(records)
    inserted = post_signals(signals)
    log.info(f"[{SOURCE_SLUG}] Done — {inserted} inserted")
    return inserted

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
