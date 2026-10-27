"""Every free public source the hunter queries."""
import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from .net import HTTP, cdx, cdx_num_pages, wb_raw, wb_link, wb_img, ts_to_iso, now_iso, log, unix_date
from .parsers import (SN, RESERVED, parse_profile_html, parse_status_html, parse_json_text, parse_live_ssr,
                      json_author_id, norm_img)

STATUS_RX = re.compile(r"/status(?:es)?/(\d+)")


# ======================================================================== live sources
def src_memorylol(R, handle=None, uid=None):
    name = f"memory.lol({'id:' + uid if uid else '@' + handle})"
    url = f"https://api.memory.lol/v1/tw/id/{uid}" if uid else f"https://api.memory.lol/v1/tw/{handle}"
    try:
        r = HTTP.get(url, timeout=25)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        data = r.json()
        R.raw[name] = data
        accts = data.get("accounts") or ([data] if data.get("screen_names") else [])
        if not accts:
            R.note(f"memory.lol has no public record for {'id ' + uid if uid else '@' + handle}")
        for acc in accts:
            aid = acc.get("id_str") or str(acc.get("id", ""))
            if len(accts) > 1:
                R.note(f"memory.lol: @{handle} has been used by {len(accts)} different accounts (id {aid} among them)")
            mine = uid or len(accts) == 1 or R.user_id in (None, aid)
            if mine and (R.user_id in (None, aid)):
                R.set_id(aid, "memory.lol")
                for sn, dates in (acc.get("screen_names") or {}).items():
                    with R.lock:
                        R.memorylol[sn] = dates
                    if dates:
                        for d in dates:
                            R.observe(d, {"screen_name": sn}, "memory.lol", f"https://memory.lol/app/#{sn}",
                                      verified=True)
                    else:
                        R.undated_handles.add(sn)
            else:
                for sn, dates in (acc.get("screen_names") or {}).items():
                    R.foreign[sn.lower()].add((aid, (dates or ["?"])[0], "memory.lol"))
        R.ok("memory.lol")
    except Exception as e:
        R.fail(name, e)


def _fx_obs(u, today):
    return {"screen_name": u.get("screen_name"), "name": u.get("name"), "description": u.get("description"),
            "location": u.get("location"), "website": (u.get("website") or {}).get("url") if isinstance(u.get("website"), dict) else u.get("website"),
            "avatar": norm_img(u.get("avatar_url") or u.get("profile_image_url")),
            "banner": u.get("banner_url") or u.get("profile_banner_url"),
            "followers": u.get("followers") or u.get("followers_count"),
            "following": u.get("following") or u.get("friends_count"),
            "tweets": u.get("tweets") or u.get("statuses_count"), "likes": u.get("likes"),
            "protected": u.get("protected")}


def fx_lookup(handle):
    """Return fxtwitter/vxtwitter user dict or None (no side effects)."""
    for url in (f"https://api.fxtwitter.com/{handle}", f"https://api.vxtwitter.com/{handle}"):
        try:
            r = HTTP.get(url, timeout=20)
            if r.status_code != 200:
                continue
            j = r.json()
            u = j.get("user", j)
            if u.get("screen_name"):
                return u
        except Exception:
            continue
    return None


