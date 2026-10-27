#!/usr/bin/env python3
"""
ECHO pivot - multi-platform public footprint for an email address, a username or a profile URL.

  python3 pivot.py <email | username | platform:username | profile URL> [--fast|--deep] [--out DIR]

Only public, logged-out pages and documented public APIs are read. It never tries to log in, never
submits sign-up / password-reset forms to test whether an email is registered, and never touches
breach dumps. A username existing on two sites does NOT prove they are the same person: results are
ranked by how strongly the profiles link to each other.
"""
import argparse
import hashlib
import html as htmlmod
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"})
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,24}$")
USER_RE = re.compile(r"^[A-Za-z0-9._\-]{1,40}$")
WEBMAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com",
           "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "pm.me", "gmx.com",
           "gmx.de", "mail.com", "yandex.com", "yandex.ru", "zoho.com", "tutanota.com", "tuta.io", "hey.com",
           "fastmail.com", "qq.com", "163.com", "web.de", "mail.ru", "rediffmail.com", "yahoo.co.uk", "yahoo.co.in"}
DISPOSABLE = {"mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "temp-mail.org",
              "yopmail.com", "trashmail.com", "sharklasers.com", "getnada.com", "dispostable.com",
              "maildrop.cc", "throwawaymail.com", "mohmal.com", "emailondeck.com", "fakeinbox.com"}

# platform -> where a link to it is recognised in other bios / link lists
LINK_PATTERNS = {
    "x": r"(?:twitter|x)\.com/(?!intent|share|home|i/)([A-Za-z0-9_]{1,15})",
    "instagram": r"instagram\.com/(?!p/|reel/|explore/)([A-Za-z0-9_.]{1,30})",
    "tiktok": r"tiktok\.com/@([A-Za-z0-9_.]{2,24})",
    "github": r"github\.com/(?!orgs/|sponsors/|features|about)([A-Za-z0-9\-]{1,39})",
    "gitlab": r"gitlab\.com/([A-Za-z0-9_.\-]{2,255})",
    "youtube": r"youtube\.com/@([A-Za-z0-9_.\-]{3,30})",
    "reddit": r"reddit\.com/(?:u|user)/([A-Za-z0-9_\-]{3,20})",
    "twitch": r"twitch\.tv/([A-Za-z0-9_]{3,25})",
    "telegram": r"(?:t\.me|telegram\.me)/([A-Za-z0-9_]{5,32})",
    "linktree": r"linktr\.ee/([A-Za-z0-9_.]{2,30})",
    "bluesky": r"bsky\.app/profile/([A-Za-z0-9.\-]{3,253})",
    "medium": r"medium\.com/@([A-Za-z0-9_.\-]{1,40})",
    "threads": r"threads\.(?:net|com)/@([A-Za-z0-9_.]{1,30})",
    "keybase": r"keybase\.io/([A-Za-z0-9_]{2,16})",
    "devto": r"dev\.to/([A-Za-z0-9_]{2,30})",
    "facebook": r"facebook\.com/(?!sharer|share|profile\.php|groups/|pages/)([A-Za-z0-9.]{5,50})",
    "linkedin": r"linkedin\.com/in/([A-Za-z0-9\-_%]{3,100})",
    "snapchat": r"snapchat\.com/add/([A-Za-z0-9_.\-]{3,15})",
}
PREFIX = {"x": "x", "twitter": "x", "tw": "x", "ig": "instagram", "insta": "instagram", "instagram": "instagram",
          "tt": "tiktok", "tiktok": "tiktok", "gh": "github", "github": "github", "gl": "gitlab", "gitlab": "gitlab",
          "yt": "youtube", "youtube": "youtube", "reddit": "reddit", "u": "reddit", "twitch": "twitch",
          "tg": "telegram", "telegram": "telegram", "bsky": "bluesky", "bluesky": "bluesky", "medium": "medium",
          "threads": "threads", "keybase": "keybase", "devto": "devto", "linktree": "linktree", "lt": "linktree",
          "mastodon": "mastodon", "hn": "hackernews", "chess": "chesscom", "lichess": "lichess",
          "docker": "dockerhub", "steam": "steam", "snap": "snapchat", "snapchat": "snapchat"}


def log(*a):
    print("[*]", *a, flush=True)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get(url, timeout=15, **kw):
    try:
        return S.get(url, timeout=timeout, **kw)
    except Exception:
        return None


def jget(url, timeout=15, **kw):
    r = get(url, timeout, **kw)
    if r is not None and r.status_code == 200:
        try:
            return r.json()
        except ValueError:
            return None
    return r.status_code if r is not None else None


def og(html_text):
    out = {}
    for k in ("og:title", "og:description", "og:image", "og:url", "description", "twitter:title"):
        m = re.search(r'<meta[^>]+(?:property|name)=["\']' + re.escape(k) + r'["\'][^>]+content=["\']([^"\']*)', html_text, re.I) \
            or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']' + re.escape(k) + r'["\']', html_text, re.I)
        if m:
            out[k] = htmlmod.unescape(m.group(1)).strip()
    return out


def hit(platform, url, status, **f):
    d = {"platform": platform, "url": url, "status": status}
    d.update({k: v for k, v in f.items() if v not in (None, "", [], {})})
    return d


