"""Turn raw observations into per-field histories + one unified chronological timeline."""
import re
from collections import defaultdict

from .net import snowflake_date, unix_date, now_iso, parse_any_date

CONF_RANK = {"low": 0, "medium": 1, "high": 2}
TEXT_FIELDS = ("screen_name", "name", "description", "location", "website", "avatar", "banner")
EVENT_TYPE = {"screen_name": "username", "name": "display_name", "description": "bio", "location": "location",
              "website": "website", "avatar": "avatar", "banner": "banner"}


def avatar_id(u):
    m = re.search(r"profile_images/(\d+)/", u or "")
    return m.group(1) if m else None


def banner_id(u):
    m = re.search(r"profile_banners/\d+/(\d+)", u or "")
    return m.group(1) if m else None


def norm_key(field, v):
    if v is None:
        return None
    if field == "screen_name":
        return v.lower()
    if field == "avatar":
        return avatar_id(v) or re.sub(r"_(normal|bigger|mini|reasonably_small|\d+x\d+)(?=\.\w+$|$)", "", v)
    if field == "banner":
        return banner_id(v) or v
    if field == "website":
        v = re.sub(r"^https?://(www\.)?", "", str(v)).rstrip("/").lower()
        return v
    if isinstance(v, str):
        return re.sub(r"\s+", " ", v).strip()
    return v


def _upgrade_confidence(R):
    """og-only / uid-less sightings become 'medium' when they fall inside a verified tenure of that handle."""
    tenure = defaultdict(lambda: [None, None])
    for o in R.obs:
        sn = o["fields"].get("screen_name")
        if sn and o["verified"]:
            t = tenure[sn.lower()]
            t[0] = min(filter(None, [t[0], o["date"]]))
            t[1] = max(filter(None, [t[1], o["date"]]))
    for o in R.obs:
        if o["verified"] or o["confidence"] != "low":
            continue
        sn = (o["fields"].get("screen_name") or "").lower()
        t = tenure.get(sn)
        if t and t[0] <= o["date"] <= t[1]:
            o["confidence"] = "medium"


def segments(R, field, min_conf="medium"):
    rows = [o for o in R.obs if field in o["fields"] and CONF_RANK[o["confidence"]] >= CONF_RANK[min_conf]]
    rows.sort(key=lambda o: (o["date"], o["ts"]))
    segs = []
    for o in rows:
        v = o["fields"][field]
        k = norm_key(field, v)
        if k is None:
            continue
        if segs and segs[-1]["_key"] == k:
            s = segs[-1]
            s["last_seen"] = o["date"]
            s["sightings"] += 1
        else:
            s = {"_key": k, "value": v, "first_seen": o["date"], "last_seen": o["date"], "sightings": 1,
                 "sources": set(), "confidence": o["confidence"], "evidence_first": o["evidence"], "evidence_last": o["evidence"]}
            segs.append(s)
        s["sources"].add(o["source"].split(":")[0] if o["source"].startswith("wayback") else o["source"])
        s["evidence_last"] = o["evidence"] or s["evidence_last"]
        if CONF_RANK[o["confidence"]] > CONF_RANK[s["confidence"]]:
            s["confidence"] = o["confidence"]
        if field in ("screen_name",) and o["fields"][field] != s["value"] and o["fields"][field] != o["fields"][field].lower():
            s["value"] = o["fields"][field]   # prefer the properly-cased spelling
    return _deflap(segs)


def _deflap(segs):
    """Drop a single-sighting segment sandwiched between two copies of the same value on the same day
    (stale CDN/cached captures), then merge the neighbours."""
    changed = True
    while changed:
        changed = False
        for i in range(1, len(segs) - 1):
            a, b, c = segs[i - 1], segs[i], segs[i + 1]
            if a["_key"] == c["_key"] and b["sightings"] == 1 and b["first_seen"] in (a["last_seen"], c["first_seen"]):
                a["last_seen"], a["sightings"] = c["last_seen"], a["sightings"] + c["sightings"]
                a["sources"] |= c["sources"]
                a["evidence_last"] = c["evidence_last"]
                del segs[i:i + 2]
                changed = True
                break
    return segs


def _finish(seg, field):
    out = {k: v for k, v in seg.items() if not k.startswith("_")}
    out["sources"] = sorted(out["sources"])
    if field == "avatar":
        iid = avatar_id(seg["value"])
        out["image_id"] = iid
        out["uploaded"] = snowflake_date(iid) if iid else None
    if field == "banner":
        bid = banner_id(seg["value"])
        out["banner_id"] = bid
        out["uploaded"] = unix_date(bid) if bid else None
    return out


