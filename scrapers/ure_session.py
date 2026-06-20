#!/usr/bin/env python3
"""
Premier Prospect™ — UREstate Session Manager
Delegates to ure_auth_engine (quad-redundancy) when available.
Falls back to direct session logic for standalone use.
"""
import os, sys, logging, re, time, requests

log = logging.getLogger(__name__)

# Delegate to quad-redundancy engine when called from the pipeline
try:
    from ure_auth_engine import get_authenticated_session as _get_engine_session
    _USE_ENGINE = True
    log.info('[ure_session] using ure_auth_engine quad-redundancy layer')
except ImportError:
    _USE_ENGINE = False
    log.info('[ure_session] ure_auth_engine not available — using direct session')

# ── CREDENTIALS os, logging, re, time, requests


# ── CREDENTIALS ───────────────────────────────────────────────────────────────
URE_USERNAME    = os.environ.get('URE_USERNAME', 'shakel')
URE_PASSWORD    = os.environ.get('URE_PASSWORD', 'Ronnal13=')
URE_BASE        = 'https://www.utahrealestate.com'
URE_MEMBER_ID   = os.environ.get('URE_MEMBER_ID', '88098')

# Persistent session cookie — stored as GitHub Secret URE_SESSION_COOKIE
# Format: "ure_login_token_88098=XXXX; ureBrowserSession=YYY; ureServerSession=YYY; PHPSESSID=ZZZ"
# Captured from Kelvin's live browser session. Auto-refreshes when expired.
URE_SESSION_COOKIE = os.environ.get('URE_SESSION_COOKIE', '')

# Default checksum for "all Utah" search (MD5 of empty criteria)
CHECKSUM_ALL_UTAH = 'd751713988987e9331980363e24189ce'

# ── HEADERS ───────────────────────────────────────────────────────────────────
def _base_headers(cookie_str: str = '', referer: str = '') -> dict:
    h = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'cache-control': 'no-cache',
        'pragma': 'no-cache',
        'x-requested-with': 'XMLHttpRequest',
    }
    if cookie_str:
        h['Cookie'] = cookie_str
    if referer:
        h['Referer'] = referer
    return h


