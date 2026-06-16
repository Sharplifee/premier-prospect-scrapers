#!/usr/bin/env python3
"""
Premier Prospect™ — UREstate Quad-Redundancy Auth Engine
=========================================================
Kelvin Sharp's UtahRealEstate.com session — fully autonomous, self-healing.

Four-layer cascade (fastest → most reliable):
  Layer 1 — Injected cookie (Supabase credentials_registry → env var → hardcoded)
  Layer 2 — Direct POST login (full browser headers, sec-ch-ua, sec-fetch-*)
  Layer 3 — Playwright headless Chromium (bypasses JS fingerprinting)
  Layer 4 — Autonomous watchdog thread (pings every 5 min, heals on failure)

Usage:
    from ure_auth_engine import get_authenticated_session, start_watchdog

    watchdog = start_watchdog(
        on_restored=lambda cookie: print(f"Healed: {cookie[:40]}..."),
        on_failed_alert=lambda count: notify(f"UREstate auth failed {count}x")
    )
    session = get_authenticated_session()
    resp = session.get("https://www.utahrealestate.com/member/1862874")
"""

import os, sys, time, logging, threading, requests, re

log = logging.getLogger(__name__)

# ── CREDENTIALS ───────────────────────────────────────────────────────────────
URE_BASE       = 'https://www.utahrealestate.com'
URE_USERNAME   = os.environ.get('URE_USERNAME', 'shakel')
URE_PASSWORD   = os.environ.get('URE_PASSWORD', 'Ronnal13=')
URE_MEMBER_ID  = '88098'

SUPABASE_URL   = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY   = os.environ.get('SUPABASE_SERVICE_KEY', '')

# Hardcoded known-good token (last-resort Layer 1 fallback)
_HARDCODED_TOKEN = os.environ.get('URE_SESSION_COOKIE', '')
_HARDCODED_COOKIE = (
    f'ure_login_token_{URE_MEMBER_ID}={_HARDCODED_TOKEN}; HELP_RIGHTS=1'
    if _HARDCODED_TOKEN else ''
)

# ── BROWSER HEADERS (full fingerprint — missing fields caused Layer 2 failures) ─
def _browser_headers(referer: str = '') -> dict:
    return {
        'User-Agent':                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept':                    'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language':           'en-US,en;q=0.9',
        'Accept-Encoding':           'gzip, deflate, br',
        'Cache-Control':             'no-cache',
        'Pragma':                    'no-cache',
        'Sec-Ch-Ua':                 '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
        'Sec-Ch-Ua-Mobile':          '?0',
        'Sec-Ch-Ua-Platform':        '"macOS"',
        'Sec-Fetch-Dest':            'document',
        'Sec-Fetch-Mode':            'navigate',
        'Sec-Fetch-Site':            'same-origin',
        'Sec-Fetch-User':            '?1',
        'Upgrade-Insecure-Requests': '1',
        'x-requested-with':          'XMLHttpRequest',
        **(({'Referer': referer}) if referer else {}),
    }

