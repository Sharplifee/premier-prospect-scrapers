"""
Parcel geometry — city, address, zip and a map point for every parcel the system
knows, from the state's per-county parcel layers (UGRC). Utah IDs are stored
without colons there; Summit and Wasatch use the recorder's own format. Summit's
layer has no city field, so city comes from point-in-polygon against
pp_municipalities in Postgres afterwards. Batched IN() queries, ~60 per call.
"""
import os, re, json, time, logging, requests
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s'); log=logging.getLogger('geo')
SB=os.environ['SUPABASE_URL'].rstrip('/'); KEY=os.environ['SUPABASE_SERVICE_KEY']
H={'apikey':KEY,'Authorization':'Bearer '+KEY,'Content-Type':'application/json'}
UG="https://services1.arcgis.com/99lidPhWCzftIe9K/ArcGIS/rest/services/{layer}/FeatureServer/0/query"
LAYERS={'Utah':'Parcels_Utah','Salt Lake':'Parcels_SaltLake','Summit':'Parcels_Summit','Wasatch':'Parcels_Wasatch'}
def layer_id(county, serial):
    return serial.replace(':','') if county=='Utah' else serial
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
    n=0
    for county,serials in want.items():
        serials=sorted(serials)[:limit]; lay=LAYERS[county]
        for i in range(0,len(serials),60):
            batch=serials[i:i+60]; ids=",".join("'"+layer_id(county,s).replace("'","''")+"'" for s in batch)
            try:
                r=requests.get(UG.format(layer=lay),params={'where':f"PARCEL_ID IN ({ids})",'outFields':'PARCEL_ID,PARCEL_ADD,PARCEL_CITY,PARCEL_ZIP','returnGeometry':'false','outSR':'4326','returnCentroid':'true','f':'json'},timeout=90).json()
            except Exception as e: log.warning(f'{county} batch {i}: {type(e).__name__}'); continue
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
    log.info(f'geocoded {n} parcels'); return n
if __name__=='__main__': main(int(os.environ.get('GEO_LIMIT','1500')))