def src_fxtwitter(R, handle):
    today = now_iso()
    for nm, url in (("api.fxtwitter.com", f"https://api.fxtwitter.com/{handle}"),
                    ("api.vxtwitter.com", f"https://api.vxtwitter.com/{handle}")):
        try:
            r = HTTP.get(url, timeout=20)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            j = r.json()
            u = j.get("user", j)
            if not u.get("screen_name"):
                raise RuntimeError(j.get("message") or "no user (suspended/deleted/renamed?)")
            uid = str(u.get("id") or u.get("id_str") or "") or None
            R.set_id(uid, nm)
            if uid and R.user_id and uid != R.user_id:
                R.note(f"@{handle} is now owned by a different account (id {uid}) - live data ignored")
                return
            with R.lock:
                R.current = R.current or u["screen_name"]
                R.raw[nm] = u
                lv = R.live
                lv.setdefault("source", nm)
                for k_out, keys in (("name", ("name",)), ("description", ("description",)), ("location", ("location",)),
                                    ("joined", ("joined", "created_at")), ("followers", ("followers", "followers_count")),
                                    ("following", ("following", "friends_count")), ("tweets", ("tweets", "statuses_count")),
                                    ("likes", ("likes",)), ("media_count", ("media_count",)),
                                    ("banner_url", ("banner_url", "profile_banner_url")),
                                    ("avatar_url", ("avatar_url", "profile_image_url")), ("website", ("website",)),
                                    ("verification", ("verification",)), ("protected", ("protected",))):
                    for k in keys:
                        if u.get(k) not in (None, "") and k_out not in lv:
                            lv[k_out] = u[k]
            R.observe(today, _fx_obs(u, today), f"live:{nm}", f"https://x.com/{u['screen_name']}")
            R.ok(nm)
            return
        except Exception as e:
            R.fail(nm, e)


def src_x_ssr(R, handle):
    """Logged-out x.com now server-renders profile JSON (restId, name, location, counts, created_at)."""
    try:
        r = HTTP.get(f"https://x.com/{handle}", timeout=25)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        o = parse_live_ssr(r.text)
        if not (o.get("user_id") or o.get("avatar")):
            raise RuntimeError("no server-rendered profile data")
        if o.get("user_id"):
            R.set_id(o["user_id"], "x.com SSR")
            if R.user_id and o["user_id"] != R.user_id:
                return
        with R.lock:
            for k in ("created_at", "followers", "following", "tweets"):
                if o.get(k) is not None:
                    R.live.setdefault("joined" if k == "created_at" else k, o[k])
            if "username_changes" in r.text or "about_profile" in r.text:
                R.raw["x_ssr_about_hint"] = True
        o["screen_name"] = o.get("screen_name") or handle
        R.observe(now_iso(), o, "live:x.com-ssr", f"https://x.com/{handle}", verified=bool(o.get("user_id")))
        R.ok("x.com server-rendered profile")
    except Exception as e:
        R.fail("x.com profile SSR", e)


def src_x_id_redirect(R, uid):
    """x.com/i/user/<id> -> 30x to the *current* handle."""
    try:
        r = HTTP.s.get(f"https://x.com/i/user/{uid}", timeout=20, allow_redirects=False)
        loc = r.headers.get("Location", "")
        m = re.search(r"(?:(?:twitter|x)\.com)?/(" + SN + r")/?(?:$|\?)", loc)
        if r.status_code in (301, 302, 303, 307, 308) and m and m.group(1).lower() not in RESERVED:
            with R.lock:
                R.current = m.group(1)
            R.observe(now_iso(), {"screen_name": m.group(1)}, "live:x.com/i/user-redirect", f"https://x.com/i/user/{uid}")
            R.ok("x.com id redirect")
            return m.group(1)
        raise RuntimeError(f"HTTP {r.status_code}, no handle redirect (suspended/deleted, or X now serves the SPA)")
    except Exception as e:
        R.fail("x.com/i/user redirect", e)


