"""Extra free/public sources used in --deep mode (and lightly in thorough).

All of these are documented public endpoints or published archives. Nothing here
bypasses Cloudflare, login walls, or paywalls.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from .net import HTTP, cdx, log, now_iso, snowflake_date, ts_to_iso
from .parsers import SN, RESERVED, norm_img
from .sources import fx_lookup

# host politeness
HTTP.min_gap.update({
    "index.commoncrawl.org": 1.0,
    "api.github.com": 0.5,
    "hn.algolia.com": 0.4,
    "api.pullpush.io": 0.6,
    "ghostarchive.org": 1.0,
})


# ======================================================================== live enrichment
def src_live_extra(R, handle):
    """Pull every field fxtwitter/vxtwitter already returned into R.live + observations."""
    u = None
    with R.lock:
        u = R.raw.get("api.fxtwitter.com") or R.raw.get("api.vxtwitter.com")
    if not u:
        u = fx_lookup(handle)
    if not u:
        return
    today = now_iso()
    extra = {
        "pinned_tweet_id": u.get("pinned_tweet_url", "").rstrip("/").split("/")[-1]
        if u.get("pinned_tweet_url") else u.get("pinned_tweet_id"),
        "media_count": u.get("media_count"),
        "likes": u.get("likes"),
        "listed": (u.get("public_metrics") or {}).get("listed_count") if isinstance(u.get("public_metrics"), dict) else None,
    }
    # fxtwitter sometimes nests pinned as id
    if not extra["pinned_tweet_id"]:
        for k in ("pinned_tweet_id", "pinned"):
            if u.get(k):
                extra["pinned_tweet_id"] = str(u[k]).split("/")[-1]
                break
    with R.lock:
        for k, v in extra.items():
            if v not in (None, "", []):
                R.live.setdefault(k, v)
        # also surface raw description entities / urls if present
        rd = u.get("raw_description") or {}
        urls = []
        for f in (rd.get("facets") or []):
            if f.get("type") == "url" and f.get("original"):
                urls.append(f.get("replacement") or f["original"])
        if urls:
            R.live.setdefault("bio_urls", urls)
        # email-shaped strings that were pasted publicly in the bio
        bio = u.get("description") or ""
        emails = sorted(set(re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", bio)))
        if emails:
            R.live["public_emails_in_bio"] = emails
            R.note(f"public email address(es) pasted in the live bio: {', '.join(emails)} "
                   "(X never exposes the account's login email; this is only what the owner wrote into the bio)")
    # re-observe with pinned
    fields = {"screen_name": u.get("screen_name"), "name": u.get("name"),
              "description": u.get("description"), "location": u.get("location"),
              "avatar": norm_img(u.get("avatar_url")), "banner": u.get("banner_url"),
              "followers": u.get("followers"), "following": u.get("following"),
              "tweets": u.get("tweets"), "likes": u.get("likes"),
              "pinned_tweet_id": extra.get("pinned_tweet_id"), "protected": u.get("protected")}
    R.observe(today, fields, "live:fxtwitter-extra", f"https://x.com/{handle}")
    R.ok("live profile enrichment (pinned/media/likes)")


# ======================================================================== Common Crawl
def src_commoncrawl(R, handle, uid=None, max_hits=40):
    """Query recent Common Crawl URL indexes for twitter.com/x.com captures of this handle/id."""
    name = "commoncrawl index"
    try:
        r = HTTP.get("https://index.commoncrawl.org/collinfo.json", timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"collinfo HTTP {r.status_code}")
        cols = r.json()
        # newest 4 crawls
        ids = [c["id"] for c in cols[:4]]
        hits = []
        patterns = [f"twitter.com/{handle}/*", f"x.com/{handle}/*", f"mobile.twitter.com/{handle}/*"]
        if uid:
            patterns += [f"twitter.com/i/user/{uid}", f"twitter.com/intent/user?user_id={uid}"]
        for cid in ids:
            for pat in patterns:
                if len(hits) >= max_hits:
                    break
                try:
                    rr = HTTP.get(f"https://index.commoncrawl.org/{cid}-index",
                                  params={"url": pat, "output": "json", "limit": 15}, timeout=40)
                    if rr.status_code == 404 or "No Captures" in rr.text[:80]:
                        continue
                    if rr.status_code != 200:
                        continue
                    for line in rr.text.splitlines():
                        if not line.strip():
                            continue
                        try:
                            j = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        hits.append({"crawl": cid, "timestamp": j.get("timestamp"), "url": j.get("url"),
                                     "status": j.get("status"), "mime": j.get("mime"),
                                     "filename": j.get("filename"), "offset": j.get("offset"), "length": j.get("length")})
                except Exception:
                    continue
        with R.lock:
            R.raw["commoncrawl"] = {"crawls_checked": ids, "hits": hits[:max_hits]}
        if hits:
            # emit a low-weight note for the earliest capture date we saw
            dates = sorted(h["timestamp"][:8] for h in hits if h.get("timestamp"))
            if dates:
                R.note(f"Common Crawl has {len(hits)} URL hit(s) for @{handle} "
                       f"(earliest index stamp {ts_to_iso(dates[0])}, latest {ts_to_iso(dates[-1])}); "
                       "full WARC fetch is available but not auto-downloaded (multi-GB). Use "
                       "https://index.commoncrawl.org to retrieve records.")
            R.ok(name)
        else:
            R.note(f"Common Crawl recent indexes: no captures for @{handle} (small accounts are often absent)")
            R.ok(name + " (empty)")
    except Exception as e:
        R.fail(name, e)


# ======================================================================== GitHub / HN / Reddit public search
def src_github_mentions(R, handle, uid=None):
    """Search public GitHub issues/repos for mentions of the handle or user id (no code search; that needs auth)."""
    name = "github public search"
    q_parts = [f'"{handle}"', f'"@{handle}"']
    if uid:
        q_parts.append(f'"{uid}"')
    found = []
    try:
        for q in q_parts:
            for kind, path in (("issues", "search/issues"), ("repositories", "search/repositories")):
                try:
                    r = HTTP.get(f"https://api.github.com/{path}",
                                 params={"q": q, "per_page": 5},
                                 timeout=20,
                                 headers={"Accept": "application/vnd.github+json"})
                    if r.status_code == 403:
                        R.note("GitHub search rate-limited (unauthenticated 10 req/min) - partial results")
                        break
                    if r.status_code != 200:
                        continue
                    for it in (r.json().get("items") or [])[:5]:
                        found.append({"kind": kind, "query": q,
                                      "url": it.get("html_url"), "title": it.get("title") or it.get("full_name"),
                                      "updated": (it.get("updated_at") or "")[:10]})
                except Exception:
                    continue
        with R.lock:
            R.raw["github_mentions"] = found
        if found:
            R.ok(name)
            R.note(f"GitHub public search: {len(found)} issue/repo hit(s) mentioning @{handle}"
                   + (f" or id {uid}" if uid else "") + " (manual review; may be unrelated)")
        else:
            R.ok(name + " (empty)")
    except Exception as e:
        R.fail(name, e)


def src_hn_mentions(R, handle):
    name = "hackernews algolia"
    try:
        r = HTTP.get("https://hn.algolia.com/api/v1/search",
                     params={"query": handle, "tags": "story,comment", "hitsPerPage": 10}, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        hits = []
        for h in r.json().get("hits") or []:
            title = h.get("title") or h.get("story_title") or ""
            text = h.get("comment_text") or ""
            blob = (title + " " + text).lower()
            if handle.lower() not in blob and f"@{handle.lower()}" not in blob:
                continue
            hits.append({"objectID": h.get("objectID"), "title": title[:120],
                         "url": f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                         "date": (h.get("created_at") or "")[:10], "author": h.get("author")})
        with R.lock:
            R.raw["hn_mentions"] = hits
        if hits:
            R.ok(name)
            R.note(f"Hacker News: {len(hits)} mention(s) of @{handle}")
        else:
            R.ok(name + " (empty)")
    except Exception as e:
        R.fail(name, e)


def src_reddit_mentions(R, handle):
    """Public PullPush (Pushshift mirror) search — no Reddit login."""
    name = "pullpush.io (reddit archive)"
    hits = []
    try:
        for kind in ("submission", "comment"):
            r = HTTP.get(f"https://api.pullpush.io/reddit/search/{kind}/",
                         params={"q": handle, "size": 5}, timeout=25)
            if r.status_code != 200:
                continue
            for it in (r.json().get("data") or [])[:5]:
                hits.append({"kind": kind, "id": it.get("id"), "subreddit": it.get("subreddit"),
                             "author": it.get("author"), "date": None,
                             "permalink": f"https://reddit.com{it['permalink']}" if it.get("permalink") else None,
                             "title": (it.get("title") or (it.get("body") or "")[:80])})
                created = it.get("created_utc")
                if created:
                    try:
                        from datetime import datetime, timezone
                        hits[-1]["date"] = datetime.fromtimestamp(int(created), tz=timezone.utc).strftime("%Y-%m-%d")
                    except Exception:
                        pass
        with R.lock:
            R.raw["reddit_mentions"] = hits
        if hits:
            R.ok(name)
            R.note(f"Reddit (PullPush): {len(hits)} hit(s) mentioning @{handle}")
        else:
            R.ok(name + " (empty)")
    except Exception as e:
        R.fail(name, e)


# ======================================================================== Ghostarchive / SocialBlade (best-effort)
def src_ghostarchive(R, handle):
    name = "ghostarchive.org"
    try:
        # Ghostarchive search often 302s; try direct archive URL patterns
        urls = [f"https://ghostarchive.org/search?term={quote('twitter.com/' + handle)}",
                f"https://ghostarchive.org/archive/twitter.com/{handle}"]
        found = []
        for u in urls:
            r = HTTP.s.get(u, timeout=20, allow_redirects=True)
            if r.status_code == 200 and ("ghostarchive" in r.url and handle.lower() in r.text.lower()):
                # crude: collect archive links
                for m in re.finditer(r'href="(/archive/[^"]+)"', r.text):
                    found.append("https://ghostarchive.org" + m.group(1))
                    if len(found) >= 8:
                        break
            if found:
                break
        with R.lock:
            R.raw["ghostarchive"] = found
        if found:
            R.ok(name)
            R.note(f"Ghostarchive: {len(found)} archive link(s) for @{handle}")
        else:
            R.note("Ghostarchive: no usable public hits (search often CAPTCHA/redirect-gated)")
            R.ok(name + " (empty/gated)")
    except Exception as e:
        R.fail(name, e)


def src_socialblade(R, handle):
    name = "socialblade.com"
    try:
        r = HTTP.get(f"https://socialblade.com/twitter/user/{handle.lower()}", timeout=20)
        if r.status_code == 403:
            raise RuntimeError("HTTP 403 (blocks automated access)")
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        # very light parse: look for created / grade strings
        created = re.search(r"User Created[^\d]*(\w+ \d+, \d{4})", r.text)
        grade = re.search(r"Social Blade Rank[^A-Z]*([A-Z+\-]+)", r.text)
        with R.lock:
            R.raw["socialblade"] = {"url": r.url, "created_hint": created.group(1) if created else None,
                                    "grade": grade.group(1) if grade else None}
        R.ok(name)
    except Exception as e:
        R.fail(name, e)


# ======================================================================== deeper sibling probe
def deepen_siblings(R, budget):
    """Broader sibling/typo matrix than the default ±3 numeric probe."""
    if not R.current or not budget.ok(40):
        return
    cur = R.current
    target = {h.lower() for h in R.handles_seen()}
    cands = set()
    m = re.match(r"^(.*?)(\d+)$", cur)
    if m:
        base, n, w = m.group(1), int(m.group(2)), len(m.group(2))
        for dlt in range(-8, 9):
            if dlt and n + dlt >= 0:
                cands.add(f"{base}{str(n + dlt).zfill(w)}")
        cands.add(base.rstrip("_"))
        # common alt bases when number suffix is kept
        for alt in ("_", "x", "real", "official", "alt"):
            cands.add(base.rstrip("_") + alt + str(n).zfill(w))
    else:
        for s in ("2", "_2", "alt", "_alt", "priv", "_priv", "x", "_x", "real", "new", "old"):
            cands.add(cur + s)
            if len(cur) > 3:
                cands.add(s.lstrip("_") + cur if not s.startswith("_") else s[1:] + "_" + cur)
    # also probe every frozen / memory.lol handle's siblings lightly
    for h in list(R.handles_seen().values())[:6]:
        mm = re.match(r"^(.*?)(\d+)$", h)
        if mm:
            for dlt in (-1, 1, 2):
                nn = int(mm.group(2)) + dlt
                if nn >= 0:
                    cands.add(f"{mm.group(1)}{str(nn).zfill(len(mm.group(2)))}")
    cands = {h for h in cands if re.fullmatch(SN, h) and h.lower() not in target and h.lower() not in RESERVED}
    # skip ones already in related
    cands -= {r.lower() for r in R.related}
    todo = sorted(cands)[:18]
    if not todo:
        return
    log(f"deep sibling probe: checking {len(todo)} nearby handles ...")
    names = {o["fields"]["name"].lower() for o in R.obs if o["verified"] and o["fields"].get("name")}

    def look(h):
        if not budget.ok(15):
            return
        u = fx_lookup(h)
        if not u:
            return
        bio = u.get("description") or ""
        links_back = any(re.search(r"(?i)@" + re.escape(t) + r"\b", bio) for t in target)
        cand = re.sub(r"\W+", "", (u.get("name") or "").lower())
        same_name = any(n and len(re.sub(r"\W+", "", n)) >= 6 and re.sub(r"\W+", "", n) == cand for n in names)
        if not (links_back or same_name):
            return
        if str(u.get("id")) == R.user_id:
            return
        with R.lock:
            R.related[h.lower()] = {
                "handle": u.get("screen_name"), "id": str(u.get("id")), "name": u.get("name"), "bio": bio,
                "created": u.get("joined"), "followers": u.get("followers"),
                "how_found": ["deep sibling-handle probe"],
                "links_back_to_target": links_back, "shares_display_name": same_name,
                "target_bio_context": [], "permalink": f"https://x.com/{u.get('screen_name')}",
                "confidence": "high" if links_back else "medium"}
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(look, todo))
    if any(r.get("how_found") == ["deep sibling-handle probe"] for r in R.related.values()):
        R.ok("deep sibling-handle probe")


# ======================================================================== id-keyed Wayback retry helpers
def src_earliest_status_probe(R, handle, budget):
    """Force a CDX look specifically at the *earliest* archived tweets (pre-gap filler)."""
    name = f"earliest-tweet CDX @{handle}"
    try:
        rows = cdx({"url": f"twitter.com/{handle}/status/*", "matchType": "prefix",
                    "fl": "timestamp,original,statuscode,mimetype", "filter": "statuscode:200|statuscode:-",
                    "from": "2006", "to": "20260326", "limit": 40, "showResumeKey": "false"},
                   timeout=90, lane="heavy")
        caps = []
        for row in rows:
            caps.append({"timestamp": row["timestamp"], "original": row["original"],
                         "statuscode": row.get("statuscode"), "mimetype": row.get("mimetype"),
                         "handle": handle, "kind": "status"})
        with R.lock:
            R.raw.setdefault("earliest_status_cdx", {})[handle] = {
                "count": len(caps),
                "first": ts_to_iso(caps[0]["timestamp"]) if caps else None,
                "last": ts_to_iso(caps[-1]["timestamp"]) if caps else None,
                "sample": [c["original"] for c in caps[:5]],
            }
        if caps:
            R.ok(name)
            R.note(f"pre-2026-03 Wayback tweet captures under @{handle}: {len(caps)} "
                   f"({ts_to_iso(caps[0]['timestamp'])} → {ts_to_iso(caps[-1]['timestamp'])})")
        else:
            R.note(f"pre-2026-03 Wayback: zero tweet captures under @{handle} "
                   "(renames hide old tweets under the old handle's URL — frozen-handle / id-keyed paths needed)")
            R.ok(name + " (empty)")
        return caps
    except Exception as e:
        R.fail(name, e)
        return []


def run_deep_sources(R, budget, deep=True):
    """Fan out the extra sources. `deep=True` runs the expensive ones."""
    handle = R.current or (None if str(R.query).isdigit() else R.query)
    if handle:
        src_live_extra(R, handle)
    jobs = []
    if handle:
        jobs.append(lambda: src_github_mentions(R, handle, R.user_id))
        jobs.append(lambda: src_hn_mentions(R, handle))
        jobs.append(lambda: src_reddit_mentions(R, handle))
        if deep:
            jobs.append(lambda: src_commoncrawl(R, handle, R.user_id))
            jobs.append(lambda: src_ghostarchive(R, handle))
            jobs.append(lambda: src_socialblade(R, handle))
            jobs.append(lambda: src_earliest_status_probe(R, handle, budget))
            # also probe every known former handle for pre-gap CDX
            for h in list(R.handles_seen().values())[:4]:
                if h.lower() != handle.lower():
                    jobs.append(lambda h=h: src_earliest_status_probe(R, h, budget))
    if not jobs:
        return
    log(f"deep sources: running {len(jobs)} extra lookups ...")
    with ThreadPoolExecutor(min(4, len(jobs))) as ex:
        list(ex.map(lambda f: f(), jobs))
    if deep and handle:
        deepen_siblings(R, budget)
