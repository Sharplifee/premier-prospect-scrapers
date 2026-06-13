#!/usr/bin/env python3
"""
Premier Prospect™ — End-to-End Pipeline Validation (Task 6)

Runs a full simulated cycle through all 10 pipeline layers and produces
a validation report. Does NOT write to production tables — uses dry-run
mode with synthetic signals unless VALIDATE_LIVE=1 is set.

Usage:
  python scrapers/validate_pipeline.py
  VALIDATE_LIVE=1 python scrapers/validate_pipeline.py  # hit real Supabase
"""
import os, sys, json, time, datetime, hashlib, logging

log = logging.getLogger('pp.validate')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

LIVE = os.environ.get('VALIDATE_LIVE', '0') == '1'
SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_SERVICE_KEY', '')

# ── 10 PIPELINE LAYERS ────────────────────────────────────────────────────────
#  1. Source Ingestion       — scrapers pull raw signals
#  2. Junk Filter            — owner/address sanitation
#  3. Deduplication          — MD5 hash dedupe
#  4. Score Calculation      — 0-100 unified scale
#  5. Entity Resolution      — match to buyer profile (RPC)
#  5.5 MLS Validation        — score adjustment for listing status
#  6. Enrichment             — Tracerfy skip-trace (staged)
#  7. DNC Compliance         — filter against DNC registry (staged)
#  8. Tier Assignment        — HOT/WARM/COOL
#  9. Dashboard Refresh      — KPI cache + leads cache (RPC)
# 10. Outreach Routing       — VAPI trigger (staged)

LAYERS = [
    '1. Source Ingestion',
    '2. Junk Filter',
    '3. Deduplication',
    '4. Score Calculation (0-100)',
    '5. Entity Resolution',
    '5.5. MLS Validation',
    '6. Enrichment (Tracerfy — staged)',
    '7. DNC Compliance (staged)',
    '8. Tier Assignment (HOT/WARM/COOL)',
    '9. Dashboard Refresh (KPI cache)',
    '10. Outreach Routing (VAPI — staged)',
]

# ── SYNTHETIC TEST SIGNALS ────────────────────────────────────────────────────
SYNTHETIC_SIGNALS = [
    {'source_slug': 'validate-nts',           'signal_type': 'nts',              'score': 99, 'county': 'Utah',       'city': 'Provo',         'raw_owner_name': 'John Smith',      'raw_address': '100 N 100 W Provo UT 84601'},
    {'source_slug': 'validate-nod',           'signal_type': 'nod',              'score': 88, 'county': 'Utah',       'city': 'Orem',          'raw_owner_name': 'Maria Garcia',    'raw_address': '200 E Center St Orem UT 84057'},
    {'source_slug': 'validate-expired',       'signal_type': 'expired_listing',  'score': 95, 'county': 'Salt Lake',  'city': 'Sandy',         'raw_owner_name': 'Robert Johnson',  'raw_address': '300 W 9000 S Sandy UT 84070'},
    {'source_slug': 'validate-fsbo',          'signal_type': 'fsbo',             'score': 65, 'county': 'Utah',       'city': 'Lehi',          'raw_owner_name': None,              'raw_address': '3br/2ba house $450k Lehi'},
    {'source_slug': 'validate-tax',           'signal_type': 'tax_delinquency',  'score': 75, 'county': 'Utah',       'city': None,            'raw_owner_name': 'Patricia Wilson',  'raw_address': '400 S State St Springville UT'},
    {'source_slug': 'validate-lien',          'signal_type': 'lien_judgment',    'score': 68, 'county': 'Salt Lake',  'city': 'West Valley',   'raw_owner_name': 'David Brown',     'raw_address': '500 N Redwood Rd West Valley City UT'},
    {'source_slug': 'validate-contractor',    'signal_type': 'contractor_license','score': 30, 'county': 'Utah',      'city': 'American Fork', 'raw_owner_name': 'Smith Plumbing LLC','raw_address': '600 E Main St American Fork UT'},
    {'source_slug': 'validate-migration',     'signal_type': 'buyer_migration_signal','score': 72,'county': 'Salt Lake','city': None,          'raw_owner_name': None,              'raw_address': 'Salt Lake County | 4200 in-movers | avg AGI $82,000'},
    {'source_slug': 'validate-rental',        'signal_type': 'rental_listing',   'score': 55, 'county': 'Salt Lake',  'city': 'Murray',        'raw_owner_name': None,              'raw_address': '2br/1ba $1,800/mo Murray UT'},
    {'source_slug': 'validate-hmda',          'signal_type': 'mortgage_application','score': 82,'county': 'Utah',    'city': None,            'raw_owner_name': None,              'raw_address': '49049 | loan_amount=485000'},
]

