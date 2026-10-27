"""HTTP layer, Wayback helpers, date utilities."""
import json, re, sys, threading, time
from collections import defaultdict
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 x-osint-hunter/2.0")
TWITTER_EPOCH_MS = 1288834974657
CFG = {"verbose": True}


def log(*a):
    if CFG["verbose"]:
        print("[*]", *a, file=sys.stderr, flush=True)


class Budget:
    """Global wall-clock budget so a run stays time-bounded."""
    def __init__(self, seconds):
        self.t0 = time.time()
        self.deadline = self.t0 + seconds

    def left(self):
        return self.deadline - time.time()

    def ok(self, need=0):
        return self.left() > need

    def elapsed(self):
        return time.time() - self.t0


class Http:
    """Session with retries, timeouts and per-host politeness throttling."""
    def __init__(self):
        self.s = requests.Session()
        r = Retry(total=3, read=1, connect=4, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET", "HEAD"]), respect_retry_after_header=True)
        for p in ("https://", "http://"):
            self.s.mount(p, HTTPAdapter(max_retries=r, pool_maxsize=16))
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en"})
        self.conc = {"web.archive.org": 4, "web.archive.org#heavy": 2, "pbs.twimg.com": 4}
        self.locks = {}
        self.min_gap = {"web.archive.org": 0.5, "web.archive.org#heavy": 1.0, "api.memory.lol": 0.3, "api.fxtwitter.com": 0.4}
        self.last = defaultdict(float)
        self.tlock = threading.Lock()

    def _sem(self, host):
        with self.tlock:
            if host not in self.locks:
                self.locks[host] = threading.Semaphore(self.conc.get(host, 3))
            return self.locks[host]

    def get(self, url, timeout=40, lane=None, **kw):
        host = re.sub(r"^https?://([^/]+).*", r"\1", url) + (f"#{lane}" if lane else "")
        with self._sem(host):
            gap = self.min_gap.get(host, 0.15)
            with self.tlock:
                wait = self.last[host] + gap - time.time()
                self.last[host] = max(time.time(), self.last[host] + gap)
            if wait > 0:
                time.sleep(wait)
            return self.s.get(url, timeout=timeout, **kw)


HTTP = Http()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def ts_to_iso(ts):
    """Wayback 14-digit timestamp -> YYYY-MM-DD"""
    ts = str(ts)
    try:
        return datetime.strptime(ts[:8], "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return ts


def snowflake_date(n, full=False):
    """Creation date of a snowflake id (tweets after Nov 2010, media / avatars after ~2014)."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    if n < 10**15 or str(n).startswith("3788"):  # pre-snowflake / 2013 legacy-range ids
        return None
    ms = (n >> 22) + TWITTER_EPOCH_MS
    try:
        d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    if d.year < 2010 or d > datetime.now(timezone.utc):
        return None
    return d.strftime("%Y-%m-%d %H:%M UTC" if full else "%Y-%m-%d")


def unix_date(n):
    try:
        d = datetime.fromtimestamp(int(n), tz=timezone.utc)
        if 2006 <= d.year <= datetime.now(timezone.utc).year:
            return d.strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def parse_any_date(s):
    """Twitter created_at formats -> YYYY-MM-DD."""
    if not s:
        return None
    if isinstance(s, (int, float)) or (isinstance(s, str) and s.isdigit()):
        v = int(s)
        return unix_date(v // 1000 if v > 10**11 else v)
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- Wayback
_cdx_fails = {"n": 0}


def cdx(params, timeout=60, lane=None):
    """Wayback CDX query. v3: circuit breaker - after 5 consecutive failures (archive.org down / overloaded)
    further calls fail instantly instead of each burning a 60 s timeout, so the rest of the hunt keeps its budget."""
    if _cdx_fails["n"] >= 5:
        CFG["wayback_offline"] = True
        raise RuntimeError("skipped: archive.org CDX unreachable in this run (circuit breaker)")
    try:
        r = _cdx_get(params, timeout, lane)
        _cdx_fails["n"] = 0
        return r
    except Exception:
        _cdx_fails["n"] += 1
        raise


def _cdx_get(params, timeout=60, lane=None):
    p = {"output": "json", **params}
    r = HTTP.get("https://web.archive.org/cdx/search/cdx", params=p, timeout=timeout, lane=lane)
    if r.status_code != 200:
        raise RuntimeError(f"CDX HTTP {r.status_code}")
    txt = r.text.strip()
    if not txt:
        return []
    if txt[0] not in "[{":
        if "Temporarily Offline" in txt[:3000] or "<html" in txt[:200].lower():
            CFG["wayback_offline"] = True
            raise RuntimeError("archive.org CDX is temporarily offline / returned an HTML error page")
        raise RuntimeError("CDX returned non-JSON")
    rows = json.loads(txt)
    if not rows:
        return []
    head = rows[0]
    if head and head[0] in ("timestamp", "urlkey", "original"):
        return [dict(zip(head, row)) for row in rows[1:]]
    return [dict(zip(params.get("fl", "").split(","), row)) for row in rows]


def cdx_num_pages(url, match="prefix", timeout=60):
    r = HTTP.get("https://web.archive.org/cdx/search/cdx",
                 params={"url": url, "matchType": match, "showNumPages": "true"}, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"CDX pages HTTP {r.status_code}")
    try:
        return int(r.text.strip() or 0)
    except ValueError:
        return 0


def wb_raw(ts, url, timeout=45, allow_redirects=True):
    u = f"https://web.archive.org/web/{ts}id_/{url}"
    try:
        return HTTP.get(u, timeout=timeout, allow_redirects=allow_redirects)
    except requests.exceptions.ContentDecodingError:
        return HTTP.get(u, timeout=timeout, allow_redirects=allow_redirects, headers={"Accept-Encoding": "identity"})


def wb_link(ts, url):
    return f"https://web.archive.org/web/{ts}/{url}"


def wb_img(ts, url):
    return f"https://web.archive.org/web/{ts}im_/{url}"
