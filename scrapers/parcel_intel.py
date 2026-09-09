"""
Parcel intelligence — assessed value + mailing address from the Utah County assessor.

Why: Utah is a non-disclosure state, so sale prices are private and most tools
cannot compute equity at all. But the ASSESSOR publishes market value, and the
tax-notice mailing address. Together they give (a) measured equity against the
recorded loan and (b) TRUE absentee detection — mailing address on a different
street or city than the property. Verified live Sept 2026:
  Property.asp?av_serial=020660013 → Market Value 2026 … $477,600 · Property
  Address 105 E 500 NORTH AMERICAN FORK · Mailing 10284 N CARRIAGE LN CEDAR HILLS.

One request per parcel; throttled; runs as a background job, never in the
daily pipeline.
"""
import os, re, json, time, logging, html, requests
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s'); log=logging.getLogger('parcel')
SB=os.environ['SUPABASE_URL'].rstrip('/'); KEY=os.environ['SUPABASE_SERVICE_KEY']
H={'apikey':KEY,'Authorization':'Bearer '+KEY,'Content-Type':'application/json'}
S=requests.Session(); S.headers['User-Agent']='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36'

def norm_street(a):
    a=(a or '').upper()
    a=re.sub(r'\b(NORTH|SOUTH|EAST|WEST)\b',lambda m:m.group(1)[0],a)
    a=re.sub(r'[^A-Z0-9 ]',' ',a); a=re.sub(r'\s+',' ',a).strip()
    return a

def fetch(serial):
    for i in range(3):
        try:
            r=S.get("https://www.utahcounty.gov/LandRecords/Property.asp",params={'av_serial':serial.replace(':','')},timeout=45)
            if r.status_code==200: break
            log.warning(f'{serial} HTTP {r.status_code}')   # county 500s intermittently — retry
        except Exception as e: log.warning(f'{serial} {type(e).__name__}')
        time.sleep(4)
    else: return None
    t=re.sub(r'<(script|style)[^>]*>.*?</\1>','',r.text,flags=re.S|re.I); t=re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>',' ',t)))
    mv=re.search(r'Market Value\s+(\d{4})\s+((?:\$[\d,]+\s+){4,})',t)
    vals=re.findall(r'\$([\d,]+)',mv.group(2)) if mv else []
    total=int(vals[-1].replace(',','')) if vals else None
    pa=re.search(r'Property Address:\s*(.*?)\s*Mailing Address:',t); ma=re.search(r'Mailing Address:\s*(.*?)\s*Acreage:',t)
    prop=(pa.group(1).strip() if pa else ''); mail=(ma.group(1).strip() if ma else '')
    # absentee: the mailing street differs from the property street. A property
    # address that is only a city name (vacant land / sliver) cannot be judged.
    ps=norm_street(re.sub(r'\s*-\s*[A-Z ]+$','',prop)); ms=norm_street(re.split(r',',mail)[0])
    absentee=None
    if ps and ms and re.search(r'\d',ps):
        absentee = ps[:12] != ms[:12]
    return {'parcel_serial':serial,'county':'Utah','market_value':total,'value_year':int(mv.group(1)) if mv else None,
            'property_address':prop or None,'mailing_address':mail or None,'is_absentee':absentee,'fetched_at':'now()'}

def index_name(raw):
    """Assessor name search wants 'LAST, FIRST' — strip suffixes and co-owners."""
    if not raw: return None
    n=raw.upper(); n=re.sub(r'\([^)]*\)',' ',n)
    n=re.sub(r'\b(TEE|TRUSTEE|SUCTEE|PERREP|PER REP|DEC|ESTATE OF|ET AL|ETAL|JR|SR|II|III)\b',' ',n)
    n=n.split('&')[0]; n=re.sub(r'[^A-Z, ]',' ',n); n=re.sub(r'\s+',' ',n).strip().strip(',').strip()
    if ',' in n:
        sur,_,rest=n.partition(','); n=f"{sur.strip()}, {rest.strip()}".strip().strip(',')
    return n or None

def norm_cmp(s):
    return re.sub(r'[^A-Z]','',(s or '').upper())