# ------------------------------------------------------------------ username checks (public APIs / pages)
def c_x(u):
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", u):
        return hit("x", None, "not_found", note="not a valid X handle")
    j = jget(f"https://api.fxtwitter.com/{u}")
    if isinstance(j, dict) and j.get("user"):
        x = j["user"]
        web = (x.get("website") or {}).get("url") if isinstance(x.get("website"), dict) else x.get("website")
        return hit("x", f"https://x.com/{x.get('screen_name')}", "found", name=x.get("name"), bio=x.get("description"),
                   created=x.get("joined"), followers=x.get("followers"), avatar=x.get("avatar_url"),
                   location=x.get("location"), links=[web] if web else [], id=x.get("id"),
                   note="run the X deep-history hunt on this handle for username/bio/avatar history")
    return hit("x", f"https://x.com/{u}", "not_found" if j == 404 or (isinstance(j, dict)) else "unknown")


def c_github(u):
    j = jget(f"https://api.github.com/users/{u}", headers={"Accept": "application/vnd.github+json"})
    if not isinstance(j, dict):
        return hit("github", f"https://github.com/{u}", "not_found" if j == 404 else "unknown",
                   note="GitHub API rate limit (60/h unauthenticated)" if j in (403, 429) else None)
    links = [l for l in [j.get("blog"), f"https://x.com/{j['twitter_username']}" if j.get("twitter_username") else None] if l]
    emails = set()
    if j.get("email"):
        emails.add(j["email"])
    ev = jget(f"https://api.github.com/users/{u}/events/public?per_page=100")
    if isinstance(ev, list):  # emails written into the user's own public commit metadata
        for e in ev:
            for c in ((e.get("payload") or {}).get("commits") or []):
                em = (c.get("author") or {}).get("email") or ""
                if em and "noreply" not in em and EMAIL_RE.match(em):
                    emails.add(em.lower())
    soc = jget(f"https://api.github.com/users/{u}/social_accounts")
    if isinstance(soc, list):
        links += [s.get("url") for s in soc if s.get("url")]
    return hit("github", j.get("html_url"), "found", name=j.get("name"), bio=j.get("bio"), created=(j.get("created_at") or "")[:10],
               followers=j.get("followers"), avatar=j.get("avatar_url"), location=j.get("location"), company=j.get("company"),
               links=links, emails=sorted(emails), extra={"public_repos": j.get("public_repos"), "updated": (j.get("updated_at") or "")[:10]})


def c_gitlab(u):
    j = jget(f"https://gitlab.com/api/v4/users?username={quote(u)}")
    if isinstance(j, list) and j:
        x = j[0]
        d = jget(f"https://gitlab.com/api/v4/users/{x['id']}")
        d = d if isinstance(d, dict) else {}
        links = [l for l in [d.get("website_url"), d.get("twitter") and f"https://x.com/{d['twitter']}",
                             d.get("linkedin") and f"https://www.linkedin.com/in/{d['linkedin']}",
                             d.get("discord") and f"discord:{d['discord']}"] if l]
        return hit("gitlab", x.get("web_url"), "found", name=x.get("name"), bio=d.get("bio"), avatar=x.get("avatar_url"),
                   created=(d.get("created_at") or "")[:10], location=d.get("location"), links=links,
                   emails=[d["public_email"]] if d.get("public_email") else [])
    return hit("gitlab", f"https://gitlab.com/{u}", "not_found" if isinstance(j, list) else "unknown")


def c_reddit(u):
    j = jget(f"https://www.reddit.com/user/{u}/about.json", headers={"User-Agent": "echo-osint/3 (personal research tool)"})
    if isinstance(j, dict) and j.get("data") and not j["data"].get("is_suspended"):
        x = j["data"]
        sub = x.get("subreddit") or {}
        ts = x.get("created_utc")
        return hit("reddit", f"https://www.reddit.com/user/{x.get('name')}", "found", name=sub.get("title"),
                   bio=sub.get("public_description"), avatar=(x.get("icon_img") or "").split("?")[0],
                   created=datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else None,
                   extra={"karma": x.get("total_karma")})
    if isinstance(j, dict) and (j.get("data") or {}).get("is_suspended"):
        return hit("reddit", f"https://www.reddit.com/user/{u}", "found", note="account suspended")
    return hit("reddit", f"https://www.reddit.com/user/{u}", "not_found" if j == 404 else "unknown")


def c_bluesky(u):
    actor = u if "." in u else f"{u}.bsky.social"
    j = jget(f"https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor={quote(actor)}")
    if isinstance(j, dict) and j.get("did"):
        return hit("bluesky", f"https://bsky.app/profile/{j.get('handle')}", "found", name=j.get("displayName"),
                   bio=j.get("description"), avatar=j.get("avatar"), created=(j.get("createdAt") or "")[:10],
                   followers=j.get("followersCount"), id=j.get("did"))
    return hit("bluesky", f"https://bsky.app/profile/{actor}", "not_found" if j in (400, 404) else "unknown")


def c_keybase(u):
    j = jget(f"https://keybase.io/_/api/1.0/user/lookup.json?usernames={quote(u)}&fields=basics,profile,proofs_summary")
    them = (j or {}).get("them") if isinstance(j, dict) else None
    if them and them[0]:
        t = them[0]
        pr = ((t.get("proofs_summary") or {}).get("all") or [])
        return hit("keybase", f"https://keybase.io/{u}", "found", name=(t.get("profile") or {}).get("full_name"),
                   bio=(t.get("profile") or {}).get("bio"), location=(t.get("profile") or {}).get("location"),
                   links=[p.get("service_url") for p in pr if p.get("service_url")],
                   note="Keybase proofs are cryptographically signed by the account owner (strong link)")
    return hit("keybase", f"https://keybase.io/{u}", "not_found" if isinstance(j, dict) else "unknown")