def build(R):
    _upgrade_confidence(R)
    hist, timeline = {}, []
    for field in TEXT_FIELDS:
        segs = [_finish(s, field) for s in segments(R, field)]
        if field == "screen_name":
            segs = _merge_memorylol(R, segs)
        for i, s in enumerate(segs):
            prev = segs[i - 1] if i else None
            if prev:
                start = prev["last_seen"]
                if field in ("avatar", "banner") and s.get("uploaded") and (not start or s["uploaded"] > start):
                    start = s["uploaded"]
                s["changed_between"] = [start, s["first_seen"]]
            ev = {"date": s["first_seen"], "type": EVENT_TYPE[field], "value": s["value"],
                  "previous": prev["value"] if prev else None,
                  "window": s.get("changed_between"), "first_seen": s["first_seen"], "last_seen": s["last_seen"],
                  "sources": s["sources"], "confidence": s["confidence"], "evidence_url": s.get("evidence_first")}
            if field in ("avatar", "banner") and s.get("uploaded"):
                ev["uploaded"] = s["uploaded"]
            if prev is None:
                ev["note"] = "earliest sighting"
            timeline.append(ev)
        hist[field] = segs

    # banner ids only seen in the image archive (never on a captured page)
    known_b = {b.get("banner_id") for b in hist["banner"]}
    for b in R.raw.get("banners_cdx", []):
        if b["banner_id"] not in known_b:
            hist["banner"].append({"value": b["url"], "banner_id": b["banner_id"], "uploaded": b["uploaded"],
                                   "first_seen": b["first_capture"], "last_seen": b["last_capture"], "sightings": b["captures"],
                                   "sources": ["wayback:pbs.twimg.com/profile_banners"], "confidence": "high",
                                   "evidence_first": b["wayback_url"], "evidence_last": b["wayback_url"]})
            timeline.append({"date": b["uploaded"] or b["first_capture"], "type": "banner", "value": b["url"], "previous": None,
                             "uploaded": b["uploaded"], "first_seen": b["first_capture"], "last_seen": b["last_capture"],
                             "sources": ["wayback:profile_banners (keyed by user id)"], "confidence": "high",
                             "evidence_url": b["wayback_url"], "note": "banner image archived under the account's id"})
    for b in hist["banner"]:
        bb = next((x for x in R.raw.get("banners_cdx", []) if x["banner_id"] == b.get("banner_id")), None)
        if bb:
            b["archived_image"] = bb["wayback_url"]
            b["image_captures"] = bb["captures"]
    hist["banner"].sort(key=lambda b: b.get("uploaded") or b.get("first_seen") or "")

    # account creation
    created = R.live.get("joined")
    created = parse_any_date(created) or created
    if not created:
        for o in R.obs:
            if o["verified"] and o["fields"].get("created_at"):
                created = parse_any_date(o["fields"]["created_at"]) or o["fields"]["created_at"]
                break
    if not created and R.user_id:
        created = snowflake_date(R.user_id)
    if created:
        timeline.append({"date": created[:10], "type": "note", "value": "account created", "previous": None,
                         "sources": ["profile created_at" if R.live.get("joined") else "id snowflake"], "confidence": "high",
                         "evidence_url": f"https://x.com/i/user/{R.user_id}" if R.user_id else None})

    # metrics: one sample per quarter
    q = {}
    for o in sorted(R.obs, key=lambda o: o["date"]):
        f = o["fields"]
        if f.get("followers") is None or not o["verified"]:
            continue
        key = o["date"][:4] + "Q" + str((int(o["date"][5:7]) - 1) // 3 + 1)
        if key not in q:
            q[key] = o
    metrics = []
    for key, o in sorted(q.items()):
        f = o["fields"]
        m = {"date": o["date"], "followers": f.get("followers"), "following": f.get("following"), "tweets": f.get("tweets"),
             "source": o["source"], "evidence_url": o["evidence"]}
        metrics.append(m)
        timeline.append({"date": o["date"], "type": "metric",
                         "value": ", ".join(f"{k}={v:,}" for k, v in m.items() if k in ("followers", "following", "tweets") and isinstance(v, int)),
                         "previous": None, "sources": [o["source"]], "confidence": o["confidence"], "evidence_url": o["evidence"]})
    if R.about:
        timeline.append({"date": R.about.get("last_changed") or now_iso(), "type": "note",
                         "value": f"X 'About this account': {R.about['username_changes']} username change(s)",
                         "previous": None, "sources": [R.about["source"]], "confidence": "high", "evidence_url": R.about["source"]})
    for rel in R.related.values():
        if rel.get("created"):
            d = parse_any_date(rel["created"])
            if d:
                timeline.append({"date": d, "type": "note", "value": f"related account @{rel['handle']} created ({', '.join(rel['how_found'])})",
                                 "previous": None, "sources": ["fxtwitter"], "confidence": rel["confidence"], "evidence_url": rel["permalink"]})
    timeline.sort(key=lambda e: (e["date"] or "", list(EVENT_TYPE.values()).index(e["type"]) if e["type"] in EVENT_TYPE.values() else 9))
    return hist, timeline, metrics


def _merge_memorylol(R, segs):
    """Attach memory.lol date ranges, add undated handles, flag renames."""
    for s in segs:
        ml = next((d for h, d in R.memorylol.items() if h.lower() == s["value"].lower()), None)
        if ml:
            s["memorylol_range"] = ml
    have = {s["value"].lower() for s in segs}
    for h in sorted(R.undated_handles):
        if h.lower() not in have:
            segs.append({"value": h, "first_seen": None, "last_seen": None, "sightings": 0, "sources": ["memory.lol (undated)"],
                         "confidence": "high", "evidence_first": f"https://memory.lol/app/#{h}"})
    # handles chained in from foreign owners are excluded upstream; sort by first sighting
    segs.sort(key=lambda s: s["first_seen"] or "0000")
    return segs
