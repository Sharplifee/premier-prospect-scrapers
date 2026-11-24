"""
End-to-end walk of one top-tier lead in the live web CRM, signed in as the permanent QA device (never a new device):
lead drawer -> "Spoke with them" (call log) -> note -> "Appointment set" -> deal saved on the Ledger page.
Checks each step landed in the database and shows on the page, then removes everything the walk wrote:
the QA device's outcome events and deal for that lead are deleted by id, and the lead's lifecycle row is put back
exactly as it was (or deleted if it did not exist). The before-image is printed first as the backup.
Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_ANON_KEY, optional LEAD (entity key).
"""
import os, sys, json, time, requests
from qa_check import qa_session, SB, HS

BASE = os.environ.get('SITE_BASE', 'https://sharplifee.github.io/prospectplus-dashboard/crm')
NOTE = 'TEST QA walk — removed automatically after the check'


def svc(path):
    r = requests.get(f'{SB}/rest/v1/{path}', headers=HS, timeout=60); r.raise_for_status(); return r.json()


def app_walk(s, lead, HA, ek, before):
    """Nov 24 2026: the iOS app's own call sequence for one lead (Sources/PremierProspectApp.swift, build 109): lead list,
    lead detail calls, call logged, note, appointment, deal, ledger. Same RPCs, same parameters, the app's user agent."""
    UA = {'User-Agent': 'PremierProspect/109 CFNetwork/3896.200.61 Darwin/27.2.0 (QA walk)', 'Content-Type': 'application/json'}
    def rpc(name, body):
        r = requests.post(f'{SB}/rest/v1/rpc/{name}', headers={**HA, **UA}, json=body, timeout=60)
        return r.status_code, (r.json() if r.text.strip() else None)
    steps = []
    c, rows = rpc('pp_app_queue', {'p_limit': 50, 'p_search': lead.split(':', 2)[2], 'p_min_docs': 1})
    steps.append(('app lead list returns the lead', c == 200 and any(r.get('entity_key') == lead for r in rows)))
    for fn in ('pp_app_lead_documents', 'pp_app_call_script'):
        c, _ = rpc(fn, {'p_entity_key': lead}); steps.append((f'app {fn} answers', c == 200))
    for ev, oc, nt in (('call', 'contacted', None), ('note', None, NOTE), ('appointment_set', None, None)):
        body = {'p_entity_key': lead, 'p_event_type': ev, 'p_channel': 'phone', 'p_actor': 'ios'}
        if oc: body['p_outcome'] = oc
        if nt: body['p_notes'] = nt
        c, _ = rpc('pp_record_outcome', body); steps.append((f'app records {ev}', c == 200))
    c, _ = rpc('pp_save_deal', {'p_id': None, 'p_entity_key': lead, 'p_stage': 'appointment', 'p_list_price': None,
                                'p_commission_pct': None, 'p_expected_close': None, 'p_notes': NOTE})
    steps.append(('app saves the deal', c == 200))
    c, led = rpc('pp_app_ledger', {})
    led = json.loads(led) if isinstance(led, str) else led
    steps.append(('deal shows in the app ledger', c == 200 and any(d.get('notes') == NOTE for d in (led or {}).get('deals', []))))
    c, _ = rpc('pp_app_next_actions', {'p_limit': 15}); steps.append(('app next actions answers', c == 200))
    lc = svc(f'pp_lead_lifecycle?entity_key=eq.{ek}&select=state,touch_count')
    steps.append((f'lifecycle moved to appointment ({lc})', bool(lc) and lc[0]['state'] == 'appointment'))
    ev = svc(f'pp_outcome_events?entity_key=eq.{ek}&select=event_type,outcome,actor')
    steps.append(('three events written by the QA device', len([e for e in ev if e['actor'] == s['user']['id']]) == 3))
    return steps


