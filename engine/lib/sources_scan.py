"""Life-span frozen-handle scan (v3).

Why this exists: the v2 frozen-handle harvest only sampled a handful of `until:` dates spread
evenly from the account's creation date. For accounts that were quiet for long stretches, most
samples landed in empty periods and the rest clustered on a few days, so the username timeline
"stopped" around 2025 (e.g. a gap 2025-03 -> 2026-09 even though public media tweets existed
for every month in between).

This module walks the account's whole life in calendar windows using X search through a public
Nitter front-end (`from:` resolves by user id, so it returns tweets posted under every former
handle), reads the posting-time handle frozen inside each media tweet's expanded_url via the
public embed endpoint, then bisects every window where the handle changes until the change is
pinned to a day or two. It also reads posting-time @mentions of the target written by *other*
accounts (reply parents / mention entities), owner-verified by the permanent user id.

Only public, logged-out endpoints are used.
"""
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from .net import HTTP, log, snowflake_date
from .parsers import SN
from . import sources_frozen as SF

HTTP.min_gap.update({h.replace("https://", ""): 0.8 for h in (
    "https://nitter.mie.foundation", "https://nitter.mizuki.st", "https://nitter.tiekoetter.com",
    "https://nitter.click", "https://nitter.pp.ua", "https://nitter.kareem.one", "https://xcancel.com",
    "https://nitter.poast.org", "https://nitter.privacyredirect.com", "https://nitter.net")})

LEVELS = {  # max windows over the account's life, seconds for the scan, bisection steps per change
    "fast": dict(windows=10, seconds=110, bisect=4, per_window=2, early_pages=1, mentions=False),
    "thorough": dict(windows=30, seconds=300, bisect=7, per_window=2, early_pages=2, mentions=True),
    "deep": dict(windows=60, seconds=560, bisect=10, per_window=3, early_pages=3, mentions=True),
}

EMBED = "https://cdn.syndication.twimg.com/tweet-result?id={tid}&token={tok}&lang=en"


# ------------------------------------------------------------------ helpers
def _d(s):
    return date.fromisoformat(s[:10])


