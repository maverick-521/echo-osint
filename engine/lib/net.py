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
        # v4: 429 is NOT retried blindly by urllib3 any more (that made archive.org block the IP); get() handles it
        r = Retry(total=3, read=1, connect=4, backoff_factor=2, status_forcelist=(500, 502, 503, 504),
                  allowed_methods=frozenset(["GET", "HEAD"]), respect_retry_after_header=True)
        for p in ("https://", "http://"):
            self.s.mount(p, HTTPAdapter(max_retries=r, pool_maxsize=16))
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en"})
        self.conc = {"web.archive.org": 4, "web.archive.org#heavy": 2, "pbs.twimg.com": 4}
        self.locks = {}
        self.min_gap = {"web.archive.org": 0.5, "web.archive.org#heavy": 1.0, "api.memory.lol": 0.3, "api.fxtwitter.com": 0.4}
        self.last = defaultdict(float)
        self.adapt, self.pause, self.rate_limited = {}, {}, defaultdict(int)
        self.consec429, self.storm_until, self.next_probe, self.storms = 0, 0, 0, 0
        from collections import deque
        self.recent = deque(maxlen=20)
        self.tlock = threading.Lock()

    def _sem(self, host):
        with self.tlock:
            if host not in self.locks:
                self.locks[host] = threading.Semaphore(self.conc.get(host, 3))
            return self.locks[host]

    def get(self, url, timeout=40, lane=None, **kw):
        base = re.sub(r"^https?://([^/]+).*", r"\1", url)
        host = base + (f"#{lane}" if lane else "")
        for attempt in range(4):
            if base == "web.archive.org" and self.storm_until > time.time():
                with self.tlock:   # archive.org is refusing this IP: one probe a minute, everything else fails fast
                    probe = time.time() >= self.next_probe
                    if probe:
                        self.next_probe = time.time() + 60
                if not probe:
                    raise requests.exceptions.ConnectionError(
                        "web.archive.org is rate-limiting this IP (HTTP 429 storm) - capture skipped")
            with self._sem(host):
                gap = max(self.min_gap.get(host, 0.15), self.adapt.get(base, 0))
                with self.tlock:
                    start = max(time.time(), self.last[host] + gap, self.pause.get(base, 0))
                    self.last[host] = start
                if start > time.time():
                    time.sleep(min(start - time.time(), 300))
                r = self.s.get(url, timeout=timeout, **kw)
            if r.status_code != 429 or base != "web.archive.org":
                if base == "web.archive.org":
                    with self.tlock:
                        self.recent.append(0)
                        self.consec429 = 0
                        if self.storm_until:
                            log("web.archive.org accepting requests again - resuming capture fetches")
                        self.storm_until = 0
                if base in self.adapt:   # slowly speed back up after a rate-limit episode
                    with self.tlock:
                        self.adapt[base] = max(0.0, self.adapt[base] * 0.97)
                return r
            try:
                ra = int(r.headers.get("Retry-After") or 0)
            except ValueError:
                ra = 0
            with self.tlock:
                self.adapt[base] = min(8.0, max(1.5, self.adapt.get(base, 0) * 2))
                self.pause[base] = max(self.pause.get(base, 0), time.time() + max(ra, 20 * (attempt + 1)))
                self.rate_limited[base] += 1
                self.consec429 += 1
                if base == "web.archive.org":
                    self.recent.append(1)
                hot = len(self.recent) >= 20 and sum(self.recent) >= 15   # >=75% of the last 20 fetches were 429
                if (self.consec429 >= 10 or hot) and not self.storm_until and base == "web.archive.org":
                    self.storm_until = time.time() + 900
                    self.next_probe = time.time() + 60
                    self.storms += 1
                    log("web.archive.org is answering most fetches with HTTP 429 - treating Wayback as rate-limited for up "
                        "to 15 min (one probe/min); remaining captures fail fast so the report still completes")
                    return r
            log(f"{base} rate-limited (429) - slowing to one request per {self.adapt[base]:.1f}s, pausing "
                f"{max(ra, 20 * (attempt + 1))}s")
        return r


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
_cdx_fails = {"n": 0, "open_until": 0.0, "trips": 0, "ok": 0}
_cdx_lock = threading.Lock()


