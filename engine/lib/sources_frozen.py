"""Frozen-handle techniques: recover the @handle an account used *at the moment it posted*.

When a tweet carries media, X stores the media's expanded URL as
    https://x.com/<handle-at-posting-time>/status/<tweet id>/photo/1
and never rewrites it after a rename. The public embed endpoint
(cdn.syndication.twimg.com/tweet-result) and api.fxtwitter.com both return that frozen URL,
while their author block shows the *current* handle and the permanent user id. One media tweet
therefore gives a dated, owner-verified (user id) username observation.

Tweet ids to test come from public Nitter front-ends (X search `from:` resolves by user id, so
it returns tweets posted under every former handle), the pinned tweet, and Wayback capture URLs.
"""
import math
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, unquote

from .net import HTTP, log, snowflake_date
from .parsers import SN

FROZEN_RX = re.compile(
    r"https?://(?:mobile\.)?(?:x|twitter)\.com/(" + SN + r")/status/(\d+)/(?:photo|video)/\d+",
    re.I,
)
NITTER_STATUS = "https://status.d420.de/api/v1/instances"
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

# politeness for new hosts
HTTP.min_gap.update({
    "cdn.syndication.twimg.com": 0.35,
    "status.d420.de": 1.0,
})
HTTP.conc.update({"cdn.syndication.twimg.com": 3})


def _b36(x):
    """JS Number.prototype.toString(36) for a positive float (embed token)."""
    ip, fp = int(x), x - int(x)
    digs = "0123456789abcdefghijklmnopqrstuvwxyz"
    s = ""
    while ip:
        ip, r = divmod(ip, 36)
        s = digs[r] + s
    s = s or "0"
    if fp:
        s += "."
        for _ in range(12):
            fp *= 36
            d = int(fp)
            s += digs[d]
            fp -= d
            if fp <= 0:
                break
    return s


def embed_token(tid):
    return re.sub(r"(0+|\.)", "", _b36((int(tid) / 1e15) * math.pi))


# ------------------------------------------------------------------ tweet fetchers
def fetch_tweet(tid):
    """Normalised dict: author id/handle (current), created, text, frozen handles, mentions."""
    out = None
    try:
        r = HTTP.get(
            f"https://cdn.syndication.twimg.com/tweet-result?id={tid}&token={embed_token(tid)}&lang=en",
            timeout=20)
        if r.status_code == 200 and r.text.strip().startswith("{"):
            j = r.json()
            if j.get("__typename") != "TweetTombstone" and j.get("user"):
                u = j["user"]
                frozen = set()  # handles whose expanded_url status id == this tweet
                tid_s = str(j.get("id_str") or tid)
                for m in (j.get("mediaDetails") or []):
                    m_ = FROZEN_RX.search(m.get("expanded_url") or "")
                    if m_ and m_.group(2) == tid_s:
                        frozen.add(m_.group(1))
                for e in ((j.get("entities") or {}).get("media") or []):
                    m_ = FROZEN_RX.search(e.get("expanded_url") or "")
                    if m_ and m_.group(2) == tid_s:
                        frozen.add(m_.group(1))
                mentions = []
                for m in ((j.get("entities") or {}).get("user_mentions") or []):
                    if m.get("id_str") and m.get("screen_name"):
                        mentions.append({"id": str(m["id_str"]), "screen_name": m["screen_name"]})
                out = {
                    "id": str(j.get("id_str") or tid),
                    "created": (j.get("created_at") or "")[:10] or snowflake_date(tid),
                    "text": j.get("text") or "",
                    "author_id": str(u.get("id_str") or u.get("id") or ""),
                    "author_handle": u.get("screen_name"),
                    "author_name": u.get("name"),
                    "frozen": sorted(frozen),
                    "mentions": mentions,
                    "source": "syndication",
                    "url": f"https://x.com/i/status/{tid}",
                }
    except Exception:
        pass
    if out and out["frozen"]:
        return out
    try:
        r = HTTP.get(f"https://api.fxtwitter.com/status/{tid}", timeout=20)
        if r.status_code != 200:
            return out
        tw = r.json().get("tweet") or {}
        a = tw.get("author") or {}
        frozen = set(out["frozen"] if out else [])
        tid_s = str(tw.get("id") or tid)
        for f in ((tw.get("raw_text") or {}).get("facets") or []):
            m_ = FROZEN_RX.search(f.get("replacement") or f.get("original") or "")
            if m_ and m_.group(2) == tid_s:
                frozen.add(m_.group(1))
        mentions = list(out["mentions"] if out else [])
        created = (tw.get("created_timestamp") and
                   datetime.fromtimestamp(int(tw["created_timestamp"]), tz=timezone.utc).strftime("%Y-%m-%d")
                   ) or (tw.get("date") or "")[:10] or snowflake_date(tid)
        out = {
            "id": str(tw.get("id") or tid),
            "created": created,
            "text": tw.get("text") or "",
            "author_id": str(a.get("id") or ""),
            "author_handle": a.get("screen_name"),
            "author_name": a.get("name"),
            "frozen": sorted(frozen),
            "mentions": mentions,
            "source": "fxtwitter" if not out else out["source"] + "+fxtwitter",
            "url": tw.get("url") or f"https://x.com/i/status/{tid}",
        }
    except Exception:
        pass
    return out


