#!/usr/bin/env python3
"""
Premier Prospect™ — MLS Validation Layer (Layer 5.5)
Sits between entity resolution (Layer 5) and enrichment (Layer 6).

Accepts a property address, returns MLS listing status and a score modifier.
Structured to accept the real Utah MLS RESO OData response format when the
forensics report is complete and the live feed is wired in.

Score modifiers:
  Listed   → -15  (active competition, agent already involved)
  Pending  → -25  (under contract, not actionable)
  Price cut (last 30d) → +10  (seller motivated, still available)
  Off market → +20  (motivated, no agent, direct approach viable)
  No match   →   0  (unknown / not in MLS)
"""
import os, json, logging, hashlib, requests
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')
MLS_TOKEN    = os.environ.get('MLS_BEARER_TOKEN', 'PENDING_DOCUSIGN')
MLS_BASE     = 'https://resoapi.utahrealestate.com/reso/odata'

# ── MLS Status → score modifier map ─────────────────────────────────────────
MLS_STATUS_MODIFIERS = {
    'Active':       -15,   # listed — agent gatekeeper in play
    'Pending':      -25,   # under contract — not actionable
    'ActiveUnderContract': -25,
    'Closed':         0,   # sold — neutral for current cycle
    'Expired':        0,   # captured separately by mls-expired-listings
    'Withdrawn':      0,   # captured separately by mls-withdrawn-listings
    'OffMarket':    +20,   # off market — direct approach viable
    'Cancelled':    +20,   # same as off market for outreach purposes
    'PriceReduced': +10,   # price cut signal — seller motivated
}

# ── Mock response schema (matches real RESO OData Property endpoint) ─────────
# This is the exact structure the real feed will return.
# Replace _fetch_mls_live() stub with real implementation when token available.

MOCK_MLS_RESPONSES = {
    # Simulated known addresses for validation cycle
    '1234 E 500 N PROVO UT': {
        'ListingKey': 'MOCK-001',
        'UnparsedAddress': '1234 E 500 N, Provo, UT 84606',
        'StandardStatus': 'Active',
        'ListPrice': 485000,
        'OriginalListPrice': 499000,
        'DaysOnMarket': 45,
        'CountyOrParish': 'Utah County',
        'City': 'Provo',
        'ModificationTimestamp': datetime.utcnow().isoformat() + 'Z',
        'PriceChangeTimestamp': (datetime.utcnow() - timedelta(days=15)).isoformat() + 'Z',
        'ListAgentFullName': 'Mock Agent',
        'PublicRemarks': 'Mock active listing for validation',
        '_mock': True,
    },
    '567 W MAIN ST LEHI UT': {
        'ListingKey': 'MOCK-002',
        'UnparsedAddress': '567 W Main St, Lehi, UT 84043',
        'StandardStatus': 'Pending',
        'ListPrice': 620000,
        'OriginalListPrice': 645000,
        'DaysOnMarket': 22,
        'CountyOrParish': 'Utah County',
        'City': 'Lehi',
        'ModificationTimestamp': datetime.utcnow().isoformat() + 'Z',
        'PriceChangeTimestamp': None,
        'ListAgentFullName': 'Mock Agent',
        'PublicRemarks': 'Under contract',
        '_mock': True,
    },
    '890 S 200 W SALT LAKE CITY UT': {
        'ListingKey': 'MOCK-003',
        'UnparsedAddress': '890 S 200 W, Salt Lake City, UT 84101',
        'StandardStatus': 'OffMarket',
        'ListPrice': 395000,
        'OriginalListPrice': 415000,
        'DaysOnMarket': 90,
        'CountyOrParish': 'Salt Lake County',
        'City': 'Salt Lake City',
        'ModificationTimestamp': (datetime.utcnow() - timedelta(days=5)).isoformat() + 'Z',
        'PriceChangeTimestamp': (datetime.utcnow() - timedelta(days=20)).isoformat() + 'Z',
        '_mock': True,
    },
}


def _normalize_address(address):
    """Normalize address for lookup key."""
    if not address:
        return ''
    return ' '.join(address.upper().strip().split())


def _fetch_mls_live(address):
    """
    Fetch from real Utah MLS RESO OData API.
    Returns raw OData record dict or None if not found.
    Stub — activate when MLS_BEARER_TOKEN is set.
    """
    if MLS_TOKEN == 'PENDING_DOCUSIGN':
        return None
    try:
        headers = {'Authorization': f'Bearer {MLS_TOKEN}', 'Accept': 'application/json'}
        params = {
            '$filter': f"UnparsedAddress eq '{address}'",
            '$select': 'ListingKey,UnparsedAddress,StandardStatus,ListPrice,OriginalListPrice,'
                       'DaysOnMarket,CountyOrParish,City,ModificationTimestamp,PriceChangeTimestamp,'
                       'ListAgentFullName,PublicRemarks',
            '$top': 1,
        }
        r = requests.get(f'{MLS_BASE}/Property', headers=headers, params=params, timeout=15)
        if not r.ok:
            log.warning(f'MLS validation: {r.status_code} for {address}')
            return None
        data = r.json()
        records = data.get('value', [])
        return records[0] if records else None
    except Exception as e:
        log.warning(f'MLS validation fetch error: {e}')
        return None


