#!/usr/bin/env python3
"""
ECHO / x-osint-hunter v4 - rebuild the full public history of an X/Twitter account from free traces.

  python3 hunt.py <username|user_id> [--fast|--deep] [--snapshots N] [--quiet]

See README.md for sources and limits.
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import net  # noqa: E402
from lib.net import Budget, log, now_iso, ts_to_iso  # noqa: E402
from lib.model import Results  # noqa: E402
from lib import sources as S  # noqa: E402
from lib import sources_frozen as SF  # noqa: E402
from lib import sources_deep as SD  # noqa: E402
from lib import sources_scan as SS  # noqa: E402
import threading  # noqa: E402
from lib.timeline import build, avatar_id, norm_key  # noqa: E402
from lib.report import to_markdown, to_html, console  # noqa: E402
from lib import dossier as DS  # noqa: E402
from lib import history as H  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

MODES = {
    "thorough": dict(budget=780, status_pages=8, status_max=150, gap_queries=18, refine_rounds=7, per_month=3, per_year=3, profile_cap=48, refine=50, avatars=24,
                     id_max=30, misc_max=16, chain_rounds=3, inventory_s=240, deep=False),
    "fast": dict(budget=220, status_pages=1, status_max=40, gap_queries=4, refine_rounds=4, per_month=2, per_year=1, profile_cap=12, refine=10, avatars=10,
                 id_max=10, misc_max=6, chain_rounds=1, inventory_s=120, deep=False),
    "deep": dict(budget=1500, status_pages=10, status_max=260, gap_queries=40, refine_rounds=9, per_month=4, per_year=4, profile_cap=60, refine=60, avatars=30,
                 id_max=40, misc_max=20, chain_rounds=4, inventory_s=300, deep=True),
}


class Hunt:
    def __init__(self, query, cfg):
        self.q = query.strip().lstrip("@")
        self.cfg = cfg
        self.B = Budget(cfg["budget"])
        self.R = Results(self.q)
        self.pool = {}        # (ts, url) -> capture
        self.done = {}        # (ts, url) -> parsed fields | None
        self.scanned = set()  # handles inventoried
        self.id_scanned = False
        self.id_retry = False

    # ------------------------------------------------------------ phases
    def live(self):
        R, q = self.R, self.q
        jobs = []
        if q.isdigit():
            R.set_id(q, "query")
            h = S.src_x_id_redirect(R, q)
            S.src_memorylol(R, uid=q)
            handle = h or R.current or next(iter(R.memorylol), None)
            if not handle:
                R.note("could not resolve a current handle for this id (suspended/deleted?) - archive-only run")
        else:
            handle = q
            jobs.append(lambda: S.src_memorylol(R, handle=q))
        if handle:
            jobs += [lambda: S.src_fxtwitter(R, handle), lambda: S.src_x_ssr(R, handle),
                     lambda: S.src_about_account(R, handle), lambda: S.src_lolarchiver(R, handle)]
        with ThreadPoolExecutor(5) as ex:
            list(ex.map(lambda f: f(), jobs))
        if R.user_id and not q.isdigit():
            S.src_memorylol(R, uid=R.user_id)
            if not R.current:
                S.src_x_id_redirect(R, R.user_id)
        if not R.current and not q.isdigit() and not R.user_id:
            R.note(f"@{q} is not live (suspended, deleted, renamed or never existed) - relying on archives")

    def inventory(self, handles):
        R, c = self.R, self.cfg
        handles = [h for h in handles if h.lower() not in self.scanned]
        for h in handles:
            self.scanned.add(h.lower())
        jobs, sinks = [], []
        for h in handles:
            for host in ("twitter.com", "x.com", "mobile.twitter.com"):
                sink = []
                sinks.append(sink)
                jobs.append(("status", h, lambda h=h, host=host, sink=sink: S.cdx_status_caps(R, h, host, c["status_pages"], self.B, sink)))
            jobs += [("profile", h, lambda h=h: S.cdx_profile_caps(R, h)),
                     ("misc", h, lambda h=h: S.cdx_handle_misc(R, h, self.cfg["fast"])),
                     ("ph", h, lambda h=h: S.src_archive_ph(R, h))]
        if R.user_id and not self.id_scanned:
            self.id_scanned = True
            jobs += [("id", None, lambda: S.cdx_id_caps(R, R.user_id, self.cfg["fast"])), ("banner", None, lambda: S.src_banners(R, R.user_id))]
        log(f"inventorying archives for {', '.join('@' + h for h in handles) or '(id only)'} ...")
        found = {}
        limit = max(30, min(self.cfg["inventory_s"], self.B.left() * 0.5))
        ex = ThreadPoolExecutor(6)
        futs = {ex.submit(f): (k, h) for k, h, f in jobs}
        done, pending = wait(futs, timeout=limit)
        ex.shutdown(wait=False, cancel_futures=True)
        for fu in pending:
            k, h = futs[fu]
            if k == "id":
                self.id_retry = True
            if k == "status":
                R.note(f"tweet-capture scan for @{h} hit the {limit:.0f}s inventory limit - using the pages read so far")
                continue
            R.fail(f"archive inventory {k} @{h or R.user_id}", f"still running after {limit:.0f}s - skipped (Wayback CDX slow)")
        for fu in done:
            k, h = futs[fu]
            try:
                res = fu.result()
            except Exception as e:
                R.fail(f"inventory {k} @{h}", e)
                continue
            if k in ("profile", "misc", "id") and res:
                found.setdefault(k, []).extend(res)
        for sink in sinks:
            found.setdefault("status", []).extend(list(sink))
        for lst in found.values():
            for cap in lst:
                self.pool.setdefault((cap["timestamp"], cap["original"]), cap)
        log("captures found: " + ", ".join(f"{k}={len(v)}" for k, v in found.items()))
        return found, handles

    def select(self, found, handles):
        c = self.cfg
        pick = []
        prof = found.get("profile", [])
        for h in handles:
            pick += S.pick_profile_samples([x for x in prof if x["handle"] == h], c["per_year"],
                                           max(6, c["profile_cap"] // max(len(handles), 1)))
        # v4: sample tweet captures over the account's whole *timeline* (all handles pooled, round-robin by month),
        # not per handle - per-handle quotas thinned out months and dropped names that lasted most of a year.
        st = [x for x in found.get("status", []) if (x["timestamp"], x["original"]) not in self.done]
        months = len({x["timestamp"][:6] for x in st})
        pick += S.pick_spread(st, min(c["status_max"], max(12, months * c.get("per_month", 3))))
        pick += S.pick_spread(found.get("id", []), c["id_max"], prefer_json=False)
        pick += S.pick_spread(found.get("misc", []), c["misc_max"], prefer_json=False)
        return [p for p in pick if (p["timestamp"], p["original"]) not in self.done]

    def analyse(self, caps, label):
        if not caps:
            return
        caps = sorted(caps, key=lambda c: (c["kind"] not in ("status", "idpage", "json"), c["timestamp"]))
        log(f"analysing {len(caps)} archived captures ({label}) ...")
        t0 = time.time()

        def one(cap):
            k = (cap["timestamp"], cap["original"])
            if k in self.done:
                return
            if not self.B.ok(20):
                return
            self.done[k] = S.analyse_capture(self.R, cap)
        with ThreadPoolExecutor(6) as ex:
            list(ex.map(one, caps))
        log(f"  done in {time.time() - t0:.0f}s, budget left {self.B.left():.0f}s")

    def chain(self):
        """New handles proven to belong to the id -> inventory them too."""
        for _ in range(self.cfg["chain_rounds"]):
            new = [h for h in self.R.handles_seen() .values() if h.lower() not in self.scanned]
            if not new or not self.B.ok(120):
                return
            log(f"handle chaining -> {', '.join('@' + h for h in new)}")
            found, hs = self.inventory(new[:4])
            self.analyse(self.select(found, hs), "chained handles")

    def refine(self):
        """Binary-search between captures that disagree, to tighten every 'changed between' window."""
        left = self.cfg["refine"]
        rounds = 0
        while left > 0 and self.B.ok(60) and rounds < self.cfg.get("refine_rounds", 6):
            rounds += 1
            seq = []
            for k, f in self.done.items():
                if not f or (f.get("user_id") and f["user_id"] != self.R.user_id):
                    continue
                if not f.get("user_id") and self.pool.get(k, {}).get("kind") != "idpage":
                    continue
                seq.append((k[0], f))
            seq.sort(key=lambda x: x[0])
            cands = []
            for (ta, fa), (tb, fb) in zip(seq, seq[1:]):
                diff = False
                for fld in ("screen_name", "name", "avatar", "description", "location"):
                    va, vb = norm_key(fld, fa.get(fld)), norm_key(fld, fb.get(fld))
                    if va is not None and vb is not None and va != vb:
                        diff = True
                        break
                if not diff:
                    continue
                between = sorted([c for k, c in self.pool.items() if ta < k[0] < tb and k not in self.done
                                  and c["kind"] in ("status", "profile", "idpage", "json")
                                  and c.get("statuscode") in ("200", "-")], key=lambda c: c["timestamp"])
                if between:
                    js = [c for c in between if "json" in (c.get("mimetype") or "")] or between
                    cands.append(js[len(js) // 2])
            if not cands:
                break
            cands = cands[:left]
            left -= len(cands)
            self.analyse(cands, f"refining change windows, round {rounds}")

    def avatars(self, hist):
        av = hist["avatar"]
        urls = {}
        for o in self.R.obs:
            a = o["fields"].get("avatar")
            if a and avatar_id(a):
                urls.setdefault(avatar_id(a), set()).add(a)
        todo = av[-self.cfg["avatars"]:]
        for a in av:
            a["seen_at_urls"] = sorted(urls.get(a.get("image_id"), {a["value"]}))
            a["base_url"] = re.sub(r"_(normal|bigger|mini|reasonably_small|\d+x\d+)(?=\.\w+$|$)", "", a["value"])
            a["url_400x400"] = re.sub(r"(\.\w+)$", r"_400x400\1", re.sub(r"^https?://[^/]+(?:/twitter_production)?/", "https://pbs.twimg.com/", a["base_url"])) \
                if re.search(r"\.\w+$", a["base_url"]) else a["base_url"] + "_400x400"
        log(f"checking {len(todo)} avatar image(s) live + in the image archive ...")
        ex = ThreadPoolExecutor(4)
        futs = [ex.submit(S.enrich_avatar, self.R, a, self.B) for a in todo]
        _, pending = wait(futs, timeout=max(45, min(150, self.B.left())))
        ex.shutdown(wait=False, cancel_futures=True)
        if pending:
            self.R.note(f"{len(pending)} avatar archive/live checks did not finish in time")
        if len(av) > len(todo):
            self.R.note(f"only the {len(todo)} most recent of {len(av)} avatars were checked live/in the image archive (use thorough mode / larger budget)")

    def retry_status(self):
        """Handles whose tweet-capture index failed earlier (archive.org 504 bursts) get one more day-index try."""
        R = self.R
        for h in list(self.scanned):
            if not self.B.ok(120):
                return
            got = sum((v or {}).get("usable_captures", 0) for v in (R.status_scans.get(h) or
                      next((R.status_scans[k] for k in R.status_scans if k.lower() == h), {})).values())
            if got:
                continue
            for host in ("twitter.com", "x.com"):
                sink = []
                S.cdx_status_daily(R, h, host, sink)
                for cap in sink:
                    self.pool.setdefault((cap["timestamp"], cap["original"]), cap)
                if sink:
                    log(f"status-index retry: @{h} on {host} -> {len(sink)} day-captures")
                    self.analyse(S.pick_spread([c for c in sink if (c["timestamp"], c["original"]) not in self.done],
                                               self.cfg["status_max"] // 2), f"retry @{h}")

    # ------------------------------------------------------------ run
    def run(self, mode):
        t0 = time.time()
        R = self.R
        self.live()
        H.load_prior(R)
        # v3: the life-span frozen-handle scan (Nitter search + embed endpoint) runs in parallel with the
        # Wayback inventory from the very start, so it can never be starved by a slow archive.org.
        level = "deep" if self.cfg.get("deep") else ("fast" if self.cfg.get("fast") else "thorough")
        scan = threading.Thread(target=SS.run_life_scan, args=(R, self.B, level), daemon=True)
        scan.start()
        handles = list(dict.fromkeys([h for h in [self.q if not self.q.isdigit() else None, R.current, *R.memorylol] if h]))
        found, hs = self.inventory(handles)
        self.analyse(self.select(found, hs), "first pass")
        if R.user_id and (not self.id_scanned or self.id_retry) and self.B.ok(150):
            self.id_scanned, self.id_retry = False, False
            found, hs = self.inventory([])
            self.analyse(self.select(found, hs), "id-keyed pages")
        scan.join(timeout=max(0, min(self.B.left() - 40, SS.LEVELS[level]["seconds"] + 30)))
        if level != "fast" and self.B.ok(90):
            SS.old_handle_mentions(R, self.B)
        self.chain()
        self.refine()
        # Frozen-handle harvest BEFORE related-account probe so former @handles get inventoried/chained.
        if self.B.ok(60):
            SF.harvest_frozen(R, self.B, deep=self.cfg.get("deep", False))
            # Newly proven handles (e.g. Anon_4567) → inventory their Wayback captures too
            new_h = [h for h in R.handles_seen().values() if h.lower() not in self.scanned]
            if new_h and self.B.ok(90):
                log(f"frozen-handle chaining -> {', '.join('@' + h for h in new_h[:4])}")
                found2, hs2 = self.inventory(new_h[:4])
                self.analyse(self.select(found2, hs2), "frozen former handles")
                self.refine()
        # v4: month-by-month completeness pass for display name / bio / avatar.
        if self.B.ok(120):
            self.retry_status()
        for _ in range(2):
            if not self.B.ok(120) or not H.gap_fill(self, self.cfg.get("gap_queries", 10)):
                break
        if self.B.ok(90):
            self.refine()
        S.find_related(R, self.B)
        if self.cfg.get("deep") and self.B.ok(60):
            SD.run_deep_sources(R, self.B, deep=True)
        elif self.B.ok(30):
            # even thorough/fast get live enrichment + light GH/HN
            if R.current:
                SD.src_live_extra(R, R.current)
        hist, timeline, metrics = build(R)
        if hist["avatar"]:
            self.avatars(hist)
        H.save_cache(R)
        self.coverage = H.coverage_report(R, self.pool)
        gaps = self.coverage.get("months_archived_but_unsampled") or []
        if gaps:
            R.note(f"{len(gaps)} archived month(s) still have no dated display-name evidence "
                   f"({', '.join(gaps[:8])}{' ...' if len(gaps) > 8 else ''}) - budget/archive.org limits; re-run Deep later "
                   "(earlier findings are kept in the cross-run cache)")
        if net.CFG.get("wayback_offline"):
            R.note("archive.org (Wayback Machine) was temporarily offline / overloaded during this run, so archived "
                   "profile & tweet pages (the main source of old display names, bios, avatars and banners) could not be "
                   "read. Re-run later for full pre-2025 profile history; the tweet-based username scan still ran.")
        if net.HTTP.storms or net.HTTP.rate_limited.get("web.archive.org"):
            R.note(f"archive.org rate-limited this run ({net.HTTP.rate_limited.get('web.archive.org', 0)} HTTP 429 replies on "
                   "capture fetches) - some archived profile/tweet captures could not be read; dated history from earlier runs "
                   "of this same user id was carried over. Re-run Deep in an hour for the remaining months.")
        if not self.B.ok(0):
            R.note(f"time budget ({self.cfg['budget']}s) ran out - some captures were not analysed; rerun with a larger --budget")
        if len(hist["screen_name"]) <= 1:
            R.note("no username change found in public traces: either the handle never changed, or older handles were never "
                   "archived/indexed (memory.lol public tier + Wayback). X's login-only 'About this account' panel shows the change count.")
        if not any(o["verified"] and o["source"].startswith("wayback") for o in R.obs):
            R.note("no owner-verified Wayback captures - historical fields rely on live data / memory.lol only")
        return self.assemble(hist, timeline, metrics, mode, time.time() - t0)

    def assemble(self, hist, timeline, metrics, mode, runtime):
        R = self.R
        lv = R.live
        latest = {}
        for o in sorted([o for o in R.obs if o["verified"]], key=lambda o: o["date"]):
            latest.update({k: v for k, v in o["fields"].items() if v not in (None,)})
        cur_av = next((a for a in reversed(hist["avatar"])), None)
        acc = {
            "user_id": R.user_id, "current_handle": R.current or latest.get("screen_name"),
            "display_name": lv.get("name") or latest.get("name"),
            "created_at": next((e["date"] for e in timeline if e["value"] == "account created"), None),
            "bio": lv.get("description", latest.get("description")), "location": lv.get("location", latest.get("location")),
            "website": (lv.get("website") or {}).get("url") if isinstance(lv.get("website"), dict) else lv.get("website") or latest.get("website"),
            "followers": lv.get("followers", latest.get("followers")), "following": lv.get("following", latest.get("following")),
            "tweets": lv.get("tweets", latest.get("tweets")), "likes": lv.get("likes"), "protected": lv.get("protected"),
            "verification": (lv.get("verification") or {}).get("type") if isinstance(lv.get("verification"), dict) else None,
            "current_avatar": lv.get("avatar_url") or (cur_av or {}).get("value"),
            "current_banner": lv.get("banner_url") or (hist["banner"][-1]["value"] if hist["banner"] else None),
            "live": bool(lv),
            "permalinks": [f"https://x.com/i/user/{R.user_id}", f"https://twitter.com/intent/user?user_id={R.user_id}"] if R.user_id else [],
        }
        reuse = {}
        for h, s in R.foreign.items():
            reuse[h] = [{"user_id": u, "date": d, "evidence": e} for u, d, e in sorted(s, key=lambda x: str(x[1]))]
        pool = list(self.pool.values())
        D = {
            "query": self.q, "generated": now_iso(), "mode": mode, "runtime_s": round(runtime),
            "account": acc, "about_account": R.about,
            "history": {"username": hist["screen_name"], "display_name": hist["name"], "bio": hist["description"],
                        "location": hist["location"], "website": hist["website"]},
            "username_history": hist["screen_name"], "display_name_history": hist["name"], "bio_history": hist["description"],
            "avatars": hist["avatar"], "banners": hist["banner"], "metrics": metrics, "timeline": timeline,
            "related": sorted(R.related.values(), key=lambda r: {"high": 0, "medium": 1, "low": 2}[r["confidence"]]),
            "handle_reuse": reuse, "memorylol": R.memorylol, "status_scans": R.status_scans, "archive_today": R.archive_ph,
            "frozen_handles": R.raw.get("frozen_handles"),
            "life_scan": R.raw.get("life_scan"),
            "deep_mentions": {k: R.raw.get(k) for k in
                              ("github_mentions", "hn_mentions", "reddit_mentions", "commoncrawl",
                               "ghostarchive", "socialblade", "earliest_status_cdx") if k in R.raw},
            "id_pages": R.id_pages[:200],
            "history_coverage": dict(getattr(self, "coverage", {}) or {}, wayback=net.wayback_health()),
            "coverage": {"observations": len(R.obs), "verified_observations": sum(o["verified"] for o in R.obs),
                         "captures_available": len(pool), "captures_analysed": len(self.done),
                         "profile_captures": sum(c["kind"] == "profile" for c in pool),
                         "status_captures": sum(c["kind"] == "status" for c in pool),
                         "id_captures": sum(c["kind"] in ("idpage", "json", "rss") and not c.get("handle") for c in pool),
                         "misc_captures": sum(c["kind"] in ("pimg", "json") and bool(c.get("handle")) for c in pool)},
            "sources_worked": sorted(R.worked), "failed_sources": R.failed, "notes": R.notes,
            "unverified_sightings": [o for o in R.obs if o["confidence"] == "low"][:100],
            "observations": sorted(R.obs, key=lambda o: o["date"]),
        }
        return D


def main():
    ap = argparse.ArgumentParser(description="Rebuild an X/Twitter account's username, name, bio, avatar & banner history.")
    ap.add_argument("target", help="@handle or numeric user id")
    ap.add_argument("--fast", action="store_true", help="~3 minute run with smaller samples")
    ap.add_argument("--deep", action="store_true",
                    help="~15-20 min: frozen-handle username recovery, Common Crawl, GH/HN/Reddit, "
                         "deeper sibling probe, pre-gap CDX")
    ap.add_argument("--snapshots", type=int, help="profile snapshots per year to analyse (default 3, fast 1)")
    ap.add_argument("--max-snapshots", type=int, help="cap on profile snapshots per handle")
    ap.add_argument("--status-max", type=int, help="max archived tweets to analyse per pass")
    ap.add_argument("--budget", type=int, help="time budget in seconds")
    ap.add_argument("--no-chain", action="store_true", help="don't follow newly discovered handles")
    ap.add_argument("--quiet", action="store_true", help="no progress log")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    if a.fast and a.deep:
        print("pick one of --fast / --deep (deep already includes thorough sampling)", file=sys.stderr)
        sys.exit(2)
    mode = "deep" if a.deep else ("fast" if a.fast else "thorough")
    cfg = dict(MODES[mode], fast=a.fast)
    if a.snapshots:
        cfg["per_year"] = a.snapshots
    if a.max_snapshots:
        cfg["profile_cap"] = a.max_snapshots
    if a.status_max:
        cfg["status_max"] = a.status_max
    if a.budget:
        cfg["budget"] = a.budget
    if a.no_chain:
        cfg["chain_rounds"] = 0
    net.CFG["verbose"] = not a.quiet
    h = Hunt(a.target, cfg)
    D = h.run(mode)
    # After thorough/deep: fold public multi-platform + leak metadata into the dossier (existing modules only).
    if mode in ("thorough", "deep"):
        try:
            DS.enrich(D, mode, log=lambda *a: log(*a) if a else None)
        except Exception as e:
            log(f"dossier enrich skipped: {type(e).__name__}: {e}")
    os.makedirs(a.out, exist_ok=True)
    base = os.path.join(a.out, re.sub(r"[^A-Za-z0-9_]", "_", h.q))
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(D, f, indent=2, ensure_ascii=False, default=lambda x: sorted(x) if isinstance(x, set) else str(x))
    with open(base + "-report.md", "w", encoding="utf-8") as f:
        f.write(to_markdown(D))
    try:
        with open(base + "-report.html", "w", encoding="utf-8") as f:
            f.write(to_html(D))
    except Exception as e:
        log(f"html dossier skipped: {type(e).__name__}: {e}")
    paths = {"json": base + ".json", "report": base + "-report.md"}
    if os.path.exists(base + "-report.html"):
        paths["html"] = base + "-report.html"
    console(D, paths)


if __name__ == "__main__":
    main()