def _ajax_headers(cookie: str = '', referer: str = '') -> dict:
    h = _browser_headers(referer)
    h['Accept'] = 'application/json, text/javascript, */*; q=0.01'
    h['Sec-Fetch-Dest'] = 'empty'
    h['Sec-Fetch-Mode'] = 'cors'
    h['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
    h['Origin'] = URE_BASE
    if cookie:
        h['Cookie'] = cookie
    return h


# ── LAYER 1: COOKIE INJECTION ─────────────────────────────────────────────────
def _layer1_get_cookie() -> str:
    """Pull live cookie from Supabase → env var → hardcoded token."""
    # 1a: Supabase credentials_registry
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            r = requests.get(
                f'{SUPABASE_URL}/rest/v1/credentials_registry',
                params={'service': 'eq.utahrealestate', 'label': 'eq.ure_session_cookie',
                        'is_active': 'eq.true', 'select': 'value,updated_at'},
                headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
                timeout=8
            )
            if r.status_code == 200:
                rows = r.json()
                if rows and rows[0].get('value'):
                    log.info('[UREAuth:L1] cookie from Supabase')
                    return rows[0]['value']
        except Exception as e:
            log.debug(f'[UREAuth:L1] Supabase fetch failed: {e}')

    # 1b: Env var
    env_cookie = os.environ.get('URE_SESSION_COOKIE', '')
    if env_cookie:
        log.info('[UREAuth:L1] cookie from env var')
        return env_cookie

    # 1c: Hardcoded known-good token
    if _HARDCODED_COOKIE:
        log.info('[UREAuth:L1] using hardcoded token')
        return _HARDCODED_COOKIE

    log.info('[UREAuth:L1] no cookie available')
    return ''


def _persist_cookie(cookie: str) -> None:
    """Write fresh cookie back to Supabase and env."""
    os.environ['URE_SESSION_COOKIE'] = cookie
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            requests.patch(
                f'{SUPABASE_URL}/rest/v1/credentials_registry',
                params={'service': 'eq.utahrealestate', 'label': 'eq.ure_session_cookie'},
                headers={
                    'apikey': SUPABASE_KEY,
                    'Authorization': f'Bearer {SUPABASE_KEY}',
                    'Content-Type': 'application/json',
                    'Prefer': 'return=minimal',
                },
                json={'value': cookie, 'updated_at': 'now()', 'is_active': True},
                timeout=8
            )
            log.info('[UREAuth] cookie persisted to Supabase')
        except Exception as e:
            log.warning(f'[UREAuth] Supabase persist failed: {e}')


# ── VERIFY ────────────────────────────────────────────────────────────────────
def _verify_session(session: requests.Session, cookie: str) -> bool:
    """Return True if the session is authenticated."""
    try:
        h = _ajax_headers(cookie, referer=f'{URE_BASE}/search/form/type/1/name/quick')
        r = session.get(
            f'{URE_BASE}/search/criteria.and.count/format/json/count/true/criteria/true/advanced_search/1',
            headers=h, timeout=15
        )
        if r.status_code == 200:
            d = r.json()
            alive = str(d.get('raw_count', '--')) not in ('--', '', 'None')
            log.info(f'[UREAuth] verify → alive={alive} count={d.get("count","--")}')
            return alive
        # 500 = not authenticated
        log.info(f'[UREAuth] verify → {r.status_code}')
        return False
    except Exception as e:
        log.warning(f'[UREAuth] verify error: {e}')
        return False


# ── LAYER 2: DIRECT POST LOGIN ────────────────────────────────────────────────
def _layer2_direct_login() -> tuple[requests.Session, str]:
    """Full two-step login with complete browser headers including sec-ch-ua."""
    sess = requests.Session()
    try:
        # Step 1: establish pre-auth session (GET homepage for PHPSESSID)
        sess.get(URE_BASE + '/', headers=_browser_headers(), timeout=10)
        time.sleep(1.5)  # mimic human pause before login form

        # Step 2: get one-time key
        r_key = sess.get(
            URE_BASE + '/auth/login.form/',
            headers=_ajax_headers(referer=URE_BASE + '/auth/login/login_redirect//force_redirect/1'),
            timeout=10
        )
        key = r_key.text.strip().strip('"\'')
        if not key:
            log.warning('[UREAuth:L2] no key returned')
            return sess, ''
        log.info(f'[UREAuth:L2] key={key}')

        time.sleep(0.8)

        # Step 3: POST credentials with dynamic field names
        r_auth = sess.post(
            URE_BASE + '/auth/authenticate/',
            data=f'login_{key}={URE_USERNAME}&pass_{key}={requests.utils.quote(URE_PASSWORD)}&remember=1&loginForm=1',
            headers=_ajax_headers(
                referer=URE_BASE + '/auth/login/login_redirect//force_redirect/1'
            ),
            timeout=15,
            allow_redirects=False
        )

        # Accept: error:none OR empty {} (both are confirmed UREstate success responses)
        body_text = r_auth.text.strip()
        success = body_text in ('{"error":"none","login_redirect":"\\/"}',
                                '{"error":"none","login_redirect":"/"}',
                                '{}', '')
        if not success:
            try:
                body_json = r_auth.json()
                success = body_json.get('error', 'fail') in ('none', None, '')
            except Exception:
                success = False

        log.info(f'[UREAuth:L2] auth → {r_auth.status_code} body={body_text[:80]} success={success}')

        if not success:
            return sess, ''

        # Step 4: follow redirect to trigger server-side token issuance
        sess.get(URE_BASE + '/', headers=_browser_headers(
            referer=URE_BASE + '/auth/authenticate/'
        ), timeout=10)

        # Build cookie string from session jar
        cookie_parts = [f'{c.name}={c.value}' for c in sess.cookies]
        cookie_str = '; '.join(cookie_parts)
        log.info(f'[UREAuth:L2] cookies: {[c.name for c in sess.cookies]}')
        return sess, cookie_str

    except Exception as e:
        log.warning(f'[UREAuth:L2] error: {e}')
        return sess, ''


# ── LAYER 3: PLAYWRIGHT HEADLESS BROWSER ─────────────────────────────────────
def _layer3_playwright_login() -> tuple[requests.Session, str]:
    """Full Chromium session — bypasses any JS fingerprinting or challenge."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning('[UREAuth:L3] playwright not installed — skipping')
        return requests.Session(), ''

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-blink-features=AutomationControlled',
                ]
            )
            ctx = browser.new_context(
                viewport={'width': 1440, 'height': 900},
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
                locale='en-US',
                timezone_id='America/Denver',
                java_script_enabled=True,
            )
            # Hide webdriver flag
            ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            page = ctx.new_page()
            page.goto(f'{URE_BASE}/auth/login/login_redirect//force_redirect/1', timeout=30000)
            page.wait_for_load_state('networkidle', timeout=15000)

            # Fill form
            page.locator('#login').fill(URE_USERNAME)
            page.locator('#pass').fill(URE_PASSWORD)

            # Check remember-me if present
            try:
                rm = page.locator('input[type=checkbox]').first
                if rm and not rm.is_checked():
                    rm.check()
            except Exception:
                pass

            # Click submit
            page.locator('#submit_button').click()
            page.wait_for_load_state('networkidle', timeout=15000)

            # Extract cookies
            cookies = ctx.cookies()
            browser.close()

            cookie_str = '; '.join(f'{c["name"]}={c["value"]}' for c in cookies)
            log.info(f'[UREAuth:L3] playwright cookies: {[c["name"] for c in cookies]}')

            # Build a requests session with the cookies
            sess = requests.Session()
            for c in cookies:
                sess.cookies.set(c['name'], c['value'], domain=c.get('domain', '.utahrealestate.com'))

            return sess, cookie_str

    except Exception as e:
        log.warning(f'[UREAuth:L3] playwright error: {e}')
        return requests.Session(), ''


# ── LOG ALERT TO SUPABASE ─────────────────────────────────────────────────────
def _log_alert(alert_type: str, severity: str, message: str, metadata: dict = None) -> None:
    if not (SUPABASE_URL and SUPABASE_KEY):
        return
    try:
        requests.post(
            f'{SUPABASE_URL}/rest/v1/system_alerts',
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}',
                     'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
            json={'alert_type': alert_type, 'severity': severity,
                  'message': message, 'metadata': metadata or {}},
            timeout=8
        )
    except Exception:
        pass


# ── MAIN AUTH CASCADE ─────────────────────────────────────────────────────────
_current_session: requests.Session | None = None
_current_cookie: str = ''
_session_lock = threading.Lock()


def get_authenticated_session() -> requests.Session:
    """
    Return an authenticated requests.Session via the fastest available layer.
    Thread-safe. Tries Layer 1→2→3 on first call or after session expiry.
    """
    global _current_session, _current_cookie

    with _session_lock:
        # Try existing session first
        if _current_session and _current_cookie:
            if _verify_session(_current_session, _current_cookie):
                return _current_session

        # Layer 1: cookie injection
        cookie = _layer1_get_cookie()
        if cookie:
            sess = requests.Session()
            if _verify_session(sess, cookie):
                _current_session, _current_cookie = sess, cookie
                log.info('[UREAuth] Layer 1 success')
                return sess
            log.info('[UREAuth] Layer 1 cookie stale — escalating')

        # Layer 2: direct POST login
        sess, cookie = _layer2_direct_login()
        if cookie:
            if _verify_session(sess, cookie):
                _persist_cookie(cookie)
                _current_session, _current_cookie = sess, cookie
                log.info('[UREAuth] Layer 2 success')
                return sess
            log.info('[UREAuth] Layer 2 login accepted but verify failed — escalating')

        # Layer 3: Playwright
        sess, cookie = _layer3_playwright_login()
        if cookie:
            if _verify_session(sess, cookie):
                _persist_cookie(cookie)
                _current_session, _current_cookie = sess, cookie
                log.info('[UREAuth] Layer 3 success')
                return sess
            log.info('[UREAuth] Layer 3 failed verify')

        # All layers failed — return unauthenticated session (scrapers handle gracefully)
        log.error('[UREAuth] All layers failed — returning unauthenticated session')
        _log_alert('ure_auth_all_layers_failed', 'critical',
                   'All 3 UREstate auth layers failed', {'username': URE_USERNAME})
        return requests.Session()


def invalidate_session() -> None:
    """Force next call to get_authenticated_session() to re-authenticate."""
    global _current_session, _current_cookie
    with _session_lock:
        _current_session = None
        _current_cookie = ''


# ── LAYER 4: AUTONOMOUS WATCHDOG THREAD ──────────────────────────────────────
class _UREWatchdog(threading.Thread):
    """
    Background thread that pings the session every 5 minutes.
    On failure: runs Layer 1→2→3 cascade with exponential backoff.
    Never stops. Calls on_restored / on_failed_alert callbacks.
    """

    def __init__(self, ping_interval: int = 300,
                 on_restored=None, on_failed_alert=None):
        super().__init__(daemon=True, name='UREWatchdog')
        self.ping_interval = ping_interval
        self.on_restored = on_restored
        self.on_failed_alert = on_failed_alert
        self._stop_event = threading.Event()
        self._fail_count = 0
        self._backoff = 30  # seconds

    def run(self):
        log.info('[UREWatchdog] started — pinging every %ds', self.ping_interval)
        while not self._stop_event.is_set():
            time.sleep(self.ping_interval)
            self._ping_and_heal()

    def _ping_and_heal(self):
        global _current_session, _current_cookie
        try:
            with _session_lock:
                sess = _current_session or requests.Session()
                cookie = _current_cookie
            alive = _verify_session(sess, cookie)
        except Exception:
            alive = False

        if alive:
            self._fail_count = 0
            self._backoff = 30
            log.debug('[UREWatchdog] session healthy')
            return

        # Session dead — run cascade
        log.warning('[UREWatchdog] session dead — running auth cascade')
        self._fail_count += 1

        try:
            new_sess = get_authenticated_session()
            with _session_lock:
                alive_now = _verify_session(new_sess, _current_cookie)

            if alive_now:
                log.info('[UREWatchdog] session healed after %d failure(s)', self._fail_count)
                self._fail_count = 0
                self._backoff = 30
                if self.on_restored and _current_cookie:
                    try:
                        self.on_restored(_current_cookie)
                    except Exception:
                        pass
            else:
                log.error('[UREWatchdog] heal failed (fail #%d)', self._fail_count)
                _log_alert('ure_watchdog_heal_failed', 'error',
                           f'UREstate session heal failed (attempt #{self._fail_count})',
                           {'fail_count': self._fail_count, 'backoff': self._backoff})
                if self.on_failed_alert:
                    try:
                        self.on_failed_alert(self._fail_count)
                    except Exception:
                        pass
                # Exponential backoff: override next ping
                time.sleep(min(self._backoff, 600))
                self._backoff = min(self._backoff * 2, 600)

        except Exception as e:
            log.error('[UREWatchdog] cascade error: %s', e)

    def stop(self):
        self._stop_event.set()


_watchdog: _UREWatchdog | None = None


def start_watchdog(ping_interval: int = 300,
                   on_restored=None,
                   on_failed_alert=None) -> _UREWatchdog:
    """
    Start the autonomous watchdog thread.
    Call once at boot. Safe to call multiple times — only one watchdog runs.

    Args:
        ping_interval:    Seconds between health checks (default 300 = 5 min)
        on_restored:      Callback(cookie_str) when session is healed
        on_failed_alert:  Callback(fail_count) after failed heal attempt

    Returns:
        The running _UREWatchdog instance
    """
    global _watchdog
    if _watchdog and _watchdog.is_alive():
        log.info('[UREWatchdog] already running')
        return _watchdog

    # Warm up the session before starting watchdog
    get_authenticated_session()

    _watchdog = _UREWatchdog(
        ping_interval=ping_interval,
        on_restored=on_restored,
        on_failed_alert=on_failed_alert,
    )
    _watchdog.start()
    log.info('[UREWatchdog] launched (ping_interval=%ds)', ping_interval)
    return _watchdog