def main():
    s = qa_session(); tok = s['access_token']; qa = s['user']['id']
    HA = {'apikey': os.environ['SUPABASE_ANON_KEY'], 'Authorization': f'Bearer {tok}'}
    lead = os.environ.get('LEAD', '').strip()
    if not lead:
        # a top-tier, reachable lead nobody has worked yet (no lifecycle row, no deal), from the same feed the app reads
        cand = requests.get(f'{SB}/rest/v1/pp_conviction_queue', headers=HA, timeout=60, params={
            'select': 'entity_key,owner_display,conviction_score,lifecycle_state', 'conviction_score': 'gte.85',
            'contactable': 'is.true', 'order': 'conviction_score.desc', 'limit': '200'}).json()
        worked = {x['entity_key'] for x in svc('pp_lead_lifecycle?select=entity_key')} | {x['entity_key'] for x in svc('pp_deals?select=entity_key')}
        if not isinstance(cand, list): sys.exit(f'queue read failed: {str(cand)[:300]}')
        print(f'{len(cand)} top-tier reachable candidates; lifecycle states seen: {sorted({str(c.get("lifecycle_state")) for c in cand})}')
        lead = next(c['entity_key'] for c in cand if c['entity_key'] not in worked and c.get('lifecycle_state') in (None, 'new'))
    ek = requests.utils.quote(lead, safe='')
    before = {'lifecycle': svc(f'pp_lead_lifecycle?entity_key=eq.{ek}'),
              'event_ids': [x['id'] for x in svc(f'pp_outcome_events?entity_key=eq.{ek}&select=id')],
              'deal_ids': [x['id'] for x in svc(f'pp_deals?entity_key=eq.{ek}&select=id')]}
    print('lead:', lead); print('BACKUP (before):', json.dumps(before))
    row = requests.get(f'{SB}/rest/v1/pp_conviction_queue', headers=HA, timeout=60,
                       params={'entity_key': f'eq.{lead}', 'select': 'owner_display,conviction_score,mailing_address,mailing_confirmed,contactable,parcel_match'}).json()[0]
    print('app feed:', json.dumps(row))

    if os.environ.get('MODE') == 'app':
        steps, errs, dialogs = [], [], []
        try:
            steps = app_walk(s, lead, HA, ek, before)
        finally:
            cleanup(ek, before)
        return finish(ek, before, steps, errs, dialogs)
    from playwright.sync_api import sync_playwright
    sess = {'access_token': tok, 'refresh_token': '', 'expires_at': int(time.time() * 1000) + 3000 * 1000, 'user': {'id': qa}}
    steps, errs, dialogs = [], [], []
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(); ctx = b.new_context(viewport={'width': 1280, 'height': 900})
            ctx.add_init_script(f"try{{localStorage.setItem('pp.session', {json.dumps(json.dumps(sess))})}}catch(e){{}}")
            p = ctx.new_page(); p.on('pageerror', lambda e: errs.append(str(e)))
            p.on('dialog', lambda d: (dialogs.append(d.message), d.accept()))
            for attempt in (1, 2):
                p.goto(f'{BASE}/leads.html?scope=all&t={int(time.time())}', wait_until='networkidle')
                try: p.wait_for_selector('#rows table', timeout=60000); break
                except Exception:
                    print(f'leads page not ready (attempt {attempt}) at {p.url}: {p.inner_text("body")[:600]!r}')
                    if attempt == 2: raise
            gate = p.evaluate("() => !!document.querySelector('input[type=password]') || /login\\.html/.test(location.href)")
            steps.append(('no login or gate on the leads page', not gate))

            def drawer():
                p.evaluate('k => PP.openLeadByKey(k)', lead); p.wait_for_selector('#drawer.open', timeout=15000); p.wait_for_timeout(600)
            drawer()
            mail = p.evaluate("() => { const r=[...document.querySelectorAll('#drawer .kv')].find(x=>x.firstElementChild&&x.firstElementChild.textContent==='Mailing address'); return r?r.lastElementChild.textContent:null }")
            steps.append(('drawer shows the mailing address', mail == row['mailing_address']))
            p.click('#drawer button:has-text("Spoke with them")'); p.wait_for_timeout(2500)
            drawer(); p.fill('#pp-note', NOTE); p.click('#drawer button:has-text("Save")'); p.wait_for_timeout(2500)
            drawer(); p.click('#drawer button:has-text("Appointment set")'); p.wait_for_timeout(2500)
            ev = svc(f'pp_outcome_events?entity_key=eq.{ek}&actor=eq.{qa}&select=id,event_type,outcome,notes&order=id')
            got = [(e['event_type'], e['outcome']) for e in ev]
            steps.append(('call logged (call / contacted)', ('call', 'contacted') in got))
            steps.append(('note saved', any(e['event_type'] == 'note' and e['notes'] == NOTE for e in ev)))
            steps.append(('appointment logged', ('appointment_set', None) in got))
            lc = svc(f'pp_lead_lifecycle?entity_key=eq.{ek}&select=state,touch_count')
            steps.append((f'lifecycle moved to appointment ({lc})', bool(lc) and lc[0]['state'] == 'appointment'))

            p.goto(f'{BASE}/ledger.html?t={int(time.time())}', wait_until='networkidle'); p.wait_for_timeout(1500)
            p.fill('#dName', row['owner_display']); p.dispatch_event('#dName', 'change')
            p.select_option('#dStage', 'appointment'); p.fill('#dNotes', NOTE); p.click('button:has-text("Save deal")'); p.wait_for_timeout(3000)
            msg = p.inner_text('#dMsg')
            deals = svc(f'pp_deals?entity_key=eq.{ek}&select=id,stage,list_price,notes,owner_user')
            steps.append((f'deal saved from the Ledger ({msg})', any(d['notes'] == NOTE and d['stage'] == 'appointment' for d in deals)))
            steps.append(('deal shows in the Ledger table', NOTE in p.inner_text('#deals')))

            p.goto(f'{BASE}/pipeline.html?t={int(time.time())}', wait_until='networkidle'); p.wait_for_timeout(2500)
            steps.append(('pipeline page loads', 'pipeline' in p.url))
            b.close()
    finally:
        cleanup(ek, before)
    return finish(ek, before, steps, errs, dialogs)