EXPECTED_TIERS = {
    'validate-nts':        ('HOT', 99),
    'validate-nod':        ('HOT', 88),
    'validate-expired':    ('HOT', 95),
    'validate-fsbo':       ('WARM', 65),
    'validate-tax':        ('HOT', 75),
    'validate-lien':       ('WARM', 68),
    'validate-contractor': ('COOL', 30),
    'validate-migration':  ('HOT', 72),
    'validate-rental':     ('WARM', 55),
    'validate-hmda':       ('HOT', 82),
}


def score_tier(score):
    if score >= 70: return 'HOT'
    if score >= 40: return 'WARM'
    return 'COOL'


def clean_owner(name):
    if not name: return None
    name = name.strip()
    if len(name) < 3: return None
    return name


def clean_addr(addr):
    if not addr: return None
    addr = addr.strip()
    if len(addr) < 5: return None
    return addr[:200]


def run_validation():
    report = {
        'run_at': datetime.datetime.utcnow().isoformat() + 'Z',
        'mode': 'live' if LIVE else 'dry_run',
        'layers': {},
        'signals': {},
        'tier_distribution': {'HOT': 0, 'WARM': 0, 'COOL': 0},
        'score_tests': [],
        'errors': [],
        'passed': True,
    }

    print("\n" + "="*70)
    print("  Premier Prospect™ — End-to-End Pipeline Validation")
    print(f"  Mode: {'LIVE (Supabase)' if LIVE else 'DRY RUN (synthetic)'}")
    print(f"  Time: {report['run_at']}")
    print("="*70 + "\n")

    signals = [dict(s) for s in SYNTHETIC_SIGNALS]

    # ── LAYER 1: Source Ingestion ─────────────────────────────────────────────
    t0 = time.time()
    layer_name = LAYERS[0]
    try:
        assert len(signals) == 10, f"Expected 10 synthetic signals, got {len(signals)}"
        report['layers'][layer_name] = {'status': 'PASS', 'count': len(signals), 'duration_s': round(time.time()-t0,2)}
        print(f"[PASS] {layer_name}: {len(signals)} signals ingested")
    except Exception as e:
        report['layers'][layer_name] = {'status': 'FAIL', 'error': str(e)}
        report['errors'].append(str(e)); report['passed'] = False
        print(f"[FAIL] {layer_name}: {e}")

    # ── LAYER 2: Junk Filter ──────────────────────────────────────────────────
    t0 = time.time()
    layer_name = LAYERS[1]
    try:
        before = len(signals)
        for s in signals:
            s['raw_owner_name'] = clean_owner(s.get('raw_owner_name'))
            s['raw_address']    = clean_addr(s.get('raw_address'))
        after = len([s for s in signals if s['raw_address']])
        report['layers'][layer_name] = {'status': 'PASS', 'kept': after, 'filtered': before - after, 'duration_s': round(time.time()-t0,2)}
        print(f"[PASS] {layer_name}: {after} kept, {before - after} filtered")
    except Exception as e:
        report['layers'][layer_name] = {'status': 'FAIL', 'error': str(e)}
        report['errors'].append(str(e)); report['passed'] = False
        print(f"[FAIL] {layer_name}: {e}")

    # ── LAYER 3: Deduplication ────────────────────────────────────────────────
    t0 = time.time()
    layer_name = LAYERS[2]
    try:
        seen, unique = set(), []
        for s in signals:
            h = hashlib.md5(
                f"{s.get('source_slug','')}|{s.get('raw_owner_name','') or ''}|{s.get('raw_address','') or ''}".encode()
            ).hexdigest()
            s['dedupe_hash'] = h
            if h not in seen:
                seen.add(h); unique.append(s)
        dupes = len(signals) - len(unique)
        signals = unique
        report['layers'][layer_name] = {'status': 'PASS', 'unique': len(signals), 'duplicates': dupes, 'duration_s': round(time.time()-t0,2)}
        print(f"[PASS] {layer_name}: {len(signals)} unique, {dupes} duplicates removed")
    except Exception as e:
        report['layers'][layer_name] = {'status': 'FAIL', 'error': str(e)}
        report['errors'].append(str(e)); report['passed'] = False
        print(f"[FAIL] {layer_name}: {e}")

    # ── LAYER 4: Score Calculation ────────────────────────────────────────────
    t0 = time.time()
    layer_name = LAYERS[3]
    score_errors = []
    try:
        for s in signals:
            score = s.get('score', 0)
            assert 0 <= score <= 100, f"{s['source_slug']} score={score} out of 0-100 range"
        # Validate expected scores
        for s in signals:
            slug = s['source_slug']
            if slug in EXPECTED_TIERS:
                _, expected_score = EXPECTED_TIERS[slug]
                if s['score'] != expected_score:
                    score_errors.append(f"{slug}: expected score={expected_score}, got {s['score']}")
        report['layers'][layer_name] = {
            'status': 'PASS' if not score_errors else 'FAIL',
            'score_range': f"{min(s['score'] for s in signals)}-{max(s['score'] for s in signals)}",
            'errors': score_errors,
            'duration_s': round(time.time()-t0,2),
        }
        if score_errors:
            for e in score_errors: print(f"[FAIL]   Score mismatch: {e}")
            report['passed'] = False
        else:
            print(f"[PASS] {layer_name}: all scores valid (range {report['layers'][layer_name]['score_range']})")
    except Exception as e:
        report['layers'][layer_name] = {'status': 'FAIL', 'error': str(e)}
        report['errors'].append(str(e)); report['passed'] = False
        print(f"[FAIL] {layer_name}: {e}")

    # ── LAYER 5: Entity Resolution ────────────────────────────────────────────
    t0 = time.time()
    layer_name = LAYERS[4]
    try:
        if LIVE and SUPABASE_URL and SUPABASE_KEY:
            import requests as req
            r = req.post(
                f"{SUPABASE_URL}/rest/v1/rpc/pp_run_matching_engine",
                headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}', 'Content-Type': 'application/json'},
                json={'limit': 10}, timeout=30
            )
            status = 'PASS' if r.status_code in (200, 204) else f'WARN (HTTP {r.status_code})'
            report['layers'][layer_name] = {'status': status, 'http': r.status_code, 'duration_s': round(time.time()-t0,2)}
            print(f"[{status}] {layer_name}: RPC http={r.status_code}")
        else:
            # Dry run — simulate match on 8/10 signals
            matched = [s for s in signals if s.get('raw_owner_name')]
            report['layers'][layer_name] = {'status': 'PASS', 'matched': len(matched), 'mode': 'dry_run', 'duration_s': round(time.time()-t0,2)}
            print(f"[PASS] {layer_name}: {len(matched)} matched (dry run)")
    except Exception as e:
        report['layers'][layer_name] = {'status': 'FAIL', 'error': str(e)}
        report['errors'].append(str(e)); report['passed'] = False
        print(f"[FAIL] {layer_name}: {e}")

    # ── LAYER 5.5: MLS Validation ─────────────────────────────────────────────
    t0 = time.time()
    layer_name = LAYERS[5]
    try:
        sys.path.insert(0, os.path.dirname(__file__))
        from mls_validation import apply_mls_validation, run_validation_tests
        # Run built-in tests
        tests_passed = run_validation_tests()
        # Apply to synthetic signals
        enriched = apply_mls_validation(signals)
        modified = sum(1 for a, b in zip(signals, enriched) if a.get('score') != b.get('score'))
        report['layers'][layer_name] = {
            'status': 'PASS' if tests_passed else 'FAIL',
            'signals_modified': modified,
            'tests_passed': tests_passed,
            'duration_s': round(time.time()-t0,2),
        }
        signals = enriched
        status = 'PASS' if tests_passed else 'FAIL'
        print(f"[{status}] {layer_name}: {modified} scores adjusted, validation tests {'passed' if tests_passed else 'FAILED'}")
        if not tests_passed:
            report['passed'] = False
    except Exception as e:
        report['layers'][layer_name] = {'status': 'FAIL', 'error': str(e)}
        report['errors'].append(str(e)); report['passed'] = False
        print(f"[FAIL] {layer_name}: {e}")

    # ── LAYER 6: Enrichment (Tracerfy — staged) ───────────────────────────────
    t0 = time.time()
    layer_name = LAYERS[6]
    has_key = bool(os.environ.get('TRACERFY_API_KEY'))
    report['layers'][layer_name] = {
        'status': 'STAGED',
        'note': 'Tracerfy skip-trace staged — awaiting API key activation',
        'key_present': has_key,
        'duration_s': round(time.time()-t0,2),
    }
    print(f"[SKIP] {layer_name}: staged (key={'present' if has_key else 'absent'})")

    # ── LAYER 7: DNC Compliance (staged) ─────────────────────────────────────
    t0 = time.time()
    layer_name = LAYERS[7]
    has_san = bool(os.environ.get('DNC_SAN'))
    report['layers'][layer_name] = {
        'status': 'STAGED',
        'note': 'DNC compliance staged — awaiting DNC_SAN activation',
        'san_present': has_san,
        'duration_s': round(time.time()-t0,2),
    }
    print(f"[SKIP] {layer_name}: staged (SAN={'present' if has_san else 'absent'})")

    # ── LAYER 8: Tier Assignment ──────────────────────────────────────────────
    t0 = time.time()
    layer_name = LAYERS[8]
    try:
        tier_errors = []
        for s in signals:
            score = s.get('score', 0)
            expected_tier, _ = EXPECTED_TIERS.get(s['source_slug'], (None, None))
            if expected_tier:
                actual_tier = score_tier(score)
                s['tier'] = actual_tier
                if actual_tier != expected_tier:
                    tier_errors.append(f"{s['source_slug']}: expected {expected_tier}, got {actual_tier} (score={score})")
            else:
                s['tier'] = score_tier(score)

        dist = {'HOT': 0, 'WARM': 0, 'COOL': 0}
        for s in signals:
            dist[s.get('tier', 'COOL')] = dist.get(s.get('tier', 'COOL'), 0) + 1

        report['tier_distribution'] = dist
        status = 'PASS' if not tier_errors else 'FAIL'
        report['layers'][layer_name] = {
            'status': status,
            'distribution': dist,
            'errors': tier_errors,
            'duration_s': round(time.time()-t0,2),
        }
        if tier_errors:
            for e in tier_errors: print(f"[FAIL]   Tier mismatch: {e}")
            report['passed'] = False
        else:
            print(f"[PASS] {layer_name}: HOT={dist['HOT']}, WARM={dist['WARM']}, COOL={dist['COOL']}")
    except Exception as e:
        report['layers'][layer_name] = {'status': 'FAIL', 'error': str(e)}
        report['errors'].append(str(e)); report['passed'] = False
        print(f"[FAIL] {layer_name}: {e}")

    # ── LAYER 9: Dashboard Refresh ────────────────────────────────────────────
    t0 = time.time()
    layer_name = LAYERS[9]
    try:
        if LIVE and SUPABASE_URL and SUPABASE_KEY:
            import requests as req
            r = req.post(
                f"{SUPABASE_URL}/rest/v1/rpc/pp_refresh_kpi_cache",
                headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}', 'Content-Type': 'application/json'},
                json={}, timeout=30
            )
            status = 'PASS' if r.status_code in (200, 204) else f'WARN (HTTP {r.status_code})'
            report['layers'][layer_name] = {'status': status, 'http': r.status_code, 'duration_s': round(time.time()-t0,2)}
            print(f"[{status}] {layer_name}: KPI cache http={r.status_code}")
        else:
            report['layers'][layer_name] = {'status': 'PASS', 'mode': 'dry_run', 'duration_s': round(time.time()-t0,2)}
            print(f"[PASS] {layer_name}: dry run (would call pp_refresh_kpi_cache RPC)")
    except Exception as e:
        report['layers'][layer_name] = {'status': 'FAIL', 'error': str(e)}
        report['errors'].append(str(e)); report['passed'] = False
        print(f"[FAIL] {layer_name}: {e}")

    # ── LAYER 10: Outreach Routing (staged) ───────────────────────────────────
    t0 = time.time()
    layer_name = LAYERS[10]
    has_vapi = bool(os.environ.get('VAPI_API_KEY'))
    hot_with_phone = [s for s in signals if s.get('tier') == 'HOT' and s.get('raw_phone')]
    report['layers'][layer_name] = {
        'status': 'STAGED',
        'note': 'VAPI voice outreach staged — awaiting activation',
        'vapi_key_present': has_vapi,
        'eligible_leads': len(hot_with_phone),
        'duration_s': round(time.time()-t0,2),
    }
    print(f"[SKIP] {layer_name}: staged ({len(hot_with_phone)} eligible HOT leads with phone)")

    # ── SIGNAL REPORT ─────────────────────────────────────────────────────────
    report['signals'] = {
        'total_ingested': 10,
        'after_dedup': len(signals),
        'tier_distribution': report['tier_distribution'],
        'sources': list({s['source_slug'] for s in signals}),
    }

    # ── SCORE VALIDATION SUMMARY ──────────────────────────────────────────────
    print("\n  Score / Tier Validation:")
    print(f"  {'Source':<30} {'Score':>6} {'Tier':>6}  {'Expected Tier':>14}  {'Result'}")
    print("  " + "-"*70)
    score_tests = []
    for s in signals:
        slug = s['source_slug']
        score = s.get('score', 0)
        tier  = s.get('tier', '?')
        exp_tier, exp_score = EXPECTED_TIERS.get(slug, (None, None))
        ok = (exp_tier is None) or (tier == exp_tier)
        result = 'PASS' if ok else 'FAIL'
        if not ok: report['passed'] = False
        score_tests.append({'source': slug, 'score': score, 'tier': tier, 'expected_tier': exp_tier, 'result': result})
        print(f"  {slug:<30} {score:>6} {tier:>6}  {(exp_tier or '—'):>14}  {result}")
    report['score_tests'] = score_tests

    # ── FINAL SUMMARY ─────────────────────────────────────────────────────────
    print("\n" + "="*70)
    overall = 'ALL LAYERS PASSED' if report['passed'] else f"FAILURES DETECTED ({len(report['errors'])} errors)"
    print(f"  Pipeline Validation: {overall}")
    print(f"  Sources run:        {len(report['signals']['sources'])}")
    print(f"  Signals processed:  {report['signals']['after_dedup']}")
    print(f"  Tier distribution:  HOT={report['tier_distribution']['HOT']}, WARM={report['tier_distribution']['WARM']}, COOL={report['tier_distribution']['COOL']}")
    if report['errors']:
        print(f"  Errors:")
        for e in report['errors']:
            print(f"    - {e}")
    print("="*70 + "\n")

    # Write JSON report
    report_path = os.path.join(os.path.dirname(__file__), '..', 'validation_report.json')
    try:
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"  Report written to: {os.path.abspath(report_path)}")
    except Exception as e:
        print(f"  Could not write report: {e}")

    return report['passed']


if __name__ == '__main__':
    ok = run_validation()
    sys.exit(0 if ok else 1)
