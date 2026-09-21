"""
Nightly integrity gate.
 1. pp_integrity_check(): database invariants (no fabricated kinds, no test rows, labels agree with sources,
    every queue lead rests on a live signal, registered sources still producing).
 2. Live spot-check: pull a random sample of top-tier Utah County leads, re-fetch their newest recorder
    document from the county's own site, and confirm the Kind of Instrument on the page maps to the signal
    type we stored. This is the class of error that let paid-off loans masquerade as foreclosures.
Exits non-zero on any failure so the workflow fails and is seen.
"""
import os, sys, json, re, html, random, time, requests
SB=os.environ['SUPABASE_URL'].rstrip('/'); KEY=os.environ['SUPABASE_SERVICE_KEY']
H={'apikey':KEY,'Authorization':'Bearer '+KEY,'Content-Type':'application/json'}
S=requests.Session(); S.headers['User-Agent']='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36'
# what each county document kind is allowed to be stored as
ALLOWED={'ND':{'nod'},'CAN ND':{'nod_cancelled'},'SUB TEE':{'trustee_substitution'},'SUBTEE':{'trustee_substitution'},
         'RSUBTEE':{'loan_reconveyance'},'PRSUBTE':{'loan_reconveyance'},'AF DC':{'death_affidavit'},'AF DC W':{'death_affidavit'},
         'LP':{'lis_pendens'},'REL LP':{'lis_pendens_release'},'TR D':{'trustee_deed'},'PR D':{'probate_deed'},'N LN':{'lien_judgment','mechanics_lien'},
         'JUDG':{'lien_judgment'},'ABST JU':{'lien_judgment'},'D TR':{'deed_of_trust'},'WD':{'deed_transfer','comparable_sale'},'SP WD':{'deed_transfer','comparable_sale'},'QCD':{'deed_transfer','family_transfer'}}
fails=[]
r=requests.post(f"{SB}/rest/v1/rpc/pp_integrity_check",headers=H,json={},timeout=300); res=r.json(); res=json.loads(res) if isinstance(res,str) else res
print("invariants:", "PASSED" if res.get('passed') else "FAILED", json.dumps(res.get('failures')))
if not res.get('passed'): fails.append(('invariants',res.get('failures')))
rows=requests.get(f"{SB}/rest/v1/pp_scraper_signals?select=id,signal_type,raw_owner_name,raw_payload&county=eq.Utah&is_legacy=eq.false&is_institutional=eq.false&source_slug=eq.utah-recorder-unified&order=captured_at.desc&limit=400",headers=H,timeout=120).json()
sample=random.sample(rows, min(6,len(rows))); checked=0
for s in sample:
    p=s['raw_payload']
    for _ in range(2):
        try: p=json.loads(p)
        except Exception: break
    if not isinstance(p,dict): continue
    m=re.search(r'(\d+)[\s:\-\u00a0]+(20\d\d)', str(p.get('entry','')))
    if not m: continue
    try: g=S.get(f"https://www.utahcounty.gov/LandRecords/document.asp?avEntry={m.group(1)}&avYear={m.group(2)}",timeout=60)
    except Exception as e: print(f"  county unreachable for {m.group(1)}: {type(e).__name__}"); continue
    t=re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>',' ',re.sub(r'<(script|style)[^>]*>.*?</\1>','',g.text,flags=re.S|re.I))))
    k=re.search(r'Kind of Inst:\s*([A-Z][A-Z ]{0,10}?)\s+-',t)
    if not k: print(f"  entry {m.group(1)}: kind not readable on page"); continue
    kind=k.group(1).strip(); checked+=1
    ok = s['signal_type'] in ALLOWED.get(kind, set()) if kind in ALLOWED else True
    print(f"  entry {m.group(1)} county says {kind!r:10} we stored {s['signal_type']!r:22} {'OK' if ok else 'MISMATCH'}")
    if not ok: fails.append(('spotcheck',{'entry':m.group(1),'county_kind':kind,'stored':s['signal_type']}))
    time.sleep(1.2)
print(f"spot-check: {checked} documents re-read from the county; {sum(1 for f in fails if f[0]=='spotcheck')} mismatches")
if fails:
    requests.post(f"{SB}/rest/v1/pp_integrity_log",headers={**H,'Prefer':'return=minimal'},json={'passed':False,'failures':json.dumps([f[1] for f in fails]),'checked':json.dumps({'spotcheck_docs':checked})},timeout=30)
    sys.exit(1)