def src_about_account(R, handle):
    """X 'About this account' (username change count + last change). Try every public route."""
    tried = []
    for url in (f"https://x.com/{handle}/about", f"https://twitter.com/{handle}/about"):
        try:
            r = HTTP.s.get(url, timeout=20, allow_redirects=False)
            loc = r.headers.get("Location", "")
            if r.status_code == 200 and ("username_changes" in r.text or "about_profile" in r.text):
                m = re.search(r'username_changes\W+count\W+(\d+)(?:\W+last_changed_at_msec\W+(\d+))?', r.text)
                if m:
                    R.about = {"username_changes": int(m.group(1)),
                               "last_changed": unix_date(int(m.group(2)) // 1000) if m.group(2) else None,
                               "source": url}
                    R.ok("X About-this-account")
                    return
            tried.append(f"{url} -> HTTP {r.status_code}{' to ' + loc[:60] if loc else ''}")
        except Exception as e:
            tried.append(f"{url} -> {type(e).__name__}")
    R.fail("X 'About this account' (username-change count)",
           "requires a logged-in session (AboutAccountQuery GraphQL; logged-out requests redirect to /i/jf/onboarding login). "
           + "; ".join(tried))
    R.note("X's 'About this account' panel (how many times the username changed + last change date) is login-only; "
           "open https://x.com/" + handle + "/about while signed in to read it manually.")


def src_lolarchiver(R, handle):
    try:
        r = HTTP.get(f"https://twitter.lolarchiver.com/main?q={handle}", timeout=20)
        credits = re.search(r"--cs-credits-left:\s*(\d+)", r.text)
        R.fail("twitter.lolarchiver.com",
               f"HTTP {r.status_code}; results load client-side behind Cloudflare + a credit system "
               f"(anonymous credits left: {credits.group(1) if credits else 'n/a'}) - paid/login only, not queried")
    except Exception as e:
        R.fail("twitter.lolarchiver.com", f"{type(e).__name__}: blocked/rate-limited (HTTP 429 / Cloudflare). It is a paid, "
               "credit-metered service with client-side results - not queried")


# ======================================================================== Wayback: capture inventory
def cdx_profile_caps(R, handle):
    caps = []
    for host in ("twitter.com", "mobile.twitter.com", "x.com"):
        try:
            rows = cdx({"url": f"{host}/{handle}", "fl": "timestamp,original,statuscode,mimetype,digest",
                        "collapse": "digest", "limit": "5000"})
            caps += [dict(r, host=host, kind="profile", handle=handle) for r in rows]
            R.ok("wayback-cdx profiles")
        except Exception as e:
            R.fail(f"wayback-cdx {host}/{handle}", e)
    # v3: profile pages archived with a query string / trailing slash (?lang=en, ?s=20, /?ref=...) are
    # separate CDX keys that an exact-URL lookup misses - often the only captures of small accounts.
    seen = {(c["timestamp"], c["original"]) for c in caps}
    for host in ("twitter.com", "x.com"):
        try:
            rows = cdx({"url": f"{host}/{handle}", "matchType": "prefix",
                        "filter": ["statuscode:200", rf"original:(?i)^https?://(www\.|mobile\.)?{host.replace('.', r'[.]')}/{handle}/?(\?.*)?$"],
                        "fl": "timestamp,original,statuscode,mimetype,digest", "collapse": "digest", "limit": "2000"},
                       timeout=60)
            for r in rows:
                if (r["timestamp"], r["original"]) not in seen:
                    seen.add((r["timestamp"], r["original"]))
                    caps.append(dict(r, host=host, kind="profile", handle=handle))
        except Exception as e:
            R.fail(f"wayback-cdx prefix {host}/{handle}?*", e)
    return caps


def cdx_status_caps(R, handle, host, max_pages, budget, sink):
    """Archived tweet URLs under <host>/<handle>/status/. CDX paging; huge accounts get evenly-spaced page samples.
    Rows are appended to `sink` as they arrive so a timed-out scan still yields partial data."""
    prefix = f"{host}/{handle}/status/"
    try:
        n = cdx_num_pages(prefix)
    except Exception as e:
        R.fail(f"wayback-cdx pages {prefix}", e)
        return sink
    info = {"pages": n, "pages_read": 0, "usable_captures": 0}
    with R.lock:
        R.status_scans.setdefault(handle, {})[host] = info
    if n <= 0:
        return sink
    pages = list(range(n)) if n <= max_pages else sorted({round(i * (n - 1) / max(max_pages - 1, 1)) for i in range(max_pages)})
    if n > max_pages and max_pages == 1:
        pages = [n - 1]   # newest tweets live on the last page

    def one(p):
        if not budget.ok(60):
            return
        try:
            rows = cdx({"url": prefix, "matchType": "prefix", "fl": "timestamp,original,statuscode,mimetype",
                        "page": str(p)}, timeout=100, lane="heavy")
        except Exception as e:
            R.fail(f"wayback-cdx {prefix} page {p}", e)
            return
        seen = set()
        for r in rows:
            if r.get("statuscode") not in ("200", "-"):
                continue
            m = STATUS_RX.search(r.get("original", ""))
            mt = r.get("mimetype") or ""
            if not m or ("html" not in mt and "json" not in mt):
                continue
            k = (m.group(1), r["timestamp"][:8])
            if k in seen:
                continue
            seen.add(k)
            sink.append(dict(r, host=host, kind="status", handle=handle))
            info["usable_captures"] += 1
            d = ts_to_iso(r["timestamp"])
            info["first"] = min(filter(None, [info.get("first"), d]))
            info["last"] = max(filter(None, [info.get("last"), d]))
        info["pages_read"] += 1
    with ThreadPoolExecutor(2) as ex:
        list(ex.map(one, pages))
    R.ok("wayback-cdx tweet captures")
    return sink


def id_page_urls(uid, fast=False):
    if fast:
        return [(f"twitter.com/intent/user?user_id={uid}", "idpage"), (f"twitter.com/i/user/{uid}", "idpage"),
                (f"x.com/i/user/{uid}", "idpage"), (f"twitter.com/account/redirect_by_id?id={uid}", "idpage")]
    return [
        (f"twitter.com/intent/user?user_id={uid}", "idpage"), (f"twitter.com/intent/follow?user_id={uid}", "idpage"),
        (f"twitter.com/account/redirect_by_id?id={uid}", "idpage"), (f"twitter.com/account/redirect_by_id/{uid}", "idpage"),
        (f"twitter.com/i/user/{uid}", "idpage"), (f"x.com/i/user/{uid}", "idpage"), (f"mobile.twitter.com/i/user/{uid}", "idpage"),
        (f"api.twitter.com/1/users/show.json?user_id={uid}", "json"), (f"api.twitter.com/1.1/users/show.json?user_id={uid}", "json"),
        (f"twitter.com/users/show.json?user_id={uid}", "json"), (f"api.twitter.com/1/users/lookup.json?user_id={uid}", "json"),
        (f"api.twitter.com/1/statuses/user_timeline.json?user_id={uid}", "json"),
        (f"twitter.com/statuses/user_timeline/{uid}.rss", "rss"), (f"api.twitter.com/1/statuses/user_timeline/{uid}.rss", "rss"),
        (f"syndication.twitter.com/srv/timeline-profile/user-id/{uid}", "idpage"),
        (f"cdn.syndication.twimg.com/widgets/followbutton/info.json?user_ids={uid}", "json"),
        (f"api.twitter.com/2/users/{uid}", "json"),
    ]


def cdx_id_caps(R, uid, fast=False):
    caps = []
    exact = re.compile(r"(?:=|/)" + uid + r"(?:$|[&/.?#])")

    def one(item):
        u, kind = item
        try:
            rows = cdx({"url": u, "matchType": "prefix", "fl": "timestamp,original,statuscode,mimetype",
                        "collapse": "timestamp:8", "limit": "400"}, timeout=60)
            return [dict(r, kind=kind, handle=None) for r in rows if exact.search(r["original"])]
        except Exception as e:
            R.fail(f"wayback-cdx {u}", e)
            return []
    with ThreadPoolExecutor(4) as ex:
        for rows in ex.map(one, id_page_urls(uid, fast)):
            caps += rows
    with R.lock:
        R.id_pages = [{"url": c["original"], "date": ts_to_iso(c["timestamp"]), "status": c["statuscode"],
                       "wayback_url": wb_link(c["timestamp"], c["original"])} for c in sorted(caps, key=lambda c: c["timestamp"])]
    if caps:
        R.ok("wayback permanent-id pages")
    return caps


def cdx_handle_misc(R, handle, fast=False):
    """twitter.com/<h>/profile_image redirects (= avatar at that date) and handle-keyed API captures."""
    caps = []
    for u, kind in ((f"twitter.com/{handle}/profile_image", "pimg"), (f"api.twitter.com/1/users/profile_image/{handle}", "pimg"),
                    (f"api.twitter.com/1/users/show.json?screen_name={handle}", "json"),
                    (f"api.twitter.com/1.1/users/show.json?screen_name={handle}", "json"),
                    (f"twitter.com/users/{handle}.json", "json"))[:2 if fast else 5]:
        try:
            rows = cdx({"url": u, "matchType": "prefix", "fl": "timestamp,original,statuscode,mimetype",
                        "collapse": "timestamp:8", "limit": "300"}, timeout=30)
            caps += [dict(r, kind=kind, handle=handle) for r in rows]
        except Exception as e:
            R.fail(f"wayback-cdx {u}", e)
    return caps


# ======================================================================== Wayback: snapshot analysis
def analyse_capture(R, c):
    """Fetch one archived capture and turn it into at most one observation of the target account."""
    ts, url, kind = c["timestamp"], c["original"], c["kind"]
    date = ts_to_iso(ts)
    ev = wb_link(ts, url)
    try:
        if c.get("statuscode") in ("301", "302", "303", "307"):
            r = wb_raw(ts, url, allow_redirects=False, timeout=30)
            loc = r.headers.get("Location", "")
            if kind == "pimg":
                img = norm_img(re.sub(r"^.*?/web/\d+(?:id_|im_)?/", "", loc))
                if img:
                    R.observe(date, {"avatar": img, "screen_name": c.get("handle")}, "wayback:profile_image-redirect",
                              ev, verified=False, ts=ts)
                    return {"avatar": img}
                return None
            m = re.search(r"(?:twitter|x)\.com/(" + SN + r")/?(?:$|\?)", loc)
            if m and m.group(1).lower() not in RESERVED and kind == "idpage":
                R.observe(date, {"screen_name": m.group(1)}, "wayback:id-redirect", ev, verified=True, ts=ts)
                return {"screen_name": m.group(1)}
            if m and kind in ("profile", "status") and m.group(1).lower() != (c.get("handle") or "").lower():
                R.observe(date, {"screen_name": m.group(1)}, f"wayback:{kind}-redirect(unverified)", ev, verified=False, ts=ts,
                          confidence="low")
            return None
        r = wb_raw(ts, url, timeout=45)
        if r.status_code != 200:
            return None
        text = r.text
        is_json = text.lstrip()[:1] in "{[" or "json" in (c.get("mimetype") or "")
        users = parse_json_text(text) if is_json else []
        if is_json:
            if not users:
                return None
            target = R.user_id
            me = next((u for u in users if u["user_id"] == target), None) if target else None
            if not me and not target:
                aid = json_author_id(text)
                me = next((u for u in users if u["user_id"] == aid), users[0] if len(users) == 1 else None)
                if me:
                    R.set_id(me["user_id"], f"wayback json {url}")
            if not me:
                aid = json_author_id(text) or (users[0]["user_id"] if len(users) == 1 else None)
                if aid and aid != target and c.get("handle"):
                    R.foreign[c["handle"].lower()].add((aid, date, ev))
                return None
            R.observe(date, me, f"wayback:{kind}-json", ev, verified=True, ts=ts)
            return me
        if kind == "status":
            o = parse_status_html(text, url, R.user_id)
        elif kind == "rss":
            sn = re.search(r"<title>Twitter / (" + SN + r")</title>", text)
            o = {"screen_name": sn.group(1), "user_id": R.user_id} if sn else None
        else:
            o = parse_profile_html(text, url)
            if kind == "idpage" and not o.get("screen_name"):
                m = re.search(r'class="nickname"[^>]*>@?(' + SN + r')<|<span class="nickname">@(' + SN + r')|screen_name=(' + SN + r')', text)
                if m:
                    o["screen_name"] = next(g for g in m.groups() if g)
                if kind == "idpage" and not o.get("user_id"):
                    o["user_id"] = R.user_id   # page is keyed by the permanent id
        if not o:
            return None
        if kind in ("profile", "status") and not o.get("screen_name"):
            o["screen_name"] = c.get("handle")
        uid = o.get("user_id")
        if uid and R.user_id is None:
            R.set_id(uid, f"wayback {kind} @{c.get('handle')}")
        if uid and R.user_id and uid != R.user_id:
            if c.get("handle"):
                R.foreign[c["handle"].lower()].add((uid, date, ev))
            return None
        verified = bool(uid) and uid == R.user_id
        if not verified:
            m = re.search(r"profile_images/(\d+)/", o.get("avatar") or "")
            conf = "medium" if (m and m.group(1) in R.known_avatar_ids()) else "low"
        else:
            conf = "high"
        R.observe(date, o, f"wayback:{kind}-html", ev, verified=verified, ts=ts, confidence=conf)
        return o
    except Exception as e:
        log(f"capture {ts} {url}: {type(e).__name__} {e}")
        return None


def pick_spread(caps, n, prefer_json=True):
    """Pick up to n captures spread over capture months (first+last of each month first)."""
    if len(caps) <= n:
        return list(caps)
    by_m = defaultdict(list)
    for c in sorted(caps, key=lambda c: c["timestamp"]):
        by_m[c["timestamp"][:6]].append(c)
    months = sorted(by_m)
    if len(months) > n:
        idx = sorted({round(i * (len(months) - 1) / (n - 1)) for i in range(n)})
        months = [months[i] for i in idx]
    queues = []
    for m in months:
        lst = by_m[m]
        if prefer_json:
            js = [c for c in lst if "json" in (c.get("mimetype") or "")]
            lst = js + [c for c in lst if c not in js] if js and len(js) >= 2 else lst
        order = [lst[0], lst[-1]] + [lst[i] for i in sorted({round(k * (len(lst) - 1) / 4) for k in range(1, 4)})]
        q, seen = [], set()
        for c in order:
            if id(c) not in seen:
                seen.add(id(c))
                q.append(c)
        queues.append(q)
    picked = []
    while len(picked) < n and any(queues):
        for q in queues:
            if q and len(picked) < n:
                picked.append(q.pop(0))
    return picked


def pick_profile_samples(caps, per_year, cap):
    good = [c for c in caps if c.get("statuscode") in ("200",) and "html" in (c.get("mimetype") or "")]
    good.sort(key=lambda c: c["timestamp"])
    by_year = defaultdict(list)
    for c in good:
        by_year[c["timestamp"][:4]].append(c)
    picked = []
    for _, lst in sorted(by_year.items()):
        if len(lst) <= per_year:
            picked += lst
            continue
        idx = sorted({round(i * (len(lst) - 1) / max(per_year - 1, 1)) for i in range(per_year)})
        picked += [lst[i] for i in idx]
    if len(picked) > cap:
        step = len(picked) / cap
        picked = [picked[int(i * step)] for i in range(cap)] + [picked[-1]]
    redirects = [c for c in caps if c.get("statuscode") in ("301", "302")]
    picked += pick_spread(redirects, 4, prefer_json=False)
    return picked


# ======================================================================== banners / avatars / archive.today
def src_banners(R, uid):
    try:
        rows = cdx({"url": f"pbs.twimg.com/profile_banners/{uid}/", "matchType": "prefix",
                    "fl": "timestamp,original,statuscode", "limit": "3000"}, timeout=60)
        out = {}
        for r in rows:
            m = re.search(r"profile_banners/\d+/(\d+)", r["original"])
            if not m:
                continue
            b = out.setdefault(m.group(1), {"banner_id": m.group(1), "uploaded": unix_date(m.group(1)), "captures": 0,
                                            "first_capture": None, "last_capture": None, "wayback_url": None,
                                            "url": f"https://pbs.twimg.com/profile_banners/{uid}/{m.group(1)}/1500x500"})
            b["captures"] += 1
            d = ts_to_iso(r["timestamp"])
            b["first_capture"] = min(filter(None, [b["first_capture"], d]))
            b["last_capture"] = max(filter(None, [b["last_capture"], d]))
            if r["statuscode"] == "200" and not b["wayback_url"]:
                b["wayback_url"] = wb_img(r["timestamp"], r["original"])
        R.raw["banners_cdx"] = list(out.values())
        R.ok("wayback banners (by user id)")
        return out
    except Exception as e:
        R.fail("wayback banners", e)
        return {}


def enrich_avatar(R, a, budget=None):
    iid = a.get("image_id")
    if not iid:
        return
    caps, res = [], {}
    if budget is None or budget.ok(25):
        hosts = []
        for u in a.get("seen_at_urls", ()):
            h = re.match(r"https?://([^/]+(?:/twitter_production)?)/", u)
            if h and h.group(1) not in hosts:
                hosts.append(h.group(1))
        hosts = (hosts + ["pbs.twimg.com"])[:1] if hosts else ["pbs.twimg.com"]
        for host in hosts:
            try:
                rows = cdx({"url": f"{host}/profile_images/{iid}/", "matchType": "prefix",
                            "fl": "timestamp,original,statuscode,mimetype", "limit": "100"}, timeout=20)
                caps += [r for r in rows if r["statuscode"] == "200"]
            except Exception as e:
                R.note(f"Wayback image lookup for avatar {iid} failed ({type(e).__name__})")
    res["archived_image_captures"] = [{"date": ts_to_iso(c["timestamp"]), "url": c["original"],
                                     "wayback_url": wb_img(c["timestamp"], c["original"])}
                                    for c in sorted(caps, key=lambda c: c["timestamp"])][:10]
    res["archived_image_capture_count"] = len(caps)
    base = a["base_url"]
    m = re.match(r"(.*?)(\.[A-Za-z0-9]+)?$", base)
    stem, ext = m.group(1), (m.group(2) or "")
    pbs_stem = re.sub(r"^https?://[^/]+(?:/twitter_production)?/", "https://pbs.twimg.com/", stem)
    live = {}
    for suf in ("_400x400", ""):
        u = f"{pbs_stem}{suf}{ext}"
        try:
            live[u] = HTTP.s.head(u, timeout=10, allow_redirects=False).status_code
        except Exception:
            live[u] = "error"
    res["live_variants"] = live
    res["still_live"] = any(v == 200 for v in live.values())
    res["live_url"] = next((u for u, v in live.items() if v == 200), None)
    a.update(res)


def src_archive_ph(R, handle):
    try:
        mem = []
        for x in ("twitter.com", "x.com"):
            r = HTTP.get(f"https://archive.ph/timemap/https://{x}/{handle}", timeout=25)
            if r.status_code == 200 and "memento" in r.text:
                mem += re.findall(r'<(https?://archive\.[a-z]+/(\d{14})/[^>]+)>;\s*rel="[^"]*memento', r.text)
            elif r.status_code != 404:
                raise RuntimeError(f"HTTP {r.status_code} (archive.today blocks automated access / CAPTCHA)")
        mem.sort(key=lambda m: m[1])
        R.archive_ph[handle] = {"count": len(mem), "first": ts_to_iso(mem[0][1]) if mem else None,
                                "last": ts_to_iso(mem[-1][1]) if mem else None,
                                "links": [m[0] for m in (mem[:3] + mem[-3:])] if mem else []}
        R.ok("archive.today timemap")
    except Exception as e:
        R.fail(f"archive.today @{handle}", e)


# ======================================================================== related accounts
REL_RX = re.compile(r"(?i)(main|alt|backup|back-up|old|new|priv(?:ate)?|spam|second|2nd|sub|fan|personal|business|"
                    r"official|real|other|ex|former|moved(?: to)?|now at|follow|nsfw|art|irl)\b[^@\n]{0,25}@(" + SN + r")")
MENTION_RX = re.compile(r"@(" + SN + r")")


def find_related(R, budget, sibling_probe=True):
    """Accounts linked from any bio we saw, plus sibling handles that link back to the target."""
    target_handles = {h.lower() for h in R.handles_seen()}
    names = {o["fields"]["name"].lower() for o in R.obs if o["verified"] and o["fields"].get("name")}
    cands = {}
    for o in R.obs:
        d = o["fields"].get("description") or ""
        for m in MENTION_RX.finditer(d):
            h = m.group(1)
            if h.lower() in target_handles:
                continue
            rel = REL_RX.search(d[max(0, m.start() - 40):m.end()])
            c = cands.setdefault(h.lower(), {"handle": h, "how": set(), "context": set(), "dates": set()})
            c["how"].add(f"mentioned in target bio ({rel.group(1).lower()})" if rel else "mentioned in target bio")
            c["context"].add(d[:160])
            c["dates"].add(o["date"])
    if sibling_probe and R.current:
        m = re.match(r"^(.*?)(\d+)$", R.current)
        sib = set()
        if m:
            base, n = m.group(1), int(m.group(2))
            w = len(m.group(2))
            for dlt in (-3, -2, -1, 1, 2, 3):
                if n + dlt >= 0:
                    sib.add(f"{base}{str(n + dlt).zfill(w)}")
            sib.add(base.rstrip("_"))
        else:
            sib |= {R.current + s for s in ("2", "_2", "_", "alt", "_alt", "priv")}
        for h in sorted(sib):
            if h.lower() not in target_handles and re.fullmatch(SN, h):
                cands.setdefault(h.lower(), {"handle": h, "how": {"sibling-handle probe"}, "context": set(), "dates": set()})
    out = {}

    def look(c):
        if not budget.ok(20):
            return
        u = fx_lookup(c["handle"])
        if not u:
            return
        bio = (u.get("description") or "")
        links_back = any(re.search(r"(?i)@" + re.escape(t) + r"\b", bio) for t in target_handles)
        cand = re.sub(r"\W+", "", (u.get("name") or "").lower())
        # only distinctive names count (generic first names like "jack" match thousands of strangers)
        same_name = any(n and len(re.sub(r"\W+", "", n)) >= 6 and (
            re.sub(r"\W+", "", n) == cand or (len(n.split()[0]) >= 6 and n.split()[0] in (u.get("name") or "").lower()))
            for n in names)
        sibling_only = c["how"] == {"sibling-handle probe"}
        if sibling_only and not (links_back or same_name):
            return
        if str(u.get("id")) == R.user_id:
            return
        out[c["handle"].lower()] = {
            "handle": u.get("screen_name"), "id": str(u.get("id")), "name": u.get("name"), "bio": bio,
            "created": u.get("joined"), "followers": u.get("followers"), "how_found": sorted(c["how"]),
            "links_back_to_target": links_back, "shares_display_name": same_name,
            "target_bio_context": sorted(c["context"])[:2], "permalink": f"https://x.com/{u.get('screen_name')}",
            "confidence": "high" if links_back else ("medium" if same_name or not sibling_only else "low")}
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(look, list(cands.values())[:14]))
    R.related = out
    if out:
        R.ok("related-account discovery")
