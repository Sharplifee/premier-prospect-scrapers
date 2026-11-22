"""
Wait for the database to answer, with backoff; fail loudly if it still doesn't.

Nov 21 2026: the Supabase project returned 522 for every API call from about 8:10 to 9:50 PM MT, then dropped out again
for about a minute at 9:52 PM. Every scheduled job (2:23 AM scrape, nightly recompute, 6:41 AM health monitor) calls
this first, so a short dropout is ridden out and a long one fails the run with a clear error instead of passing green.

  python scrapers/db_wait.py [max_seconds]      # exit 0 when the database answers, 1 (with ::error::) if not
  from db_wait import wait_for_db               # same, returns True / False

Retries 10s, 20s, 40s, 80s, 160s, then every 300s until max_seconds (default DB_WAIT_MAX or 1200 = 20 min).
A 401/403/404 is a key or config problem, not a dropout: it fails at once.
"""
import os, sys, time
import requests


def wait_for_db(max_wait=None, label=''):
    max_wait = int(max_wait or os.environ.get('DB_WAIT_MAX', '1200'))
    base = os.environ['SUPABASE_URL'].rstrip('/')
    key = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ['SUPABASE_KEY']
    url = f'{base}/rest/v1/pp_run_log?select=id&limit=1'
    hdr = {'apikey': key, 'Authorization': f'Bearer {key}'}
    waited, delay, attempt, last = 0, 10, 0, ''
    where = f' ({label})' if label else ''
    while True:
        attempt += 1
        try:
            r = requests.get(url, headers=hdr, timeout=25)
            if r.status_code == 200:
                if attempt > 1:
                    print(f'database answering again after {waited}s ({attempt} tries){where}', flush=True)
                return True
            last = f'HTTP {r.status_code} {r.text[:120]}'
            if r.status_code in (401, 403, 404):
                print(f'::error::database refused the request{where}: {last} — a key or config problem, not a dropout', flush=True)
                return False
        except Exception as e:
            last = type(e).__name__
        if waited >= max_wait:
            break
        print(f'database not answering ({last}){where}; retry {attempt} in {delay}s', flush=True)
        time.sleep(delay)
        waited += delay
        delay = min(delay * 2, 300)
    print(f'::error::database unreachable for {waited}s over {attempt} tries{where}: {last}', flush=True)
    return False


if __name__ == '__main__':
    sys.exit(0 if wait_for_db(sys.argv[1] if len(sys.argv) > 1 else None) else 1)