# ── SESSION MANAGEMENT ────────────────────────────────────────────────────────
class URESession:
    """
    Self-healing session manager for utahrealestate.com.

    Priority order for session:
      1. URE_SESSION_COOKIE env var (persistent token from Kelvin's browser)
      2. Fresh login via /auth/authenticate/ (PHPSESSID-based, works for most endpoints)
    """

    def __init__(self):
        self.session = requests.Session()
        self.cookie_str = URE_SESSION_COOKIE
        self._login_attempted = False

    def ensure_alive(self) -> bool:
        """Verify session is authenticated. Re-login if not."""
        if self._is_alive():
            return True
        log.info('[URESession] Session dead — attempting login')
        return self._login()

    def _is_alive(self) -> bool:
        """Ping the auth endpoint — returns member_id if alive, empty if guest."""
        try:
            h = _base_headers(self.cookie_str, referer=URE_BASE + '/search/form/type/1/name/quick')
            h['Accept'] = 'application/json, text/javascript, */*; q=0.01'
            r = self.session.get(
                f'{URE_BASE}/search/criteria.and.count/format/json/count/true/criteria/true/advanced_search/1',
                headers=h, timeout=15
            )
            if r.status_code == 200:
                d = r.json()
                alive = d.get('raw_count', '--') not in ('--', '', None)
                log.info(f'[URESession] alive={alive} count={d.get("count","--")}')
                return alive
            log.info(f'[URESession] ping {r.status_code}')
            return False
        except Exception as e:
            log.warning(f'[URESession] ping error: {e}')
            return False

    def _login(self) -> bool:
        """
        Two-step login:
          1. GET /auth/login.form/ → one-time key
          2. POST /auth/authenticate/ → PHPSESSID + ureBrowserSession
        The ure_login_token persistent token is only issued via browser JS.
        For server-side scraping, PHPSESSID session is used for web endpoints
        (/member/{listno}, /search/perform/) which don't require the persistent token.
        """
        try:
            self.session = requests.Session()
            self.session.get(URE_BASE + '/', headers=_base_headers(), timeout=10)

            # Get one-time key
            r_key = self.session.get(
                URE_BASE + '/auth/login.form/',
                headers={**_base_headers(), 'Accept': 'text/plain, */*'},
                timeout=10
            )
            key = r_key.text.strip().strip('"\'')
            if not key:
                log.error('[URESession] Failed to get login key')
                return False

            # Authenticate
            r_auth = self.session.post(
                URE_BASE + '/auth/authenticate/',
                data=f'login_{key}={URE_USERNAME}&pass_{key}={requests.utils.quote(URE_PASSWORD)}&remember=1&loginForm=1',
                headers={
                    **_base_headers(referer=URE_BASE + '/auth/login/login_redirect//force_redirect/1'),
                    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                    'Accept': 'application/json, text/javascript, */*; q=0.01',
                    'Origin': URE_BASE,
                },
                timeout=15, allow_redirects=False
            )
            body = r_auth.json() if r_auth.headers.get('content-type','').startswith('application/json') else {}
            if body.get('error', '') != 'none':
                log.error(f'[URESession] Login failed: {body}')
                return False

            # Build cookie string from session jar
            self.cookie_str = '; '.join(f'{c.name}={c.value}' for c in self.session.cookies)
            # Append URE_SESSION_COOKIE if available (for the persistent token)
            if URE_SESSION_COOKIE:
                self.cookie_str = URE_SESSION_COOKIE + '; ' + self.cookie_str

            log.info(f'[URESession] Login OK — cookies: {[c.name for c in self.session.cookies]}')
            self._login_attempted = True
            return True

        except Exception as e:
            log.error(f'[URESession] Login error: {e}')
            return False

    # ── API CALLS ─────────────────────────────────────────────────────────────

    def get_listing(self, listno: str) -> bytes | None:
        """
        Fetch full listing detail page for a given listno.
        Returns HTML bytes or None.
        /member/{listno} — authenticated, returns 164KB full detail
        """
        if not self.ensure_alive():
            return None
        try:
            h = _base_headers(self.cookie_str, referer=URE_BASE + '/search/form/type/1/name/quick')
            h['Accept'] = 'text/html,application/xhtml+xml,*/*'
            r = self.session.get(f'{URE_BASE}/member/{listno}', headers=h, timeout=30)
            if r.status_code == 200:
                return r.content
            log.warning(f'[URESession] listing {listno}: {r.status_code}')
            return None
        except Exception as e:
            log.warning(f'[URESession] listing {listno} error: {e}')
            return None

    def search_perform(self, checksum: str = CHECKSUM_ALL_UTAH, page: int = 1) -> bytes | None:
        """
        Execute a search and return HTML results page.
        Parse listing IDs from the HTML then call get_listing() per ID.
        """
        if not self.ensure_alive():
            return None
        try:
            h = _base_headers(self.cookie_str, referer=URE_BASE + '/search/form/type/1/name/quick')
            h['Accept'] = 'text/html,*/*'
            r = self.session.get(
                f'{URE_BASE}/search/perform/md/{checksum}/recent/{page}',
                headers=h, timeout=30
            )
            if r.status_code == 200:
                return r.content
            log.warning(f'[URESession] search page {page}: {r.status_code}')
            return None
        except Exception as e:
            log.warning(f'[URESession] search error: {e}')
            return None

    def get_criteria_and_count(self, checksum: str = None) -> dict | None:
        """
        Fetch search criteria, listing count, and params-chksum.
        Requires persistent ure_login_token (not just PHPSESSID).
        Returns dict with keys: raw_count, count, checksum
        """
        if not self.ensure_alive():
            return None
        try:
            h = _base_headers(self.cookie_str, referer=URE_BASE + '/search/form/type/1/name/quick')
            h['Accept'] = 'application/json, text/javascript, */*; q=0.01'
            r = self.session.get(
                f'{URE_BASE}/search/criteria.and.count/format/json/count/true/criteria/true/advanced_search/1',
                headers=h, timeout=15
            )
            if r.status_code != 200:
                return None
            d = r.json()
            html = d.get('html', '')
            m = re.search(r'id="params-chksum"[^>]*>([a-f0-9]{32})', html)
            if not m:
                m = re.search(r'([a-f0-9]{32})', html)
            return {
                'raw_count': int(d.get('raw_count', 0) or 0),
                'count': d.get('count', '0'),
                'checksum': m.group(1) if m else CHECKSUM_ALL_UTAH,
            }
        except Exception as e:
            log.warning(f'[URESession] criteria error: {e}')
            return None