def cleanup(ek, before):
        # clean up everything the walk wrote, by id, and put the lifecycle row back as it was
        new_ev = [x['id'] for x in svc(f'pp_outcome_events?entity_key=eq.{ek}&select=id') if x['id'] not in before['event_ids']]
        new_dl = [x['id'] for x in svc(f'pp_deals?entity_key=eq.{ek}&select=id') if x['id'] not in before['deal_ids']]
        for tbl, ids in (('pp_outcome_events', new_ev), ('pp_deals', new_dl)):
            if ids:
                r = requests.delete(f'{SB}/rest/v1/{tbl}?id=in.({",".join(map(str, ids))})', headers={**HS, 'Prefer': 'return=minimal'}, timeout=60)
                print(f'cleanup {tbl} {ids}: {r.status_code}')
        if before['lifecycle']:
            r = requests.patch(f'{SB}/rest/v1/pp_lead_lifecycle?entity_key=eq.{ek}', headers={**HS, 'Prefer': 'return=minimal'},
                               json=before['lifecycle'][0], timeout=60)
        else:
            r = requests.delete(f'{SB}/rest/v1/pp_lead_lifecycle?entity_key=eq.{ek}', headers={**HS, 'Prefer': 'return=minimal'}, timeout=60)
        print(f'cleanup pp_lead_lifecycle: {r.status_code}')


def finish(ek, before, steps, errs, dialogs):
    after = {'lifecycle': svc(f'pp_lead_lifecycle?entity_key=eq.{ek}'),
             'event_ids': [x['id'] for x in svc(f'pp_outcome_events?entity_key=eq.{ek}&select=id')],
             'deal_ids': [x['id'] for x in svc(f'pp_deals?entity_key=eq.{ek}&select=id')]}
    restored = after == before
    steps.append(('lead put back exactly as before', restored))
    for name, ok in steps: print(('PASS ' if ok else 'FAIL ') + name)
    print('dialogs:', json.dumps(dialogs)); print('page errors:', json.dumps(errs))
    if errs or not all(ok for _, ok in steps): sys.exit(1)


if __name__ == '__main__':
    main()
