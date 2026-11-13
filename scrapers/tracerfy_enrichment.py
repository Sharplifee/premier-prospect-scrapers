#!/usr/bin/env python3
"""
Premier Prospect — phone numbers via Tracerfy Instant Trace (rewritten Nov 12 2026).

Who gets traced: leads at or above TRACE_MIN conviction that have an assessor-CONFIRMED mailing address and no phone
yet, highest score first, never re-traced within 90 days. The mailing address is where the owner gets their tax bill,
which is where they actually live — the best address to find them by.

What an agent receives: only phones that belong to a returned person whose name matches the lead (surname + first
name), that Tracerfy does NOT flag as on the Do-Not-Call registry (dnc) or as a TCPA risk (tcpa), and only when that
person is not flagged as a known TCPA litigator. Everything else is counted in pp_skiptrace and discarded. A wrong or
illegal number is worse than no number.

API (docs: tracerfy.com/skip-tracing-api-documentation): POST {BASE}/trace/lookup/  Authorization: Bearer <token>
  body {address, city, state, zip, find_owner:true} -> {hit, persons:[{first_name,last_name,litigator,phones:[{number,type,dnc,tcpa,rank}]}], credits_deducted}
  5 credits per hit, 0 per miss. GET {BASE}/analytics/ -> {balance}.
Sandbox: TRACERFY_BASE=https://mock.tracerfy.com/v1/api with any token.
"""
import os, re, sys, json, time, logging, requests
try:
    import eventlog as _eventlog; _eventlog.install(job=__file__)
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s'); log = logging.getLogger('tracerfy')
SB = os.environ['SUPABASE_URL'].rstrip('/'); KEY = os.environ['SUPABASE_SERVICE_KEY']
H = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'}
TOKEN = os.environ.get('TRACERFY_API_KEY', '')
BASE = os.environ.get('TRACERFY_BASE', 'https://tracerfy.com/v1/api').rstrip('/')
LIMIT = int(os.environ.get('TRACE_LIMIT', '50'))
MIN_SCORE = int(os.environ.get('TRACE_MIN', '85'))
TH = {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'}
NOISE = {'TR', 'TRS', 'TRUSTEE', 'TEE', 'JT', 'ET', 'AL', 'JR', 'SR', 'II', 'III', 'THE', 'OF', 'AND', 'DECEASED', 'SUC', 'LIVING', 'REVOCABLE', 'FAMILY'}


def toks(s): return [t for t in re.findall(r"[A-Z']+", re.sub(r'\([^)]*\)', ' ', (s or '').upper())) if t not in NOISE and len(t) > 1]


def person_matches(lead, p):
    first, last = (p.get('first_name') or '').upper().strip(), (p.get('last_name') or '').upper().strip()
    if not first or not last: return False
    for part in re.split(r',(?![^()]*\))', lead or '')[:4]:
        t = toks(part)
        if last in t and first in t: return True
    return False


def rest_get(path):
    r = requests.get(f'{SB}/rest/v1/{path}', headers=H, timeout=120); r.raise_for_status(); return r.json()


def page(path):
    out, off = [], 0
    while True:
        p = rest_get(f'{path}&limit=1000&offset={off}')
        if not p: return out
        out += p; off += len(p)


def split_mail(addr):
    r = requests.post(f'{SB}/rest/v1/rpc/pp_split_mail', headers=H, json={'p': addr}, timeout=30)
    rows = r.json() if r.ok else []
    return rows[0] if rows else None


def log_attempt(key, hit, persons, credits, kept, rejected, note):
    requests.post(f'{SB}/rest/v1/pp_skiptrace?on_conflict=entity_key', headers={**H, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
                  json={'entity_key': key, 'traced_at': 'now()', 'hit': hit, 'persons': persons, 'credits': credits,
                        'phones_kept': kept, 'phones_rejected': rejected, 'note': note[:300]}, timeout=30)


def save_phone(key, phone, note):
    r = requests.post(f'{SB}/rest/v1/pp_contacts?on_conflict=entity_key', headers={**H, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
                      json={'entity_key': key, 'phone': phone, 'notes': note, 'updated_at': 'now()'}, timeout=30)
    if r.status_code >= 400: log.error(f'  save {key[:30]} {r.status_code} {r.text[:120]}')
    return r.status_code < 400


def run():
    if not TOKEN:
        log.warning('TRACERFY_API_KEY is not set — skip tracing did not run. No phones were added.')
        return 0
    b = requests.get(f'{BASE}/analytics/', headers=TH, timeout=30)
    if b.status_code != 200:
        log.error(f'Tracerfy analytics HTTP {b.status_code}: {b.text[:200]}'); return 0
    balance = b.json().get('balance')
    log.info(f'Tracerfy credit balance: {balance}')
    budget = min(LIMIT, int(balance or 0) // 5) if isinstance(balance, (int, float)) else LIMIT
    if budget <= 0:
        log.warning('Not enough Tracerfy credits for an instant trace (5 credits per hit).'); return 0
    tried = {r['entity_key'] for r in page('pp_skiptrace?select=entity_key&traced_at=gte.' + time.strftime('%Y-%m-%d', time.gmtime(time.time() - 90 * 86400)))}
    leads = [l for l in page(f'pp_entity_conviction?select=entity_key,owner_display,mailing_address,conviction_score&mailing_confirmed=is.true&has_phone=is.false'
                             f'&resolved_flag=is.false&conviction_score=gte.{MIN_SCORE}&order=conviction_score.desc,entity_key')
             if l['entity_key'] not in tried][:budget]
    log.info(f'{len(leads)} leads to trace (min score {MIN_SCORE}, budget {budget})')
    found = 0
    for l in leads:
        m = split_mail(l['mailing_address'])
        if not m or not m.get('city') or not m.get('state'):
            log_attempt(l['entity_key'], False, 0, 0, 0, 0, 'mailing address could not be split'); continue
        street = re.sub(r'^C/O .*?(?=\d)', '', m['street'] or '').strip()
        body = {'address': street, 'city': m['city'], 'state': m['state'], 'zip': (m.get('zip') or '')[:5], 'find_owner': True}
        for attempt in range(3):
            r = requests.post(f'{BASE}/trace/lookup/', headers=TH, json=body, timeout=60)
            if r.status_code == 429: time.sleep(int((r.json() or {}).get('retry_after_seconds', 20))); continue
            if r.status_code in (500, 502, 503): time.sleep(10); continue
            break
        if r.status_code == 402: log.warning('Out of Tracerfy credits — stopping.'); break
        if r.status_code != 200:
            log.error(f"  {l['owner_display'][:30]} HTTP {r.status_code}: {r.text[:160]}"); continue
        j = r.json()
        persons = j.get('persons') or []
        kept, rejected, notes = [], 0, []
        for p in persons:
            if not person_matches(l['owner_display'], p):
                rejected += len(p.get('phones') or []); continue
            if p.get('litigator'):
                rejected += len(p.get('phones') or []); notes.append('litigator flag — all numbers withheld'); continue
            if p.get('deceased'): notes.append(f"{p.get('full_name')} listed deceased")
            for ph in sorted(p.get('phones') or [], key=lambda x: x.get('rank') or 99):
                if ph.get('dnc') or ph.get('tcpa'): rejected += 1; continue
                num = re.sub(r'\D', '', str(ph.get('number') or ''))
                if len(num) == 11 and num.startswith('1'): num = num[1:]
                if len(num) != 10: rejected += 1; continue
                kept.append((num, ph.get('type') or '?', p.get('full_name') or ''))
        note = f"Tracerfy {time.strftime('%Y-%m-%d')} · " + ('; '.join(f'{t} {n[:3]}-{n[3:6]}-{n[6:]} ({who})' for n, t, who in kept[:3]) or 'no callable number')
        if notes: note += ' · ' + '; '.join(notes)
        if kept:
            n, t, who = kept[0]
            if save_phone(l['entity_key'], f'{n[:3]}-{n[3:6]}-{n[6:]}', note): found += 1
        log_attempt(l['entity_key'], bool(j.get('hit')), len(persons), int(j.get('credits_deducted') or 0), len(kept), rejected, note)
        log.info(f"  {l['owner_display'][:34]:36} hit={j.get('hit')} persons={len(persons)} kept={len(kept)} rejected={rejected}")
        time.sleep(0.2)
    log.info(f'phones added: {found}')
    return found


if __name__ == '__main__':
    run()