# ── LISTING PARSER ────────────────────────────────────────────────────────────
def parse_listing(html: bytes, listno: str) -> dict:
    """
    Parse a /member/{listno} HTML page into structured fields.
    All 20+ fields confirmed live from reverse-engineered session.
    """
    if not html:
        return {}
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        text = soup.get_text(' ', strip=True)

        def extract(pattern: str, flags=re.IGNORECASE) -> str:
            m = re.search(pattern, text, flags)
            return m.group(1).strip() if m else ''

        def extract_html(pattern: str) -> str:
            m = re.search(pattern, str(soup), re.IGNORECASE | re.DOTALL)
            return m.group(1).strip() if m else ''

        # Cordless widget fields (most reliable)
        price_raw = extract(r'\$([0-9,]+)(?:\s*/\s*mo|\s+List Price)?')
        price = int(price_raw.replace(',', '')) if price_raw else 0

        # Photos
        photos = []
        for tag in soup.find_all(attrs={'data-name': re.compile(r'^\d+')}):
            name = tag.get('data-name', '')
            if name:
                photos.append({
                    'url': f"https://webdrive.utahrealestate.com/photos/{name}",
                    'room_type': tag.get('data-type', ''),
                    'alt': tag.get('alt', ''),
                })

        # Agent from inline JS
        agent_match = re.search(r"_LT\._trackEvent\([^,]+,[^,]+,['\"]([^'\"]+)['\"]", str(soup))

        return {
            'listno':          listno,
            'address':         extract(r'(\d+\s+[A-Z][^,\n]{5,50}(?:St|Ave|Dr|Rd|Blvd|Ln|Way|Ct|Cir)[^\n,]*)'),
            'city':            extract(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?),\s*UT'),
            'state':           'UT',
            'price':           price,
            'status':          extract(r'(?:Status|MLS Status)[:\s]+([A-Za-z\s]+?)(?:\n|$)'),
            'property_type':   extract(r'(?:Property Type|Type)[:\s]+([A-Za-z\s/]+?)(?:\n|$)'),
            'year_built':      extract(r'(?:Year Built|Yr Built)[:\s]+(\d{4})'),
            'sqft':            extract(r'(\d[\d,]+)\s*(?:sq\.?\s*ft|SF)'),
            'lot_size':        extract(r'Lot Size[:\s]+([^\n]+?)(?:\n|$)'),
            'beds':            extract(r'(\d+(?:\.\d+)?)\s*(?:Bed|BR)'),
            'baths':           extract(r'(\d+(?:\.\d+)?)\s*Bath'),
            'hoa':             extract(r'HOA[:\s]+\$?([0-9,]+)'),
            'days_on_market':  extract(r'(\d+)\s+(?:Days? on|DOM)'),
            'county':          extract(r'(?:County|Co\.)[:\s]+([A-Za-z\s]+?)(?:\n|$)'),
            'school_district': extract(r'School\s+Dist[^:]*:[:\s]+([^\n]+?)(?:\n|$)'),
            'agent_id':        agent_match.group(1) if agent_match else '',
            'photos':          photos,
            'photo_count':     len(photos),
        }
    except Exception as e:
        log.warning(f'[parse_listing] {listno}: {e}')
        return {}


# ── SEARCH RESULTS PARSER ─────────────────────────────────────────────────────
def parse_search_listnos(html: bytes) -> list[str]:
    """Extract listing IDs from a /search/perform/ HTML results page."""
    if not html:
        return []
    try:
        listnos = re.findall(r'(?:listno|listing_id|/member/)[\s=/"\']+(\d{7,8})', html.decode('utf-8', errors='ignore'))
        return list(dict.fromkeys(listnos))  # dedupe preserving order
    except Exception as e:
        log.warning(f'[parse_search_listnos] error: {e}')
        return []


# ── SINGLETON ─────────────────────────────────────────────────────────────────
_session: URESession | None = None

def get_session() -> URESession:
    """Get or create the singleton URE session."""
    global _session
    if _session is None:
        _session = URESession()
    return _session


def get_listing_html(sess: requests.Session, cookie: str, listno: str) -> bytes:
    """Fetch a single MLS listing page — returns raw HTML bytes."""
    URE_BASE = 'https://www.utahrealestate.com'
    try:
        r = sess.get(
            f'{URE_BASE}/member/{listno}',
            headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Cookie': cookie,
                'Referer': f'{URE_BASE}/search/results/',
            },
            timeout=15)
        return r.content if r.status_code == 200 else b''
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f'get_listing_html {listno}: {e}')
        return b''