def resolve_parcels_by_name(limit):
    """For high-conviction Utah owners with NO parcel on any signal, find their parcels
    via the assessor owner-name search. EXACT-match only on the normalised name — an
    under-match is safe, a mis-match puts an agent at a stranger's house."""
    q=(f"{SB}/rest/v1/pp_entity_conviction?select=entity_key,owner_display&contactable=is.true&resolved_flag=is.false"
       f"&county=eq.Utah&order=conviction_score.desc&limit=400")
    owners=requests.get(q,headers=H,timeout=30).json()
    have=set(x['owner_key'] for x in requests.get(f"{SB}/rest/v1/pp_parcel_intel?select=owner_key&owner_key=not.is.null",headers=H,timeout=30).json())
    # owners whose signals already carry a parcel are handled by the parcel loop
    with_parcel=set()
    for i in range(0,len(owners),80):
        ks=','.join('"'+o['entity_key'].replace('"','')+'"' for o in owners[i:i+80])
        for r in requests.get(f"{SB}/rest/v1/pp_scraper_signals?select=owner_key&is_legacy=eq.false&parcel_serial=not.is.null&owner_key=in.({requests.utils.quote(ks)})",headers=H,timeout=30).json():
            with_parcel.add(r['owner_key'])
    todo=[o for o in owners if o['entity_key'] not in have and o['entity_key'] not in with_parcel][:limit]
    log.info(f'{len(todo)} owners to resolve by name')
    n=0
    for o in todo:
        nm=index_name(o['owner_display'])
        if not nm or ',' not in nm: continue
        try:
            r=S.get("https://www.utahcounty.gov/LandRecords/NameSearch.asp",params={'av_name':nm,'av_valid':'...','Submit':' Search '},timeout=45)
        except Exception as e: log.warning(f'  {nm[:30]} {type(e).__name__}'); time.sleep(3); continue
        time.sleep(1.4)
        hits=[]
        for m in re.finditer(r'<tr[^>]*>(.*?)</tr>',r.text,re.S|re.I):
            c=[re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>','',x))).strip() for x in re.findall(r'<td[^>]*>(.*?)</td>',m.group(1),re.S|re.I)]
            if len(c)>=2 and re.match(r'^\d{2}:\d{3}:\d{4}$',c[1]) and norm_cmp(index_name(c[0]))==norm_cmp(nm):
                hits.append(c[1])
        if not hits: log.info(f'  {nm[:34]:36} no exact parcel match'); continue
        for serial in sorted(set(hits))[:4]:
            d=fetch(serial); time.sleep(1.4)
            if not d or d['market_value'] is None: continue
            d.pop('fetched_at'); d['owner_key']=o['entity_key']
            w=requests.post(f"{SB}/rest/v1/pp_parcel_intel?on_conflict=parcel_serial",json=d,headers={**H,'Prefer':'resolution=merge-duplicates,return=minimal'},timeout=30)
            if w.status_code<400: n+=1; log.info(f"  {nm[:30]:32} {serial} ${d['market_value']:,} {'ABSENTEE' if d['is_absentee'] else ''}")
    log.info(f'resolved {n} parcels by name'); return n

def obit_to_index(raw):
    """'Roger Orvel Ladle' → 'LADLE, ROGER ORVEL'. Strips memorial prefixes and suffixes."""
    n=re.sub(r'^\s*(IN LOVING MEMORY( OF)?|IN MEMORY OF|OBITUARY[:\-]?)\s*','',(raw or '').upper()).strip()
    n=re.sub(r'\b(JR|SR|II|III|IV)\b\.?','',n); n=re.sub(r'[^A-Z ]',' ',n); n=re.sub(r'\s+',' ',n).strip()
    parts=n.split(' ')
    if len(parts)<2: return None
    return f"{parts[-1]}, {' '.join(parts[:-1])}"

def obit_match(query, cand):
    """Surname + first name exact; if both carry a middle, initials must agree."""
    def split(s):
        sur,_,rest=s.partition(','); toks=rest.strip().split()
        return sur.strip(), (toks[0] if toks else ''), (toks[1][:1] if len(toks)>1 else '')
    qs,qf,qm=split(query); cs,cf,cm=split(index_name(cand) or '')
    return qs==cs and qf==cf and qf!='' and (not qm or not cm or qm==cm)

def resolve_obituaries(limit):
    """A deceased person confirmed as a property owner is an estate — one of the
    strongest sell signals there is. Obituary rows sit at score 20 until matched."""
    rows=requests.get(f"{SB}/rest/v1/pp_scraper_signals?select=id,raw_owner_name,owner_key&signal_type=eq.obituary&is_legacy=eq.false&score=lte.20&order=captured_at.desc&limit={limit}",headers=H,timeout=30).json()
    log.info(f'{len(rows)} unmatched obituaries')
    n=0
    for r in rows:
        q=obit_to_index(r['raw_owner_name'])
        if not q: continue
        try: s=S.get("https://www.utahcounty.gov/LandRecords/NameSearch.asp",params={'av_name':q,'av_valid':'...','Submit':' Search '},timeout=45)
        except Exception as e: log.warning(f'  {q[:30]} {type(e).__name__}'); time.sleep(3); continue
        time.sleep(1.4)
        hits=[]
        for m in re.finditer(r'<tr[^>]*>(.*?)</tr>',s.text,re.S|re.I):
            c=[re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>','',x))).strip() for x in re.findall(r'<td[^>]*>(.*?)</td>',m.group(1),re.S|re.I)]
            if len(c)>=2 and re.match(r'^\d{2}:\d{3}:\d{4}$',c[1]) and obit_match(q,c[0]): hits.append((c[0],c[1]))
        if not hits: log.info(f'  {q[:34]:36} no owner match'); continue
        # promote the obituary: this person owned property → estate signal
        serial=hits[0][1]
        w=requests.patch(f"{SB}/rest/v1/pp_scraper_signals?id=eq.{r['id']}",json={'signal_type':'deceased_owner','score':85,'parcel_serial':serial,
              'raw_address':f'Deceased owner — parcel {serial}'},headers={**H,'Prefer':'return=minimal'},timeout=30)
        if w.status_code>=400: log.error(f'  {q[:30]} promote {w.status_code}'); continue
        for name,ser in hits[:4]:
            d=fetch(ser); time.sleep(1.4)
            if not d or d['market_value'] is None: continue
            d.pop('fetched_at'); d['owner_key']=r['owner_key']
            requests.post(f"{SB}/rest/v1/pp_parcel_intel?on_conflict=parcel_serial",json=d,headers={**H,'Prefer':'resolution=merge-duplicates,return=minimal'},timeout=30)
        n+=1; log.info(f"  {q[:34]:36} ESTATE — {len(hits)} parcel(s), {hits[0][1]} ({hits[0][0][:26]})")
    log.info(f'promoted {n} obituaries to deceased_owner'); return n

def main(limit=60):
    resolve_obituaries(int(os.environ.get('OBIT_LIMIT','40')))
    resolve_parcels_by_name(int(os.environ.get('NAME_LIMIT','40')))
    # Parcels belonging to the HIGHEST-CONVICTION real owners first. Ordering by
    # raw signal score pulled a developer's row of identical lots to the front;
    # order by the owner's conviction and skip institutional rows instead.
    q=(f"{SB}/rest/v1/pp_entity_conviction?select=entity_key&contactable=is.true&resolved_flag=is.false"
       f"&county=eq.Utah&order=conviction_score.desc&limit=300")
    keys=[r['entity_key'] for r in requests.get(q,headers=H,timeout=30).json()]
    rows=[]
    for i in range(0,len(keys),60):
        ks=','.join('"'+k.replace('"','')+'"' for k in keys[i:i+60])
        rows+=requests.get(f"{SB}/rest/v1/pp_scraper_signals?select=parcel_serial&is_legacy=eq.false&is_institutional=eq.false"
                           f"&parcel_serial=not.is.null&owner_key=in.({requests.utils.quote(ks)})",headers=H,timeout=30).json()
    serials=[]; seen=set()
    for r in rows:
        s=r['parcel_serial']
        if s and s not in seen and re.match(r'^\d{2}:\d{3}:\d{4}$',s): seen.add(s); serials.append(s)
    have=set(x['parcel_serial'] for x in requests.get(f"{SB}/rest/v1/pp_parcel_intel?select=parcel_serial&fetched_at=gte."+time.strftime('%Y-%m-%d',time.gmtime(time.time()-120*86400)),headers=H,timeout=30).json())
    todo=[s for s in serials if s not in have][:limit]
    log.info(f'{len(todo)} parcels to fetch')
    n=0
    for s in todo:
        d=fetch(s); time.sleep(1.4)
        if not d or d['market_value'] is None: log.info(f'  {s} no value'); continue
        d.pop('fetched_at')
        w=requests.post(f"{SB}/rest/v1/pp_parcel_intel?on_conflict=parcel_serial",json=d,headers={**H,'Prefer':'resolution=merge-duplicates,return=minimal'},timeout=30)
        if w.status_code<400: n+=1; log.info(f"  {s} ${d['market_value']:,} {'ABSENTEE' if d['is_absentee'] else ''} {d['property_address'] or ''}"[:90])
        else: log.error(f'  {s} write {w.status_code} {w.text[:100]}')
    log.info(f'enriched {n} parcels'); return n

if __name__=='__main__': main(int(os.environ.get('PARCEL_LIMIT','60')))