def c_hackernews(u):
    j = jget(f"https://hacker-news.firebaseio.com/v0/user/{u}.json")
    if isinstance(j, dict) and j.get("id"):
        about = re.sub(r"<[^>]+>", " ", htmlmod.unescape(j.get("about") or "")).strip()
        return hit("hackernews", f"https://news.ycombinator.com/user?id={u}", "found", bio=about,
                   created=datetime.fromtimestamp(j["created"], tz=timezone.utc).strftime("%Y-%m-%d") if j.get("created") else None,
                   extra={"karma": j.get("karma")}, links=re.findall(r"https?://[^\s<>\"]+", htmlmod.unescape(j.get("about") or "")))
    return hit("hackernews", f"https://news.ycombinator.com/user?id={u}", "not_found" if j is None or j == 200 else "unknown")


def c_devto(u):
    j = jget(f"https://dev.to/api/users/by_username?url={quote(u)}")
    if isinstance(j, dict) and j.get("id"):
        links = [l for l in [j.get("website_url"), j.get("twitter_username") and f"https://x.com/{j['twitter_username']}",
                             j.get("github_username") and f"https://github.com/{j['github_username']}"] if l]
        return hit("devto", f"https://dev.to/{u}", "found", name=j.get("name"), bio=j.get("summary"), avatar=j.get("profile_image"),
                   location=j.get("location"), created=j.get("joined_at"), links=links)
    return hit("devto", f"https://dev.to/{u}", "not_found" if j == 404 else "unknown")


def c_chesscom(u):
    j = jget(f"https://api.chess.com/pub/player/{u.lower()}")
    if isinstance(j, dict) and j.get("player_id"):
        return hit("chesscom", j.get("url"), "found", name=j.get("name"), avatar=j.get("avatar"),
                   created=datetime.fromtimestamp(j["joined"], tz=timezone.utc).strftime("%Y-%m-%d") if j.get("joined") else None,
                   location=j.get("location"), extra={"country": (j.get("country") or "").rsplit("/", 1)[-1]})
    return hit("chesscom", f"https://www.chess.com/member/{u}", "not_found" if j == 404 else "unknown")


