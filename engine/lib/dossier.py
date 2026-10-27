"""Investigator-brief layer for finished X-history hunts.

enrich(D, mode)  -> after a thorough/deep hunt, re-uses the existing public modules (engine/pivot.py and
                    engine/breach.py) on the current handle + former handles the hunt already proved, and returns a
                    compact dict that is stored as D["dossier_extra"]. Nothing new is collected here: no logins, no
                    paid gates, no leaked records (breach.py keeps breach *names/metadata* only).
brief(D)         -> plain-English sections (Overview, Username history, Other profiles, Contacts listed publicly,
                    Breach notes, Timeline) used by both the HTML report and the markdown report.

Confidence is kept honest: a profile that merely uses the same username elsewhere is labelled
"Same username only" unless it links back to one of the account's proven X handles.
"""
import contextlib
import io
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PLAT = {"x": "X", "github": "GitHub", "gitlab": "GitLab", "youtube": "YouTube", "tiktok": "TikTok", "bluesky": "Bluesky",
        "hackernews": "Hacker News", "linktree": "Linktree", "keybase": "Keybase", "reddit": "Reddit", "instagram": "Instagram",
        "threads": "Threads", "telegram": "Telegram", "gravatar": "Gravatar", "devto": "DEV", "chesscom": "Chess.com",
        "lichess": "Lichess", "dockerhub": "Docker Hub", "mastodon": "Mastodon", "twitch": "Twitch", "medium": "Medium",
        "steam": "Steam", "npm": "npm", "web": "Website"}
SURE = {"high": "Strong evidence", "target": "Strong evidence", "medium": "Likely", "low": "Possible",
        "same username only": "Same username only", "username match only": "Same username only"}
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
PLAIN_SRC = {"wayback": "Internet Archive", "memory.lol": "memory.lol", "frozen-handle": "X embedded posts",
             "syndication": "X embedded posts", "live": "live profile", "archive.today": "archive.today",
             "nitter": "Nitter mirror", "life-scan": "post-by-post scan", "commoncrawl": "Common Crawl",
             "ghostarchive": "Ghostarchive"}
LIMITS = {"thorough": dict(handles=3, pivot="fast", emails=3), "deep": dict(handles=5, pivot="fast", emails=5)}
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{3,}\.[A-Za-z]{2,24}$")


def plat(p):
    return PLAT.get(p, (p or "").capitalize())


def sure(c):
    return SURE.get(c or "", "Possible")


def when(s):
    m = re.match(r"(\d{4})-(\d{2})", str(s or ""))
    return f"{MON[int(m.group(2)) - 1]} {m.group(1)}" if m else (str(s) if s else "")


def plain_sources(srcs):
    out = []
    for s in srcs or []:
        s = str(s)
        n = next((v for k, v in PLAIN_SRC.items() if k in s), s.split(":")[0])
        if n not in out:
            out.append(n)
    return out


def big(u):
    return re.sub(r"_(normal|bigger|mini|reasonably_small)(\.\w+)$", r"_400x400\2", u) if u else u


def handles_of(D):
    acc = D.get("account") or {}
    hs = [acc.get("current_handle")] + [s.get("value") for s in (D.get("username_history") or [])]
    for v in ((D.get("life_scan") or {}).get("handles") or {}).values():
        hs.append(v.get("handle"))
    for v in ((D.get("frozen_handles") or {}).get("handles") or {}).values():
        hs.append(v.get("handle"))
    out, seen = [], set()
    for h in hs:
        if h and re.fullmatch(r"[A-Za-z0-9_]{1,15}", str(h)) and h.lower() not in seen:
            seen.add(h.lower())
            out.append(h)
    return out


def _join(items):
    items = [x for x in items if x]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


# ------------------------------------------------------------------ enrichment (existing public modules only)
def _quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return fn(*a, **k)


