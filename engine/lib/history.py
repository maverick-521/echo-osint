"""v4 profile-history completeness: month-by-month coverage, gap filling, and a cross-run evidence cache.

Why this exists: long-lived display names / bios / avatars (ones that lasted many months) were sometimes missing
from Deep reports. Root causes found in v3:
  * a permanent Wayback circuit breaker - one burst of 504s and every later CDX lookup in the run was skipped;
  * the raw-paged tweet-capture index was fragile (one failed page query -> "zero captures" for a whole handle);
  * per-handle capture sampling thinned out months when an account had many active months;
  * nothing re-checked *which months* had no profile evidence, so whole eras could silently go unsampled;
  * every run started from scratch, so a flaky archive.org on the day of a run erased earlier proven history.
This module fixes the last three; net.py / sources.py fix the first two. Nothing here is account-specific."""
import glob
import json
import os
import re
from datetime import date, datetime, timezone

from .net import cdx, log, snowflake_date
from .sources import cdx_status_daily

PROFILE_FIELDS = ("name", "description", "avatar")
# Sources whose observations may carry name/bio/avatar (they read the profile as it was on that date).
PROFILE_SOURCES = ("wayback:", "live:", "archive.today")
CACHE_SOURCES = ("wayback:", "life-scan:", "frozen-handle:", "memory.lol")


def _month(d):
    return (d or "")[:7]


