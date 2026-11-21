"""
Site + app check for a few leads, signed in as the ONE permanent QA device (pp_qa_device, agent 'QA device (automated
checks)'). Nov 21 2026: earlier checks signed up a throwaway anonymous device each time and left a pp_agents row behind.
This never signs up: it refreshes the QA device's stored session, writes the rotated refresh token back, and reads
  - the app feed (pp_conviction_queue, exactly what the iOS app reads, under the QA device's own token), and
  - the live site's lead drawer (Playwright), for each lead.
The browser gets only a short-lived access token (no refresh token), so the page cannot rotate the stored session.
Env: SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_ANON_KEY, LEADS (comma-separated entity keys; default: top 3 reachable).
"""
import os, sys, json, time, requests

SB = os.environ['SUPABASE_URL'].rstrip('/'); SVC = os.environ['SUPABASE_SERVICE_KEY']; ANON = os.environ['SUPABASE_ANON_KEY']
SITE = os.environ.get('SITE', 'https://sharplifee.github.io/prospectplus-dashboard/crm/leads.html')
HS = {'apikey': SVC, 'Authorization': f'Bearer {SVC}', 'Content-Type': 'application/json'}


def qa_session():
    row = requests.get(f'{SB}/rest/v1/pp_qa_device?id=eq.1&select=agent_id,refresh_token', headers=HS, timeout=30).json()[0]
    r = requests.post(f'{SB}/auth/v1/token?grant_type=refresh_token', headers={'apikey': ANON, 'Content-Type': 'application/json'},
                      json={'refresh_token': row['refresh_token']}, timeout=30)
    if r.status_code != 200:
        sys.exit(f'QA device session could not be refreshed ({r.status_code}); not signing up a new device. Re-seed pp_qa_device.')
    s = r.json()
    w = requests.patch(f'{SB}/rest/v1/pp_qa_device?id=eq.1', headers={**HS, 'Prefer': 'return=minimal'},
                       json={'refresh_token': s['refresh_token'], 'refreshed_at': 'now()'}, timeout=30)
    if w.status_code >= 300: sys.exit(f'could not store the rotated QA token: {w.status_code} {w.text[:200]}')
    assert s['user']['id'] == row['agent_id'], 'stored session is not the QA device'
    return s


def main():
    before = requests.get(f'{SB}/rest/v1/pp_agents?select=agent_id', headers={**HS, 'Prefer': 'count=exact', 'Range': '0-0'}, timeout=30)
    s = qa_session(); tok = s['access_token']; HA = {'apikey': ANON, 'Authorization': f'Bearer {tok}'}
    leads = [k.strip() for k in os.environ.get('LEADS', '').split(',') if k.strip()]
    if not leads:
        leads = [x['entity_key'] for x in requests.get(f'{SB}/rest/v1/pp_conviction_queue?select=entity_key&contactable=is.true&order=conviction_score.desc&limit=3', headers=HA, timeout=30).json()]
    app = {}
    for k in leads:
        rows = requests.get(f'{SB}/rest/v1/pp_conviction_queue', headers=HA, timeout=30,
                            params={'entity_key': f'eq.{k}', 'select': 'entity_key,conviction_score,mailing_address,mailing_confirmed,contactable,lead_label,value_label'}).json()
        app[k] = rows[0] if rows else None
    from playwright.sync_api import sync_playwright
    sess = {'access_token': tok, 'refresh_token': '', 'expires_at': int(time.time() * 1000) + 3000 * 1000, 'user': {'id': s['user']['id']}}
    out, errs = [], []
    with sync_playwright() as pw:
        b = pw.chromium.launch(); ctx = b.new_context(viewport={'width': 1280, 'height': 900})
        ctx.add_init_script(f"try{{localStorage.setItem('pp.session', {json.dumps(json.dumps(sess))})}}catch(e){{}}")
        p = ctx.new_page(); p.on('pageerror', lambda e: errs.append(str(e)))
        p.goto(f'{SITE}?scope=all&t={int(time.time())}', wait_until='networkidle'); p.wait_for_selector('#rows table', timeout=40000)
        for k in leads:
            p.evaluate('k => PP.openLeadByKey(k)', k); p.wait_for_selector('#drawer.open', timeout=15000); p.wait_for_timeout(700)
            d = p.evaluate("""() => { const g = l => { const r = [...document.querySelectorAll('#drawer .kv')].find(x => x.firstElementChild && x.firstElementChild.textContent === l); return r ? r.lastElementChild.textContent : null; };
                                  return {head: document.querySelector('#drawer .hd .muted').innerText, mail: g('Mailing address'), status: g('Address status'), value: g('County value')}; }""")
            p.evaluate('() => PP.closeDrawer()')
            a = app[k] or {}
            ok = bool(a) and d['mail'] == a.get('mailing_address') and d['head'].startswith(str(a.get('conviction_score')))
            out.append({'lead': k, 'app': a, 'site': d, 'site_matches_app': ok})
        b.close()
    after = requests.get(f'{SB}/rest/v1/pp_agents?select=agent_id', headers={**HS, 'Prefer': 'count=exact', 'Range': '0-0'}, timeout=30)
    for o in out: print(json.dumps(o))
    n0, n1 = before.headers.get('content-range', '*/?').split('/')[-1], after.headers.get('content-range', '*/?').split('/')[-1]
    print(f'pp_agents rows before {n0}, after {n1} (QA device reused: {s["user"]["id"]})')
    print('page errors:', json.dumps(errs))
    if errs or not all(o['site_matches_app'] for o in out) or n0 != n1: sys.exit(1)


if __name__ == '__main__':
    main()