def enrich(D, mode, log=print):
    """Call pivot + breach on proven handles; fold public hits into D['dossier_extra']."""
    if mode not in LIMITS:
        return None
    if ENGINE not in sys.path:
        sys.path.insert(0, ENGINE)
    PV = BR = None
    try:
        import pivot as PV  # noqa: N812
    except Exception as e:
        log(f"dossier: pivot module unavailable ({type(e).__name__})")
    try:
        import breach as BR  # noqa: N812
    except Exception as e:
        log(f"dossier: breach module unavailable ({type(e).__name__})")
    if not PV and not BR:
        return None

    lim = LIMITS[mode]
    handles = handles_of(D)[:lim["handles"]]
    if not handles:
        return None
    known = {h.lower() for h in handles_of(D)}
    log(f"dossier: cross-checking {', '.join('@' + h for h in handles)} on other public sites")

    pv, br_u = {}, {}
    with ThreadPoolExecutor(6) as ex:
        pf = {h: ex.submit(_quiet, PV.run, h, lim["pivot"]) for h in handles} if PV else {}
        bf = {h: ex.submit(_quiet, BR.run, h, None, h, "fast") for h in handles} if BR else {}
        for h, f in pf.items():
            try:
                pv[h] = f.result(timeout=240)
            except Exception as e:
                log(f"dossier: pivot @{h} failed ({type(e).__name__})")
        for h, f in bf.items():
            try:
                br_u[h] = f.result(timeout=120)
            except Exception as e:
                log(f"dossier: breach @{h} failed ({type(e).__name__})")

    x_link = re.compile(
        r"(?:twitter|x)\.com/(?:#!/)?@?([A-Za-z0-9_]{1,15})\b|(?<![\w.])@([A-Za-z0-9_]{1,15})\b", re.I)
    profiles, seen_p = [], set()
    contacts, seen_c = [], set()
    for h, P in pv.items():
        for p in P.get("profiles") or []:
            if p.get("platform") == "x" or not p.get("url"):
                continue
            key = p["url"].rstrip("/").lower()
            if key in seen_p:
                continue
            seen_p.add(key)
            txt = " ".join([p.get("bio") or ""] + [str(l) for l in (p.get("links") or [])])
            backs = {(a or b).lower() for a, b in x_link.findall(txt)} & known
            why = p.get("why") or ""
            raw_conf = p.get("confidence") or "username match only"
            if backs:
                conf, reason = "high", "Its profile links back to @" + sorted(backs)[0] + " on X."
            elif "linked from" in why:
                conf, reason = "high", "Linked from another profile found in this search."
            elif raw_conf in ("high", "medium", "target"):
                conf, reason = ("medium" if raw_conf == "target" else raw_conf), why or "Listed publicly."
            else:
                conf, reason = "same username only", (
                    "Same username as @" + h + " — not proof it’s the same person.")
            profiles.append({
                "platform": p.get("platform"), "url": p.get("url"), "name": p.get("name"),
                "handle": p.get("queried") or h, "bio": (p.get("bio") or "")[:280] or None,
                "avatar": p.get("avatar"), "confidence": conf, "label": sure(conf), "reason": reason,
                "via_handle": h,
            })
            for em in p.get("emails") or []:
                em = (em or "").strip().lower()
                if EMAIL_RE.match(em) and em not in seen_c:
                    seen_c.add(em)
                    contacts.append({"kind": "email", "value": em, "from": plat(p.get("platform")),
                                     "url": p.get("url"), "confidence": conf, "label": sure(conf)})
        for e in P.get("emails_found") or []:
            em = (e.get("email") or "").strip().lower()
            if EMAIL_RE.match(em) and em not in seen_c:
                seen_c.add(em)
                contacts.append({"kind": "email", "value": em, "from": plat(e.get("from")),
                                 "url": e.get("url"), "confidence": "medium", "label": sure("medium")})

    # website / location from the live X profile also count as public contacts of a sort
    acc = D.get("account") or {}
    if acc.get("website"):
        w = str(acc["website"]).strip()
        if w and w not in seen_c:
            seen_c.add(w)
            contacts.append({"kind": "website", "value": w, "from": "X profile", "url": w,
                             "confidence": "high", "label": sure("high")})

    # breach-check publicly listed emails (metadata only)
    breaches = []
    if BR and contacts:
        emails = [c["value"] for c in contacts if c["kind"] == "email"][:lim["emails"]]
        if emails:
            log(f"dossier: leak-checking {len(emails)} publicly listed email(s)")
            with ThreadPoolExecutor(4) as ex:
                ef = {em: ex.submit(_quiet, BR.run, em, None, None, "fast") for em in emails}
                for em, f in ef.items():
                    try:
                        R = f.result(timeout=90)
                    except Exception as e:
                        log(f"dossier: breach {em} failed ({type(e).__name__})")
                        continue
                    breaches.append({
                        "query": em, "kind": "email",
                        "verdict": R.get("verdict"),
                        "count": len(R.get("breaches") or []),
                        "names": [b.get("name") for b in (R.get("breaches") or [])[:8] if b.get("name")],
                    })
    for h, R in br_u.items():
        if not R:
            continue
        breaches.append({
            "query": "@" + h, "kind": "username",
            "verdict": R.get("verdict"),
            "count": len(R.get("breaches") or []),
            "names": [b.get("name") for b in (R.get("breaches") or [])[:8] if b.get("name")],
        })

    # related X accounts already found by the hunt
    related = []
    for r in D.get("related") or []:
        related.append({
            "platform": "x", "handle": r.get("handle"), "name": r.get("name"),
            "url": r.get("permalink") or ("https://x.com/" + (r.get("handle") or "")),
            "confidence": r.get("confidence") or "medium",
            "label": sure(r.get("confidence")),
            "reason": ("Its bio mentions this account." if r.get("links_back_to_target")
                       else "Shares a display name." if r.get("shares_display_name")
                       else "Found nearby in public records."),
        })

    order = {"high": 0, "medium": 1, "same username only": 2, "low": 3}
    profiles.sort(key=lambda p: (order.get(p["confidence"], 9), plat(p["platform"])))
    extra = {
        "handles_checked": handles,
        "other_profiles": profiles,
        "related_x": related,
        "contacts": contacts,
        "breach_notes": breaches,
        "notes": [
            "Same username on another site is not proof it’s the same person — trust “Strong evidence” first.",
            "Only public pages and free breach indexes were checked. Nothing private was accessed.",
        ],
    }
    D["dossier_extra"] = extra
    log(f"dossier: {len(profiles)} other profile(s), {len(contacts)} public contact(s), "
        f"{sum(b['count'] for b in breaches)} breach hit(s)")
    return extra


