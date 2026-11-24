"""
Nov 24 2026: read the CURRENT owner names for every Utah parcel that gives a lead its address (Utah County Property.asp,
free) into pp_parcel_intel.current_owners. Highest-scoring leads first; parcels read in the last READ_DAYS days are
skipped, so repeated runs work through the rest. Writes only current_owners and owners_read_at. Time budget OWNER_MINUTES.
"""
import os, time, logging, requests
import parcel_intel as P
log = logging.getLogger('parcel')

def page(path):
    out, off = [], 0
    while True:
        pg = requests.get(f"{P.SB}/rest/v1/{path}&limit=1000&offset={off}", headers=P.H, timeout=90).json()
        if not isinstance(pg, list) or not pg: break
        out += pg; off += len(pg)
        if len(pg) < 1000: break
    return out

def main():
    budget = time.time() + 60 * float(os.environ.get('OWNER_MINUTES', '90'))
    cut = time.strftime('%Y-%m-%d', time.gmtime(time.time() - int(os.environ.get('READ_DAYS', '30')) * 86400))
    rows = page("rpc/pp_utah_mail_parcels?select=parcel_serial,score&order=score.desc,parcel_serial")
    fresh = {r['parcel_serial'] for r in page(f"pp_parcel_intel?select=parcel_serial&county=eq.Utah&owners_read_at=gte.{cut}&order=parcel_serial")}
    todo, seen = [], set()
    for r in rows:
        s = r['parcel_serial']
        if s not in seen and s not in fresh: seen.add(s); todo.append(s)
    log.info(f'{len(rows)} lead parcels; {len(todo)} to read')
    n = err = 0
    for s in todo:
        if time.time() > budget: log.info(f'time budget used after {n} parcels; the rest go first next run'); break
        d = P.fetch(s); time.sleep(1.2)
        if not d: err += 1; continue
        w = requests.patch(f"{P.SB}/rest/v1/pp_parcel_intel?parcel_serial=eq.{s}", headers={**P.H, 'Prefer': 'return=minimal'},
                           json={'current_owners': d['current_owners'], 'owners_read_at': 'now()'}, timeout=30)
        if w.status_code < 400: n += 1
        else: err += 1; log.error(f'{s} write {w.status_code} {w.text[:80]}')
    log.info(f'read current owners for {n} parcels ({err} errors)')

if __name__ == '__main__':
    main()
