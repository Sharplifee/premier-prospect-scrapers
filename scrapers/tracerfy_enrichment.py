#!/usr/bin/env python3
"""
Premier Prospect™ — Tracerfy Skip Trace Enrichment
Status: READY — add TRACERFY_API_KEY to GitHub Secrets when funded (~$200/mo)
Purpose: Resolves phone numbers for NTS owner names that have no address
API: api.tracerfy.com
Cost: ~$0.02/lookup, run on Primed II NTS records only
"""
import os, json, logging, requests

log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
TRACERFY_KEY = os.environ.get("TRACERFY_API_KEY", "PENDING_FUNDING")
TRACERFY_BASE = "https://api.tracerfy.com/v1"

MAX_LOOKUPS_PER_RUN = 100  # Cost control — $2/run at $0.02 each

def get_unenriched_nts():
    """Get Primed II NTS records with owner name but no phone number."""
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/pp_scraper_signals",
        headers=headers,
        params={
            "select": "id,raw_owner_name,county,captured_at",
            "signal_type": "eq.nts",
            "primed_stage": "eq.2",
            "raw_owner_name": "not.is.null",
            "phone_number": "is.null",
            "order": "score.desc",
            "limit": MAX_LOOKUPS_PER_RUN
        },
        timeout=15
    )
    return resp.json() if resp.ok else []

def lookup_phone(owner_name, county):
    """Call Tracerfy API to resolve phone from owner name + county."""
    if TRACERFY_KEY == "PENDING_FUNDING":
        return None
    resp = requests.post(
        f"{TRACERFY_BASE}/search",
        headers={"Authorization": f"Bearer {TRACERFY_KEY}", "Content-Type": "application/json"},
        json={"name": owner_name, "state": "UT", "county": county},
        timeout=10
    )
    if resp.ok:
        data = resp.json()
        phones = data.get("phones", [])
        return phones[0].get("number") if phones else None
    return None

def update_phone(record_id, phone):
    """Write phone number back to the signal record."""
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/pp_scraper_signals?id=eq.{record_id}",
        headers=headers, json={"phone_number": phone}, timeout=10
    )

def run():
    if TRACERFY_KEY == "PENDING_FUNDING":
        log.warning("TRACERFY_API_KEY not set — skip trace skipped (fund account at tracerfy.com)")
        return 0

    records = get_unenriched_nts()
    log.info(f"[tracerfy] {len(records)} NTS records to enrich")
    enriched = 0
    for r in records:
        phone = lookup_phone(r["raw_owner_name"], r.get("county", "Utah"))
        if phone:
            update_phone(r["id"], phone)
            enriched += 1
    log.info(f"[tracerfy] Enriched {enriched}/{len(records)} records with phone numbers")
    return enriched

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