def _has_price_cut_last_30_days(record):
    """True if PriceChangeTimestamp exists and is within last 30 days."""
    ts = record.get('PriceChangeTimestamp')
    if not ts:
        return False
    try:
        cut_date = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return (datetime.utcnow().replace(tzinfo=cut_date.tzinfo) - cut_date).days <= 30
    except Exception:
        return False


def validate_address(address):
    """
    Layer 5.5 — MLS Validation.
    Returns a dict with status, score_modifier, and metadata.

    Schema matches what the real live feed will return.
    """
    norm = _normalize_address(address)

    # Try live feed first (no-op when token pending)
    record = _fetch_mls_live(address)

    # Fall back to mock data for validation cycle
    if record is None:
        record = MOCK_MLS_RESPONSES.get(norm)

    if record is None:
        return {
            'found': False,
            'status': 'NoMatch',
            'score_modifier': 0,
            'source': 'mls_validation_stub',
            'mock': True,
            'record': None,
        }

    status = record.get('StandardStatus', 'Unknown')
    modifier = MLS_STATUS_MODIFIERS.get(status, 0)

    # Price cut override: if Active + recent price cut, apply +10 instead of -15
    if status == 'Active' and _has_price_cut_last_30_days(record):
        modifier = MLS_STATUS_MODIFIERS['PriceReduced']
        status = 'PriceReduced'

    return {
        'found': True,
        'status': status,
        'score_modifier': modifier,
        'listing_key': record.get('ListingKey'),
        'list_price': record.get('ListPrice'),
        'original_price': record.get('OriginalListPrice'),
        'days_on_market': record.get('DaysOnMarket'),
        'county': record.get('CountyOrParish'),
        'city': record.get('City'),
        'price_cut_last_30d': _has_price_cut_last_30_days(record),
        'modification_ts': record.get('ModificationTimestamp'),
        'source': 'mls_validation_stub',
        'mock': record.get('_mock', False),
    }


def apply_mls_validation(signals):
    """
    Apply MLS validation score modifiers to a list of signal dicts.
    Called from scoring engine after entity resolution (Layer 5).
    Modifies score in-place. Returns enriched signal list.
    """
    enriched = []
    for sig in signals:
        address = sig.get('raw_address', '')
        result = validate_address(address)
        if result['found']:
            original_score = sig.get('score', 0)
            new_score = max(0, min(100, original_score + result['score_modifier']))
            sig = dict(sig)
            sig['score'] = new_score
            # Recompute tier after MLS adjustment
            if new_score >= 70:
                sig['tier'] = 'HOT'
            elif new_score >= 40:
                sig['tier'] = 'WARM'
            else:
                sig['tier'] = 'COOL'
            # Store MLS metadata in raw_payload
            try:
                payload = json.loads(sig.get('raw_payload') or '{}')
            except Exception:
                payload = {}
            payload['mls_validation'] = {
                'status': result['status'],
                'modifier': result['score_modifier'],
                'original_score': original_score,
                'listing_key': result.get('listing_key'),
                'mock': result.get('mock', True),
            }
            sig['raw_payload'] = json.dumps(payload)
        enriched.append(sig)
    return enriched


# ── Score modifier validation tests ──────────────────────────────────────────
def run_validation_tests():
    """Confirm correct score modifier for each MLS status."""
    tests = [
        ('1234 E 500 N PROVO UT',           -15, 'Active→PriceReduced overrides to +10 if cut'),
        ('567 W MAIN ST LEHI UT',           -25, 'Pending → -25'),
        ('890 S 200 W SALT LAKE CITY UT',   +20, 'OffMarket → +20'),
        ('999 UNKNOWN STREET NOWHERE UT',     0, 'NoMatch → 0'),
    ]
    passed = failed = 0
    for addr, expected_modifier, desc in tests:
        result = validate_address(addr)
        actual = result['score_modifier']
        # Special case: Active with price cut flips to PriceReduced modifier
        if desc.startswith('Active→PriceReduced'):
            ok = actual == MLS_STATUS_MODIFIERS['PriceReduced']
        else:
            ok = actual == expected_modifier
        status = 'PASS' if ok else 'FAIL'
        if ok: passed += 1
        else: failed += 1
        print(f"  [{status}] {addr[:40]:40s} modifier={actual:+d}  — {desc}")
    print(f"\nMLS Validation: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == '__main__':
    import logging as _l
    _l.basicConfig(level=_l.INFO)
    print("=== MLS Validation Layer Tests ===")
    run_validation_tests()

    print("\n=== Sample: apply_mls_validation to 3 signals ===")
    sample_signals = [
        {'raw_address': '1234 E 500 N PROVO UT',         'score': 75, 'signal_type': 'nts'},
        {'raw_address': '567 W MAIN ST LEHI UT',          'score': 80, 'signal_type': 'nod'},
        {'raw_address': '890 S 200 W SALT LAKE CITY UT', 'score': 60, 'signal_type': 'tax_delinquency'},
    ]
    enriched = apply_mls_validation(sample_signals)
    for s in enriched:
        print(f"  {s['raw_address'][:45]:45s} score={s['score']:3d} tier={s.get('tier','?')}")
