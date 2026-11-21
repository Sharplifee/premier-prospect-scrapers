"""
Parcel geometry — city, address, zip and a map point for every parcel the system
knows, from the state's per-county parcel layers (UGRC). Utah IDs are stored
without colons there; Summit and Wasatch use the recorder's own format. Summit's
layer has no city field, so city comes from point-in-polygon against
pp_municipalities in Postgres afterwards. Batched IN() queries, 10 per call.
"""
import os, re, json, time, logging, requests
try:
    import eventlog as _eventlog; _eventlog.install(job=__file__)
except Exception as _e:  # event log must never block a worker
    pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s'); log=logging.getLogger('geo')
SB=os.environ['SUPABASE_URL'].rstrip('/'); KEY=os.environ['SUPABASE_SERVICE_KEY']
H={'apikey':KEY,'Authorization':'Bearer '+KEY,'Content-Type':'application/json'}
UG="https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/{layer}/FeatureServer/0/query"
LAYERS={'Utah':'Parcels_Utah','Salt Lake':'Parcels_SaltLake','Summit':'Parcels_Summit','Wasatch':'Parcels_Wasatch'}
def layer_id(county, serial):
    return serial.replace(':','') if county=='Utah' else serial
def layer_query(lay, county, batch):
    """Nov 21 2026: the state layer answered 504 five times today and each failed batch of 60 parcels was skipped.
    Retry with backoff (5s, 15s, 45s); if a batch still fails, split it in half once and try the halves."""
    def one(b):
        ids=",".join("'"+layer_id(county,s).replace("'","''")+"'" for s in b)
        for wait in (0, 5, 15, 45):
            if wait: time.sleep(wait)
            try:
                resp=requests.get(UG.format(layer=lay),params={'where':f"PARCEL_ID IN ({ids})",'outFields':'PARCEL_ID,PARCEL_ADD,PARCEL_CITY,PARCEL_ZIP','returnGeometry':'false','outSR':'4326','returnCentroid':'true','f':'json'},timeout=90)
                if resp.status_code==200:
                    j=resp.json()
                    if 'error' not in j: return j
                log.warning(f'{county} state layer HTTP {resp.status_code} — retrying')
            except Exception as e: log.warning(f'{county} state layer {type(e).__name__} — retrying')
        return None
    r=one(batch)
    if r is None and len(batch)>1:
        h=len(batch)//2; a=one(batch[:h]); b=one(batch[h:])
        if a is None and b is None: return None
        r={'features':(a or {}).get('features',[])+(b or {}).get('features',[])}
    return r
def main(limit=1500):
    # every parcel attached to a live signal or to parcel_intel, not yet geocoded
    have=set(x['parcel_serial'] for x in requests.get(f"{SB}/rest/v1/pp_parcel_geo?select=parcel_serial",headers=H,timeout=60).json())
    want={}
    # queue parcels first (what the app shows), then buyer parcels, then the rest — per county
    for county in LAYERS:
        ce=requests.utils.quote(county)
        for path in [f"pp_conviction_queue?select=property_ref,county&county=eq.{ce}&property_ref=not.is.null&limit=3000",
                     f"pp_parcel_intel?select=parcel_serial,county&county=eq.{ce}&limit=3000",
                     f"pp_scraper_signals?select=parcel_serial,county&county=eq.{ce}&is_legacy=eq.false&parcel_serial=not.is.null&signal_type=neq.tax_delinquency&limit=5000"]:
            for r in requests.get(f"{SB}/rest/v1/{path}",headers=H,timeout=120).json():
                s=r.get('parcel_serial') or r.get('property_ref')
                if s and s not in have and re.match(r'^[A-Z0-9:\-]{5,20}$',s): want.setdefault(county,set()).add(s)
    todo=sum(len(v) for v in want.values()); log.info(f'{todo} parcels to geocode across {len(want)} counties')
    n=0; missed=0
    for county,serials in want.items():
        serials=sorted(serials)[:limit]; lay=LAYERS[county]
        # Nov 21 2026: the state layer now spends ~50s computing centroids for 30 parcels and rejects 60 ("Invalid query
        # parameters" after ~55s, or 504); 10 per call returns in under a second.
        for i in range(0,len(serials),10):
            batch=serials[i:i+10]
            r=layer_query(lay, county, batch)
            if r is None: missed+=len(batch); log.warning(f'{county} batch {i}: state layer failed after retries ({len(batch)} parcels left for the next run)'); continue
            back={}
            for f in r.get('features',[]):
                a=f['attributes']; c=f.get('centroid') or {}
                if not c:   # fall back to first ring vertex
                    rings=f.get('geometry',{}).get('rings',[[]]); c={'x':rings[0][0][0],'y':rings[0][0][1]} if rings and rings[0] else {}
                back[a['PARCEL_ID']]=(a.get('PARCEL_ADD'),a.get('PARCEL_CITY'),a.get('PARCEL_ZIP'),c.get('x'),c.get('y'))
            rows=[]
            for s in batch:
                v=back.get(layer_id(county,s))
                if not v or v[3] is None: continue
                rows.append({'parcel_serial':s,'county':county,'city':(v[1] or None),'address':(v[0] or None),'zip':(str(v[2]) if v[2] else None),'lon':v[3],'lat':v[4]})
            if rows:
                w=requests.post(f"{SB}/rest/v1/pp_parcel_geo?on_conflict=parcel_serial",json=rows,headers={**H,'Prefer':'resolution=merge-duplicates,return=minimal'},timeout=60)
                if w.status_code<400: n+=len(rows)
                else: log.error(f'write {w.status_code} {w.text[:100]}')
            time.sleep(0.4)
        log.info(f'  {county}: done')
    # geometry column + city by point-in-polygon where the layer had none
    requests.post(f"{SB}/rest/v1/rpc/pp_finish_parcel_geo",json={},headers=H,timeout=180)
    log.info(f'geocoded {n} parcels; {missed} left for the next run after retries'); return n
if __name__=='__main__': main(int(os.environ.get('GEO_LIMIT','1500')))
