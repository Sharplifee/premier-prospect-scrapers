"""
URE (UtahRealEstate.com) session authentication.

Why this exists
---------------
The four MLS scrapers authenticate with a session cookie stored in the
URE_SESSION_COOKIE GitHub secret. That cookie expires every few weeks, and when
it does UtahRealEstate does NOT return 401/403 — it 302-redirects to '/'. With
redirects followed that looks like a normal HTTP 200 on the homepage, so the
scrapers parsed marketing HTML as search results and silently reported
"0 listings" for weeks while appearing healthy.

This module removes the recurring manual step: it logs in with URE_USERNAME /
URE_PASSWORD (already present in GitHub Secrets) and mints a fresh session
cookie on demand.

Login flow (verified against the live site)
-------------------------------------------
  GET  /auth/login/login_redirect//force_redirect/1   -> sets PHPSESSID,
                                                         ureBrowserSession,
                                                         ureServerSession
  POST (same URL)  login=<user>&pass=<pass>           -> authenticates session

Success is NOT assumed from the POST status code. It is verified by calling the
real search endpoint and confirming it does not redirect — the same check that
detects expiry in the first place.

The resulting cookie is cached at module scope, so a pipeline run that touches
all four MLS scrapers performs exactly one login, not four.
"""

import os
import logging

import requests

log = logging.getLogger(__name__)

_URE_BASE = 'https://www.utahrealestate.com'
_LOGIN_PATH = '/auth/login/login_redirect//force_redirect/1'
# Cheapest authenticated endpoint we can use as a liveness probe.
_VERIFY_PATH = '/search/perform/format/json/type/1/count/1/start/0/checksum/0'

_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')

# Module-level cache: one login per process, shared by all four MLS scrapers.
_CACHED_COOKIE = None
_LOGIN_ATTEMPTED = False


def _cookie_header(session):
    """Serialize a session's cookie jar into a Cookie: header string."""
    return '; '.join(f'{c.name}={c.value}' for c in session.cookies)


def cookie_is_live(cookie):
    """
    True only if `cookie` can actually reach the authenticated search endpoint.

    allow_redirects=False is load-bearing: an expired URE session 302s to '/'
    rather than returning an auth error, so following redirects would yield a
    misleading HTTP 200.
    """
    if not cookie:
        return False
    try:
        r = requests.get(
            _URE_BASE + _VERIFY_PATH,
            headers={
                'User-Agent': _UA,
                'Cookie': cookie,
                'x-requested-with': 'XMLHttpRequest',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Referer': _URE_BASE + '/search/form/type/1/',
            },
            timeout=20,
            allow_redirects=False,
        )
        # Unauthenticated sessions are bounced with a 3xx to '/'. Any non-redirect
        # response means we cleared authentication — including a 500, which this
        # probe's deliberately-invalid checksum=0 provokes on a *valid* session.
        # Testing for ==200 here would reject a perfectly good login.
        return r.status_code not in (301, 302, 303, 307, 308)
    except Exception as e:
        log.warning(f'[ure_auth] cookie liveness check failed: {e}')
        return False


def login() -> str:
    """
    Authenticate with URE_USERNAME / URE_PASSWORD and return a fresh cookie
    header string. Returns '' if credentials are absent or login fails.
    """
    user = os.environ.get('URE_USERNAME', '')
    pw = os.environ.get('URE_PASSWORD', '')
    if not user or not pw:
        log.warning('[ure_auth] URE_USERNAME / URE_PASSWORD not set — '
                    'cannot auto-refresh session')
        return ''

    s = requests.Session()
    s.headers['User-Agent'] = _UA
    url = _URE_BASE + _LOGIN_PATH

    try:
        # URE's login form is onsubmit="return false;" — it never submits natively.
        # Authenticate.initLoginForm() performs a three-step handshake, and posting
        # login/pass straight at the page URL simply re-renders the login page
        # (HTTP 200, no session), which is what made this look like bad credentials.
        #
        #   1. GET  the login page                -> establishes PHPSESSID
        #   2. POST /auth/login.form/             -> returns a per-session key
        #   3. POST /auth/authenticate/           -> fields renamed login_<key>/pass_<key>
        s.get(url, timeout=20)

        kr = s.post(_URE_BASE + '/auth/login.form/',
                    headers={'X-Requested-With': 'XMLHttpRequest', 'Referer': url},
                    timeout=20)
        key = kr.text.strip().strip('"')
        if kr.status_code != 200 or not key:
            log.error(f'[ure_auth] could not obtain login form key (HTTP {kr.status_code})')
            return ''

        r = s.post(
            _URE_BASE + '/auth/authenticate/',
            data={f'login_{key}': user, f'pass_{key}': pw},
            headers={'X-Requested-With': 'XMLHttpRequest', 'Referer': url},
            timeout=25,
        )
        if r.status_code >= 400:
            log.error(f'[ure_auth] authenticate returned HTTP {r.status_code}')
            return ''
        # Endpoint reports failure in-band with HTTP 200, e.g. {"error":"Invalid login"}
        try:
            err = (r.json() or {}).get('error', 'none')
        except Exception:
            err = 'none'
        if str(err).lower() not in ('none', '', 'false'):
            log.error(f'[ure_auth] authentication rejected: {err}')
            return ''

        cookie = _cookie_header(s)
        if not cookie:
            log.error('[ure_auth] login produced no cookies')
            return ''

        # Verify against the real endpoint rather than trusting the POST status.
        if not cookie_is_live(cookie):
            log.error('[ure_auth] login completed but session is not authenticated '
                      '— credentials may be wrong or the account is locked')
            return ''

        log.info('[ure_auth] URE session refreshed successfully')
        return cookie
    except Exception as e:
        log.error(f'[ure_auth] login failed: {type(e).__name__}: {e}')
        return ''


def get_cookie() -> str:
    """
    Return a working URE session cookie.

    Order of preference:
      1. Cached cookie from an earlier call in this same run.
      2. URE_SESSION_COOKIE secret, if it is still live.
      3. A fresh auto-login.

    Login is attempted at most once per process, so four MLS scrapers cost one
    login, and a hard credential failure does not retry four times.
    """
    global _CACHED_COOKIE, _LOGIN_ATTEMPTED

    if _CACHED_COOKIE:
        return _CACHED_COOKIE

    static = os.environ.get('URE_SESSION_COOKIE', '')
    if static and cookie_is_live(static):
        log.info('[ure_auth] existing URE_SESSION_COOKIE is still valid')
        _CACHED_COOKIE = static
        return _CACHED_COOKIE

    if static:
        log.warning('[ure_auth] URE_SESSION_COOKIE is expired — auto-refreshing via login')

    if _LOGIN_ATTEMPTED:
        # Already tried and failed this run; don't hammer the login endpoint.
        return ''
    _LOGIN_ATTEMPTED = True

    fresh = login()
    if fresh:
        _CACHED_COOKIE = fresh
        log.info('[ure_auth] NOTE: refreshed cookie lives for this run only. '
                 'Update the URE_SESSION_COOKIE secret to persist it across runs.')
    return fresh
