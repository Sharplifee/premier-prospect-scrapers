#!/usr/bin/env python3
"""
Premier Prospect™ — VAPI Outreach Trigger
Status: READY — add VAPI_API_KEY to GitHub Secrets to activate
Purpose: Triggers automated AI voice call for Primed II leads via Bryce assistant
Assistant ID: 5a9c3bcb-1f34-4ea9-8d7a-43a94e94e875 (Outbound Virtual Assistant)
Runs: After each scraper cycle, queues new Primed II leads not yet contacted
"""
import os, json, logging, requests
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
VAPI_KEY = os.environ.get("VAPI_API_KEY", "PENDING_ACTIVATION")
VAPI_ASSISTANT_ID = "5a9c3bcb-1f34-4ea9-8d7a-43a94e94e875"
VAPI_PHONE_ID = "883758f0-e266-42c8-abf0-bb8aade74d6f"
MAX_CALLS_PER_RUN = 10  # Hard cap — DNC compliance before scaling

def get_primed_ii_queue():
    """Get new Primed II leads with phone numbers not yet called."""
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/pp_scraper_signals",
        headers=headers,
        params={
            "select": "id,raw_owner_name,raw_address,county,phone_number,signal_type,score",
            "primed_stage": "eq.2",
            "outreach_routing": "eq.direct_agent",
            "phone_number": "not.is.null",
            "vapi_called": "is.null",
            "captured_at": f"gte.{(datetime.now()-timedelta(hours=24)).isoformat()}",
            "order": "score.desc",
            "limit": MAX_CALLS_PER_RUN
        },
        timeout=15
    )
    return resp.json() if resp.ok else []

def trigger_call(lead):
    """Fire VAPI outbound call for a single lead."""
    if VAPI_KEY == "PENDING_ACTIVATION":
        return False
    resp = requests.post(
        "https://api.vapi.ai/call/phone",
        headers={"Authorization": f"Bearer {VAPI_KEY}", "Content-Type": "application/json"},
        json={
            "assistantId": VAPI_ASSISTANT_ID,
            "phoneNumberId": VAPI_PHONE_ID,
            "customer": {
                "number": lead["phone_number"],
                "name": lead.get("raw_owner_name", "Homeowner")
            },
            "assistantOverrides": {
                "variableValues": {
                    "owner_name": lead.get("raw_owner_name", ""),
                    "property_address": lead.get("raw_address", ""),
                    "county": lead.get("county", "Utah"),
                    "signal_type": lead.get("signal_type", ""),
                    "score": str(lead.get("score", 0))
                }
            }
        },
        timeout=15
    )
    return resp.ok

def mark_called(record_id):
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/pp_scraper_signals?id=eq.{record_id}",
        headers=headers, json={"vapi_called": datetime.now().isoformat()}, timeout=10
    )

def run():
    if VAPI_KEY == "PENDING_ACTIVATION":
        log.warning("VAPI_API_KEY not set — outreach calls skipped (activate at dashboard.vapi.ai)")
        return 0

    leads = get_primed_ii_queue()
    log.info(f"[vapi-trigger] {len(leads)} leads in call queue")
    called = 0
    for lead in leads:
        if trigger_call(lead):
            mark_called(lead["id"])
            called += 1
    log.info(f"[vapi-trigger] {called} calls triggered")
    return called

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
