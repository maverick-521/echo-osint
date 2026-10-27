"""Markdown report + console summary."""
from .net import now_iso

HONESTY = ("Full global username-history indexes are NOT freely available: memory.lol's complete/trusted tier is "
           "restricted to vetted researchers (the public API only covers accounts in its public dataset), and "
           "lolarchiver is a paid, credit-metered service behind Cloudflare. This tool rebuilds history from free public "
           "traces (memory.lol public tier, Wayback Machine captures of profiles, tweets, id-keyed URLs and images, "
           "archive.today, live mirrors). Wayback coverage of small accounts is sparse and uneven, so every 'changed between' "
           "window is bounded by the nearest captures on either side - the real change happened somewhere inside it.")


def esc(s, n=110):
    if s is None:
        return ""
    s = str(s).replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= n else s[:n - 1] + "…"


def link(u, label="link"):
    return f"[{label}]({u})" if u else ""


def window(s):
    w = s.get("changed_between")
    return f"{w[0]} → {w[1]}" if w else "—"


def hist_table(segs, title, vlabel, md):
    md.append(f"\n## {title}\n")
    if not segs:
        md.append("_No sightings._\n")
        return
    md.append(f"| # | {vlabel} | first seen | last seen | changed between | sightings | confidence | sources | evidence |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    for i, s in enumerate(segs, 1):
        v = s["value"]
        if vlabel == "handle":
            v = f"@{v}" + (f" (memory.lol {s['memorylol_range'][0]} → {s['memorylol_range'][-1]})" if s.get("memorylol_range") else "")
        md.append(f"| {i} | {esc(v) if v != '' else '_(empty)_'} | {s.get('first_seen') or '?'} | {s.get('last_seen') or '?'} | "
                  f"{window(s)} | {s.get('sightings', 0)} | {s.get('confidence')} | {esc(', '.join(s.get('sources', [])), 60)} | "
                  f"{link(s.get('evidence_first'), 'first')} {link(s.get('evidence_last'), 'last') if s.get('evidence_last') != s.get('evidence_first') else ''} |")


def to_markdown(D):
    acc, H = D["account"], D["history"]
    md = [f"# X / Twitter OSINT timeline: `{D['query']}`", "",
          f"Generated {D['generated']} · runtime {D['runtime_s']}s · mode {D['mode']}", ""]
    # Plain-English brief first (photo URL + sections); technical tables follow.
    try:
        from . import dossier as DS
        B = DS.brief(D)
        md += ["## Overview", ""]
        if B.get("avatar"):
            md.append(f"![profile]({B['avatar']})")
            md.append("")
        md.append(f"**{esc(B['name'], 80)}** · `{B['handle']}`")
        if B.get("meta"):
            md.append("")
            md.append(" · ".join(B["meta"]))
        md.append("")
        for p in B.get("overview") or []:
            md.append(p)
            md.append("")
        if B.get("username_history"):
            md += ["## Username history", ""]
            for r in B["username_history"]:
                md.append(f"- **{r['value']}** — {r.get('when') or '—'} · {r.get('label')}"
                          + (f" · seen {r['sightings']}×" if r.get("sightings") else ""))
            md.append("")
        if B.get("other_profiles"):
            md += ["## Other profiles", ""]
            for p in B["other_profiles"]:
                label = DS.plat(p.get("platform"))
                if p.get("platform") == "x" and p.get("handle"):
                    label = "@" + str(p["handle"]).lstrip("@")
                md.append(f"- **{label}** [{p.get('label')}]({p.get('url') or '#'}) — {esc(p.get('reason') or p.get('name') or '', 160)}")
            md.append("")
        if B.get("contacts"):
            md += ["## Contacts listed publicly", ""]
            for c in B["contacts"]:
                md.append(f"- **{c.get('kind')}**: `{esc(c.get('value'), 80)}` — {esc(c.get('from') or '', 40)} · {c.get('label')}")
            md.append("")
        if B.get("breach_notes"):
            md += ["## Breach notes", ""]
            for n in B["breach_notes"]:
                md.append(f"- {n}")
            md.append("")
        md += ["## Timeline (plain)", ""]
        for e in (B.get("timeline") or [])[:40]:
            md.append(f"- {e.get('when') or e.get('date')} · {e.get('type')} · {esc(e.get('value'), 90)} · {e.get('label')}")
        md += ["", "---", "", "## Identity (technical)", "", "| field | value |", "|---|---|"]
    except Exception:
        md += ["## Identity", "", "| field | value |", "|---|---|"]
    for k in ("user_id", "current_handle", "display_name", "created_at", "bio", "location", "website", "followers", "following",
              "tweets", "likes", "protected", "verification", "current_avatar", "current_banner", "permalinks"):
        v = acc.get(k)
        if v in (None, "", []):
            continue
        if isinstance(v, list):
            v = " · ".join(v)
        md.append(f"| {k} | {esc(v, 300)} |")
    if D.get("about_account"):
        a = D["about_account"]
        md.append(f"| X 'About' username changes | {a['username_changes']} (last {a.get('last_changed')}) |")

    hist_table(H["username"], "Username (@handle) history", "handle", md)
    hist_table(H["display_name"], "Display-name history", "display name", md)
    hist_table(H["bio"], "Bio history", "bio", md)
    hist_table(H["location"], "Location history", "location", md)
    hist_table(H["website"], "Website history", "website", md)

    md.append("\n## Profile pictures\n")
    if D["avatars"]:
        md.append("| # | image id | uploaded (snowflake) | first seen | last seen | changed between | live? | live URL | archived image | evidence |")
        md.append("|---|---|---|---|---|---|---|---|---|---|")
        for i, a in enumerate(D["avatars"], 1):
            arch = a.get("archived_image_captures") or []
            md.append(f"| {i} | {a.get('image_id') or esc(a['value'], 40)} | {a.get('uploaded') or '?'} | {a.get('first_seen')} | "
                      f"{a.get('last_seen')} | {window(a)} | {'LIVE' if a.get('still_live') else 'dead' if 'still_live' in a else '?'} | "
                      f"{link(a.get('live_url') or a['value'], 'image')} | {link(arch[0]['wayback_url'], arch[0]['date']) if arch else ''} | "
                      f"{link(a.get('evidence_first'), 'page')} |")
    else:
        md.append("_None found._")

    md.append("\n## Banners\n")
    if D["banners"]:
        md.append("| # | banner id | uploaded | first seen | last seen | archived image | evidence |")
        md.append("|---|---|---|---|---|---|---|")
        for i, b in enumerate(D["banners"], 1):
            md.append(f"| {i} | {b.get('banner_id')} | {b.get('uploaded') or '?'} | {b.get('first_seen')} | {b.get('last_seen')} | "
                      f"{link(b.get('archived_image'), 'image')} | {link(b.get('evidence_first'), 'page')} |")
    else:
        md.append("_None found._")

    if D["metrics"]:
        md.append("\n## Metrics over time (one sample per quarter)\n")
        md.append("| date | followers | following | tweets | evidence |")
        md.append("|---|---|---|---|---|")
        for m in D["metrics"]:
            f = lambda x: f"{x:,}" if isinstance(x, int) else ""
            md.append(f"| {m['date']} | {f(m['followers'])} | {f(m['following'])} | {f(m['tweets'])} | {link(m['evidence_url'])} |")

    md.append("\n## Related accounts\n")
    if D["related"]:
        md.append("| handle | id | name | created | how found | links back? | confidence | bio |")
        md.append("|---|---|---|---|---|---|---|---|")
        for r in D["related"]:
            md.append(f"| [@{r['handle']}]({r['permalink']}) | {r['id']} | {esc(r['name'], 40)} | {esc(r.get('created'), 30)} | "
                      f"{esc(', '.join(r['how_found']), 50)} | {'yes' if r['links_back_to_target'] else 'no'} | {r['confidence']} | {esc(r['bio'], 120)} |")
    else:
        md.append("_None found._")

    ls = D.get("life_scan") or {}
    if ls:
        md.append("\n## Life-span username scan (every period of the account)\n")
        md.append("Searched the whole account life window-by-window (X search via a public Nitter front-end resolves "
                  "`from:` by user id, so it also returns tweets posted under former handles), then read the "
                  "posting-time @handle frozen inside each tweet's media URL / other people's @mentions.")
        md.append(f"\nSearch host: {esc(ls.get('host'), 60)} · tweets checked: {ls.get('tweets_checked', 0)} · "
                  f"bisection steps: {ls.get('bisect_steps', 0)} · first public tweet near: {ls.get('first_public_tweet_near') or '?'}\n")
        hs = ls.get("handles") or {}
        if hs:
            md.append("| handle | first seen | last seen | samples | how | evidence |")
            md.append("|---|---|---|---|---|---|")
            for v in sorted(hs.values(), key=lambda x: x.get("first_seen") or ""):
                md.append(f"| @{v['handle']} | {v['first_seen']} | {v['last_seen']} | {v['samples']} | "
                          f"{esc(', '.join(v.get('how') or []), 60)} | {link(v.get('evidence_first'), 'first')} · {link(v.get('evidence_last'), 'last')} |")
        for c in ls.get("changes") or []:
            md.append(f"- **@{c['from']} → @{c['to']}** happened between {c['after']} and {c['before']} "
                      f"({link(c['evidence'][0], 'last old')} · {link(c['evidence'][1], 'first new')})")
        ws = ls.get("windows") or []
        if ws:
            def cell(w):
                if w.get("media"):
                    return f"{w['media']}{'+' if w['media'] >= 20 else ''} media"
                if w.get("any"):
                    return f"{w['any']}{'+' if w['any'] >= 20 else ''} text"
                return "—"
            md.append("\n<details><summary>Activity map (public tweets found per window)</summary>\n")
            md.append("| window | tweets | sampled |")
            md.append("|---|---|---|")
            for w in ws:
                md.append(f"| {w['since']} → {w['until']} | {cell(w)} | {w.get('sampled', 0)} |")
            md.append("\n</details>")
        if ls.get("no_public_tweets_window"):
            g = ls["no_public_tweets_window"]
            md.append(f"\n_No public tweets exist between {g['from']} and {g['to']}; handles used only in that period "
                      "can't be recovered from tweets._")

    fh = D.get("frozen_handles") or {}
    if fh:
        md.append("\n## Username recovery via frozen media URLs\n")
        md.append("X freezes the posting-time @handle inside `media.expanded_url`. Renames never rewrite it.")
        md.append(f"\nTweets checked: {fh.get('checked_tweets', 0)}\n")
        handles = fh.get("handles") or {}
        if handles:
            md.append("| handle | first seen | last seen | how | evidence |")
            md.append("|---|---|---|---|---|")
            for v in sorted(handles.values(), key=lambda x: x.get("first_seen") or ""):
                md.append(f"| @{v['handle']} | {v.get('first_seen')} | {v.get('last_seen')} | {v.get('how')} | "
                          f"{link(v.get('evidence'), 'tweet')} |")
        else:
            md.append("_No alternate handles in sampled media URLs._")

    dm = D.get("deep_mentions") or {}
    if dm:
        md.append("\n## Deep-source mentions & indexes\n")
        for key, label in (("github_mentions", "GitHub"), ("hn_mentions", "Hacker News"),
                           ("reddit_mentions", "Reddit/PullPush"), ("ghostarchive", "Ghostarchive"),
                           ("socialblade", "SocialBlade"), ("commoncrawl", "Common Crawl"),
                           ("earliest_status_cdx", "Pre-gap Wayback CDX")):
            v = dm.get(key)
            if not v:
                continue
            if key == "commoncrawl":
                md.append(f"- **{label}**: {len(v.get('hits') or [])} URL hit(s) across crawls {', '.join(v.get('crawls_checked') or [])}")
            elif key == "earliest_status_cdx":
                for h, info in v.items():
                    md.append(f"- **{label} @{h}**: {info.get('count', 0)} captures"
                              + (f" ({info.get('first')} → {info.get('last')})" if info.get('count') else " (none)"))
            elif key == "socialblade" and isinstance(v, dict):
                md.append(f"- **{label}**: grade {v.get('grade') or '?'} · created hint {v.get('created_hint') or '?'} · {link(v.get('url'), 'page')}")
            elif key == "ghostarchive" and isinstance(v, list):
                md.append(f"- **{label}**: {len(v)} link(s)")
            elif isinstance(v, list):
                md.append(f"- **{label}**: {len(v)} hit(s)")
                for it in v[:5]:
                    u = it.get("url") or it.get("permalink")
                    md.append(f"  - {esc(it.get('title') or it.get('url'), 100)} {link(u) if u else ''}")

    md.append("\n## Unified timeline\n")
    md.append("| date | type | value | previous | changed between | confidence | sources | evidence |")
    md.append("|---|---|---|---|---|---|---|---|")
    for e in D["timeline"]:
        w = e.get("window")
        md.append(f"| {e['date']} | {e['type']} | {esc(e['value'], 90) if e['value'] != '' else '_(empty)_'} | {esc(e.get('previous'), 60)} | "
                  f"{(w[0] + ' → ' + w[1]) if w else ''} | {e['confidence']} | {esc(', '.join(e['sources']), 50)} | {link(e.get('evidence_url'))} |")

    if D["handle_reuse"]:
        md.append("\n## Handle reuse (other accounts that used one of these handles)\n")
        for h, lst in D["handle_reuse"].items():
            for x in lst[:5]:
                md.append(f"- @{h}: account id {x['user_id']} seen {x['date']} {link(x['evidence'], 'evidence') if str(x['evidence']).startswith('http') else ''}")

    md.append("\n## Coverage\n")
    cv = D["coverage"]
    md.append(f"- observations: {cv['observations']} ({cv['verified_observations']} owner-verified by user id)")
    md.append(f"- archived captures analysed: {cv['captures_analysed']} of {cv['captures_available']} usable captures found "
              f"(profiles {cv['profile_captures']}, tweets {cv['status_captures']}, id-keyed {cv['id_captures']}, other {cv['misc_captures']})")
    for h, s in D["status_scans"].items():
        for host, v in s.items():
            if v.get("pages"):
                md.append(f"- archived tweets under {host}/{h}: {v.get('usable_captures', 0)} usable captures "
                          f"({v.get('first', '?')} → {v.get('last', '?')}), read {v.get('pages_read', 0)} of {v['pages']} CDX pages")
    for h, a in D["archive_today"].items():
        if not a["count"]:
            md.append(f"- archive.today @{h}: no snapshots")
            continue
        md.append(f"- archive.today @{h}: {a['count']} snapshots ({a.get('first')} → {a.get('last')})")
    md.append(f"- sources that returned data: {', '.join(D['sources_worked'])}")

    md.append("\n## Gaps, failed sources & caveats\n")
    for n in D["notes"]:
        md.append(f"- {n}")
    for f in D["failed_sources"]:
        md.append(f"- ✗ {f}")
    md.append(f"\n> {HONESTY}\n")
    return "\n".join(md) + "\n"


def console(D, paths):
    acc, H = D["account"], D["history"]
    p = print
    p("\n" + "=" * 78)
    p(f" X OSINT TIMELINE  {D['query']}   id={acc.get('user_id')}   now @{acc.get('current_handle')}   created {acc.get('created_at')}")
    p("=" * 78)
    ls = (D.get("life_scan") or {}).get("handles") or {}
    if ls:
        p("\nLIFE-SPAN SCAN (frozen posting-time handles)")
        for v in sorted(ls.values(), key=lambda x: x.get("first_seen") or ""):
            p(f"  @{v['handle']:<22} {v.get('first_seen')} → {v.get('last_seen')}  ({v.get('samples')} samples)")
    fh = (D.get("frozen_handles") or {}).get("handles") or {}
    if fh:
        p("\nFROZEN-HANDLE RECOVERY (media expanded_url)")
        for v in sorted(fh.values(), key=lambda x: x.get("first_seen") or ""):
            p(f"  @{v['handle']:<22} {v.get('first_seen')} → {v.get('last_seen')}  [{v.get('how')}]")
    p("\nUSERNAME HISTORY")
    for s in H["username"]:
        ml = f"  memory.lol {s['memorylol_range'][0]}→{s['memorylol_range'][-1]}" if s.get("memorylol_range") else ""
        p(f"  @{s['value']:<22} {s.get('first_seen') or '?'} → {s.get('last_seen') or '?'}"
          f"{'   changed ' + window(s) if s.get('changed_between') else ''}  [{s['confidence']}]{ml}")
    if len(H["username"]) <= 1:
        p("  (no rename detected in the available traces)")
    p("\nDISPLAY-NAME HISTORY")
    for s in H["display_name"]:
        p(f"  {esc(s['value'], 30):<32} {s['first_seen']} → {s['last_seen']}"
          f"{'   changed ' + window(s) if s.get('changed_between') else ''}  [{s['confidence']}]")
    for key, title in (("bio", "BIO"), ("location", "LOCATION"), ("website", "WEBSITE")):
        if H[key]:
            p(f"\n{title} HISTORY ({len(H[key])})")
            if len(H[key]) > 8:
                p(f"  (last 8 shown - full list in the report)")
            for s in H[key][-8:]:
                p(f"  {s['first_seen']} → {s['last_seen']}  {esc(s['value'], 70) or '(empty)'}")
    p(f"\nPROFILE PICTURES ({len(D['avatars'])})")
    if len(D["avatars"]) > 12:
        p("  (last 12 shown - full list in the report)")
    for a in D["avatars"][-12:]:
        p(f"  {a.get('image_id') or '?':<20} uploaded {a.get('uploaded') or '?':<10} seen {a['first_seen']} → {a['last_seen']}  "
          f"{'LIVE' if a.get('still_live') else 'dead' if 'still_live' in a else ''}")
    p(f"\nBANNERS ({len(D['banners'])})")
    for b in D["banners"][-6:]:
        p(f"  {b.get('banner_id')}  uploaded {b.get('uploaded')}  seen {b.get('first_seen')} → {b.get('last_seen')}")
    if D["related"]:
        p("\nRELATED ACCOUNTS")
        for r in D["related"]:
            p(f"  @{r['handle']} (id {r['id']}, \"{esc(r['name'], 30)}\") {', '.join(r['how_found'])}"
              f"{' — links back' if r['links_back_to_target'] else ''} [{r['confidence']}]")
    p(f"\nTIMELINE: {len(D['timeline'])} events · {D['coverage']['captures_analysed']} archived captures analysed · runtime {D['runtime_s']}s")
    p(f"FAILED/GATED SOURCES: {len(D['failed_sources'])}  (see report)")
    for k, v in paths.items():
        p(f"  {k}: {v}")
    p("")


# ------------------------------------------------------------------ HTML investigator brief
def _hesc(s, n=None):
    if s is None:
        return ""
    s = str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    if n and len(s) > n:
        s = s[: n - 1] + "…"
    return s


def to_html(D):
    """Standalone HTML dossier: photo header + plain-English sections. Markdown evidence stays separate."""
    from . import dossier as DS
    B = DS.brief(D)
    css = """
:root{--bg:#0e0f12;--ink:#e8ecf2;--mut:#9aa3b2;--faint:#6b7380;--edge:rgba(255,255,255,.08);--acc:#c9d3e2;--card:rgba(255,255,255,.035)}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(1200px 700px at 20% 0%,#1a1d24,#0e0f12 55%);
color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif;padding:28px 18px 64px}
.wrap{max-width:820px;margin:0 auto}.mark{font-size:11px;letter-spacing:.22em;text-transform:uppercase;color:var(--faint);margin-bottom:14px}
.head{display:flex;gap:20px;align-items:flex-start;padding:22px;border-radius:22px;background:var(--card);border:1px solid var(--edge);
position:relative;overflow:hidden}.banner{position:absolute;inset:0;background-size:cover;background-position:center;opacity:.14;filter:blur(16px);transform:scale(1.12)}
.banner:after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(14,15,18,.15),rgba(14,15,18,.9))}
.id{position:relative;flex:1;min-width:0}.name{margin:4px 0 0;font:600 26px/1.15 -apple-system,"SF Pro Display","Segoe UI",sans-serif}
.handle{margin:4px 0 0;color:var(--acc)}.meta{margin:8px 0 0;color:var(--mut);font-size:13px}.bio{margin:10px 0 0;opacity:.9;max-width:560px}
.avatar{position:relative;flex:none;width:112px;height:112px;border-radius:50%;overflow:hidden;background:rgba(255,255,255,.04);
box-shadow:0 0 0 1px rgba(255,255,255,.12),0 0 0 7px rgba(255,255,255,.03)}.avatar img{width:100%;height:100%;object-fit:cover}
.avatar .ph{display:grid;place-items:center;width:100%;height:100%;font:300 40px/1 -apple-system,sans-serif;color:var(--mut)}
section{margin-top:28px}h2{margin:0 0 10px;font-size:11px;font-weight:500;letter-spacing:.16em;text-transform:uppercase;color:var(--faint)}
section p{margin:0 0 8px;max-width:680px;line-height:1.65}ol.trail,ul.plain{list-style:none;margin:0;padding:0}
ol.trail{padding-left:18px;position:relative}ol.trail:before{content:"";position:absolute;left:4px;top:8px;bottom:8px;width:1px;background:rgba(255,255,255,.12)}
ol.trail li{position:relative;padding:8px 0;display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline}
ol.trail li:before{content:"";position:absolute;left:-17px;top:15px;width:7px;height:7px;border-radius:50%;background:var(--bg);border:1px solid rgba(214,222,234,.45)}
ol.trail li.now:before{background:var(--acc);border-color:var(--acc)}
ol.trail b{font-weight:600}.when{color:var(--mut);font-size:13px}.sure{color:var(--faint);font-size:12px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}
.card{display:block;padding:14px;border-radius:14px;background:var(--card);border:1px solid var(--edge);color:var(--ink);text-decoration:none}
.card b{display:block}.card .sure{display:block;margin-top:2px}.card p{margin:6px 0 0;font-size:12.5px;color:var(--mut)}
.pics{display:flex;flex-wrap:wrap;gap:14px}.pics figure{margin:0;flex:none;text-align:center;width:84px}.pics a{color:inherit}
.pics img{width:64px;height:64px;border-radius:50%;object-fit:cover;background:#222}ol.bios li{flex-direction:column;gap:2px}ol.bios q{max-width:640px;line-height:1.5;white-space:pre-wrap}ol.bios q:before,ol.bios q:after{color:var(--faint)}a.ev{color:var(--faint);font-size:11px;margin-left:6px}.pics figcaption{font-size:11px;color:var(--faint);margin-top:4px}
.lim li,.plain li{margin:4px 0;color:var(--mut);font-size:13.5px}.plain{padding-left:18px;list-style:disc}
.foot{margin-top:36px;color:var(--faint);font-size:12px}details.sources{margin-top:18px;color:var(--faint)}
details.sources summary{cursor:pointer}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--edge);vertical-align:top}
th{color:var(--faint);font-weight:500}a{color:var(--acc)}
@media(max-width:560px){.head{gap:14px;padding:16px}.avatar{width:76px;height:76px}.name{font-size:20px}}
"""
    parts = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>",
             f"<title>ECHO · {_hesc(B['handle'])}</title><style>{css}</style></head><body><div class=wrap>",
             f"<div class=mark>ECHO · investigator brief</div><header class=head>"]
    if B.get("banner"):
        parts.append(f"<div class=banner style=\"background-image:url('{_hesc(B['banner'])}')\"></div>")
    parts.append(f"<div class=id><div class=name>{_hesc(B['name'])}</div>"
                 f"<div class=handle>{_hesc(B['handle'])}</div>")
    if B.get("meta"):
        parts.append(f"<div class=meta>{_hesc(' · '.join(B['meta']))}</div>")
    if B.get("bio"):
        parts.append(f"<p class=bio>“{_hesc(B['bio'])}”</p>")
    parts.append("</div>")
    init = _hesc((B["name"] or B["handle"] or "?").lstrip("@")[:1].upper() or "?")
    if B.get("avatar"):
        parts.append(f"<div class=avatar><img src=\"{_hesc(B['avatar'])}\" alt=\"\" referrerpolicy=\"no-referrer\" "
                     f"onerror=\"this.parentNode.innerHTML='<span class=ph>{init}</span>'\"></div>")
    else:
        parts.append(f"<div class=avatar><span class=ph>{init}</span></div>")
    parts.append("</header>")

    def sec(title, body_html):
        parts.append(f"<section><h2>{_hesc(title)}</h2>{body_html}</section>")

    if B["overview"]:
        sec("Overview", "".join(f"<p>{_hesc(p)}</p>" for p in B["overview"]))

    if B["username_history"]:
        lis = []
        for r in B["username_history"]:
            cls = " class=now" if r.get("current") else ""
            extra = f" · seen {r['sightings']}×" if r.get("sightings") else ""
            lis.append(f"<li{cls}><b>{_hesc(r['value'])}</b><span class=when>{_hesc(r.get('when'))}</span>"
                       f"<span class=sure>{_hesc(r.get('label'))}{extra}</span></li>")
        sec("Username history", f"<ol class=trail>{''.join(lis)}</ol>")

    def _ev(u, t="archive"):
        return f" <a class=ev href=\"{_hesc(u)}\" target=_blank rel=noopener>{t}</a>" if u else ""

    if B.get("display_names"):
        lis = []
        for r in B["display_names"]:
            cls = " class=now" if r.get("current") else ""
            seen = f" · seen {r['sightings']}×" if r.get("sightings") else ""
            lis.append(f"<li{cls}><b>{_hesc(r['value'])}</b><span class=when>{_hesc(r.get('when'))}</span>"
                       f"<span class=sure>{_hesc(r.get('label'))}{seen}{_ev(r.get('evidence'))}</span></li>")
        sec(f"Display names ({len(B['display_names'])})", f"<ol class=trail>{''.join(lis)}</ol>")
    if B.get("bios"):
        lis = []
        for r in B["bios"]:
            cls = " class=now" if r.get("current") else ""
            txt = _hesc(r["value"]) if r.get("value") else "<i>(empty bio)</i>"
            lis.append(f"<li{cls}><span class=when>{_hesc(r.get('when'))}</span><q>{txt}</q>"
                       f"<span class=sure>{_hesc(r.get('label'))}{_ev(r.get('evidence'))}</span></li>")
        sec(f"Bio history ({len(B['bios'])})", f"<ol class=\"trail bios\">{''.join(lis)}</ol>")
    if B.get("avatars"):
        figs = []
        for a in B["avatars"]:
            if not a.get("url"):
                continue
            fb = a.get("fallback")
            onerr = f" onerror=\"if(this.dataset.f!=1){{this.dataset.f=1;this.src='{_hesc(fb)}'}}\"" if fb and fb != a["url"] else ""
            figs.append(f"<figure><a href=\"{_hesc(a.get('archived') or a['url'])}\" target=_blank rel=noopener>"
                        f"<img src=\"{_hesc(a['url'])}\" alt=\"\" referrerpolicy=\"no-referrer\" loading=lazy{onerr}></a>"
                        f"<figcaption>{_hesc(a.get('when'))}{' · current' if a.get('current') else ''}</figcaption></figure>")
        if figs:
            sec(f"Profile pictures ({len(figs)})", f"<div class=pics>{''.join(figs)}</div>")

    if B["other_profiles"]:
        cards = []
        for p in B["other_profiles"]:
            title = DS.plat(p.get("platform"))
            handle = p.get("handle")
            if handle and p.get("platform") == "x":
                title = "@" + str(handle).lstrip("@")
            line = " · ".join(x for x in [p.get("name"), p.get("reason")] if x)
            cards.append(
                f"<a class=card href=\"{_hesc(p.get('url'))}\" target=_blank rel=\"noopener noreferrer\">"
                f"<b>{_hesc(title)}</b><span class=sure>{_hesc(p.get('label') or '')}</span>"
                f"{('<p>' + _hesc(line, 220) + '</p>') if line else ''}</a>"
            )
        sec("Other profiles", f"<div class=cards>{''.join(cards)}</div>")

    if B["contacts"]:
        lis = []
        for c in B["contacts"]:
            kind = c.get("kind") or "contact"
            val = c.get("value")
            src = c.get("from") or ""
            lab = c.get("label") or ""
            if c.get("url") and str(c["url"]).startswith("http"):
                val_h = f"<a href=\"{_hesc(c['url'])}\" target=_blank rel=\"noopener noreferrer\">{_hesc(val)}</a>"
            else:
                val_h = f"<code>{_hesc(val)}</code>"
            lis.append(f"<li><b>{_hesc(kind)}</b> · {val_h}"
                       f"{(' · listed on ' + _hesc(src)) if src else ''}"
                       f"{(' · ' + _hesc(lab)) if lab else ''}</li>")
        sec("Contacts listed publicly", f"<ul class=plain>{''.join(lis)}</ul>")

    if B["breach_notes"]:
        sec("Breach notes", "<ul class=plain>" + "".join(f"<li>{_hesc(n)}</li>" for n in B["breach_notes"]) + "</ul>")

    if B["timeline"]:
        rows = ["<table><thead><tr><th>When</th><th>What</th><th>Value</th><th>Confidence</th></tr></thead><tbody>"]
        for e in B["timeline"][:60]:
            rows.append(f"<tr><td>{_hesc(e.get('when') or e.get('date'))}</td><td>{_hesc(e.get('type'))}</td>"
                        f"<td>{_hesc(e.get('value'), 120)}</td><td>{_hesc(e.get('label'))}</td></tr>")
        rows.append("</tbody></table>")
        sec("Timeline", "".join(rows))

    if B.get("limits"):
        sec("Limits", "<ul class=lim>" + "".join(f"<li>{_hesc(n)}</li>" for n in B["limits"]) + "</ul>")

    parts.append(
        f"<details class=sources><summary>Technical sources (collapsed)</summary>"
        f"<p>Mode { _hesc(B.get('mode')) } · generated { _hesc(B.get('generated')) } · "
        f"runtime { _hesc(B.get('runtime_s')) }s. Full capture tables live in the companion .md / .json.</p></details>"
    )
    parts.append(f"<p class=foot>ECHO · public sources only · {_hesc(B.get('handle'))}</p></div></body></html>")
    return "".join(parts)
