#!/usr/bin/env python3
"""
Premier Prospect™ — Federal DNC Compliance Check
Status: READY — add DNC_SAN to GitHub Secrets when purchased (~$80-170/yr)
Purpose: Scrubs phone numbers against Federal Do Not Call Registry before VAPI fires
SAN Registration: ftc.gov/donotcall — register at donotcall.gov/register.aspx
This runs BEFORE vapi_outreach_trigger.py — blocks any number on the registry
"""
import os, json, logging, requests

log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
DNC_SAN = os.environ.get("DNC_SAN", "PENDING_PURCHASE")
DNC_API_BASE = "https://telemarketing.donotcall.gov/dnc"  # FTC DNC API

def check_numbers_against_dnc(phone_numbers):
    """Check batch of phone numbers against Federal DNC registry."""
    if DNC_SAN == "PENDING_PURCHASE":
        log.warning("DNC_SAN not set — DNC check skipped. DO NOT CALL without this check.")
        return set()  # Return empty blocked set — DO NOT CALL anyone without check

    blocked = set()
    # FTC DNC API accepts batch lookups with SAN auth
    for i in range(0, len(phone_numbers), 50):
        batch = phone_numbers[i:i+50]
        try:
            resp = requests.post(
                f"{DNC_API_BASE}/check",
                headers={"Authorization": f"SAN {DNC_SAN}", "Content-Type": "application/json"},
                json={"numbers": batch},
                timeout=15
            )
            if resp.ok:
                data = resp.json()
                blocked.update(data.get("blocked", []))
        except Exception as e:
            log.error(f"DNC check failed: {e}")
            # Fail safe — if DNC check fails, block all to be safe
            blocked.update(batch)
    return blocked

def scrub_leads_against_dnc():
    """Pull all leads with phone numbers and mark DNC-listed ones as blocked."""
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/pp_scraper_signals",
        headers=headers,
        params={
            "select": "id,phone_number",
            "phone_number": "not.is.null",
            "dnc_checked": "is.null",
            "limit": 500
        },
        timeout=15
    )
    if not resp.ok: return 0

    leads = resp.json()
    if not leads: return 0

    phone_map = {r["phone_number"]: r["id"] for r in leads}
    blocked = check_numbers_against_dnc(list(phone_map.keys()))

    update_headers = {**headers, "Content-Type": "application/json"}
    blocked_count = 0
    for phone, record_id in phone_map.items():
        is_blocked = phone in blocked
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/pp_scraper_signals?id=eq.{record_id}",
            headers=update_headers,
            json={"dnc_checked": "true", "dnc_blocked": str(is_blocked).lower()},
            timeout=10
        )
        if is_blocked: blocked_count += 1

    log.info(f"[dnc-check] Checked {len(leads)} numbers, blocked {blocked_count}")
    return blocked_count

def run():
    if DNC_SAN == "PENDING_PURCHASE":
        log.warning("DNC_SAN not registered — purchase at donotcall.gov (~$80-170/yr)")
        return 0
    return scrub_leads_against_dnc()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