def cdx(params, timeout=60, lane=None, attempts=3):
    """Wayback CDX query with retries.

    v4: archive.org answers 504 / connection resets in bursts (rate limiting), then recovers. v3 used a
    *permanent* circuit breaker, so one bad minute silently wiped every later Wayback lookup from the run - that is
    what dropped long-lived display names (e.g. a name that lasted 9-12 months) from some Deep reports. Now each
    query retries with backoff, and the breaker only *pauses* CDX for a cooldown, then lets traffic through again."""
    last = None
    for i in range(max(1, attempts)):
        with _cdx_lock:
            wait = _cdx_fails["open_until"] - time.time()
        if wait > 0:
            time.sleep(min(wait, 300))
        try:
            r = _cdx_get(params, timeout, lane)
            with _cdx_lock:
                _cdx_fails["n"] = 0
                _cdx_fails["ok"] += 1
            return r
        except Exception as e:
            last = e
            if "429" in str(e):
                continue   # _cdx_get already set a pause; next attempt waits it out
            with _cdx_lock:
                _cdx_fails["n"] += 1
                if _cdx_fails["n"] >= 5:
                    _cdx_fails["trips"] += 1
                    _cdx_fails["open_until"] = time.time() + min(20 * _cdx_fails["trips"], 90)
                    _cdx_fails["n"] = 0
                    if _cdx_fails["trips"] >= 6 and not _cdx_fails["ok"]:
                        CFG["wayback_offline"] = True
            if i + 1 < attempts:
                time.sleep(3 * (i + 1) ** 2)
    raise last


def wayback_health():
    with _cdx_lock:
        return {"breaker_trips": _cdx_fails["trips"], "successful_queries": _cdx_fails["ok"],
                "rate_limited_429": _cdx_fails.get("rate_limited", 0),
                "capture_fetch_429": HTTP.rate_limited.get("web.archive.org", 0),
                "capture_429_storms": HTTP.storms}


# v4: archive.org's CDX server allows roughly one query per second per IP and answers bursts with 429, then
# firewalls the IP for minutes. All CDX calls (every lane) now share ONE polite gate, never auto-retry a 429 inside
# urllib3, and a 429 pauses all CDX traffic for Retry-After (min 60 s) instead of hammering on.
_CDX_S = requests.Session()
_CDX_S.mount("https://", HTTPAdapter(max_retries=Retry(total=2, read=1, connect=3, backoff_factor=3,
                                                     status_forcelist=(502, 503, 504),
                                                     allowed_methods=frozenset(["GET"])), pool_maxsize=4))
_CDX_S.headers.update({"User-Agent": UA, "Accept-Language": "en"})
_CDX_GATE = {"sem": threading.Semaphore(2), "lock": threading.Lock(), "last": 0.0, "gap": 1.3}


def _cdx_get(params, timeout=60, lane=None):
    p = {"output": "json", **params}
    with _CDX_GATE["sem"]:
        with _CDX_GATE["lock"]:
            now = time.time()
            start = max(now, _CDX_GATE["last"] + _CDX_GATE["gap"], _cdx_fails.get("open_until", 0))
            _CDX_GATE["last"] = start
        if start > time.time():
            time.sleep(start - time.time())
        r = _CDX_S.get("https://web.archive.org/cdx/search/cdx", params=p, timeout=timeout)
    if r.status_code == 429:
        try:
            ra = int(r.headers.get("Retry-After") or 0)
        except ValueError:
            ra = 0
        with _cdx_lock:
            _cdx_fails["open_until"] = max(_cdx_fails["open_until"], time.time() + max(60, min(ra, 300)))
            _cdx_fails["rate_limited"] = _cdx_fails.get("rate_limited", 0) + 1
        log(f"archive.org CDX rate-limited (429) - pausing CDX queries {max(60, min(ra, 300))}s")
        raise RuntimeError("CDX HTTP 429 rate-limited (paused, will resume)")
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
    with _CDX_GATE["sem"]:
        with _CDX_GATE["lock"]:
            start = max(time.time(), _CDX_GATE["last"] + _CDX_GATE["gap"], _cdx_fails.get("open_until", 0))
            _CDX_GATE["last"] = start
        if start > time.time():
            time.sleep(min(start - time.time(), 300))
        r = _CDX_S.get("https://web.archive.org/cdx/search/cdx",
                       params={"url": url, "matchType": match, "showNumPages": "true"}, timeout=timeout)
    if r.status_code == 429:
        with _cdx_lock:
            _cdx_fails["open_until"] = max(_cdx_fails["open_until"], time.time() + 60)
            _cdx_fails["rate_limited"] = _cdx_fails.get("rate_limited", 0) + 1
    if r.status_code != 200:
        raise RuntimeError(f"CDX pages HTTP {r.status_code}")
    try:
        return int(r.text.strip() or 0)
    except ValueError:
        return 0


def wb_raw(ts, url, timeout=45, allow_redirects=True, attempts=2):
    u = f"https://web.archive.org/web/{ts}id_/{url}"
    last = None
    for i in range(attempts):
        try:
            r = HTTP.get(u, timeout=timeout, allow_redirects=allow_redirects)
            if r.status_code in (502, 503, 504, 520, 522, 524) and i + 1 < attempts:
                time.sleep(4)
                continue
            return r
        except requests.exceptions.ContentDecodingError:
            return HTTP.get(u, timeout=timeout, allow_redirects=allow_redirects, headers={"Accept-Encoding": "identity"})
        except Exception as e:
            last = e
            if i + 1 < attempts:
                time.sleep(4)
    raise last


def wb_link(ts, url):
    return f"https://web.archive.org/web/{ts}/{url}"


def wb_img(ts, url):
    return f"https://web.archive.org/web/{ts}im_/{url}"
