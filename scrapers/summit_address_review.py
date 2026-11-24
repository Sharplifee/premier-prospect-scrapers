"""
Read-only review (Nov 24 2026): Summit leads whose confirmed mailing address comes from a parcel that the lead's own
filing names, but whose parcel row belongs to a different lead. For each, read the parcel's CURRENT owner from the
county's public layer (ParcelQuery OwnershipTable) and decide whether the lead is still a person on title.

A person matches when the county owner string holds the same surname AND a first name that starts with the same three
letters (tolerates CHRISTOHER/CHRISTOPHER, ELIZABTH/ELIZABETH, STEVE/STEVEN). Trusts named for the person match too.
Prints one JSON line per lead: keep / wrong / unknown, with the county owner it was judged against. Writes nothing.
"""
import os, re, json, time, requests

SB = os.environ['SUPABASE_URL'].rstrip('/'); KEY = os.environ['SUPABASE_SERVICE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}', 'Content-Type': 'application/json'}
SUMMIT = 'https://maps.summitcounty.org/arcgis/rest/services/Maps/ParcelQuery/MapServer/0/query'
NOISE = {'TRUSTEE', 'TR', 'TRS', 'SUC', 'JT', 'DECEASED', 'JR', 'SR', 'II', 'III', 'IV', 'ET', 'AL', 'THE', 'OF', 'AND',
         'H/W', 'W/H', 'LIVING', 'FAMILY', 'REVOCABLE', 'TRUST'}


def people(name):
    """[(surname, first)] from 'LAST FIRST M, LAST2 FIRST2 (DECEASED)'."""
    out = []
    for part in re.split(r'\s*,\s*', re.sub(r'\([^)]*\)', ' ', (name or '').upper())):
        t = [w for w in re.findall(r"[A-Z][A-Z'\-]*", part) if w not in NOISE]
        if len(t) >= 2: out.append((t[0], t[1]))
    return out


def owner_has(owner, persons):
    o = ' ' + re.sub(r'[^A-Z\- ]', ' ', (owner or '').upper()) + ' '
    words = o.split()
    for sur, first in persons:
        if f' {sur} ' not in o: continue
        i = [k for k, w in enumerate(words) if w == sur]
        for k in i:
            nxt = words[k + 1:k + 4]
            if any(w[:3] == first[:3] for w in nxt): return True
    return False


def county_owners(serials):
    out = {}
    for i in range(0, len(serials), 50):
        ids = ','.join("'" + s.replace("'", "''") + "'" for s in serials[i:i + 50])
        for attempt in range(3):
            try:
                j = requests.get(SUMMIT, params={'where': f'OwnershipTable_SERIAL IN ({ids})', 'outFields': 'OwnershipTable_SERIAL,OwnershipTable_OWNER',
                                                 'returnGeometry': 'false', 'f': 'json'}, timeout=60).json()
                for f in j.get('features', []):
                    a = f['attributes']; out.setdefault(a['OwnershipTable_SERIAL'], []).append(a.get('OwnershipTable_OWNER') or '')
                break
            except Exception as e:
                time.sleep(5 * (attempt + 1))
        time.sleep(1)
    return out


def main():
    rows = requests.post(f'{SB}/rest/v1/rpc/pp_summit_address_candidates', headers=H, json={}, timeout=120).json()
    serials = sorted({r['serial'] for r in rows})
    own = county_owners(serials)
    by_lead = {}
    for r in rows: by_lead.setdefault(r['k'], {'name': r['name'], 'mail': r['mail'], 'score': r['score'], 'serials': set()})['serials'].add(r['serial'])
    counts = {'keep': 0, 'wrong': 0, 'unknown': 0}
    for k, v in sorted(by_lead.items()):
        ps = people(v['name'])
        owners = [o for s in v['serials'] for o in own.get(s, [])]
        if not owners: verdict = 'unknown'
        elif any(owner_has(o, ps) for o in owners): verdict = 'keep'
        else: verdict = 'wrong'
        counts[verdict] += 1
        print(json.dumps({'verdict': verdict, 'k': k, 'name': v['name'], 'score': v['score'], 'mail': v['mail'], 'serials': sorted(v['serials']), 'county_owner': owners}))
    print('SUMMARY', json.dumps(counts))


if __name__ == '__main__':
    main()
