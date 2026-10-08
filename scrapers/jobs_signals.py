"""
Jobs moving into and out of our cities (Utah, Salt Lake, Wasatch, Summit counties).

Utah publishes no free list of new hires or newlyweds (marriage licenses are vital records), so the real public signal
of people relocating is company-level:
  in  — Governor's Office of Economic Opportunity incentive announcements (business.utah.gov, WordPress API):
        "Jobs: N" / "New Jobs: N" with the city in the title or body.
  out — DWS WARN layoff notices (jobs.utah.gov/employer/business/warnnotices.html): date, company, location, workers.
Only events in a city inside our four counties are kept; city → county comes from pp_municipalities. Never names a person.
"""
import os, re, html, datetime, logging, requests
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s'); log = logging.getLogger('jobs')
SB = os.environ['SUPABASE_URL'].rstrip('/'); KEY = os.environ['SUPABASE_SERVICE_KEY']
H = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'}
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36'}
SINCE = datetime.date.today() - datetime.timedelta(days=int(os.environ.get('JOBS_DAYS', '400')))

def text(s): return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).strip()

munis = requests.get(f"{SB}/rest/v1/pp_municipalities?select=name,county", headers=H, timeout=60).json()
# longest names first so "South Salt Lake" wins over "Salt Lake City"/"Salt Lake"
munis = sorted({(m['name'], m['county']) for m in munis if m.get('name')}, key=lambda m: -len(m[0]))
def place(*texts):
    for t in texts:
        u = (t or '').upper()
        for name, county in munis:
            if re.search(r'\b' + re.escape(name.upper()) + r'\b', u): return name, county
        m = re.search(r'\b(UTAH|SALT LAKE|WASATCH|SUMMIT) COUNTY\b', u)
        if m: return None, m.group(1).title()
    return None, None

rows = []
# --- jobs coming in: GOEO incentive announcements -------------------------------------------------------------
page = 1
while page <= 20:
    r = requests.get('https://business.utah.gov/wp-json/wp/v2/posts', params={'search': 'EDTIF', 'per_page': 50, 'page': page,
                     '_fields': 'date,title,link,content'}, headers=UA, timeout=60)
    if r.status_code != 200: log.warning(f'GOEO page {page}: HTTP {r.status_code}'); break
    posts = r.json()
    if not posts: break
    stop = False
    for p in posts:
        d = datetime.date.fromisoformat(p['date'][:10])
        if d < SINCE: stop = True; continue
        title = text(p['title']['rendered']); body = text(p['content']['rendered'])
        m = re.search(r'\b(?:New )?Jobs:\s*([\d,]+)', body, re.I)
        jobs = int(m.group(1).replace(',', '')) if m else None
        city, county = place(title, body)
        company = re.split(r'\s+(?:Expands|Expansion|Moves|to Build|to Develop|to Expand|Will|Selects|Chooses|Relocates|Opens|Adds|Announces|Invests|Establishes|Brings|Plans|Deepens|Makes|Launches|Boosts|Breaks|Grows|Commits|Builds|Develops|Creates)\b', title)[0].strip() or title
        company = re.sub(r'^(Utah-Based|Aerospace Leader|Utah)\s+', '', company).strip()
        rows.append({'source': 'goeo-incentives', 'event_date': str(d), 'company': company[:200], 'city': city, 'county': county,
                     'jobs': jobs, 'direction': 'in', 'title': title[:300], 'url': p['link']})
    if stop: break
    page += 1
log.info(f"GOEO: {sum(1 for x in rows if x['source']=='goeo-incentives')} announcements since {SINCE}")

# --- jobs going out: WARN layoff notices ----------------------------------------------------------------------
w = requests.get('https://jobs.utah.gov/employer/business/warnnotices.html', headers=UA, timeout=60)
n_warn = 0
if w.status_code == 200:
    for raw in re.findall(r'<tr[^>]*>(.*?)</tr>', w.text, re.S):
        c = [text(x) for x in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', raw, re.S)]
        if len(c) < 4 or not re.match(r'\d{1,2}/\d{1,2}/\d{2,4}$', c[0]): continue
        mm, dd, yy = c[0].split('/'); yy = int(yy) + (2000 if len(yy) == 2 else 0)
        try: d = datetime.date(yy, int(mm), int(dd))
        except ValueError: continue
        if d < SINCE: continue
        city, county = place(c[2])
        jobs = int(re.sub(r'[^\d]', '', c[3]) or 0) or None
        rows.append({'source': 'dws-warn', 'event_date': str(d), 'company': c[1][:200], 'city': city, 'county': county,
                     'jobs': jobs, 'direction': 'out', 'title': f"{c[1]}: {c[3]} workers, {c[2]}"[:300], 'url': 'https://jobs.utah.gov/employer/business/warnnotices.html'})
        n_warn += 1
else:
    log.warning(f'WARN page HTTP {w.status_code}')
log.info(f'WARN: {n_warn} notices since {SINCE}')

if rows:
    r = requests.post(f"{SB}/rest/v1/pp_job_events?on_conflict=source,title", json=rows,
                      headers={**H, 'Prefer': 'resolution=merge-duplicates,return=minimal'}, timeout=60)
    log.info(f'saved {len(rows)} events: HTTP {r.status_code} {r.text[:200]}')
    if r.status_code >= 400: raise SystemExit(1)
in_area = [x for x in rows if x['county']]
log.info(f"in our four counties: {len(in_area)} ({sum(1 for x in in_area if x['direction']=='in')} adding jobs, {sum(1 for x in in_area if x['direction']=='out')} layoffs)")