# ------------------------------------------------------------------ Nitter discovery
_nitter_cache = {"hosts": [], "t": 0}


def nitter_hosts(limit=6):
    """Prefer instances that actually answer search; status.d420.de 'healthy' is not enough."""
    import time
    if _nitter_cache["hosts"] and time.time() - _nitter_cache["t"] < 600:
        return _nitter_cache["hosts"][:limit]
    # Known-good first (verified 2026-10-27); then status feed; then other fallbacks.
    preferred = [
        "https://nitter.mie.foundation",
        "https://nitter.mizuki.st",
        "https://nitter.tiekoetter.com",
        "https://nitter.click",
        "https://nitter.pp.ua",
        "https://nitter.kareem.one",
    ]
    listed = []
    try:
        r = HTTP.get(NITTER_STATUS, timeout=12)
        if r.status_code == 200:
            for h in (r.json().get("hosts") or []):
                if h.get("healthy") and not h.get("is_bad_host") and h.get("url"):
                    listed.append(h["url"].rstrip("/"))
    except Exception:
        pass
    ordered = []
    for h in preferred + listed:
        if h not in ordered:
            ordered.append(h)
    # Quick probe: keep hosts that return a timeline for a tiny search (budget ~2s each, stop at `limit`)
    good = []
    for base in ordered:
        if len(good) >= min(2, limit):  # one solid search host is enough to start
            break
        try:
            rr = HTTP.get(f"{base}/search?f=tweets&q=from%3Ajack+filter%3Amedia", timeout=8)
            if rr.status_code == 200 and "timeline-item" in rr.text:
                good.append(base)
                continue
            # RSS-only hosts still help for seed ids sometimes
            rr2 = HTTP.get(f"{base}/jack/rss", timeout=10)
            if rr2.status_code == 200 and "<item>" in rr2.text and base not in good:
                good.append(base)
        except Exception:
            continue
    if not good:
        good = preferred[:limit]
    else:
        for h in preferred:
            if h not in good:
                good.append(h)
            if len(good) >= limit:
                break
    _nitter_cache["hosts"] = good
    _nitter_cache["t"] = time.time()
    log(f"nitter: using {', '.join(good)}")
    return good[:limit]


def _parse_nitter_date(title):
    # "Sep 8, 2024 · 12:25 PM UTC"
    m = re.match(r"([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})", title or "")
    if not m:
        return None
    return f"{m.group(3)}-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"


