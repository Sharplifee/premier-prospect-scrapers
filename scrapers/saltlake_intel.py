"""
Salt Lake County owner → parcel resolution via the county assessor's owner-name search
(apps.saltlakecounty.gov/assessor/new/resultsMain.cfm?itemname=LAST, FIRST&searchType=owner).

Salt Lake leads come from court calendars and carry no parcel, so they had no city, no
map point, and no value. The assessor answers an exact "LAST, FIRST" query with either a
single parcel detail page or a list. Rule: EXACT owner-name match only (suffixes like
"; JT ET AL" stripped). Under-matching is safe; a wrong parcel puts an agent at a
stranger's door. Writes pp_parcel_intel (parcel, value, address) and pp_parcel_geo
(the page's own lat/lon; city filled by pp_finish_parcel_geo point-in-polygon).
"""
import os, re, html, time, logging, requests
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s'); log=logging.getLogger('slco')
SB=os.environ['SUPABASE_URL'].rstrip('/'); KEY=os.environ['SUPABASE_SERVICE_KEY']
H={'apikey':KEY,'Authorization':'Bearer '+KEY,'Content-Type':'application/json'}
S=requests.Session(); S.headers['User-Agent']='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36'
ASSESSOR="https://apps.saltlakecounty.gov/assessor/new/resultsMain.cfm"
NOISE=re.compile(r'\b(JT|ET AL|ETAL|TR|TRS|TRUSTEE|TRUSTEES|TTEE|TEE|JR|SR|II|III|AKA|FKA|DBA|ATA)\b')
def norm(n):
    n=(n or '').upper().split(';')[0]; n=re.sub(r'\(.*?\)','',n); n=NOISE.sub(' ',n); n=re.sub(r'[^A-Z, ]',' ',n); return re.sub(r'\s+',' ',n).strip(' ,')
def query_name(n):
    """LAST, FIRST at the assessor's granularity (it stores no middle names). Reverses FIRST LAST; couples take the first person."""
    q=norm(n)
    if ',' in q: last,first=[p.strip() for p in q.split(',',1)]
    else:
        parts=q.split()
        if len(parts)<2: return None
        last,first=parts[-1],' '.join(parts[:-1])
    first=first.split()[0] if first.split() else ''
    return f"{last}, {first}" if last and first else None
def strip_tags(s): return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>','|',re.sub(r'<(script|style)[^>]*>.*?</\1>','',s,flags=re.S|re.I))))
def parse_detail(page):
    t=strip_tags(page)
    pid=re.search(r'Parcel\s*\|?\s*(\d{14})',t); owner=re.search(r'Owner\s*(?:\|\s*)+([^|]+?)\s*\|',t); addr=re.search(r'\|\s*Address\s*(?:\|\s*)+([^|]+?)\s*\|',t)
    val=re.search(r'Market Value\s*(?:\|\s*)+\$\s*([\d,]+)',t); ll=re.search(r'\|\s*(4\d\.\d{4,})\s*\|\s*(-11\d\.\d{4,})',t)
    if not pid: return None
    return {'parcel':pid.group(1),'owner':(owner.group(1).strip() if owner else ''),'address':(addr.group(1).strip() if addr else None),
            'value':(int(val.group(1).replace(',','')) if val else None),'lat':(float(ll.group(1)) if ll else None),'lon':(float(ll.group(2)) if ll else None)}
def resolve(name):
    q=query_name(name)
    if not q: return None
    r=S.get(ASSESSOR,params={'itemname':q,'searchType':'owner'},timeout=40)
    d=parse_detail(r.text)
    if d:                                                            # single parcel came back as a detail page
        return d if query_name(d['owner'])==q else None
    hits={}                                                          # list page → parcel ids whose owner matches exactly
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>',r.text,re.S|re.I):
        m=re.search(r"Parcel_id=(\d{14})",tr)
        if not m: continue
        txt=re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>',' ',tr))).strip(); own=re.sub(r'^\d+\s+','',txt).split(';')[0]
        if query_name(own)==q: hits[m.group(1)]=own
    if len(hits)!=1: return None                                     # zero or ambiguous → skip; never guess
    pid=next(iter(hits))
    d=S.get("https://apps.saltlakecounty.gov/assessor/new/valuationInfoExpanded.cfm",params={'Parcel_id':pid},timeout=40)
    det=parse_detail(d.text); return det if det and query_name(det['owner'])==q else None
def main(limit=60):
    have=set(x['owner_key'] for x in requests.get(f"{SB}/rest/v1/pp_parcel_intel?select=owner_key&county=eq.Salt%20Lake&limit=10000",headers=H,timeout=60).json())
    todo=[x for x in requests.get(f"{SB}/rest/v1/pp_conviction_queue?select=entity_key,owner_display&county=eq.Salt%20Lake&order=conviction_score.desc&limit=3000",headers=H,timeout=60).json() if x['entity_key'] not in have][:limit]
    log.info(f'{len(todo)} Salt Lake owners to resolve'); n=0
    for x in todo:
        try: d=resolve(x['owner_display'])
        except Exception as e: log.warning(f"  {x['owner_display'][:30]}: {type(e).__name__}"); continue
        if not d: continue
        requests.post(f"{SB}/rest/v1/pp_parcel_intel?on_conflict=parcel_serial",json={'parcel_serial':d['parcel'],'owner_key':x['entity_key'],'county':'Salt Lake','market_value':d['value'],'property_address':d['address'],'fetched_at':'now()'},headers={**H,'Prefer':'resolution=merge-duplicates,return=minimal'},timeout=30)
        if d['lat'] and d['lon']:
            requests.post(f"{SB}/rest/v1/pp_parcel_geo?on_conflict=parcel_serial",json={'parcel_serial':d['parcel'],'county':'Salt Lake','address':d['address'],'lon':d['lon'],'lat':d['lat']},headers={**H,'Prefer':'resolution=merge-duplicates,return=minimal'},timeout=30)
        n+=1; log.info(f"  {x['owner_display'][:28]:28} → {d['parcel']} {str(d['address'])[:26]:26} ${d['value'] or 0:,}")
        time.sleep(0.8)
    requests.post(f"{SB}/rest/v1/rpc/pp_finish_parcel_geo",json={},headers=H,timeout=180)
    log.info(f'resolved {n}/{len(todo)}'); return n
if __name__=='__main__': main(int(os.environ.get('SL_LIMIT','60')))