def c_lichess(u):
    j = jget(f"https://lichess.org/api/user/{u}")
    if isinstance(j, dict) and j.get("id") and not j.get("disabled"):
        p = j.get("profile") or {}
        return hit("lichess", j.get("url"), "found", name=" ".join(filter(None, [p.get("firstName"), p.get("lastName")])) or None,
                   bio=p.get("bio"), location=p.get("location"), links=re.findall(r"https?://\S+", p.get("links") or ""),
                   created=datetime.fromtimestamp(j["createdAt"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if j.get("createdAt") else None)
    return hit("lichess", f"https://lichess.org/@/{u}", "not_found" if j == 404 else "unknown")


def c_dockerhub(u):
    j = jget(f"https://hub.docker.com/v2/users/{u}/")
    if isinstance(j, dict) and j.get("id"):
        return hit("dockerhub", f"https://hub.docker.com/u/{u}", "found", name=j.get("full_name"), location=j.get("location"),
                   company=j.get("company"), created=(j.get("date_joined") or "")[:10], avatar=j.get("gravatar_url"),
                   links=[j["profile_url"]] if j.get("profile_url") else [])
    return hit("dockerhub", f"https://hub.docker.com/u/{u}", "not_found" if j == 404 else "unknown")


def c_mastodon(u):
    j = jget(f"https://mastodon.social/api/v1/accounts/lookup?acct={quote(u)}")
    if isinstance(j, dict) and j.get("id"):
        note = re.sub(r"<[^>]+>", " ", htmlmod.unescape(j.get("note") or "")).strip()
        links = [re.sub(r"<[^>]+>", "", htmlmod.unescape(f.get("value") or "")) for f in j.get("fields") or []]
        return hit("mastodon", j.get("url"), "found", name=j.get("display_name"), bio=note, avatar=j.get("avatar"),
                   created=(j.get("created_at") or "")[:10], followers=j.get("followers_count"),
                   links=[l for l in links if l.startswith("http")], note="mastodon.social only (other servers not searched)")
    return hit("mastodon", f"https://mastodon.social/@{u}", "not_found" if j == 404 else "unknown")


def c_gravatar_user(u):
    j = jget(f"https://en.gravatar.com/{u}.json")
    if isinstance(j, dict) and j.get("entry"):
        return gravatar_hit(j["entry"][0])
    return hit("gravatar", f"https://gravatar.com/{u}", "not_found" if j == 404 else "unknown")


def gravatar_hit(e):
    links = [a.get("url") for a in e.get("accounts") or [] if a.get("url")] + [x.get("value") for x in e.get("urls") or [] if x.get("value")]
    return hit("gravatar", e.get("profileUrl"), "found", name=e.get("displayName") or (e.get("name") or {}).get("formatted"),
               bio=e.get("aboutMe"), location=e.get("currentLocation"), avatar=e.get("thumbnailUrl"), links=links,
               extra={"username": e.get("preferredUsername")})


def c_telegram(u):
    r = get(f"https://t.me/{u}")
    if r is not None and r.status_code == 200:
        o = og(r.text)
        if 'tgme_page_title' in r.text and o.get("og:title") and not o.get("og:title", "").startswith("Telegram: Contact"):
            members = re.search(r'tgme_page_extra">([^<]+)', r.text)
            return hit("telegram", f"https://t.me/{u}", "found", name=o.get("og:title"), bio=o.get("og:description"),
                       avatar=o.get("og:image") if "telegram.org/img" not in (o.get("og:image") or "") else None,
                       extra={"page_extra": members.group(1).strip() if members else None})
        return hit("telegram", f"https://t.me/{u}", "not_found")
    return hit("telegram", f"https://t.me/{u}", "unknown")


def c_instagram(u):
    j = jget(f"https://i.instagram.com/api/v1/users/web_profile_info/?username={quote(u)}",
             headers={"x-ig-app-id": "936619743392459", "User-Agent": UA})
    if isinstance(j, dict) and (j.get("data") or {}).get("user"):
        x = j["data"]["user"]
        links = [l.get("url") for l in x.get("bio_links") or [] if l.get("url")] + ([x["external_url"]] if x.get("external_url") else [])
        return hit("instagram", f"https://www.instagram.com/{u}/", "found", name=x.get("full_name"), bio=x.get("biography"),
                   avatar=x.get("profile_pic_url"), followers=(x.get("edge_followed_by") or {}).get("count"), id=x.get("id"),
                   links=links, extra={"private": x.get("is_private"), "posts": (x.get("edge_owner_to_timeline_media") or {}).get("count"),
                                       "category": x.get("category_name"), "business_email": x.get("business_email")})
    if j == 404:
        return hit("instagram", f"https://www.instagram.com/{u}/", "not_found")
    # fallback: public page meta (often blocked logged-out)
    r = get(f"https://www.instagram.com/{u}/")
    if r is not None and r.status_code == 200:
        o = og(r.text)
        if o.get("og:description") and ("Followers" in o["og:description"] or "followers" in o["og:description"]):
            return hit("instagram", f"https://www.instagram.com/{u}/", "found", name=o.get("og:title"), bio=o.get("og:description"),
                       avatar=o.get("og:image"))
    return hit("instagram", f"https://www.instagram.com/{u}/", "unknown", note="Instagram rate-limits / login-walls logged-out reads")


def c_tiktok(u):
    r = get(f"https://www.tiktok.com/@{u}")
    if r is None or r.status_code not in (200, 404):
        return hit("tiktok", f"https://www.tiktok.com/@{u}", "unknown")
    m = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', r.text, re.S)
    if m:
        try:
            d = json.loads(m.group(1))
            ui = d["__DEFAULT_SCOPE__"]["webapp.user-detail"]
            if ui.get("statusCode") in (10221, 10202):
                return hit("tiktok", f"https://www.tiktok.com/@{u}", "not_found")
            usr, st = ui["userInfo"]["user"], ui["userInfo"].get("stats") or {}
            return hit("tiktok", f"https://www.tiktok.com/@{u}", "found", name=usr.get("nickname"), bio=usr.get("signature"),
                       avatar=usr.get("avatarLarger") or usr.get("avatarMedium"), id=usr.get("id"),
                       created=datetime.fromtimestamp(usr["createTime"], tz=timezone.utc).strftime("%Y-%m-%d") if usr.get("createTime") else None,
                       followers=st.get("followerCount"), links=[(usr.get("bioLink") or {}).get("link")] if (usr.get("bioLink") or {}).get("link") else [],
                       extra={"private": usr.get("privateAccount"), "videos": st.get("videoCount"), "region": usr.get("region"),
                              "nickname_modified": usr.get("nickNameModifyTime"), "unique_id_modified": usr.get("uniqueIdModifyTime")})
        except Exception:
            pass
    return hit("tiktok", f"https://www.tiktok.com/@{u}", "unknown", note="TikTok served a bot-check page")


def c_youtube(u):
    r = get(f"https://www.youtube.com/@{u}", headers={"Cookie": "CONSENT=YES+1"})
    if r is not None and r.status_code == 200:
        o = og(r.text)
        cid = re.search(r'"(?:externalId|channelId)":"(UC[\w-]{22})"', r.text)
        subs = re.search(r'"subscriberCountText":\{"accessibility":\{"accessibilityData":\{"label":"([^"]+)"', r.text) or \
            re.search(r'([\d.,]+[KMB]?) subscribers', r.text)
        return hit("youtube", f"https://www.youtube.com/@{u}", "found", name=o.get("og:title"), bio=o.get("og:description"),
                   avatar=o.get("og:image"), id=cid.group(1) if cid else None, extra={"subscribers": subs.group(1) if subs else None})
    return hit("youtube", f"https://www.youtube.com/@{u}", "not_found" if r is not None and r.status_code == 404 else "unknown")


def c_twitch(u):
    r = get(f"https://www.twitch.tv/{u}")
    if r is not None and r.status_code == 200:
        o = og(r.text)
        if o.get("og:title") and o["og:title"].lower() != "twitch" and u.lower() in r.text.lower():
            return hit("twitch", f"https://www.twitch.tv/{u}", "found", name=o.get("og:title"), bio=o.get("og:description"),
                       avatar=o.get("og:image"), note="existence from page meta (Twitch API needs a client id)")
        return hit("twitch", f"https://www.twitch.tv/{u}", "not_found")
    return hit("twitch", f"https://www.twitch.tv/{u}", "unknown")


def c_linktree(u):
    r = get(f"https://linktr.ee/{u}")
    if r is not None and r.status_code == 200:
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
        try:
            pp = json.loads(m.group(1))["props"]["pageProps"]
            acc = pp.get("account") or {}
            links = [l.get("url") for l in acc.get("links") or pp.get("links") or [] if l.get("url")]
            links += [s.get("url") for s in acc.get("socialLinks") or [] if s.get("url")]
            return hit("linktree", f"https://linktr.ee/{u}", "found", name=acc.get("pageTitle") or acc.get("username"),
                       bio=acc.get("description"), avatar=acc.get("profilePictureUrl"), links=links)
        except Exception:
            return hit("linktree", f"https://linktr.ee/{u}", "found")
    return hit("linktree", f"https://linktr.ee/{u}", "not_found" if r is not None and r.status_code == 404 else "unknown")


def c_medium(u):
    r = get(f"https://medium.com/@{u}")
    if r is not None and r.status_code == 200:
        o = og(r.text)
        if o.get("og:title") and "Medium" in o.get("og:title", "") and "@" + u.lower() in r.text.lower():
            return hit("medium", f"https://medium.com/@{u}", "found", name=o["og:title"].replace(" – Medium", ""),
                       bio=o.get("og:description"), avatar=o.get("og:image"))
    return hit("medium", f"https://medium.com/@{u}", "not_found" if r is not None and r.status_code == 404 else "unknown")


def c_steam(u):
    r = get(f"https://steamcommunity.com/id/{u}/?xml=1")
    if r is not None and r.status_code == 200 and "<steamID64>" in r.text:
        g = lambda t: (re.search(rf"<{t}><!\[CDATA\[(.*?)\]\]></{t}>", r.text, re.S) or re.search(rf"<{t}>(.*?)</{t}>", r.text, re.S))
        sid = g("steamID64")
        name, summ, av, loc, since = g("steamID"), g("summary"), g("avatarFull"), g("location"), g("memberSince")
        return hit("steam", f"https://steamcommunity.com/id/{u}", "found", name=name and name.group(1), id=sid and sid.group(1),
                   bio=summ and re.sub(r"<[^>]+>", " ", summ.group(1)).strip(), avatar=av and av.group(1),
                   location=loc and loc.group(1), created=since and since.group(1))
    return hit("steam", f"https://steamcommunity.com/id/{u}", "not_found" if r is not None and r.status_code == 200 else "unknown")


def c_npm(u):
    j = jget(f"https://registry.npmjs.org/-/v1/search?text=maintainer:{quote(u)}&size=5")
    if isinstance(j, dict) and j.get("total"):
        return hit("npm", f"https://www.npmjs.com/~{u}", "found",
                   extra={"packages": j.get("total"), "sample": [o["package"]["name"] for o in j.get("objects", [])[:5]]})
    return hit("npm", f"https://www.npmjs.com/~{u}", "not_found" if isinstance(j, dict) else "unknown")


def c_threads(u):
    r = get(f"https://www.threads.net/@{u}")
    if r is not None and r.status_code == 200:
        o = og(r.text)
        if o.get("og:title") and f"@{u.lower()}" in o["og:title"].lower():
            return hit("threads", f"https://www.threads.net/@{u}", "found", name=o.get("og:title"), bio=o.get("og:description"),
                       avatar=o.get("og:image"))
        return hit("threads", f"https://www.threads.net/@{u}", "not_found")
    return hit("threads", f"https://www.threads.net/@{u}", "unknown")


CHECKS = {"x": c_x, "github": c_github, "gitlab": c_gitlab, "reddit": c_reddit, "bluesky": c_bluesky, "keybase": c_keybase,
          "hackernews": c_hackernews, "devto": c_devto, "chesscom": c_chesscom, "lichess": c_lichess, "dockerhub": c_dockerhub,
          "mastodon": c_mastodon, "gravatar": c_gravatar_user, "telegram": c_telegram, "instagram": c_instagram,
          "tiktok": c_tiktok, "youtube": c_youtube, "twitch": c_twitch, "linktree": c_linktree, "medium": c_medium,
          "steam": c_steam, "npm": c_npm, "threads": c_threads}
FAST = ["x", "github", "reddit", "instagram", "tiktok", "youtube", "telegram", "bluesky", "keybase", "gitlab",
        "linktree", "twitch", "gravatar", "hackernews", "mastodon"]


# ------------------------------------------------------------------ email checks
def email_checks(email, deep):
    out = {"email": email, "findings": [], "domain": {}}
    local, dom = email.split("@", 1)
    dom = dom.lower()
    md5 = hashlib.md5(email.strip().lower().encode()).hexdigest()
    sha = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    # Gravatar: public profile keyed by the email hash (the owner chose to publish it)
    j = jget(f"https://en.gravatar.com/{md5}.json")
    if isinstance(j, dict) and j.get("entry"):
        out["findings"].append(dict(gravatar_hit(j["entry"][0]), how="Gravatar profile for this email hash"))
    else:
        r = get(f"https://gravatar.com/avatar/{md5}?d=404&s=200")
        if r is not None and r.status_code == 200:
            out["findings"].append(hit("gravatar", f"https://gravatar.com/avatar/{md5}?s=400", "found",
                                       avatar=f"https://gravatar.com/avatar/{md5}?s=400", how="avatar exists for this email hash (no public profile)"))
    j = jget(f"https://api.gravatar.com/v3/profiles/{sha}")
    if isinstance(j, dict) and j.get("display_name"):
        out["findings"].append(hit("gravatar", j.get("profile_url"), "found", name=j.get("display_name"), bio=j.get("description"),
                                   location=j.get("location"), avatar=j.get("avatar_url"),
                                   links=[a.get("url") for a in j.get("verified_accounts") or [] if a.get("url")],
                                   how="Gravatar v3 profile (verified accounts)"))
    # GitHub: commits authored with this email + users that list it publicly
    j = jget(f"https://api.github.com/search/commits?q=author-email:{quote(email)}&per_page=20",
             headers={"Accept": "application/vnd.github.cloak-preview+json"})
    if isinstance(j, dict) and j.get("total_count"):
        users, names = {}, set()
        for it in j.get("items") or []:
            a = it.get("author") or {}
            if a.get("login"):
                users[a["login"]] = a.get("html_url")
            n = ((it.get("commit") or {}).get("author") or {}).get("name")
            if n:
                names.add(n)
        out["findings"].append(hit("github", f"https://github.com/search?q=author-email%3A{quote(email)}&type=commits", "found",
                                   how=f"{j['total_count']} public commit(s) authored with this email",
                                   links=list(users.values()), extra={"accounts": sorted(users), "commit_author_names": sorted(names)}))
    j = jget(f"https://api.github.com/search/users?q={quote(email)}+in:email")
    if isinstance(j, dict) and j.get("total_count"):
        out["findings"].append(hit("github", None, "found", how="GitHub user(s) showing this email publicly",
                                   links=[i.get("html_url") for i in j.get("items") or []]))
    # OpenPGP: verified keys only (keys.openpgp.org publishes an email only after the owner confirms it)
    r = get(f"https://keys.openpgp.org/vks/v1/by-email/{quote(email)}")
    if r is not None and r.status_code == 200 and "BEGIN PGP PUBLIC KEY" in r.text:
        out["findings"].append(hit("openpgp", f"https://keys.openpgp.org/search?q={quote(email)}", "found",
                                   how="verified OpenPGP key published for this email"))
    # Keybase lookup by email is not public any more; skip. Domain intelligence:
    d = out["domain"]
    d["name"] = dom
    d["type"] = "disposable" if dom in DISPOSABLE else ("webmail" if dom in WEBMAIL else "custom/organisation")
    mx = jget(f"https://dns.google/resolve?name={quote(dom)}&type=MX")
    if isinstance(mx, dict):
        d["mx"] = sorted({a.get("data", "").split()[-1].rstrip(".") for a in mx.get("Answer") or [] if a.get("data")})
        d["accepts_mail"] = bool(d["mx"])
        prov = " ".join(d["mx"]).lower()
        d["mail_provider"] = ("Google Workspace" if "google" in prov else "Microsoft 365" if "outlook" in prov or "protection.outlook" in prov
                              else "Proton" if "proton" in prov else "Zoho" if "zoho" in prov else None)
    if d["type"] == "custom/organisation":
        rd = jget(f"https://rdap.org/domain/{quote(dom)}")
        if isinstance(rd, dict):
            ev = {e.get("eventAction"): (e.get("eventDate") or "")[:10] for e in rd.get("events") or []}
            d["registered"] = ev.get("registration")
            d["expires"] = ev.get("expiration")
            regs = [en for en in rd.get("entities") or [] if "registrar" in (en.get("roles") or [])]
            if regs:
                vc = (regs[0].get("vcardArray") or [None, []])[1]
                d["registrar"] = next((v[3] for v in vc if v and v[0] == "fn"), None)
        r = get(f"https://{dom}/", timeout=12)
        if r is not None and r.status_code < 400:
            t = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.S | re.I)
            d["website_title"] = htmlmod.unescape(t.group(1)).strip()[:120] if t else None
            d["website"] = r.url
    out["hashes"] = {"md5": md5, "sha256": sha}
    out["manual_links"] = {
        "Have I Been Pwned (breach exposure, needs a browser)": f"https://haveibeenpwned.com/account/{quote(email)}",
        "Google exact-match search": f"https://www.google.com/search?q=%22{quote(email)}%22",
        "Bing exact-match search": f"https://www.bing.com/search?q=%22{quote(email)}%22",
        "GitHub code search": f"https://github.com/search?q=%22{quote(email)}%22&type=code",
    }
    return out


# ------------------------------------------------------------------ orchestration
def classify(raw):
    t = raw.strip()
    if EMAIL_RE.match(t):
        return "email", None, t.lower()
    if re.match(r"^https?://", t, re.I) or re.match(r"^(www\.)?[a-z0-9\-]+\.[a-z]{2,}/", t, re.I):
        url = t if t.lower().startswith("http") else "https://" + t
        for plat, pat in LINK_PATTERNS.items():
            m = re.search(pat, url, re.I)
            if m:
                return "username", plat, m.group(1)
        return "url", None, url
    m = re.match(r"^([a-z]{1,10}):@?(.+)$", t, re.I)
    if m and m.group(1).lower() in PREFIX:
        return "username", PREFIX[m.group(1).lower()], m.group(2).strip()
    return "username", None, t.lstrip("@")


def extract_links(text):
    out = set()
    for plat, pat in LINK_PATTERNS.items():
        for m in re.finditer(pat, text or "", re.I):
            out.add((plat, m.group(1).rstrip(".")))
    return out


def run(raw, level="thorough"):
    t0 = time.time()
    kind, plat, value = classify(raw)
    D = {"query": raw, "kind": kind, "platform_hint": plat, "value": value, "generated": now_iso(), "mode": level,
         "email": None, "profiles": [], "not_found": [], "unknown": [], "linked_accounts": [], "emails_found": [],
         "notes": []}
    usernames = []
    if kind == "email":
        log(f"email pivot: {value}")
        E = email_checks(value, level == "deep")
        D["email"] = E
        for f in E["findings"]:
            selfdecl = (f.get("how") or "").startswith("GitHub user(s)")
            for l in f.get("links") or []:
                for p, u in extract_links(l):
                    usernames.append((p, u, "GitHub profile lists this email (self-declared, unverified)" if selfdecl
                                      else f"linked from {f['platform']} ({f.get('how')})"))
            for acc in (f.get("extra") or {}).get("accounts") or []:
                usernames.append(("github", acc, "authored public commits with this email"))
            un = (f.get("extra") or {}).get("username")
            if un:
                usernames.append((None, un, "Gravatar username"))
        local = value.split("@")[0]
        if USER_RE.match(local) and len(local) >= 3 and level != "fast":
            usernames.append((None, re.sub(r"[+].*$", "", local), "email local-part (guess - same string, maybe not same person)"))
        log(f"email pivot: {len(E['findings'])} direct finding(s); domain {E['domain'].get('name')} ({E['domain'].get('type')})")
    elif kind == "url":
        r = get(value)
        if r is not None and r.status_code < 400:
            o = og(r.text)
            D["profiles"].append(hit("web", value, "found", name=o.get("og:title"), bio=o.get("og:description") or o.get("description"),
                                     links=sorted({m for m in re.findall(r'href="(https?://[^"]+)"', r.text)
                                                   if any(re.search(p, m, re.I) for p in LINK_PATTERNS.values())})[:40]))
            for l in D["profiles"][-1].get("links", []):
                for p, u in extract_links(l):
                    usernames.append((p, u, "linked from the page"))
    else:
        usernames.append((plat, value, "input"))

    # sweep: given-platform first, then the same username across platforms
    seen = set()
    queue = []
    for p, u, why in usernames:
        if not USER_RE.match(u or ""):
            continue
        plats = [p] if p else []
        if why in ("input", "Gravatar username") or why.startswith("email local-part"):
            plats += [q for q in (FAST if level == "fast" else CHECKS) if q != p]
        for q in plats:
            if q in CHECKS and (q, u.lower()) not in seen:
                seen.add((q, u.lower()))
                queue.append((q, u, why))
    found_links = []

    def do(q, u, why):
        try:
            h = CHECKS[q](u)
        except Exception as e:
            h = hit(q, None, "unknown", note=f"error: {type(e).__name__}")
        h["queried"], h["why"] = u, why
        return h

    deadline = t0 + {"fast": 90, "thorough": 180, "deep": 300}.get(level, 180)
    rounds = 0
    while queue and time.time() < deadline and rounds < (1 if level == "fast" else 2):
        rounds += 1
        log(f"checking {len(queue)} platform/username pair(s) (round {rounds}) ...")
        batch, queue = queue, []
        with ThreadPoolExecutor(8) as ex:
            futs = [ex.submit(do, *b) for b in batch]
            for f in as_completed(futs):
                h = f.result()
                bucket = {"found": "profiles", "not_found": "not_found"}.get(h["status"], "unknown")
                D[bucket].append(h)
                if h["status"] == "found":
                    log(f"  + {h['platform']}: {h.get('url')}  {(h.get('name') or '')[:40]}")
                    txt = " ".join([h.get("bio") or ""] + [l for l in h.get("links") or [] if l])
                    for p, u in extract_links(txt):
                        found_links.append({"from": h["platform"], "from_url": h.get("url"), "platform": p, "username": u})
                        if p in CHECKS and (p, u.lower()) not in seen and USER_RE.match(u):
                            seen.add((p, u.lower()))
                            queue.append((p, u, f"linked from {h['platform']} profile"))
                    for em in h.get("emails") or []:
                        if em.lower() not in {e["email"] for e in D["emails_found"]}:
                            D["emails_found"].append({"email": em.lower(), "from": h["platform"], "url": h.get("url")})
    D["linked_accounts"] = found_links
    # confidence: a profile is "linked" when another found profile (or the email) points to it
    linked = {(l["platform"], l["username"].lower()) for l in found_links}
    for h in D["profiles"]:
        key = (h["platform"], (h.get("queried") or "").lower())
        if "self-declared" in (h.get("why") or ""):
            h["confidence"] = "medium"
        elif h.get("why") == "input" or "linked from" in (h.get("why") or "") or "commits with this email" in (h.get("why") or ""):
            h["confidence"] = "high" if h.get("why") != "input" else "target"
        elif key in linked:
            h["confidence"] = "high"
        else:
            h["confidence"] = "username match only"
    if any(h["platform"] == "x" for h in D["profiles"]):
        D["notes"].append("An X account was found - run ECHO's X deep-history mode on it for its username / bio / avatar history.")
    D["notes"].append("Same username on two sites is not proof of the same person; trust 'high' (cross-linked) results first.")
    D["notes"].append("'unknown' = the site blocked or rate-limited the logged-out check (Instagram/TikTok/Threads often do); "
                      "open the link manually.")
    D["notes"].append("No registration probing (password-reset / sign-up checks) and no breach databases are used.")
    D["runtime_s"] = round(time.time() - t0)
    return D


def esc(s, n=160):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    s = s.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")
    return s[:n] + ("…" if len(s) > n else "")


def to_markdown(D):
    md = [f"# ECHO pivot - {esc(D['query'], 80)}", "",
          f"Generated {D['generated']} · runtime {D['runtime_s']}s · mode {D['mode']} · input type: {D['kind']}"
          + (f" ({D['platform_hint']})" if D.get("platform_hint") else ""), ""]
    E = D.get("email")
    if E:
        dm = E["domain"]
        md += ["## Email", "", f"- address: `{esc(E['email'])}`",
               f"- domain: {esc(dm.get('name'))} — {dm.get('type')}"
               + (f", mail by {dm['mail_provider']}" if dm.get("mail_provider") else "")
               + (f", MX: {esc(', '.join(dm.get('mx') or []), 120)}" if dm.get("mx") else ", no MX record (cannot receive mail)" if "mx" in dm else "")]
        if dm.get("registered"):
            md.append(f"- domain registered {dm['registered']} (expires {dm.get('expires')}) via {esc(dm.get('registrar'), 60)}")
        if dm.get("website_title"):
            md.append(f"- domain website: [{esc(dm['website_title'], 80)}]({dm.get('website')})")
        md.append(f"- Gravatar hash: `{E['hashes']['md5']}`")
        md += ["", "| source | what | link |", "|---|---|---|"]
        for f in E["findings"]:
            extra = f.get("extra") or {}
            what = esc(f.get("how")) + (f" — name: {esc(f.get('name'), 50)}" if f.get("name") else "") + \
                (f" — accounts: {esc(', '.join(extra.get('accounts') or []), 80)}" if extra.get("accounts") else "") + \
                (f" — commit names: {esc(', '.join(extra.get('commit_author_names') or []), 80)}" if extra.get("commit_author_names") else "")
            md.append(f"| {f['platform']} | {what} | {('[open](' + f['url'] + ')') if f.get('url') else ''} |")
        if not E["findings"]:
            md.append("| — | no public profile, commit or key is tied to this email | |")
        md += ["", "Manual checks (open in your browser):"]
        for k, v in E["manual_links"].items():
            md.append(f"- [{k}]({v})")
        md.append("")
    md += ["## Accounts found", ""]
    if D["profiles"]:
        md += ["| platform | profile | name | created | followers | confidence | why checked | bio |", "|---|---|---|---|---|---|---|---|"]
        order = {"target": 0, "high": 1, "medium": 2, "username match only": 3}
        for h in sorted(D["profiles"], key=lambda h: (order.get(h.get("confidence"), 3), h["platform"])):
            md.append(f"| {h['platform']} | {('[' + esc(h.get('queried') or h['url'], 40) + '](' + h['url'] + ')') if h.get('url') else ''} | "
                      f"{esc(h.get('name'), 40)} | {esc(h.get('created'), 12)} | {esc(h.get('followers'), 12)} | {h.get('confidence')} | "
                      f"{esc(h.get('why'), 50)} | {esc(h.get('bio'), 140)} |")
        md += ["", "### Details", ""]
        for h in D["profiles"]:
            bits = []
            for k in ("id", "location", "company", "note"):
                if h.get(k):
                    bits.append(f"{k}: {esc(h[k], 100)}")
            for k, v in (h.get("extra") or {}).items():
                if v not in (None, "", [], {}):
                    bits.append(f"{k}: {esc(v if not isinstance(v, list) else ', '.join(map(str, v)), 100)}")
            if h.get("links"):
                bits.append("links: " + ", ".join(f"[{esc(l, 50)}]({l})" if str(l).startswith("http") else esc(l, 50) for l in h["links"][:12]))
            if h.get("emails"):
                bits.append("emails: " + ", ".join(f"`{esc(e)}`" for e in h["emails"]))
            if h.get("avatar") and str(h["avatar"]).startswith("http"):
                bits.append(f"[avatar]({h['avatar']})")
            md.append(f"- **{h['platform']} / {esc(h.get('queried'), 40)}** — " + (" · ".join(bits) if bits else "no extra public fields"))
    else:
        md.append("_No public account found._")
    if D["emails_found"]:
        md += ["", "## Emails seen in public profile / commit data", ""]
        for e in D["emails_found"]:
            md.append(f"- `{esc(e['email'])}` — from {e['from']} ({e.get('url')})")
    if D["linked_accounts"]:
        md += ["", "## Cross-links between profiles", ""]
        for l in D["linked_accounts"]:
            md.append(f"- {l['from']} → {l['platform']}: {esc(l['username'], 60)}")
    if D["unknown"]:
        md += ["", "## Could not check (blocked / rate-limited)", ""]
        md.append(", ".join(f"[{h['platform']}]({h['url']})" if h.get("url") else h["platform"] for h in D["unknown"]))
    if D["not_found"]:
        md += ["", "## Not found", "", ", ".join(sorted({h["platform"] for h in D["not_found"]}))]
    md += ["", "## Notes", ""] + [f"- {n}" for n in D["notes"]]
    return "\n".join(md) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Public multi-platform footprint for an email, username or profile URL.")
    ap.add_argument("target")
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--deep", action="store_true")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    level = "deep" if a.deep else ("fast" if a.fast else "thorough")
    D = run(a.target, level)
    os.makedirs(a.out, exist_ok=True)
    base = os.path.join(a.out, re.sub(r"[^A-Za-z0-9_]", "_", a.target.strip().lstrip("@")))
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(D, f, indent=2, ensure_ascii=False, default=str)
    with open(base + "-report.md", "w", encoding="utf-8") as f:
        f.write(to_markdown(D))
    print(f"\n[*] pivot done in {D['runtime_s']}s: {len(D['profiles'])} account(s) found, "
          f"{len(D['unknown'])} blocked, {len(D['not_found'])} not found -> {base}-report.md", flush=True)


if __name__ == "__main__":
    main()
