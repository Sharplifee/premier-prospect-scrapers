"""
Utah County recorder history backfill (Oct 13 2026, buyer depth).

Why: utah-recorder-unified reads the newest ~800 documents (4 pages of 200) each morning, which keeps
the feed current but gives the buyer side only the history it has been running for (first WD mid-July).
Every deed-derived buyer layer (repeat buyers, flippers, 1031 window, taken-off-title) and the buyer
backtest get stronger with each extra month of warranty deeds. Verified live Oct 13: the DocDesc filter
is ignored server-side, pages are 200 rows, and `offset` pages back through the date range.

This walks the SAME endpoint and the SAME parser as the daily sweep, a bounded number of pages per run,
and remembers where it stopped in pp_backfill_state so each nightly run resumes deeper. It stops by
itself once a page comes back empty (the range is exhausted). Nothing is guessed: rows go through
post_batch and the same dedupe_hash, so re-reading a page already captured inserts nothing.

Env: BACKFILL_PAGES (default 25), BACKFILL_RANGE days (default 180), BACKFILL_SECONDS cap (default 780).
"""
import os, re, sys, json, time, logging, requests
sys.path.insert(0, os.path.dirname(__file__))
import run_all as R
from bs4 import BeautifulSoup

log = logging.getLogger('backfill'); logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
SB = os.environ['SUPABASE_URL'].rstrip('/'); KEY = os.environ['SUPABASE_SERVICE_KEY']
H = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY, 'Content-Type': 'application/json'}
SLUG = 'utah-recorder-unified'   # same slug: these are the same documents, just older
STATE_KEY = 'utah-recorder-backfill'

def get_state():
    r = requests.get(f"{SB}/rest/v1/pp_backfill_state?key=eq.{STATE_KEY}&select=offset_next,done", headers=H, timeout=30).json()
    return (r[0]['offset_next'], r[0]['done']) if r else (800, False)

def set_state(offset_next, done, pages, inserted):
    requests.post(f"{SB}/rest/v1/pp_backfill_state?on_conflict=key", headers={**H, 'Prefer': 'resolution=merge-duplicates,return=minimal'},
                  json={'key': STATE_KEY, 'offset_next': offset_next, 'done': done, 'last_pages': pages, 'last_inserted': inserted, 'updated_at': 'now()'}, timeout=30)

def parse_page(text):
    soup = BeautifulSoup(text, 'html.parser'); out, dates = [], []
    for row in soup.select('tr'):
        cells = row.select('td')
        if len(cells) < 6: continue
        rec_dt = cells[1].get_text(strip=True); koi = cells[2].get_text(strip=True)
        entry = cells[3].get_text(' ', strip=True).replace('\xa0', ' ')
        grantor = cells[4].get_text(strip=True); grantee = cells[5].get_text(strip=True)
        if not re.match(r'^\d+/\d+/\d{4}', rec_dt): continue
        dates.append(rec_dt)
        if koi not in R.RECORDER_KOI_MAP or not entry: continue
        sig_type, score, label = R.RECORDER_KOI_MAP[koi]
        party = grantee if sig_type in ('nod', 'nod_cancelled') else grantor
        extra = {}
        if sig_type == 'death_affidavit' and grantee and not re.search(r'WHOM OF INTEREST|LLC|INC\b|BANK|MORTGAGE|CORP', grantee, re.I):
            party, extra = grantee, {'decedent': grantor, 'party_role': 'successor'}
        out.append({'source_slug': SLUG, 'signal_type': sig_type, 'score': score, 'county': 'Utah', 'city': None,
                    'raw_owner_name': R.clean_owner(party) if party else None, 'raw_address': f'Entry #{entry}',
                    'raw_payload': json.dumps({'koi': koi, 'label': label, 'entry': entry, 'rec_date': rec_dt,
                                               'grantor': grantor, 'grantee': grantee, **extra})})
    return out, dates

def main():
    pages = int(os.environ.get('BACKFILL_PAGES', '25')); rng = os.environ.get('BACKFILL_RANGE', '180')
    budget = int(os.environ.get('BACKFILL_SECONDS', '780')); t0 = time.time()
    offset, done = get_state()
    if done: log.info('backfill already complete for this range'); return 0
    inserted = 0; walked = 0; last_dates = None
    for _ in range(pages):
        if time.time() - t0 > budget: log.info('time budget reached'); break
        r = R.safe_get('https://www.utahcounty.gov/LandRecords/DocDescSearch.asp',
                       params={'DocDesc': '', 'DateRange': rng, 'County': 'Utah', 'offset': offset}, timeout=60)
        if not r: log.warning(f'offset {offset} unreachable; stopping this run'); break
        sigs, dates = parse_page(r.text)
        if not dates:
            log.info(f'offset {offset}: no rows — range exhausted'); done = True; break
        n = R.post_batch(sigs) or 0; inserted += n; walked += 1
        last_dates = (dates[0], dates[-1])
        log.info(f'offset {offset}: {len(dates)} rows, {len(sigs)} of interest, {n} new  ({dates[0]} … {dates[-1]})')
        offset += 200; time.sleep(2)
    set_state(offset, done, walked, inserted)
    log.info(f'walked {walked} pages, {inserted} new signals, next offset {offset}, done={done}' + (f', reached {last_dates[1]}' if last_dates else ''))
    return inserted

if __name__ == '__main__':
    main()