def nitter_search(query, pages=2, host=None):
    """Yield {id, handle, date, url} from a Nitter search page. `from:` resolves by user id."""
    hosts = [host] if host else nitter_hosts()
    seen = set()
    for base in hosts:
        got = 0
        cursor = None
        for _ in range(pages):
            url = f"{base}/search?f=tweets&q={quote(query)}"
            if cursor:
                url += f"&cursor={quote(cursor)}"
            try:
                r = HTTP.get(url, timeout=25)
            except Exception:
                break
            if r.status_code != 200 or "timeline-item" not in r.text:
                break
            # Parse each timeline-item block independently (more robust than one big regex)
            page_got = 0
            for block in re.split(r'class="timeline-item', r.text)[1:]:
                hm = re.search(r'href="/(' + SN + r')/status/(\d+)#m"', block)
                dm = re.search(r'tweet-date"><a[^>]*title="([^"]+)"', block)
                if not hm:
                    continue
                tid = hm.group(2)
                if tid in seen:
                    continue
                seen.add(tid)
                got += 1
                page_got += 1
                yield {"id": tid, "handle": hm.group(1),
                       "date": _parse_nitter_date(dm.group(1) if dm else None),
                       "url": f"https://x.com/{hm.group(1)}/status/{tid}", "nitter": base}
            cm = re.search(r'cursor=([^"&\s]+)', r.text)
            if not cm or page_got == 0:
                break
            cursor = unquote(cm.group(1))
        if got:
            return  # one working host is enough


def seed_windows(created_iso, n=8):
    """Date windows that sample an account's life evenly (for `from:handle until:` probes)."""
    try:
        start = datetime.strptime(created_iso[:10], "%Y-%m-%d").date()
    except Exception:
        start = date(2024, 1, 1)
    end = datetime.now(timezone.utc).date()
    days = max((end - start).days, 1)
    out = []
    for i in range(n):
        # evenly spaced until-dates, plus a few early windows
        d = start + timedelta(days=int(days * (i + 1) / (n + 1)))
        out.append(d.isoformat())
    # always include ~1 month after creation and today-ish midpoints
    out.append((start + timedelta(days=45)).isoformat())
    out.append((start + timedelta(days=150)).isoformat())
    out.append((start + timedelta(days=300)).isoformat())
    return sorted(set(d for d in out if start <= date.fromisoformat(d) <= end))


# ------------------------------------------------------------------ orchestration
def discover_tweet_ids(R, budget, deep=False):
    """Collect candidate tweet ids that may carry a frozen former handle."""
    ids = []
    seen = set()

    def add(tid, why):
        tid = str(tid)
        if tid.isdigit() and tid not in seen:
            seen.add(tid)
            ids.append((tid, why))

    # pinned + any pinned from observations
    pin = None
    for o in R.obs:
        p = o["fields"].get("pinned_tweet_id")
        if p:
            pin = p
            break
    if pin:
        add(pin, "pinned")

    # tweet ids already known from Wayback capture URLs
    for o in R.obs:
        ev = o.get("evidence") or ""
        m = re.search(r"/status(?:es)?/(\d+)", ev)
        if m:
            add(m.group(1), "wayback-url")

    if not R.current or not budget.ok(40):
        return ids
    if (R.raw.get("life_scan") or {}).get("host"):
        return ids[:30]  # v3: the life-span scan already walked the whole timeline via Nitter

    created = R.live.get("joined")
    for o in R.obs:
        if o["fields"].get("created_at"):
            created = created or o["fields"]["created_at"]
            break
    created = (created or "")[:10] or (snowflake_date(R.user_id) if R.user_id else "2024-01-01")

    windows = seed_windows(created, n=10 if deep else 5)
    # early-life windows matter most for pre-archive renames
    queries = [f"from:{R.current} until:{w} filter:media" for w in windows[: (8 if deep else 4)]]
    queries += [f"from:{R.current} until:{w}" for w in windows[:3]]
    if deep:
        queries.append(f"from:{R.current} filter:media")

    log(f"frozen-handle: Nitter-searching {len(queries)} windows for @{R.current} ...")
    for q in queries:
        if not budget.ok(30):
            break
        for hit in nitter_search(q, pages=2 if deep else 1):
            add(hit["id"], f"nitter:{q[:40]}")
            # Nitter still shows the *current* handle in the card; the frozen one is in the media URL
        if len(ids) >= (80 if deep else 35):
            break
    return ids


