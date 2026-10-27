# x-osint-hunter v3: X/Twitter account timeline hunter

Rebuilds an account's public history from **free, keyless, public traces**, in the spirit of memory.lol and lolarchiver:
past **@handles with dates and change windows**, **display names**, **bio / location / website**, **profile pictures**,
**banners**, **follower metrics over time**, and **related accounts**. Everything is merged into one dated timeline,
and every row has an evidence link.

```bash
pip install requests                       # only dependency
python3 hunt.py Yaghistani_521             # thorough (~8-12 min, time-bounded)
python3 hunt.py Yaghistani_521 --deep      # ~15-20 min: frozen-handle username recovery + extra indexes
python3 hunt.py 12                         # by permanent numeric user id
python3 hunt.py jack --fast                # ~3 min, smaller samples
python3 hunt.py jack --snapshots 5         # profile snapshots per year (default 3, fast 1)
python3 hunt.py jack --quiet --budget 900  # no progress log, 15-min budget
#   other flags: --max-snapshots N  --status-max N  --no-chain  --out DIR
```

Output:
- `out/<query>.json`: everything, including `timeline[]`, `username_history`, `display_name_history`, `bio_history`,
  `avatars`, `banners`, `metrics`, `related`, `handle_reuse`, raw `observations`, `failed_sources` and `notes`.
- `out/<query>-report.md`: an identity block, then history tables and the unified timeline, then gaps and failed sources.
- Console summary: username history first, then display-name history.

Timeline event shape:
```json
{"date": "2026-05-13", "type": "display_name", "value": "Zérak", "previous": "Sam",
 "window": ["2026-05-05", "2026-05-13"], "first_seen": "2026-05-13", "last_seen": "2026-09-18",
 "sources": ["wayback"], "confidence": "high", "evidence_url": "https://web.archive.org/web/2026…"}
```
`type` ∈ username, display_name, bio, location, website, avatar, banner, metric, note.
`window` means the change happened between the last capture of the old value and the first capture of the new one.
For avatars and banners, the window start is raised to the image's upload time, which is encoded in its id.

## How it works
1. **Live:** memory.lol (by handle, then by id), api.fxtwitter.com (api.vxtwitter.com as fallback), the server-rendered
   logged-out x.com profile (id, counts, created_at) and the `x.com/i/user/<id>` redirect (the current handle).
2. **Archive inventory (Wayback CDX):** this runs per handle and per permanent id.
   - profile pages on twitter.com, mobile.twitter.com and x.com
   - **every archived tweet URL** `…/<handle>/status/*` (paged; huge accounts get evenly spaced page samples)
   - id-keyed URLs: `intent/user?user_id=`, `intent/follow`, `account/redirect_by_id`, `i/user/<id>`,
     `api.twitter.com/1(.1)/users/show.json?user_id=`, `users/lookup`, `user_timeline` (json/rss),
     `syndication…/timeline-profile/user-id/<id>`, the followbutton `info.json?user_ids=` endpoint and `api.twitter.com/2/users/<id>`
   - handle-keyed `twitter.com/<h>/profile_image` redirects (each one dates an avatar) and `users/show.json?screen_name=`
   - banner images `pbs.twimg.com/profile_banners/<id>/*`
3. **Snapshot analysis:** sampled captures are fetched raw (`id_`) and parsed with layout-specific parsers for 2007-2012, 2013,
   2014-2020, mobile, 2023+ og-tags and **API JSON**. Since 2026, Wayback's tweet captures are stored as X API v2 JSON whose
   `includes.users[]` holds the author's username, name, bio, location, avatar and counts *at capture time*.
   That is why even small accounts get a dense name/bio/avatar timeline.
4. **Owner verification:** each capture's embedded user id must match the target id. Captures that belong to another
   account under the same handle are listed under *Handle reuse*. Pages that carry no id (og-only) are only trusted
   when their handle and date fall inside a verified tenure, or when their avatar id matches a verified one.
5. **Change-window refinement:** whenever two neighbouring captures disagree (handle, name, bio, location or avatar), the
   hunter fetches the capture in the middle. It keeps bisecting to tighten the "changed between" window.