# ------------------------------------------------------------------ plain-English brief
def brief(D):
    """Return a dict of plain-English sections for HTML / markdown / UI."""
    acc = D.get("account") or {}
    uh = D.get("username_history") or D.get("history", {}).get("username") or []
    dn = D.get("display_name_history") or D.get("history", {}).get("display_name") or []
    handle = acc.get("current_handle") or D.get("query") or "?"
    name = acc.get("display_name") or ("@" + handle)
    avatar = big(acc.get("current_avatar"))
    banner = acc.get("current_banner")
    if banner and not re.search(r"/\d+x\d+$", banner):
        banner = banner.rstrip("/") + "/600x200"

    olds = [s for s in uh if (s.get("value") or "").lower() != handle.lower()]
    overview = []
    if olds:
        overview.append(
            f"Since {when(uh[0].get('first_seen')) or when(acc.get('created_at')) or 'its first public post'}, "
            f"this account has used {len(uh)} username{'s' if len(uh) != 1 else ''}. "
            f"Before @{handle} it was {_join(['@' + s['value'] + ' (' + when(s.get('first_seen')) + ')' for s in olds])}."
        )
    else:
        overview.append(
            f"No earlier usernames turned up — as far as public records show, it has always been @{handle}."
        )
    if dn and len(dn) > 1:
        overview.append(
            f"Its display name has changed {len(dn) - 1} time{'s' if len(dn) != 2 else ''}: "
            + _join([f"“{s.get('value')}”" for s in dn]) + "."
        )
    if acc.get("bio"):
        overview.append(f"Current bio: “{acc['bio'].strip()}”.")
    bits = []
    if acc.get("created_at"):
        bits.append("joined " + when(acc["created_at"]))
    if acc.get("location"):
        bits.append(acc["location"])
    if acc.get("followers") is not None:
        bits.append(f"{acc['followers']:,} followers")
    if acc.get("tweets") is not None:
        bits.append(f"{acc['tweets']:,} posts")
    if bits:
        overview.append("Live profile: " + ", ".join(bits) + ".")

    username_history = []
    for i, s in enumerate(uh):
        span = when(s.get("first_seen"))
        last = when(s.get("last_seen"))
        if span and last and span != last:
            span = f"{span} – {'now' if i == len(uh) - 1 else last}"
        elif not span:
            span = last or ""
        username_history.append({
            "value": "@" + s.get("value", ""),
            "when": span,
            "confidence": s.get("confidence"),
            "label": sure(s.get("confidence")),
            "sightings": s.get("sightings"),
            "sources": plain_sources(s.get("sources")),
            "current": i == len(uh) - 1,
        })

    extra = D.get("dossier_extra") or {}
    other = list(extra.get("other_profiles") or [])
    # also surface related X accounts as "other profiles"
    for r in extra.get("related_x") or []:
        other.append({
            "platform": "x", "url": r.get("url"), "name": r.get("name"),
            "handle": r.get("handle"), "bio": None, "avatar": None,
            "confidence": r.get("confidence"), "label": r.get("label") or sure(r.get("confidence")),
            "reason": r.get("reason"), "via_handle": handle,
        })
    # fall back to hunt-native related if enrich wasn't run
    if not extra.get("related_x"):
        for r in D.get("related") or []:
            other.append({
                "platform": "x", "url": r.get("permalink") or ("https://x.com/" + (r.get("handle") or "")),
                "name": r.get("name"), "handle": r.get("handle"), "bio": None, "avatar": None,
                "confidence": r.get("confidence"), "label": sure(r.get("confidence")),
                "reason": ("Its bio mentions this account." if r.get("links_back_to_target")
                           else "Found nearby in public records."),
                "via_handle": handle,
            })

    contacts = list(extra.get("contacts") or [])
    if not contacts and acc.get("website"):
        contacts.append({"kind": "website", "value": acc["website"], "from": "X profile",
                         "url": acc["website"], "confidence": "high", "label": sure("high")})

    breach_notes = []
    for b in extra.get("breach_notes") or []:
        if b.get("count"):
            breach_notes.append(
                f"{b['query']} appears in {b['count']} known breach"
                f"{'es' if b['count'] != 1 else ''}"
                + (": " + _join(b.get("names") or []) if b.get("names") else ".")
                + ("." if b.get("names") else "")
            )
        else:
            breach_notes.append(
                f"{b['query']}: not found in the free breach indexes that answered "
                f"({b.get('verdict') or 'inconclusive'})."
            )
    if not breach_notes and extra.get("handles_checked"):
        breach_notes.append(
            "No free-source breach hits for the usernames checked. "
            "That is not a clean bill — private dumps aren’t covered."
        )

    timeline = []
    for e in D.get("timeline") or []:
        if e.get("type") in ("metric", "note"):
            continue
        val = e.get("value")
        if e.get("type") == "username" and val:
            val = "@" + str(val).lstrip("@")
        if e.get("type") == "avatar" and isinstance(val, str) and val.startswith("http"):
            val = "new profile picture"
        if e.get("type") == "banner" and isinstance(val, str) and val.startswith("http"):
            val = "new banner"
        timeline.append({
            "date": e.get("date"),
            "when": when(e.get("date")),
            "type": (e.get("type") or "").replace("_", " "),
            "value": val if val not in (None, "") else "(empty)",
            "previous": e.get("previous"),
            "label": sure(e.get("confidence")),
            "sources": plain_sources(e.get("sources")),
        })

    return {
        "name": name,
        "handle": "@" + handle.lstrip("@"),
        "avatar": avatar,
        "banner": banner,
        "meta": bits,
        "bio": (acc.get("bio") or "").strip() or None,
        "overview": overview,
        "username_history": username_history,
        "display_names": [{"value": s.get("value"), "when": when(s.get("first_seen")),
                           "label": sure(s.get("confidence")), "current": i == len(dn) - 1}
                          for i, s in enumerate(dn)],
        "other_profiles": other,
        "contacts": contacts,
        "breach_notes": breach_notes,
        "timeline": timeline[:80],
        "avatars": [{"url": big(a.get("value") or a.get("live_url")), "when": when(a.get("first_seen"))}
                    for a in (D.get("avatars") or []) if a.get("value") or a.get("live_url")],
        "limits": (extra.get("notes") or []) + [
            "Public archives are incomplete; anything never saved can’t be recovered.",
            "X’s signed-in “About this account” change count is not available to logged-out tools.",
        ],
        "generated": D.get("generated"),
        "mode": D.get("mode"),
        "runtime_s": D.get("runtime_s"),
        "query": D.get("query"),
    }
