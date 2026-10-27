# Deep sources research — how real username-history tools work, and what we can legally replicate

_Last updated: 2026-10-27 (Europe/London)._

## The “real deal” (memory.lol / lolarchiver)

### [travisbrown/memory.lol](https://github.com/travisbrown/memory.lol)
- **What it is:** Index of ~542M historical screen names for ~443M Twitter accounts.
- **How the data was built (documented in the README):** scraped from two **public Internet Archive** corpora:
  1. [Archive Team Twitter Stream Grab](https://archive.org/details/twitterstream) (“Spritzer” JSON, monthly tarballs, ~2011 → early 2023)
  2. Wayback Machine profile/status captures
- **Storage:** RocksDB (`accounts` keyed by user id → screen_name→dates; `screen_names` reverse index). Import formats: NDJSON Twitter user objects + `snapshot` epoch, or CSV `(user_id, screen_name, epoch)`.
- **License:** **not OSI open source** (Anti-Capitalist / custom — individuals, non-profits, worker-owned businesses). Source is visible; redistributing a hosted clone of the full DB is not free-for-all.
- **Public API gate:** unauthenticated lookups are limited to facts observed in the **last 60 days**, plus a curated “bad actor” allow-list. Full history requires **approved** GitHub/Google auth ([device-flow docs in README](https://github.com/travisbrown/memory.lol)).
- **What we can replicate for free:**
  - Call the public API (already in v2) — often empty for ordinary accounts.
  - Optionally process Stream Grab months ourselves with [travisbrown/at-twitter-stream](https://github.com/travisbrown/at-twitter-stream) — **legit but huge** (tens of GB per month; account created Jul 2024 is **after** the Stream Grab ends ~Jan 2023, so Stream Grab cannot contain `@Yaghistani_521` / id `1810109221251493888` at all).
- **Integrate decision:** keep public API; document trust gate; **do not** ship a pirated copy of the private RocksDB. For post-2023 accounts, Stream Grab is irrelevant — need other techniques (below).

### Companion: [travisbrown/at-twitter-stream](https://github.com/travisbrown/at-twitter-stream)
Rust extractor over Stream Grab JSON → local RocksDB. Same data lineage as memory.lol. Useful if we ever pre-index specific months; not useful for 2024+ accounts.

### lolarchiver (`twitter.lolarchiver.com`)
- Credit-metered + Cloudflare; client-side results. Coverage claims: late 2011 → Jan 2023, then 2026+.
- **Integrate decision:** probe & fail honestly (already in v2). No CF bypass.

### Wrappers (not new data)
| Project | Notes | Decision |
|---|---|---|
| [kukapay/twitter-username-changes-mcp](https://github.com/kukapay/twitter-username-changes-mcp) | Thin MCP over `api.memory.lol` | Reject (duplicate) |
| [BankkRoll/memoryloler](https://github.com/BankkRoll/memoryloler) | Next.js UI + GitHub OAuth device flow for memory.lol | Reject as dependency; OAuth flow documented for humans |
| [MONIBAGS/monibags-sdk](https://github.com/MONIBAGS/monibags-sdk) | POSTs `monibags.xyz/api/check-username-history` (opaque commercial) | Reject — host unreachable / non-public corpus |

## X “About this account” (change *count*, not names)

Since ~Nov 2025 X exposes `username_changes.count` + last change time in the About panel. Logged-out requests redirect to login (`AboutAccountQuery` GraphQL).

| Project | Approach | Decision |
|---|---|---|
| [antidotcb/country-reveal-4x](https://github.com/antidotcb/country-reveal-4x) | Chrome ext; sniffs the **user’s own** logged-in bearer + ct0 to call About GraphQL | Reject for headless hunter (needs the operator’s X session in a browser ext — not something we steal/automate) |
| [Sorsa `/v3/about`](https://api.sorsa.io/playground/username-history-check), TryAPI, twitterapi.io tools, RapidAPI twitter-api45 | Paid/freemium proxies that return the same About fields | Optional later with user-supplied API key; **not** free unlimited. Playground is 5 checks/day — fragile to scrape. |
| Our `src_about_account` | Direct fetch of `/about` | Keep; records login-only failure + manual URL |

**Important:** About gives a **count**, never the previous strings. Names still need archives / frozen URLs.

## Frozen media-URL technique (NEW — this is the breakthrough for 2024+ accounts)

When a tweet has media, X stores:

```
https://x.com/<handle_at_posting_time>/status/<id>/photo/1
```

inside `media.expanded_url` / syndication `mediaDetails`. **Renames never rewrite that path.** Public endpoints that return it without auth:

- `https://cdn.syndication.twimg.com/tweet-result?id=<id>&token=<js-token>`
- `https://api.fxtwitter.com/status/<id>` (facets `replacement`)

Combined with Nitter search (`from:current_handle` resolves by **user id**, so it returns tweets originally posted under every former handle), we can:

1. Sample media tweets across the account’s life
2. Read the frozen path → dated former `@handle`
3. Owner-verify via `author.id` / `author_id`

**Proven on the demo account (manual probe, 2026-10-27):**

| Date (tweet) | Frozen handle in media URL | Current author id |
|---|---|---|
| 2024-09-08 | `@NotAverage_22` | `1810109221251493888` |
| 2024-12-30 (Wayback under Anon_4567) | `@Anon_4567` | same id |
| 2025-06-24 (pinned) | `@Anon_4567` | same id |
| 2026+ | `@Yaghistani_521` | same id |

So the username timeline is at least:

**`NotAverage_22` (by Sep 2024) → `Anon_4567` (by Dec 2024, still on pinned Jun 2025) → `Yaghistani_521` (by Mar 2026 Wayback / now).**

Integrated as `lib/sources_frozen.py` (`harvest_frozen`), enabled fully under `--deep`.

Related prior art: [deepseagirl/twid](https://github.com/deepseagirl/twid) (Wayback → id for a handle), [claromes/waybacktweets](https://github.com/claromes/waybacktweets), [humandecoded/twayback](https://github.com/humandecoded/twayback) — tweet recovery, not frozen-handle extraction. Our technique is complementary.

## Other free indexes / scrapers

| Source | Yield | Decision |
|---|---|---|
| Wayback CDX (profiles, statuses, id-keyed, API JSON) | Core of v2; dense name/bio/PFP after Mar 2026 for this account | Keep / already integrated |
| archive.today timemap | Spotty; often CAPTCHA | Keep best-effort |
| Ghostarchive search | Often redirect/CAPTCHA | Deep best-effort |
| Common Crawl URL index (`index.commoncrawl.org`) | URL hits only; WARC fetch is heavy | Deep: index query + note; no auto multi-GB download |
| [archive.org twitterstream](https://archive.org/details/twitterstream) | Ends ~2023-01; **cannot** cover Jul-2024 accounts | Document only; optional offline pipeline later |
| Kaggle “2.8B id↔screenname” (Nov 2022 snapshot) | One-shot mapping, pre-dates target | Reject for this account; optional offline for old ids |
| Nitter instances ([status.d420.de](https://status.d420.de)) | Search + RSS; many dead after Aug 2026 X C&D | Integrate as tweet-id discovery for frozen-handle |
| SocialBlade | Often 403 to bots | Deep best-effort |
| GitHub issues/repos search (unauth) | Mentions of handle/id | Deep |
| HN Algolia | Mentions | Deep |
| PullPush.io (Reddit archive) | Mentions | Deep |
| holehe / SocialPwned / breach-oriented email tools | Login-email inference via third parties | **Reject as product feature** — X login email is private; we only report emails **pasted in bio/tweets** |
| twint / snscrape-era scrapers | Dead vs modern X | Reject |
| Undocumented X GraphQL with stolen bearer | Violates hard rules | **Reject** |

## Email

X does not publish the account login email on public profiles or in About. We only surface addresses that appear as plain text in bios/tweets/archives. No breach-DB product feature.

## Integrate summary (code)

| Module | Role |
|---|---|
| `lib/sources.py` | Live (memory.lol, fxtwitter, SSR, About, lolarchiver), Wayback CDX, related |
| `lib/sources_frozen.py` | Nitter id discovery + syndication/fxtwitter frozen media URLs → username history |
| `lib/sources_deep.py` | Common Crawl index, GH/HN/Reddit, Ghostarchive, SocialBlade, earliest CDX, deep siblings, live field enrichment |
| `hunt.py --deep` | Longer budget; runs frozen + deep after first archive pass; re-inventories newly found handles |

## True remaining blockers

1. **memory.lol full index** — needs Travis Brown’s trust approval (GitHub device flow).
2. **lolarchiver** — paid credits + Cloudflare.
3. **X About panel strings** — login-only; third-party About APIs are freemium.
4. **Pre-first-media window** — if an account renamed before posting any media, frozen URLs cannot see that handle; only Stream Grab / memory.lol / manual About count help.
5. **Login email** — not publicly available by design.

## Validation run (2026-10-27 Europe/London)

`python3 hunt.py Yaghistani_521 --deep` → exit 0, ~1130s.

Recovered username chain (high confidence, owner-verified by user id `1810109221251493888`):

| Handle | Window | Primary evidence |
|---|---|---|
| `@NotAverage_22` | 2024-07-08 → 2024-12-28 | frozen media + 51 Wayback tweet captures |
| `@Anon_4567` | 2024-12-30 → 2025-03-29 | frozen media (syndication); change ~2024-12-28→30 |
| `@Yaghistani_521` | 2026-09-25 → now | frozen + live; change window 2025-03-29 → 2026-09-25 (possible intermediate handle(s) in that gap if any media-free renames) |

Also: display Abid → Yaseén; location Islamabad → Khyber; related `@yaghistani_523` (Yaseén 2.0); avatar from account-create snowflake; no public email.

Artifacts: `out/Yaghistani_521-report.md`, `out/Yaghistani_521.json`, `out/Yaghistani_521-deep-run.log`.

## v3 upgrade (2026-10-27 Europe/London): life-span scan + multi-platform pivot

### Why older history was sometimes missing
- Only 4 seed windows were sampled (first results page each), so long account lives were mostly unsampled.
- The frozen-URL harvest ran late and was starved of budget by Wayback waits.
- Dormant months used up windows; mention-based evidence was not collected.
- Display names were polluted by the *current* author_name on old embeds.

### What v3 does (`lib/sources_scan.py`, runs in parallel from the start of every hunt)
1. Splits the whole account life (creation snowflake → now) into monthly windows (fast 10 / thorough 30 / deep 60) and runs a Nitter `from:` search per window. `from:` resolves by user id, so it returns tweets made under **former** handles.
2. For each hit it checks syndication/fxtwitter JSON:
   - the media `expanded_url` freezes the posting-time handle (high confidence);
   - mention entities in other people's replies, reply parents and quoted tweets freeze the handle as it was then (medium confidence, always uid-verified).
3. Walks to the earliest public tweets, then **bisects** between adjacent handles to narrow every change to a day range (falls back to `filter:replies`).
4. Runs `to:<old handle>` searches limited to each old handle's period and keeps only uid-matched results (handles get reused, e.g. Anon_4567 now belongs to another account).
5. Report section "Life-span username scan": handles table with first/last seen and evidence, change lines, an activity map and a note when there were no public tweets.
- Wayback CDX circuit breaker: after 5 straight failures or a "Temporarily Offline" HTML page, CDX is skipped and the report says so.
- Profile-capture CDX also uses prefix queries for twitter.com/x.com, `statuscode:200`.

Result: Yaghistani_521 in **fast** mode (179s) → NotAverage_22 (2024-09-08) → Anon_4567 (2024-12-30 → 2026-03-25) → Yaghistani_521 (2026-03-27 →). v2 deep had a 2025-03 → 2026-09 gap.

### Multi-platform pivot (`engine/pivot.py`)
- Input: an email, `prefix:user` (ig, tt/tiktok, gh, gl, reddit, bsky, yt, twitch, tg, steam, x ...), a profile URL, or a username.
- Checks 23 public profile endpoints. Followers, bio and created date come from public JSON where it exists.
- Email: Gravatar (md5 + v3), GitHub commit-author search and self-declared profile emails, keys.openpgp.org, MX/provider, RDAP for custom domains. HIBP/Google/Bing are manual links only.
- Round 2 checks accounts cross-linked from found profiles.
- Confidence: target / high (cross-linked) / medium (self-declared) / username match only.
- Out of scope: password-reset or registration probing, breach DBs, logins.
