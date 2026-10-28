"""
Append-only event log client for the Premier Prospect workers.

Every job start/step/end, every outbound HTTP request (fetch), every retry and
every error lands in Corpus HQ `public.event_log` via the `log_events` RPC.
The table is append-only at the database level (UPDATE/DELETE/TRUNCATE are
blocked by trigger), so what a run wrote can never be edited afterwards.

Usage (one line at the top of any worker script):

    import eventlog; eventlog.install(job=__file__)

`install()`:
  * writes a `job_start` row now and a `job_end` row at interpreter exit
    (status=fail if the process is dying from an uncaught exception),
  * patches `requests.Session.request` so EVERY outbound HTTP call made by the
    script is logged with host/path, status, duration and attempt number
    (repeat calls to the same URL inside one job are numbered 1, 2, 3 … so a
    retry shows up as `retry` rather than `fetch`),
  * hooks `sys.excepthook` so uncaught exceptions are recorded as `error` rows.

`step(name, **payload)` records a `job_step` for the current job.
`error(exc, **payload)` records a handled error.
Nothing in here ever raises into the caller.
"""
import atexit
import datetime as _dt
import json
import os
import sys
import threading
import time
import uuid

try:
    import requests as _requests
except Exception:  # pragma: no cover - requests is a hard dependency of every worker anyway
    _requests = None

PROJECT = os.environ.get("EVENT_LOG_PROJECT", "prospectplus")
SURFACE = "worker"
_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
_KEY = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY") or ""
_ENDPOINT = f"{_URL}/rest/v1/rpc/log_events" if _URL else ""
_RUN = os.environ.get("GITHUB_RUN_ID")
_BUILD = (os.environ.get("GITHUB_SHA") or "")[:12] or None
_ENV = "production" if os.environ.get("GITHUB_ACTIONS") else "development"

_lock = threading.Lock()
_queue = []
_job = {"name": None, "trace_id": None, "started": None, "steps": 0}
_attempts = {}
_installed = False
_raw_request = None


def _now():
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _bounded(p):
    try:
        s = json.dumps(p, default=str)
        if len(s) <= 8000:
            return json.loads(s)
        return {"truncated": True, "bytes": len(s), "head": s[:7000]}
    except Exception:
        return {"unserializable": True}


def log_event(event_type, name, status=None, **kw):
    """Queue one event. Flushes every 25 events or at exit."""
    ev = {
        "project": PROJECT,
        "surface": SURFACE,
        "env": _ENV,
        "event_type": event_type,
        "name": str(name)[:300],
        "status": status,
        "actor_type": "cron" if os.environ.get("GITHUB_ACTIONS") else "service",
        "actor_id": f"github-actions:{os.environ.get('GITHUB_WORKFLOW', '')}:{_RUN}" if _RUN else f"local:{os.environ.get('USER', 'worker')}",
        "trace_id": _job["trace_id"],
        "session_id": _RUN,
        "build": _BUILD,
        "client_ts": _now(),
    }
    payload = kw.pop("payload", {}) or {}
    for k, v in kw.items():
        if v is not None:
            ev[k] = v
    ev["payload"] = _bounded(payload)
    if ev.get("error_message"):
        ev["error_message"] = str(ev["error_message"])[:4000]
    with _lock:
        _queue.append(ev)
        n = len(_queue)
    if n >= 25:
        flush()


def flush():
    if not _ENDPOINT or not _KEY or _requests is None:
        with _lock:
            _queue.clear()
        return
    with _lock:
        batch, _queue[:] = _queue[:], []
    if not batch:
        return
    try:
        # use the unpatched request so the log never logs itself
        sess = _requests.Session()
        fn = _raw_request.__get__(sess, _requests.Session) if _raw_request else sess.request
        r = fn("POST", _ENDPOINT, json={"p": batch}, timeout=10,
               headers={"apikey": _KEY, "Authorization": f"Bearer {_KEY}", "Content-Type": "application/json", "Prefer": "return=minimal"})
        if r.status_code >= 400:
            sys.stderr.write(f"[event_log] write failed {r.status_code}: {r.text[:200]}\n")
    except Exception as e:  # never break the worker because logging failed
        sys.stderr.write(f"[event_log] write threw: {e}\n")


def step(name, status="ok", **payload):
    _job["steps"] += 1
    log_event("job_step", f"{_job['name']}.{name}" if _job["name"] else name, status,
              attempt=_job["steps"], payload=payload)


def error(exc, name=None, **payload):
    log_event("error", name or f"{_job['name']}.error", "fail",
              error_code=type(exc).__name__, error_message=str(exc), payload=payload)


def _host_path(url):
    try:
        from urllib.parse import urlsplit
        u = urlsplit(url)
        return u.netloc, (u.path or "/")[:300]
    except Exception:
        return url[:80], ""


def _patched_request(self, method, url, *args, **kwargs):
    if _ENDPOINT and url.startswith(_ENDPOINT):
        return _raw_request(self, method, url, *args, **kwargs)
    host, path = _host_path(url)
    key = f"{method} {host}{path}"
    with _lock:
        _attempts[key] = _attempts.get(key, 0) + 1
        attempt = _attempts[key]
    t0 = time.time()
    try:
        resp = _raw_request(self, method, url, *args, **kwargs)
        log_event("retry" if attempt > 1 else "fetch", f"{method.upper()} {host}{path}",
                  "ok" if resp.status_code < 400 else "fail",
                  http_method=method.upper(), http_path=f"{host}{path}", http_status=resp.status_code,
                  attempt=attempt, duration_ms=int((time.time() - t0) * 1000))
        return resp
    except Exception as e:
        log_event("error", f"{method.upper()} {host}{path}", "fail",
                  http_method=method.upper(), http_path=f"{host}{path}", attempt=attempt,
                  duration_ms=int((time.time() - t0) * 1000),
                  error_code=type(e).__name__, error_message=str(e))
        raise


def _excepthook(exc_type, exc, tb):
    try:
        log_event("error", f"{_job['name']}.uncaught", "fail",
                  error_code=exc_type.__name__, error_message=str(exc))
        _job["failed"] = True
    finally:
        sys.__excepthook__(exc_type, exc, tb)


def _at_exit():
    if _job["name"]:
        status = "fail" if _job.get("failed") else "ok"
        log_event("job_end", _job["name"], status,
                  duration_ms=int((time.time() - _job["started"]) * 1000),
                  payload={"steps": _job["steps"], "http_calls": sum(_attempts.values())})
    flush()


def install(job=None):
    """Idempotent. Call once at the top of a worker script."""
    global _installed, _raw_request
    if _installed:
        return
    _installed = True
    if _requests is not None:
        _raw_request = _requests.Session.request
        _requests.Session.request = _patched_request
    name = os.path.basename(job) if job else os.path.basename(sys.argv[0] or "worker")
    name = name[:-3] if name.endswith(".py") else name
    _job.update(name=f"pp.{name}", trace_id=uuid.uuid4().hex, started=time.time(), steps=0)
    sys.excepthook = _excepthook
    atexit.register(_at_exit)
    log_event("job_start", _job["name"], "pending",
              payload={"workflow": os.environ.get("GITHUB_WORKFLOW"), "run_number": os.environ.get("GITHUB_RUN_NUMBER"), "argv": sys.argv[1:]})
    flush()