def _months(start, end, maxw):
    """Calendar windows [since, until) covering start..end, at most `maxw` of them."""
    first = date(start.year, start.month, 1)
    n_months = (end.year - first.year) * 12 + end.month - first.month + 1
    step = max(1, -(-n_months // maxw))  # ceil
    out, cur = [], first
    while cur <= end:
        m = cur.month - 1 + step
        nxt = date(cur.year + m // 12, m % 12 + 1, 1)
        out.append((cur.isoformat(), min(nxt, end + timedelta(days=1)).isoformat()))
        cur = nxt
    return out


def pick_search_host(handle, budget):
    """First Nitter host whose search actually returns this account's tweets."""
    for base in SF.nitter_hosts(limit=8):
        if not budget.ok(20):
            break
        hits = list(SF.nitter_search(f"from:{handle}", pages=1, host=base))
        if hits:
            return base
    return None


def fetch_embed(tid):
    try:
        r = HTTP.get(EMBED.format(tid=tid, tok=SF.embed_token(tid)), timeout=20)
        if r.status_code == 200 and r.text.lstrip().startswith("{"):
            j = r.json()
            if j.get("__typename") != "TweetTombstone" and j.get("user"):
                return j
    except Exception:
        pass
    return None


def _created(j, tid=None):
    c = (j or {}).get("created_at") or ""
    return c[:10] if re.match(r"\d{4}-\d\d-\d\d", c) else snowflake_date(tid or (j or {}).get("id_str"))


def _mentions_of(j, uid):
    """Posting-time @handle of `uid` as written inside tweet j (text is never rewritten on rename)."""
    out = []
    if not j or not uid:
        return out
    text = j.get("text") or ""
    for m in ((j.get("entities") or {}).get("user_mentions") or []):
        if str(m.get("id_str")) != uid:
            continue
        sn = m.get("screen_name")
        ind = m.get("indices") or []
        if len(ind) == 2:
            seg = text[ind[0]:ind[1]]
            mm = re.fullmatch(r"@(" + SN + ")", seg or "")
            if mm:
                sn = mm.group(1)
        if sn and re.fullmatch(SN, sn):
            out.append(sn)
    return out


def analyse_tweet(R, tid, why):
    """-> list of (date, handle, how, evidence) facts about the target found in this tweet."""
    j = fetch_embed(tid)
    facts = []
    uid = R.user_id
    if j:
        tid_s = str(j.get("id_str") or tid)
        author = str((j.get("user") or {}).get("id_str") or "")
        d = _created(j, tid)
        if not uid or author == uid:
            for m in (j.get("mediaDetails") or []) + ((j.get("entities") or {}).get("media") or []):
                fm = SF.FROZEN_RX.search(m.get("expanded_url") or "")
                if fm and fm.group(2) == tid_s:
                    facts.append((d, fm.group(1), "media-expanded-url", f"https://x.com/i/status/{tid_s}"))
                    break
        else:
            for sn in _mentions_of(j, uid):
                facts.append((d, sn, "mention-by-other-account", f"https://x.com/i/status/{tid_s}"))
        # reply parent written by someone else that addressed the target
        p = j.get("parent")
        if p and str((p.get("user") or {}).get("id_str") or "") != uid:
            for sn in _mentions_of(p, uid):
                facts.append((_created(p), sn, "mention-in-reply-parent", f"https://x.com/i/status/{p.get('id_str')}"))
        # quoted tweet by someone else that mentioned the target
        q = j.get("quoted_tweet")
        if q and str((q.get("user") or {}).get("id_str") or "") != uid:
            for sn in _mentions_of(q, uid):
                facts.append((_created(q), sn, "mention-in-quoted-tweet", f"https://x.com/i/status/{q.get('id_str')}"))
        return facts, d
    # embed endpoint failed -> fxtwitter fallback (frozen media URL only)
    tw = SF.fetch_tweet(tid)
    if tw and (not uid or tw.get("author_id") == uid):
        for h in tw.get("frozen") or []:
            facts.append((tw["created"], h, "media-expanded-url", tw["url"]))
        return facts, tw.get("created")
    return facts, None


# ------------------------------------------------------------------ main scan
class LifeScan:
    def __init__(self, R, budget, level="thorough"):
        self.R, self.B = R, budget
        self.cfg = LEVELS.get(level, LEVELS["thorough"])
        self.deadline_left = None
        self.host = None
        self.samples = []      # {date, handle, how, evidence}
        self.windows = []      # {since, until, media, any, sampled}
        self.changes = []
        self.lock = threading.Lock()
        self.checked = set()
        import time
        self.t_end = time.time() + self.cfg["seconds"]

    def alive(self, need=10):
        import time
        return self.B.ok(need) and time.time() < self.t_end

    def search(self, q, pages=1):
        if not self.alive(15):
            return []
        try:
            return list(SF.nitter_search(q, pages=pages, host=self.host))
        except Exception:
            return []

    def probe(self, hits, n):
        """Check newest, oldest (and middle) tweet of a result list."""
        if not hits:
            return []
        idx = [0, len(hits) - 1] + ([len(hits) // 2] if n >= 3 else [])
        out = []
        for i in dict.fromkeys(idx):
            tid = hits[i]["id"]
            if tid in self.checked or not self.alive(10):
                continue
            self.checked.add(tid)
            facts, _ = analyse_tweet(self.R, tid, "scan")
            for d, h, how, ev in facts:
                if d:
                    s = {"date": d, "handle": h, "how": how, "evidence": ev}
                    with self.lock:
                        self.samples.append(s)
                    out.append(s)
        return out

    def scan_window(self, w):
        h = self.R.current
        media = self.search(f"from:{h} since:{w['since']} until:{w['until']} filter:media")
        w["media"] = len(media)
        if media:
            w["sampled"] = len(self.probe(media, self.cfg["per_window"]))
        else:
            anyt = self.search(f"from:{h} since:{w['since']} until:{w['until']}")
            w["any"] = len(anyt)
            if anyt and self.cfg["mentions"]:
                # replies quote the other person's tweet as `parent`: that text addresses us by our handle then
                w["sampled"] = len(self.probe(anyt, 2))
        return w

    def earliest(self):
        """Walk back to the oldest public tweets of the first active window."""
        act = [w for w in self.windows if w.get("media") or w.get("any")]
        if not act:
            return
        w = act[0]
        for filt in (" filter:media", ""):
            hits = self.search(f"from:{self.R.current} since:{w['since']} until:{w['until']}{filt}",
                               pages=self.cfg["early_pages"])
            if hits:
                self.probe(hits[-2:], 2)
                self.first_tweet = min(filter(None, [x.get("date") for x in hits] + [w["until"]]))
                break

    def bisect(self):
        """Tighten every A -> B handle switch to a day or two."""
        for _ in range(12):
            seq = sorted(self.samples, key=lambda s: s["date"])
            pairs = []
            for a, b in zip(seq, seq[1:]):
                if a["handle"].lower() != b["handle"].lower() and (_d(b["date"]) - _d(a["date"])).days > 2:
                    pairs.append((a, b))
            pairs = [p for p in pairs if (p[0]["date"], p[1]["date"]) not in self._dead]
            if not pairs or not self.alive(30):
                return
            for a, b in pairs:
                if not self.alive(30):
                    return
                lo, hi = _d(a["date"]), _d(b["date"])
                mid = lo + (hi - lo) / 2
                got = []
                # left half then right half; any media tweet in between moves one of the bounds
                for s, u in (((lo + timedelta(days=1)).isoformat(), (mid + timedelta(days=1)).isoformat()),
                             ((mid + timedelta(days=1)).isoformat(), hi.isoformat())):
                    if s >= u:
                        continue
                    hits = self.search(f"from:{self.R.current} since:{s} until:{u} filter:media")
                    found = self.probe(hits, 2)
                    if not found and self.cfg["mentions"]:
                        # no media in this half: replies carry the other person's tweet that @-addressed us
                        found = self.probe(self.search(f"from:{self.R.current} since:{s} until:{u} filter:replies"), 2)
                    got += found
                if not got:
                    self._dead.add((a["date"], b["date"]))
                self.steps += 1
                if self.steps >= self.cfg["bisect"] * 4:
                    return

    def run(self):
        R = self.R
        if not R.current:
            R.note("life-span scan skipped: no live handle (suspended/deleted accounts cannot be searched)")
            return
        self._dead, self.steps, self.first_tweet = set(), 0, None
        created = (R.live.get("joined") or "")
        try:
            start = datetime.strptime(created[:10], "%Y-%m-%d").date()
        except ValueError:
            try:
                start = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y").date()
            except ValueError:
                sd = snowflake_date(R.user_id) if R.user_id else None
                start = _d(sd) if sd else date(2010, 1, 1)
        end = datetime.now(timezone.utc).date()
        self.host = pick_search_host(R.current, self.B)
        if not self.host:
            R.fail("life-span scan (Nitter search)", "no public Nitter instance returned search results right now")
            R.note("life-span scan could not run: every public Nitter search front-end was down/blocked. "
                   "Re-run later; Wayback + pinned/archived tweet ids were still checked.")
            return
        self.windows = [{"since": s, "until": u, "media": 0, "any": 0, "sampled": 0}
                        for s, u in _months(start, end, self.cfg["windows"])]
        log(f"life-span scan: {len(self.windows)} windows {start} -> {end} via {self.host} ...")
        with ThreadPoolExecutor(2) as ex:
            list(ex.map(self.scan_window, reversed(self.windows)))
        self.earliest()
        self.bisect()
        self.emit(start)

    def emit(self, start):
        R = self.R
        seen = {}
        for s in self.samples:
            R.observe(s["date"], {"screen_name": s["handle"]}, f"life-scan:{s['how']}", s["evidence"],
                      verified=True, confidence="high" if s["how"] == "media-expanded-url" else "medium")
            k = s["handle"].lower()
            v = seen.setdefault(k, {"handle": s["handle"], "first_seen": s["date"], "last_seen": s["date"],
                                    "samples": 0, "how": set(), "evidence_first": s["evidence"],
                                    "evidence_last": s["evidence"]})
            v["samples"] += 1
            v["how"].add(s["how"])
            if s["date"] < v["first_seen"]:
                v["first_seen"], v["evidence_first"] = s["date"], s["evidence"]
            if s["date"] > v["last_seen"]:
                v["last_seen"], v["evidence_last"] = s["date"], s["evidence"]
        seq = sorted(self.samples, key=lambda s: s["date"])
        changes = []
        for a, b in zip(seq, seq[1:]):
            if a["handle"].lower() != b["handle"].lower():
                changes.append({"from": a["handle"], "to": b["handle"], "after": a["date"], "before": b["date"],
                                "evidence": [a["evidence"], b["evidence"]]})
        active = [w for w in self.windows if w.get("media") or w.get("any")]
        empty_lead = None
        if active and self.windows and active[0]["since"] > self.windows[0]["since"]:
            empty_lead = {"from": start.isoformat(), "to": active[0]["since"]}
        with R.lock:
            R.raw["life_scan"] = {
                "host": self.host, "account_start": start.isoformat(), "windows": self.windows,
                "tweets_checked": len(self.checked), "first_public_tweet_near": self.first_tweet,
                "handles": {k: dict(v, how=sorted(v["how"])) for k, v in seen.items()},
                "changes": changes, "no_public_tweets_window": empty_lead,
                "bisect_steps": self.steps,
            }
        if seen:
            R.ok("life-span frozen-handle scan")
        if empty_lead:
            R.note(f"life-span scan: no public tweets were found between account creation ({empty_lead['from']}) "
                   f"and {empty_lead['to']} - tweets from that period were deleted or never existed, so no "
                   "tweet-based source can see handles used then (only memory.lol's gated index / X's login-only "
                   "About panel could)")
        log(f"life-span scan: {len(self.checked)} tweets checked, handles: "
            + ", ".join(f"@{v['handle']} ({v['first_seen']}→{v['last_seen']})" for v in seen.values())
            + (f"; {len(changes)} change(s)" if changes else ""))


def run_life_scan(R, budget, level="thorough"):
    try:
        LifeScan(R, budget, level).run()
    except Exception as e:  # never kill the hunt
        R.fail("life-span scan", e)


def old_handle_mentions(R, budget, max_handles=4):
    """`to:<former handle>` searches inside the period that handle was ours -> more dated sightings
    (owner-verified: the mention entity must carry our permanent user id; handles get reused)."""
    scan = R.raw.get("life_scan") or {}
    host = scan.get("host")
    if not host or not R.user_id:
        return
    cur = (R.current or "").lower()
    n = 0
    for k, v in list((scan.get("handles") or {}).items())[:max_handles]:
        if k == cur or not budget.ok(40):
            continue
        q = f"to:{v['handle']} since:{v['first_seen']} until:{(_d(v['last_seen']) + timedelta(days=60)).isoformat()}"
        try:
            hits = list(SF.nitter_search(q, pages=1, host=host))
        except Exception:
            continue
        for h in hits[:6] + hits[-4:]:
            j = fetch_embed(h["id"])
            for sn in _mentions_of(j, R.user_id):
                R.observe(_created(j, h["id"]), {"screen_name": sn}, "life-scan:mention-by-other-account",
                          f"https://x.com/i/status/{h['id']}", verified=True, confidence="medium")
                n += 1
    if n:
        R.ok("former-handle mention search")
