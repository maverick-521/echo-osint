#!/usr/bin/env python3
"""
ECHO leak check - has an email address, phone number or username shown up in known data breaches?

  python3 breach.py <email | +phone | username> [--phone +4477...] [--username name] [--fast|--deep] [--out DIR]

Only free, public lookup services are queried, and only *metadata* is kept (breach name, date, which kinds
of data leaked). Leaked passwords / records are never downloaded, printed or stored. "Not found" means the
free sources that answered did not list it - it is never proof the address/number is clean.

Sources
  * XposedOrNot (free public API)           - breach list + per-breach data types for an email
  * LeakCheck public API (free)              - breach source names + dates for email / username / phone
  * ProxyNova COMB lookup (free)             - match COUNT only in the 2021 "Compilation of Many Breaches"
  * Have I Been Pwned                         - official API only if HIBP_API_KEY is set; public breach
                                                catalogue (no key) is used to enrich breach details
  * Gravatar                                  - whether the email has a public profile (exposure, not a breach)
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote

import requests

UA = "ECHO-leak-check/1.0 (personal self-check; +local)"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept": "application/json"})
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,24}$")
USER_RE = re.compile(r"^@?[A-Za-z0-9._\-]{3,40}$")
SENSITIVE = {"passwords", "password hints", "security questions and answers", "credit cards", "partial credit card data",
             "bank account numbers", "social security numbers", "government issued ids", "passport numbers",
             "auth tokens", "private messages", "password", "ssn", "credit_card", "security_question", "pin"}
PERSONAL = {"phone numbers", "physical addresses", "dates of birth", "ip addresses", "geographic locations",
            "names", "phone", "address", "dob", "ip", "location", "name", "first_name", "last_name", "zip", "city"}
CC = {"1": "USA/Canada (NANP)", "7": "Russia/Kazakhstan", "20": "Egypt", "27": "South Africa", "30": "Greece",
      "31": "Netherlands", "32": "Belgium", "33": "France", "34": "Spain", "36": "Hungary", "39": "Italy",
      "40": "Romania", "41": "Switzerland", "43": "Austria", "44": "United Kingdom", "45": "Denmark", "46": "Sweden",
      "47": "Norway", "48": "Poland", "49": "Germany", "51": "Peru", "52": "Mexico", "54": "Argentina", "55": "Brazil",
      "56": "Chile", "57": "Colombia", "60": "Malaysia", "61": "Australia", "62": "Indonesia", "63": "Philippines",
      "64": "New Zealand", "65": "Singapore", "66": "Thailand", "81": "Japan", "82": "South Korea", "84": "Vietnam",
      "86": "China", "90": "Turkey", "91": "India", "92": "Pakistan", "93": "Afghanistan", "94": "Sri Lanka",
      "95": "Myanmar", "98": "Iran", "212": "Morocco", "213": "Algeria", "216": "Tunisia", "218": "Libya",
      "234": "Nigeria", "254": "Kenya", "351": "Portugal", "353": "Ireland", "380": "Ukraine", "880": "Bangladesh",
      "961": "Lebanon", "962": "Jordan", "963": "Syria", "964": "Iraq", "965": "Kuwait", "966": "Saudi Arabia",
      "968": "Oman", "970": "Palestine", "971": "UAE", "972": "Israel", "973": "Bahrain", "974": "Qatar",
      "977": "Nepal", "992": "Tajikistan", "993": "Turkmenistan", "994": "Azerbaijan", "998": "Uzbekistan"}


def log(*a):
    print("[*]", *a, flush=True)


def jget(url, timeout=20, **kw):
    """-> (status, json|None). status: 'ok' | 'http NNN' | 'error: ...'"""
    try:
        r = S.get(url, timeout=timeout, **kw)
    except Exception as e:
        return f"error: {type(e).__name__}", None
    if r.status_code != 200:
        return f"http {r.status_code}", None
    try:
        return "ok", r.json()
    except ValueError:
        return "bad json", None


def norm_phone(p):
    raw = (p or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("00"):
        digits = digits[2:]
    if not (7 <= len(digits) <= 15):
        return None
    region = None
    for n in (3, 2, 1):
        if digits[:n] in CC:
            region = CC[digits[:n]]
            break
    if not (raw.startswith("+") or raw.startswith("00")):
        region = (region + " (guessed - add +country code to be sure)") if region else None
    return {"input": raw, "e164": "+" + digits, "digits": digits, "region": region}


# ------------------------------------------------------------------ sources
def src_xposedornot(email):
    st, j = jget(f"https://api.xposedornot.com/v1/breach-analytics?email={quote(email)}")
    res = {"source": "XposedOrNot", "url": f"https://xposedornot.com/", "status": "could not check", "detail": st, "breaches": []}
    if st != "ok" or not isinstance(j, dict):
        return res
    det = ((j.get("ExposedBreaches") or {}).get("breaches_details")) or []
    res["status"] = "found" if det else "not found"
    for b in det:
        res["breaches"].append({
            "name": b.get("breach"), "domain": b.get("domain"), "date": str(b.get("xposed_date") or ""),
            "data": [x.strip() for x in str(b.get("xposed_data") or "").split(";") if x.strip()],
            "records": b.get("xposed_records"), "password_risk": b.get("password_risk"),
            "industry": b.get("industry"), "about": (b.get("details") or "")[:400], "verified": b.get("verified")})
    pastes = (j.get("PastesSummary") or {}).get("cnt") or 0
    res["pastes"] = pastes
    risk = ((j.get("BreachMetrics") or {}).get("risk") or [{}])[0]
    if risk:
        res["risk"] = {"label": risk.get("risk_label"), "score": risk.get("risk_score")}
    return res


def src_leakcheck(q, what):
    st, j = jget(f"https://leakcheck.io/api/public?check={quote(q)}")
    res = {"source": f"LeakCheck (public, {what})", "url": "https://leakcheck.io/", "status": "could not check", "detail": st, "breaches": []}
    if st != "ok" or not isinstance(j, dict):
        return res
    if j.get("success") and j.get("found"):
        res["status"] = "found"
        res["count"] = j.get("found")
        res["fields"] = j.get("fields") or []
        for s in j.get("sources") or []:
            res["breaches"].append({"name": s.get("name"), "date": s.get("date") or "", "data": []})
    elif str(j.get("error", "")).lower().startswith("not found") or j.get("found") == 0:
        res["status"] = "not found"
    else:
        res["detail"] = str(j.get("error") or "unexpected reply")[:120]
    return res


def src_comb(email):
    """Count exact matches in COMB. The lines contain passwords: they are discarded immediately, never stored."""
    res = {"source": "COMB combo-list (ProxyNova, count only)", "url": "https://www.proxynova.com/tools/comb",
           "status": "could not check", "breaches": []}
    st, j = jget(f"https://api.proxynova.com/comb?query={quote(email)}&start=0&limit=100", timeout=30)
    res["detail"] = st
    if st != "ok" or not isinstance(j, dict):
        return res
    e = email.lower()
    n = sum(1 for l in (j.get("lines") or []) if str(l).lower().split(":", 1)[0].strip() == e)
    j = None  # drop leaked lines right away
    res["status"] = "found" if n else "not found"
    if n:
        res["matches"] = n
        res["breaches"].append({"name": "COMB (Compilation of Many Breaches, 2021)", "date": "2021-02",
                                "data": ["Email addresses", "Passwords"], "about": f"{n} email:password line(s) in the combo list"})
    return res


def src_hibp(email, key):
    res = {"source": "Have I Been Pwned (official API)", "url": f"https://haveibeenpwned.com/", "status": "could not check", "breaches": []}
    h = {"hibp-api-key": key, "user-agent": UA}
    try:
        r = S.get(f"https://haveibeenpwned.com/api/v3/breachedaccount/{quote(email)}?truncateResponse=false", headers=h, timeout=20)
    except Exception as e:
        res["detail"] = type(e).__name__
        return res
    if r.status_code == 404:
        res["status"] = "not found"
    elif r.status_code == 200:
        res["status"] = "found"
        for b in r.json():
            res["breaches"].append({"name": b.get("Title") or b.get("Name"), "domain": b.get("Domain"),
                                    "date": b.get("BreachDate"), "data": b.get("DataClasses") or [],
                                    "records": b.get("PwnCount"), "verified": b.get("IsVerified")})
    else:
        res["detail"] = f"http {r.status_code}"
        return res
    time.sleep(6.5)  # free-tier rate limit
    try:
        r = S.get(f"https://haveibeenpwned.com/api/v3/pasteaccount/{quote(email)}", headers=h, timeout=20)
        res["pastes"] = len(r.json()) if r.status_code == 200 else 0
    except Exception:
        pass
    return res


def hibp_catalogue():
    st, j = jget("https://haveibeenpwned.com/api/v3/breaches", timeout=30)
    cat = {}
    if st == "ok" and isinstance(j, list):
        for b in j:
            for k in {b.get("Name"), b.get("Title"), b.get("Domain")}:
                if k:
                    cat[re.sub(r"[^a-z0-9]", "", k.lower())] = b
    return cat


def src_gravatar(email):
    sha = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    st, j = jget(f"https://api.gravatar.com/v3/profiles/{sha}")
    if st == "ok":
        return {"source": "Gravatar", "status": "public profile", "url": f"https://gravatar.com/{sha}",
                "detail": "this email has a public Gravatar profile (visible to anyone who has the email)"}
    if st == "http 404":
        return {"source": "Gravatar", "status": "no public profile", "url": "https://gravatar.com/"}
    return {"source": "Gravatar", "status": "could not check", "detail": st, "url": "https://gravatar.com/"}


# ------------------------------------------------------------------ analysis
def severity(data):
    d = {x.lower() for x in data}
    if d & SENSITIVE:
        return "high"
    if d & PERSONAL:
        return "medium"
    return "low"


def key(n):
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\.(com|net|org|ru|io|co\.uk|so|to)$", "", (n or "").lower()))


def merge(results, cat):
    out = {}
    for r in results:
        for b in r.get("breaches") or []:
            k = key(b.get("name"))
            if not k:
                continue
            e = out.setdefault(k, {"name": b.get("name"), "date": "", "data": [], "sources": [], "domain": None,
                                   "records": None, "about": None})
            e["sources"].append(r["source"].split(" (")[0])
            for f in ("date", "domain", "records", "about"):
                if b.get(f) and not e.get(f):
                    e[f] = b[f]
            for d in b.get("data") or []:
                if d not in e["data"]:
                    e["data"].append(d)
    for k, e in out.items():
        h = cat.get(k) or cat.get(key(e.get("domain") or ""))
        if h:
            e["hibp_name"] = h.get("Name")
            e["date"] = e["date"] or h.get("BreachDate")
            e["records"] = e["records"] or h.get("PwnCount")
            e["domain"] = e["domain"] or h.get("Domain")
            for d in h.get("DataClasses") or []:
                if d not in e["data"]:
                    e["data"].append(d)
        e["severity"] = severity(e["data"]) if e["data"] else "unknown"
        e["sources"] = sorted(set(e["sources"]))
    order = {"high": 0, "medium": 1, "unknown": 2, "low": 3}
    res = sorted(out.values(), key=lambda e: str(e.get("date") or ""), reverse=True)
    return sorted(res, key=lambda e: order[e["severity"]])


def advice(D):
    b = D["breaches"]
    a = []
    if not b:
        a.append("Nothing listed by the free sources that answered. Still: use a unique password per site and keep 2-step verification on.")
    if any(e["severity"] == "high" for e in b):
        a.append("A breach exposed passwords or other secrets: change the password on those sites AND anywhere you reused it.")
    if b:
        a.append("Turn on 2-step verification (authenticator app or passkey) on your email first, then banking/social accounts.")
        a.append("Use a password manager so every site gets a unique password.")
    if any({"phone numbers", "phone"} & {x.lower() for x in e["data"]} for e in b):
        a.append("Your phone number leaked somewhere: expect smishing/scam calls; ask your carrier for a SIM-swap / port-out PIN.")
    if any({"physical addresses", "dates of birth", "address", "dob"} & {x.lower() for x in e["data"]} for e in b):
        a.append("Address / date of birth leaked: be wary of identity checks; consider a credit freeze or alert.")
    a.append("Get alerted about future breaches: subscribe free at haveibeenpwned.com/NotifyMe and Mozilla Monitor.")
    return a


def run(target, phone=None, username=None, level="thorough"):
    t0 = time.time()
    t = (target or "").strip()
    email = t if EMAIL_RE.match(t) else None
    if not email and not phone and norm_phone(t) and re.fullmatch(r"[+0-9()\s.\-]{7,22}", t):
        phone = t
    elif not email and not username and USER_RE.match(t):
        username = t.lstrip("@")
    P = norm_phone(phone) if phone else None
    D = {"query": t, "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "mode": level,
         "email": email, "phone": P, "username": username, "checks": [], "breaches": [], "exposure": [],
         "notes": [], "links": {}}
    jobs = []
    key_ = os.environ.get("HIBP_API_KEY", "").strip()
    with ThreadPoolExecutor(6) as ex:
        if email:
            variants = list(dict.fromkeys([email, email.lower()]))
            log(f"checking email {email} (variants: {', '.join(variants)})")
            jobs.append(ex.submit(src_xposedornot, email.lower()))
            for v in variants:
                jobs.append(ex.submit(src_leakcheck, v, "email" + ("" if v == email.lower() else ", as typed")))
            if level != "fast":
                jobs.append(ex.submit(src_comb, email.lower()))
            if key_:
                jobs.append(ex.submit(src_hibp, email.lower(), key_))
            g = ex.submit(src_gravatar, email)
        if P:
            log(f"checking phone {P['e164']} ({P.get('region') or 'unknown region'})")
            jobs.append(ex.submit(src_leakcheck, P["digits"], "phone"))
            if P["digits"] != P["e164"]:
                jobs.append(ex.submit(src_leakcheck, P["e164"], "phone, +format"))
        if username:
            log(f"checking username {username}")
            jobs.append(ex.submit(src_leakcheck, username, "username"))
        cat_f = ex.submit(hibp_catalogue)
        results = [j.result() for j in jobs]
        cat = cat_f.result()
        if email:
            D["exposure"].append(g.result())
    for r in results:
        log(f"{r['source']}: {r['status']}" + (f" ({len(r['breaches'])} breach(es))" if r.get("breaches") else "")
            + (f" [{r['detail']}]" if r.get("detail") and r["status"] == "could not check" else ""))
        D["checks"].append({k: v for k, v in r.items() if k != "breaches"} | {"breach_count": len(r.get("breaches") or [])})
    D["breaches"] = merge(results, cat)
    D["hibp_catalogue"] = bool(cat)
    st = [c["status"] for c in D["checks"]]
    D["verdict"] = ("exposed" if D["breaches"] else "not found in free sources" if "not found" in st
                    else "inconclusive (no source answered)")
    D["advice"] = advice(D)
    L = D["links"]
    if email:
        e = quote(email)
        L["Have I Been Pwned (email)"] = f"https://haveibeenpwned.com/account/{e}"
        L["Have I Been Pwned (home - paste the email)"] = "https://haveibeenpwned.com/"
        L["Mozilla Monitor (free, sign-in)"] = "https://monitor.mozilla.org/"
        L["XposedOrNot"] = f"https://xposedornot.com/xposed#{e}"
        L["Avast Hack Check"] = "https://www.avast.com/hackcheck"
        L["Google: email in pastes"] = f"https://www.google.com/search?q=%22{e}%22+site%3Apastebin.com+OR+site%3Aghostbin.co+OR+site%3Arentry.co"
        L["Google: exact email anywhere"] = f"https://www.google.com/search?q=%22{e}%22"
        L["Bing: exact email"] = f"https://www.bing.com/search?q=%22{e}%22"
        L["Google account security checkup"] = "https://myaccount.google.com/security-checkup" if email.lower().endswith(("@gmail.com", "@googlemail.com")) else None
        L["Google Password Checkup"] = "https://passwords.google.com/checkup" if email.lower().endswith(("@gmail.com", "@googlemail.com")) else None
    if P:
        L["Have I Been Pwned (phone - supports some breaches)"] = f"https://haveibeenpwned.com/account/{quote(P['e164'])}"
        L["Google: exact number"] = f"https://www.google.com/search?q=%22{quote(P['e164'])}%22+OR+%22{P['digits']}%22"
        L["Bing: exact number"] = f"https://www.bing.com/search?q=%22{quote(P['e164'])}%22"
    if username:
        L["Google: username in pastes"] = f"https://www.google.com/search?q=%22{quote(username)}%22+site%3Apastebin.com"
    D["links"] = {k: v for k, v in L.items() if v}
    if not key_ and email:
        D["notes"].append("Have I Been Pwned's per-email search needs a paid API key (from ~US$4/month). Set HIBP_API_KEY to add it; "
                          "otherwise open the HIBP link above - it's the most complete free check in a browser.")
    D["notes"].append("Free sources only. 'Not found' means none of the answering sources list it; it is not a guarantee. "
                      "Infostealer logs and private dumps are not covered by any free source.")
    D["notes"].append("Only breach metadata is kept. No leaked passwords or records are downloaded or stored (COMB is count-only).")
    if P:
        D["notes"].append("Free phone breach coverage is thin (mostly LeakCheck's public index). A 'not found' phone is not a clean bill.")
    D["runtime_s"] = round(time.time() - t0, 1)
    return D


def esc(s, n=200):
    s = re.sub(r"\s+", " ", str(s if s is not None else "")).strip().replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")
    return s[:n] + ("…" if len(s) > n else "")


def to_markdown(D):
    who = " · ".join(x for x in [D.get("email"), (D.get("phone") or {}).get("e164"), D.get("username") and "@" + D["username"]] if x)
    md = [f"# ECHO leak check - {esc(who or D['query'], 90)}", "",
          f"Generated {D['generated']} · runtime {D['runtime_s']}s · mode {D['mode']}", "",
          f"**Verdict: {D['verdict']}** — {len(D['breaches'])} breach(es) listed", ""]
    if D.get("phone"):
        p = D["phone"]
        md += [f"Phone: `{esc(p['e164'])}` — region: {esc(p.get('region') or 'unknown')}", ""]
    md += ["## Breaches", ""]
    if D["breaches"]:
        md += ["| severity | breach | date | data exposed | records | seen by |", "|---|---|---|---|---|---|"]
        for b in D["breaches"]:
            md.append(f"| {b['severity']} | {esc(b['name'], 60)}" + (f" ({esc(b['domain'], 40)})" if b.get("domain") else "")
                      + f" | {esc(b.get('date') or '?', 12)} | {esc(', '.join(b['data']) or 'not listed', 180)} | "
                      f"{('{:,}'.format(b['records'])) if isinstance(b.get('records'), int) else ''} | {esc(', '.join(b['sources']), 60)} |")
    else:
        md.append("_No breach listed by the free sources that answered._")
    md += ["", "## Sources checked", "", "| source | result | note |", "|---|---|---|"]
    for c in D["checks"]:
        note = []
        if c.get("detail") and c["status"] == "could not check":
            note.append(str(c["detail"]))
        if c.get("count"):
            note.append(f"{c['count']} record(s)")
        if c.get("matches"):
            note.append(f"{c['matches']} line(s), count only")
        if c.get("pastes"):
            note.append(f"{c['pastes']} paste(s)")
        if c.get("risk") and c["risk"].get("label"):
            note.append(f"risk {c['risk']['label']}")
        md.append(f"| [{esc(c['source'], 60)}]({c['url']}) | {c['status']} | {esc('; '.join(note), 120)} |")
    for x in D["exposure"]:
        md.append(f"| [{x['source']}]({x['url']}) | {x['status']} | {esc(x.get('detail') or '', 120)} |")
    md += ["", "## What to do", ""] + [f"- {esc(a, 300)}" for a in D["advice"]]
    md += ["", "## Check it yourself", ""] + [f"- [{k}]({v})" for k, v in D["links"].items()]
    md += ["", "## Notes", ""] + [f"- {esc(n, 300)}" for n in D["notes"]]
    return "\n".join(md) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Free breach / leak check for an email, phone or username (metadata only).")
    ap.add_argument("target")
    ap.add_argument("--phone")
    ap.add_argument("--username")
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--deep", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    level = "deep" if a.deep else ("fast" if a.fast else "thorough")
    D = run(a.target, a.phone, a.username, level)
    os.makedirs(a.out, exist_ok=True)
    base = os.path.join(a.out, re.sub(r"[^A-Za-z0-9_]", "_", a.target.strip().lstrip("@")))
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(D, f, indent=2, ensure_ascii=False, default=str)
    with open(base + "-report.md", "w", encoding="utf-8") as f:
        f.write(to_markdown(D))
    print(f"\n[*] leak check done in {D['runtime_s']}s: verdict {D['verdict']}, {len(D['breaches'])} breach(es) -> {base}-report.md", flush=True)


if __name__ == "__main__":
    main()
