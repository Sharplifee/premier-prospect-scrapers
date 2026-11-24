"""
Read-only review (Nov 24 2026): Utah divorce filings promoted to divorce_homeowner on ONE spouse's name before the
stricter rule existed. Re-searches each spouse on the free Utah County owner-name search and reads the attached parcel's
CURRENT owners (Property.asp). The parcel stands when a spouse is on today's title and either both spouses show on the
parcel in the name search or the matching spouse is one person on the county roll (one owner name). Covers every
divorce_homeowner filing, marital homes included (a marital home can have been sold since). Prints one JSON line per filing: keep / wrong / unknown. Writes nothing.
"""
import os, re, json, time, html, requests
import parcel_intel as P

def search(q):
    for i in range(3):
        try:
            r = P.S.get("https://www.utahcounty.gov/LandRecords/NameSearch.asp", params={'av_name': q, 'av_valid': '...', 'Submit': ' Search '}, timeout=45)
            if r.status_code == 200: return r.text
        except Exception: pass
        time.sleep(4)
    return None

def main():
    rows = []; off = 0
    while True:
        pg = requests.get(f"{P.SB}/rest/v1/pp_scraper_signals?select=id,owner_key,parcel_serial,raw_payload&signal_type=eq.divorce_homeowner&county=eq.Utah"
                          f"&is_legacy=eq.false&order=id&limit=1000&offset={off}", headers=P.H, timeout=60).json()
        rows += pg; off += len(pg)
        if len(pg) < 1000: break
    counts = {'keep': 0, 'wrong': 0, 'unknown': 0}
    for r in rows:
        pl = r['raw_payload']; pl = json.loads(pl) if isinstance(pl, str) else pl
        if isinstance(pl, str): pl = json.loads(pl)
        parties = re.split(r'\s+(?:and|vs\.?)\s+', pl.get('parties', ''), flags=re.I)
        spouses = [s for s in (P.obit_to_index(re.sub(r'\s+et al\.?$', '', p, flags=re.I)) for p in parties[:2]) if s]
        hits, owners, failed = {}, {}, False
        for si, q in enumerate(spouses):
            t = search(q); time.sleep(1.3)
            if t is None: failed = True; continue
            for m in re.finditer(r'<tr[^>]*>(.*?)</tr>', t, re.S | re.I):
                c = [re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', x))).strip() for x in re.findall(r'<td[^>]*>(.*?)</td>', m.group(1), re.S | re.I)]
                if len(c) >= 2 and re.match(r'^\d{2}:\d{3}:\d{4}$', c[1]) and P.obit_match(q, c[0]):
                    hits.setdefault(c[1], set()).add(si); owners.setdefault(si, set()).add(P.norm_cmp(P.index_name(c[0])))
        sp = hits.get(r['parcel_serial'], set())
        d = P.fetch(r['parcel_serial']); time.sleep(1.3)
        cur = (d or {}).get('current_owners')
        if failed or not d: v = 'unknown'
        elif not any(P.on_title(x, cur) for x in spouses): v = 'wrong'          # no spouse on today's title
        elif sum(P.on_title(x, cur) for x in spouses) >= 2: v = 'keep'           # both spouses on today's title
        elif len(sp) >= 2 or any(len(owners.get(i, ())) == 1 for i in sp): v = 'keep'
        else: v = 'wrong'                                                         # name shared by several people on the roll
        counts[v] += 1
        print(json.dumps({'verdict': v, 'id': r['id'], 'k': r['owner_key'], 'serial': r['parcel_serial'], 'spouses': spouses,
                          'owners': {str(k): sorted(x) for k, x in owners.items()}, 'parcels': {k: sorted(x) for k, x in hits.items()}, 'current_owners': cur, 'household': pl.get('household')}))
    print('SUMMARY', json.dumps(counts))

if __name__ == '__main__':
    main()
