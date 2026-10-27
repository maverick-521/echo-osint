"""Parsers for every Twitter/X layout seen in public archives (2007 -> 2026).

Each parser returns normalised "profile observation" dicts:
  {user_id, screen_name, name, description, location, website, avatar, banner,
   followers, following, tweets, created_at, pinned_tweet_id, verified, protected}
Missing fields are simply absent / None.
"""
import html as htmlmod
import json
import re
from collections import Counter

from .net import parse_any_date

SN = r"[A-Za-z0-9_]{1,20}"
RESERVED = {"i", "intent", "account", "login", "home", "share", "search", "hashtag", "settings", "signup",
            "explore", "notifications", "messages", "tos", "privacy", "logout", "web", "widgets", "status",
            "statuses", "about", "help", "jobs", "download", "compose", "who_to_follow", "x", "twitter"}


def clean(s):
    if s is None:
        return None
    s = re.sub(r"<img[^>]*alt=\"([^\"]*)\"[^>]*>", r"\1", s)              # emoji images -> alt text
    s = re.sub(r"<span[^>]*class=\"Emoji[^\"]*\"[^>]*title=\"[^\"]*\"[^>]*>(?:&nbsp;)?</span>", "", s)
    s = re.sub(r'<span[^>]*class="[^"]*(?:visuallyhidden|u-hiddenVisually)[^"]*"[^>]*>.*?</span>', "", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    s = htmlmod.unescape(s).replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def first(rx, text, flags=0):
    m = re.search(rx, text, flags) if isinstance(rx, str) else rx.search(text)
    if not m:
        return None
    return next((g for g in m.groups() if g is not None), None)


def num(s):
    if s is None:
        return None
    s = str(s).replace(",", "").replace(".", "").strip()
    return int(s) if s.isdigit() else None


# ---------------------------------------------------------------- avatar / banner helpers
def norm_img(u):
    if not u:
        return None
    u = htmlmod.unescape(u).replace("\\/", "/").split("?")[0]
    if u.startswith("//"):
        u = "https:" + u
    u = re.sub(r"^https?://web\.archive\.org/web/\d+(?:im_|id_)?/", "", u)
    if "profile_images" not in u and "default_profile" not in u:
        return None
    return u


def banner_from(text, uid=None):
    for m in re.finditer(r"profile_banners/(\d+)/(\d+)", text):
        if uid is None or m.group(1) == uid:
            return f"https://pbs.twimg.com/profile_banners/{m.group(1)}/{m.group(2)}"
    return None


# ---------------------------------------------------------------- profile pages (HTML)
RX = {
    "profilenav_id": re.compile(r'class="ProfileNav"[^>]*data-user-id="(\d+)"'),
    "rss_id": re.compile(r'statuses/user_timeline/(\d+)\.(?:rss|atom)'),
    "rest_id": re.compile(r'"rest_id"\s*:\s*"(\d+)"'),
    "identifier": re.compile(r'"identifier"\s*:\s*"(\d+)"'),
    "restId_js": re.compile(r'restId:"(\d+)"'),
    "data_user_id": re.compile(r'data-user-id="(\d+)"'),
    "page_sn": re.compile(r'<meta content="(' + SN + r')" name="page-user-screen_name"|name="page-user-screen_name" content="(' + SN + r')"'),
    "pnav_sn": re.compile(r'class="ProfileHeaderCard-screennameLink[^"]*"[^>]*href="/(' + SN + r')"'),
    "og_url": re.compile(r'<meta[^>]+content="https?://(?:mobile\.)?(?:twitter|x)\.com/(' + SN + r')"[^>]+property="og:url"|property="og:url"[^>]+content="https?://(?:mobile\.)?(?:twitter|x)\.com/(' + SN + r')"'),
    "title_sn": re.compile(r'\(@(' + SN + r')\)'),
    "screenName_js": re.compile(r'screenName:"(' + SN + r')"'),
    "avatar_new": re.compile(r'<img class="ProfileAvatar-image[^"]*" src="([^"]+)"'),
    "avatar_2013": re.compile(r'<img src="([^"]+profile_images[^"]+)"[^>]*class="avatar size(?:73|128)"'),
    "avatar_og": re.compile(r'<meta[^>]+content="([^"]+profile_images[^"]+)"[^>]+property="og:image"|property="og:image"[^>]+content="([^"]+profile_images[^"]+)"'),
    "avatar_intent": re.compile(r'<img class="photo" src="([^"]+profile_images[^"]+)"'),
    "avatar_mobile": re.compile(r'<td class="avatar">\s*(?:<a[^>]*>\s*)?<img[^>]*src="([^"]+profile_images[^"]+)"'),
    "avatar_json": re.compile(r'"profile_image_url(?:_https)?"\s*:\s*"([^"]+)"'),
    "avatar_old": re.compile(r'<img[^>]*id="profile-image"[^>]*src="([^"]+)"|<img[^>]*src="([^"]+)"[^>]*id="profile-image"'),
}

BIO_RX = [r'<p class="ProfileHeaderCard-bio[^"]*"[^>]*>(.*?)</p>',
          r'<span class="bio">(.*?)</span>',
          r'<p class="bio[^"]*"[^>]*>(.*?)</p>',
          r'<div class="bio">\s*<div[^>]*>(.*?)</div>']
LOC_RX = [r'<span class="ProfileHeaderCard-locationText[^"]*"[^>]*>(.*?)</span>',
          r'<span class="adr">(.*?)</span>',
          r'<span class="location[^"]*">(.*?)</span>',
          r'<p class="adr">\s*<span class="label">(.*?)</span>',
          r'<div class="location">(.*?)</div>']
WEB_RX = [r'<span class="ProfileHeaderCard-urlText[^"]*"[^>]*>\s*<a[^>]*title="([^"]+)"',
          r'<a href="([^"]+)" class="url" rel="me',
          r'<a class="url external" rel="me" href="[^"]*">([^<]+)</a>',
          r'<span class="url[^"]*">\s*<span[^>]*>\s*<a[^>]*title="([^"]+)"',
          r'<span class="url[^"]*">\s*<span[^>]*>\s*<a[^>]*href="(http[^"]+)"',
          r'<div class="url">\s*<div[^>]*>\s*<a[^>]*data-url="(http[^"]+)"']
NAME_RX = [r'<a href="/' + SN + r'" class="ProfileHeaderCard-nameLink[^"]*">(.*?)</a>',
           r'<img class="ProfileAvatar-image[^"]*" src="[^"]+" alt="([^"]*)"',
           r'<span class="fn">(.*?)</span>',
           r'<h1 class="fullname[^"]*"[^>]*>(.*?)</h1>',
           r'<div class="fullname">(.*?)<',
           r'<title[^>]*>\s*(?:Twitter / )?([^<(]{1,80}?)\s*\(@?' + SN + r'\)']
FOLLOWERS_RX = [r'ProfileNav-item--followers.*?data-count="?(\d+)',
                r'id="follower_count"[^>]*>\s*([\d,]+)',
                r'followers_count["\s:]+(\d+)',
                r'<a[^>]*href="/' + SN + r'/followers"[^>]*>\s*<div class="statnum">([\d,]+)']
TWEETS_RX = [r'ProfileNav-item--tweets.*?data-count="?(\d+)', r'id="update_count"[^>]*>\s*([\d,]+)']


def _first_of(rxs, text):
    for rx in rxs:
        v = first(rx, text, re.S)
        if v is not None:
            return v
    return None


def owner_id(text):
    uid = first(RX["profilenav_id"], text) or first(RX["rss_id"], text) or first(RX["restId_js"], text) \
        or first(RX["rest_id"], text) or first(RX["identifier"], text)
    if not uid:
        ids = Counter(RX["data_user_id"].findall(text))
        if ids:
            uid, n = ids.most_common(1)[0]
            if n < 3:
                uid = None
    if not uid:
        b = Counter(re.findall(r'profile_banners/(\d+)/', text))
        uid = b.most_common(1)[0][0] if b else None
    return uid


def parse_profile_html(text, url=""):
    """Archived/live profile page -> observation dict."""
    o = {"user_id": owner_id(text)}
    sn = first(RX["page_sn"], text) or first(RX["pnav_sn"], text) or first(RX["og_url"], text) \
        or first(RX["screenName_js"], text)
    if not sn and o["user_id"]:
        m = re.search(r'data-screen-name="(' + SN + r')"[^>]*data-user-id="' + o["user_id"] + '"', text)
        sn = m.group(1) if m else None
    if not sn:
        sn = first(RX["title_sn"], text[:30000])
    o["screen_name"] = sn
    av = first(RX["avatar_new"], text) or first(RX["avatar_2013"], text) or first(RX["avatar_og"], text) \
        or first(RX["avatar_mobile"], text) or _old_profile_image(text) or first(RX["avatar_intent"], text)
    if not av and len(text) < 20000:
        av = first(RX["avatar_json"], text)
    o["avatar"] = norm_img(av)
    o["name"] = clean(_first_of(NAME_RX, text))
    if o["name"] and (o["name"].lower() in ("twitter", "x") or len(o["name"]) > 60):
        o["name"] = None
    bio = _first_of(BIO_RX, text)
    if bio is None and re.search(r'\(@' + SN + r'\) (?:on X|/ X|\| Twitter)', text[:30000]) and "/status/" not in url:
        bio = first(r'<meta content="([^"]*)" (?:name="description"|property="og:description")|'
                    r'(?:name="description"|property="og:description") content="([^"]*)"', text)
        if bio and re.match(r"(?:The latest|Les derniers|Die neuesten)", bio):
            bio = None
    o["description"] = clean(bio) if bio is not None else None
    loc = _first_of(LOC_RX, text)
    o["location"] = clean(loc) if loc is not None else None
    web = _first_of(WEB_RX, text)
    o["website"] = clean(web) or None if web is not None else None
    o["banner"] = banner_from(text, o["user_id"])
    o["followers"] = num(_first_of(FOLLOWERS_RX, text))
    o["tweets"] = num(_first_of(TWEETS_RX, text))
    j = first(r'ProfileHeaderCard-joinDateText[^>]*title="[^"]*?(\d{1,2} \w{3} \d{4})"', text)
    if j:
        try:
            from datetime import datetime
            o["created_at"] = datetime.strptime(j, "%d %b %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    return o


def _old_profile_image(text):
    for m in RX["avatar_old"].finditer(text):
        u = next((g for g in m.groups() if g), None)
        if u and "_mini." not in u:
            return u
    return None


# ---------------------------------------------------------------- status pages (HTML)
def parse_status_html(text, url, target_uid=None):
    """Archived tweet page -> observation for the tweet author (+ authors of other tweets on page)."""
    out = {}
    # 2014-2020 desktop layout: every tweet div carries data-screen-name/data-name/data-user-id
    for m in re.finditer(r'data-screen-name="(' + SN + r')"\s+data-name="([^"]*)"\s+data-user-id="(\d+)"(.{0,4000})', text, re.S):
        sn, name, uid, tail = m.groups()
        av = first(r'<img class="avatar[^"]*js-action-profile-avatar[^"]*"\s+src="([^"]+)"', tail)
        cur = out.setdefault(uid, {"user_id": uid, "screen_name": sn, "name": clean(name), "_n": 0})
        cur["_n"] += 1
        if av and not cur.get("avatar"):
            cur["avatar"] = norm_img(av)
    if out:
        if target_uid and target_uid in out:
            o = out[target_uid]
        else:
            m = re.search(r"/status(?:es)?/(\d+)", url)
            tid = m.group(1) if m else None
            perma = re.search(r'data-tweet-id="' + (tid or "x") + r'"[^>]*?data-screen-name="(' + SN + r')"[^>]*?data-user-id="(\d+)"', text, re.S)
            o = out.get(perma.group(2)) if perma else max(out.values(), key=lambda x: x["_n"])
        o = dict(o)
        o.pop("_n", None)
        o["banner"] = banner_from(text, o["user_id"])
        return o
    o = {"user_id": None}
    # 2008-2012 layouts
    sn = first(RX["page_sn"], text)
    if sn:
        o["screen_name"] = sn
        o["name"] = clean(first(r'<div class="full-name">(.*?)</div>', text, re.S)
                          or first(r'<img alt="([^"]+)" class="photo fn"', text))
        o["avatar"] = norm_img(first(r'<div class="thumb">\s*<a[^>]*>\s*<img[^>]*src="([^"]+)"', text, re.S)
                               or first(r'class="tweet-url profile-pic url"[^>]*>\s*<img[^>]*src="([^"]+)"', text, re.S))
        return o
    # mobile.twitter.com 2013-2020
    if 'class="user-info"' in text or 'class="tweet-header' in text:
        o["screen_name"] = first(r'<span class="username">\s*(?:<span>@</span>)?@?(' + SN + r')', text) \
            or first(r'<div class="username">\s*<span[^>]*>@</span>(' + SN + r')', text)
        o["name"] = clean(first(r'<(?:div|strong) class="fullname">(.*?)</(?:div|strong)>', text, re.S))
        o["avatar"] = norm_img(first(r'<td class="avatar"[^>]*>\s*(?:<a[^>]*>\s*)?<img[^>]*src="([^"]+)"', text, re.S))
        if o.get("screen_name"):
            return o
    # 2023+ x.com server stub: og tags
    t = first(r'<meta content="([^"]+)" property="og:title"|property="og:title" content="([^"]+)"', text)
    if t:
        t = htmlmod.unescape(t)
        m = re.match(r"^(.*?) \(@(" + SN + r")\) on X", t)
        if m:
            o["name"], o["screen_name"] = m.group(1).strip(), m.group(2)
        else:
            m = re.match(r"^(.*?) on (?:Twitter|X)$", t)
            if m:
                o["name"] = m.group(1).strip()
                um = re.search(r"(?:twitter|x)\.com/(" + SN + r")/status", url)
                o["screen_name"] = um.group(1) if um else None
        o["avatar"] = norm_img(first(RX["avatar_og"], text))
        if o.get("screen_name") or o.get("avatar"):
            return o
    return None


# ---------------------------------------------------------------- JSON (API v1.1, v2, GraphQL captures)
def _v2_user(d):
    o = {"user_id": str(d.get("id")), "screen_name": d.get("username"), "name": d.get("name"),
         "description": d.get("description"), "location": d.get("location"),
         "avatar": norm_img(d.get("profile_image_url")), "created_at": parse_any_date(d.get("created_at")),
         "pinned_tweet_id": d.get("pinned_tweet_id"), "verified": d.get("verified"),
         "protected": d.get("protected")}
    pm = d.get("public_metrics") or {}
    o.update(followers=pm.get("followers_count"), following=pm.get("following_count"),
             tweets=pm.get("tweet_count"), likes=pm.get("like_count"))
    url = d.get("url")
    try:
        url = d["entities"]["url"]["urls"][0].get("expanded_url") or url
    except (KeyError, IndexError, TypeError):
        pass
    o["website"] = url
    return o


def _v1_user(d, uid=None):
    o = {"user_id": str(uid or d.get("id_str") or d.get("id")), "screen_name": d.get("screen_name"),
         "name": d.get("name"), "description": d.get("description"), "location": d.get("location"),
         "avatar": norm_img(d.get("profile_image_url_https") or d.get("profile_image_url")),
         "banner": (d.get("profile_banner_url") or None), "created_at": parse_any_date(d.get("created_at")),
         "followers": d.get("followers_count"), "following": d.get("friends_count"),
         "tweets": d.get("statuses_count"), "verified": d.get("verified"), "protected": d.get("protected")}
    url = d.get("url")
    try:
        url = d["entities"]["url"]["urls"][0].get("expanded_url") or url
    except (KeyError, IndexError, TypeError):
        pass
    o["website"] = url
    return o


def parse_json_users(obj):
    """Walk any JSON blob and return every user object found (deduped by id)."""
    found = {}

    def add(o):
        if not o.get("user_id") or o["user_id"] in ("None", ""):
            return
        if not o.get("screen_name"):
            return
        cur = found.setdefault(o["user_id"], {})
        for k, v in o.items():
            if v not in (None, "") and k not in cur:
                cur[k] = v

    def walk(x, depth=0):
        if depth > 40:
            return
        if isinstance(x, dict):
            if "username" in x and "id" in x and ("name" in x or "profile_image_url" in x):
                add(_v2_user(x))
            elif "screen_name" in x and ("id_str" in x or "id" in x) and "name" in x:
                add(_v1_user(x))
            elif "rest_id" in x and isinstance(x.get("legacy"), dict):
                leg = dict(x["legacy"])
                core = x.get("core") or {}
                leg.setdefault("screen_name", core.get("screen_name"))
                leg.setdefault("name", core.get("name"))
                leg.setdefault("created_at", core.get("created_at"))
                if isinstance(x.get("avatar"), dict):
                    leg.setdefault("profile_image_url_https", x["avatar"].get("image_url"))
                if isinstance(x.get("location"), dict):
                    leg.setdefault("location", x["location"].get("location"))
                if leg.get("screen_name"):
                    add(_v1_user(leg, x["rest_id"]))
            for v in x.values():
                walk(v, depth + 1)
        elif isinstance(x, list):
            for v in x:
                walk(v, depth + 1)

    walk(obj)
    return list(found.values())


def parse_json_text(text):
    try:
        return parse_json_users(json.loads(text))
    except Exception:
        return []


def json_author_id(text):
    try:
        j = json.loads(text)
        return str((j.get("data") or {}).get("author_id") or "") or None
    except Exception:
        return None


# ---------------------------------------------------------------- live x.com server-rendered profile
def parse_live_ssr(text):
    o = parse_profile_html(text, "https://x.com/profile")
    js = {}
    for k, rx in (("user_id", r'restId:"(\d+)"'), ("screen_name", r'screenName:"(' + SN + r')"'),
                  ("name", r',name:"((?:[^"\\]|\\.)*)",possiblySensitive'), ("location", r'location:"((?:[^"\\]|\\.)*)",name:'),
                  ("followers", r'followers:(\d+)'), ("following", r'following:(\d+)'), ("tweets", r'tweets:(\d+)')):
        v = first(rx, text)
        if v is not None:
            js[k] = v
    for k in ("name", "location"):
        if k in js:
            try:
                js[k] = json.loads('"' + js[k] + '"')
            except Exception:
                pass
    for k in ("followers", "following", "tweets"):
        if k in js:
            js[k] = int(js[k])
    ca = first(r'created_at_ms\W{1,6}(\d{12,14})\W{1,6}name\W{1,6}.{0,200}?screen_name\W{1,6}' + re.escape(js.get("screen_name") or "~") + r'\b', text)
    if ca:
        js["created_at"] = parse_any_date(int(ca))
    o.update({k: v for k, v in js.items() if v not in (None,)})
    if "location" in js and js["location"] == "":
        o["location"] = ""
    return o