def months_between(start, end):
    y, m = start.year, start.month
    out = []
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def account_start(R):
    created = R.live.get("joined") or ""
    for fmt, cut in (("%Y-%m-%d", 10), ("%a %b %d %H:%M:%S %z %Y", None)):
        try:
            return datetime.strptime(created[:cut] if cut else created, fmt).date()
        except ValueError:
            pass
    sd = snowflake_date(R.user_id) if R.user_id else None
    if sd:
        try:
            return datetime.strptime(sd[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    ds = sorted(o["date"] for o in R.obs if o.get("verified") and o.get("date"))
    return datetime.strptime(ds[0][:10], "%Y-%m-%d").date() if ds else None


def coverage(R):
    """month -> set of profile fields with an owner-verified dated observation in that month."""
    cov = {}
    for o in R.obs:
        if not o.get("verified") or not o["source"].startswith(PROFILE_SOURCES):
            continue
        fs = {k for k in PROFILE_FIELDS if k in o["fields"]}
        if fs:
            cov.setdefault(_month(o["date"]), set()).update(fs)
    return cov


def active_months(R, pool):
    """Months in which the account demonstrably existed and was archived (tweet/profile captures, sightings)."""
    act = set()
    for (ts, _), c in pool.items():
        if c.get("kind") in ("status", "profile", "idpage", "json"):
            act.add(f"{ts[:4]}-{ts[4:6]}")
    for o in R.obs:
        if o.get("verified"):
            act.add(_month(o["date"]))
    return act


def coverage_report(R, pool):
    start = account_start(R)
    if not start:
        return {}
    end = datetime.now(timezone.utc).date()
    allm = months_between(start, end)
    cov = coverage(R)
    act = active_months(R, pool)
    named = [m for m in allm if "name" in cov.get(m, ())]
    missing_active = [m for m in allm if m in act and "name" not in cov.get(m, ())]
    return {"account_start": start.isoformat(), "months_total": len(allm), "months_with_profile_evidence": len(named),
            "months_archived_but_unsampled": missing_active,
            "months_without_any_archive": [m for m in allm if m not in act and m not in cov]}


def _handle_for_month(R, m):
    """Handles that were (by verified evidence) in use nearest to month m - try those first."""
    rows = []
    for o in R.obs:
        sn = o["fields"].get("screen_name")
        if sn and o.get("verified"):
            rows.append((abs(_mi(_month(o["date"])) - _mi(m)), sn))
    rows.sort()
    out = []
    for _, sn in rows:
        if sn.lower() not in [x.lower() for x in out]:
            out.append(sn)
    return out


def _mi(m):
    try:
        return int(m[:4]) * 12 + int(m[5:7])
    except (ValueError, TypeError):
        return 0


def gap_fill(hunt, max_queries, per_month=1):
    """For every month of the account's life with no verified name/bio/avatar evidence:
    1) analyse an already-inventoried capture from that month; 2) else run a month-bounded day-collapsed CDX query
    for the handle(s) in use around then. Returns number of captures queued."""
    R, pool, done = hunt.R, hunt.pool, hunt.done
    rep = coverage_report(R, pool)
    if not rep:
        return 0
    start = datetime.strptime(rep["account_start"], "%Y-%m-%d").date()
    allm = months_between(start, datetime.now(timezone.utc).date())
    cov = coverage(R)
    gaps = [m for m in allm if "name" not in cov.get(m, ())]
    if not gaps:
        return 0
    by_m = {}
    for k, c in pool.items():
        if c.get("kind") in ("status", "profile", "idpage", "json") and c.get("statuscode") in ("200", "-"):
            by_m.setdefault(f"{k[0][:4]}-{k[0][4:6]}", []).append(c)
    queue, queried = [], 0
    for m in gaps:
        cands = [c for c in by_m.get(m, []) if (c["timestamp"], c["original"]) not in done]
        if not cands and queried < max_queries and hunt.B.ok(90):
            frm, to = m.replace("-", ""), m.replace("-", "")
            for h in _handle_for_month(R, m)[:2]:
                if queried >= max_queries:
                    break
                for host in ("twitter.com", "x.com"):
                    sink = []
                    queried += 1
                    got = cdx_status_daily(R, h, host, sink, frm=frm, to=to, limit=40)
                    for cap in sink:
                        pool.setdefault((cap["timestamp"], cap["original"]), cap)
                    cands += [c for c in sink if (c["timestamp"], c["original"]) not in done]
                    if got:
                        break
                if cands:
                    break
        if cands:
            js = [c for c in cands if "json" in (c.get("mimetype") or "")] or cands
            js.sort(key=lambda c: c["timestamp"])
            picks = [js[len(js) // 2]] if per_month == 1 else [js[0], js[-1]]
            queue += [p for p in picks if p not in queue]
    if queue:
        log(f"gap-fill: {len(gaps)} month(s) had no dated display-name evidence; sampling {len(queue)} capture(s)"
            f" ({queried} month-bounded CDX queries)")
        hunt.analyse(queue, "monthly gap-fill")
    return len(queue)


# ------------------------------------------------------------------ cross-run evidence cache
def cache_dir():
    d = os.environ.get("ECHO_CACHE") or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "cache")
    os.makedirs(d, exist_ok=True)
    return d


def _reports_dirs(extra=None):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ds = [os.path.join(root, "reports"), os.path.join(root, "engine", "out")]
    if extra:
        ds.append(extra)
    return [d for d in ds if os.path.isdir(d)]


def _cacheable(o):
    return o.get("verified") and str(o.get("source", "")).startswith(CACHE_SOURCES) and o.get("evidence")


def _clean(o):
    f = dict(o["fields"])
    if not o["source"].startswith(PROFILE_SOURCES):
        f = {k: v for k, v in f.items() if k == "screen_name"}   # tweet-side sightings never carry profile fields
    return {"date": o["date"], "ts": o.get("ts") or o["date"].replace("-", ""), "source": o["source"],
            "evidence": o["evidence"], "verified": True, "confidence": o.get("confidence") or "high", "fields": f}


def load_prior(R, extra_reports=None):
    """Merge owner-verified archive evidence proven by earlier ECHO runs of the SAME permanent user id.
    Each carried observation keeps its original archive URL and is flagged `carried_over`."""
    if not R.user_id:
        return 0
    rows = []
    p = os.path.join(cache_dir(), f"x-{R.user_id}.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                rows += json.load(f).get("observations", [])
        except Exception:
            pass
    for d in _reports_dirs(extra_reports):
        for fp in glob.glob(os.path.join(d, "*", "*.json")) + glob.glob(os.path.join(d, "*.json")):
            if fp.endswith(("-dossier.json", "job.json")) or os.path.getsize(fp) > 60_000_000:
                continue
            try:
                with open(fp, encoding="utf-8") as f:
                    D = json.load(f)
            except Exception:
                continue
            if not isinstance(D, dict) or str((D.get("account") or {}).get("user_id")) != R.user_id:
                continue
            rows += [o for o in D.get("observations") or [] if _cacheable(o)]
    have = {(o["ts"], o["evidence"], o["source"]) for o in R.obs}
    n = 0
    with R.lock:
        for o in rows:
            if not _cacheable(o):
                continue
            c = _clean(o)
            if not c["fields"]:
                continue
            k = (c["ts"], c["evidence"], c["source"])
            if k in have:
                continue
            have.add(k)
            c["carried_over"] = True
            R.obs.append(c)
            n += 1
    if n:
        R.note(f"{n} owner-verified archive observation(s) proven by earlier ECHO runs of this same user id were merged "
               "(each keeps its original Wayback/tweet evidence link) - so a flaky archive.org on one day cannot erase "
               "history found before")
        log(f"cross-run cache: merged {n} earlier verified observations for id {R.user_id}")
    return n


def save_cache(R):
    if not R.user_id:
        return
    p = os.path.join(cache_dir(), f"x-{R.user_id}.json")
    rows = {}
    for o in R.obs:
        if _cacheable(o):
            c = _clean(o)
            if c["fields"]:
                rows[(c["ts"], c["evidence"], c["source"])] = c
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"user_id": R.user_id, "saved": datetime.now(timezone.utc).isoformat(),
                       "observations": sorted(rows.values(), key=lambda o: o["ts"])}, f, ensure_ascii=False)
    except Exception as e:
        log(f"cache save failed: {e}")