def harvest_frozen(R, budget, deep=False):
    """Main entry: find media tweets, read frozen media URLs, emit username observations."""
    name = "frozen-handle (media URL / syndication)"
    try:
        cands = discover_tweet_ids(R, budget, deep=deep)
        if not cands:
            R.note("frozen-handle: no candidate tweet ids (Nitter down or account has no public media?)")
            R.fail(name, "no candidate tweets")
            return []
        log(f"frozen-handle: probing {len(cands)} tweet(s) via syndication / fxtwitter ...")
        found = {}  # handle_lower -> {handle, first, last, evidence, tweets}
        checked = 0
        for tid, why in cands:
            if not budget.ok(20):
                break
            tw = fetch_tweet(tid)
            checked += 1
            if not tw:
                continue
            # owner-verify: author must be our user id
            if R.user_id and tw["author_id"] and tw["author_id"] != R.user_id:
                # still useful: if WE are mentioned under an old handle in someone else's reply
                for m in tw.get("mentions") or []:
                    if m["id"] == R.user_id:
                        h = m["screen_name"]
                        slot = found.setdefault(h.lower(), {"handle": h, "first": tw["created"], "last": tw["created"],
                                                            "evidence": tw["url"], "tweets": [], "how": "mention-entity"})
                        slot["first"] = min(slot["first"], tw["created"]) if slot["first"] else tw["created"]
                        slot["last"] = max(slot["last"], tw["created"]) if slot["last"] else tw["created"]
                        slot["tweets"].append(tid)
                        R.observe(tw["created"] or snowflake_date(tid), {"screen_name": h},
                                  "frozen-handle:mention-entity", tw["url"], verified=True, confidence="medium")
                continue
            for h in tw.get("frozen") or []:
                if not re.fullmatch(SN, h):
                    continue
                slot = found.setdefault(h.lower(), {"handle": h, "first": tw["created"], "last": tw["created"],
                                                    "evidence": tw["url"], "tweets": [], "how": "media-expanded-url"})
                if tw["created"]:
                    slot["first"] = min(filter(None, [slot["first"], tw["created"]]))
                    slot["last"] = max(filter(None, [slot["last"], tw["created"]]))
                slot["tweets"].append(tid)
                slot["evidence"] = tw["url"]
                R.observe(tw["created"] or snowflake_date(tid),
                          {"screen_name": h},  # v3: never attach today's display name to an old date
                          f"frozen-handle:{tw['source']}", tw["url"], verified=True, confidence="high")
        # record summary
        with R.lock:
            R.raw["frozen_handles"] = {
                "life_scan_handles": (R.raw.get("life_scan") or {}).get("handles", {}),
                "checked_tweets": checked,
                "handles": {k: {"handle": v["handle"], "first_seen": v["first"], "last_seen": v["last"],
                                "how": v["how"], "tweet_ids": v["tweets"][:12], "evidence": v["evidence"]}
                            for k, v in found.items()},
            }
        if found:
            R.ok(name)
            news = [v["handle"] for v in found.values()
                    if v["handle"].lower() not in {h.lower() for h in R.handles_seen().values()}]
            # also count current if it was the only one — still a success (proves technique)
            log(f"frozen-handle: {len(found)} handle(s) recovered from media URLs: "
                + ", ".join("@" + v["handle"] for v in found.values()))
            if news:
                R.note(f"frozen-handle recovered former username(s): "
                       + ", ".join("@" + h for h in news)
                       + " (X freezes the posting-time handle inside media expanded_url; never rewritten after a rename)")
            return list(found.values())
        R.note("frozen-handle: checked media tweets but every frozen URL already matched the current handle "
               "(account may never have renamed, or only renamed before its first media post)")
        R.ok(name + " (no rename in sampled media)")
        return []
    except Exception as e:
        R.fail(name, e)
        return []