6. **Handle chaining:** every newly proven handle (from memory.lol, id-redirects or snapshots) is inventoried too.
7. **Avatars:** each image id is decoded (snowflake gives the upload time), HEAD-checked live on pbs.twimg.com, and
   looked up in Wayback's image archive.
8. **Related accounts:** `@mentions` in any historical bio (for example "main @x" or "alt @y"), plus a sibling-handle probe
   (`name_521` leads to probes of `name_518…524`) that only reports accounts linking back to the target or sharing its
   display name.

## What each source gives
| Source | Gives | Limits |
|---|---|---|
| api.memory.lol | handle history with first/last dates, handle→id | public tier only covers its public dataset; most small accounts return nothing |
| api.fxtwitter.com | current profile: id, name, bio, location, website, avatar, banner, counts, joined | live only; vxtwitter currently errors |
| x.com SSR / i/user redirect | id, counts, created_at, current handle | live only |
| Wayback tweet captures (JSON/HTML) | the richest history: handle, name, bio, location, avatar, counts per capture | capture density varies widely |
| Wayback profile captures | bio, location, website, banner, counts (2007-2020, 2023+) | 2020-2022 pages are JS shells with no data |
| Wayback id-keyed URLs | the handle at a date, straight from the permanent id | rare for small accounts |
| Wayback image archive | archived avatar and banner files | partial |
| archive.today timemap | snapshot count, first/last | content pages are CAPTCHA-gated |
| X "About this account" | username-change count and last change | **login-only.** Logged-out requests go to the login wall, so it is recorded as failed |
| twitter.lolarchiver.com | full username/display-name history | **paid.** Cloudflare plus a credit system (anonymous visitors get 0 credits); results load client-side. Probed and recorded as failed |

## Honest limits
- Full global username-history indexes (memory.lol's trusted researcher tier, lolarchiver's paid database) are **not
  freely available**. This tool reconstructs history from whatever public archives happen to hold. If a handle was never
  archived and isn't in memory.lol's public tier, it cannot be recovered.
- Wayback CDX is slow (a single page of tweet captures can take 20-60s) and coverage is sparse and uneven. All windows
  are bounded by the nearest captures, and all runs are time-boxed (`--budget`).
- No X API keys, cookies or logged-in sessions are used, by design.
- `hunt_v1_legacy.py` is the previous version, kept for reference.


## v3 additions (beyond free Wayback)

### Frozen media-URL username recovery (`lib/sources_frozen.py`)
X stores the **posting-time** `@handle` inside every media tweet's `expanded_url`
(`https://x.com/<old_handle>/status/<id>/photo/1`) and **never rewrites it after a rename**.
Public syndication (`cdn.syndication.twimg.com/tweet-result`) and `api.fxtwitter.com/status/<id>`
return that path. Combined with Nitter `from:` search (resolves by user id → returns tweets from
every former handle), this recovers dated username history for accounts that never appear in
memory.lol's public 60-day window or in Archive Team Stream Grab (which ended ~2023).

### Deep extras (`lib/sources_deep.py`, `--deep`)
- Live field enrichment (pinned tweet, media_count, likes, bio URLs, emails **only if pasted in bio**)
- Common Crawl URL index (no multi-GB WARC auto-download)
- GitHub issues/repos, Hacker News Algolia, PullPush Reddit mentions
- Ghostarchive / SocialBlade best-effort
- Pre-gap Wayback CDX (`to=20260326`) under every known handle
- Broader sibling/typo handle matrix

### Research notes
See [`research/deep-sources.md`](research/deep-sources.md) for how memory.lol actually builds its
index (Stream Grab + Wayback), what is gated/paid, and why we reject undocumented GraphQL,
Cloudflare bypasses, and breach-DB email lookup.

## Honest gaps (still true)
- **memory.lol** full history needs Travis Brown's trusted access (public tier ≈ last 60 days).
- **lolarchiver** is credit-metered + Cloudflare — not queried beyond a probe.
- **X "About this account"** username-change *count* is login-only in a browser; third-party About APIs are freemium.
- **Login email** is never on public X profiles. We only report addresses the owner pasted into a bio/tweet/archive.
- Accounts that renamed **before any media tweet** leave no frozen URL; Stream Grab cannot cover accounts created after ~2023-01.
