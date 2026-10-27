#!/usr/bin/env python3
"""
ECHO — Evidence Continuum of Handle Origins. Local web UI around the OSINT engine (engine/hunt.py).

Binds to 127.0.0.1 only. Each run is a subprocess of engine/hunt.py, so the engine is
used exactly as on the command line (same --fast / --deep / thorough behaviour).
"""
import ipaddress
import json
import os
import secrets
import socket
import re
import subprocess
import sys
import threading
import time
import uuid

import markdown
import nh3
from flask import Flask, abort, jsonify, request, send_file, send_from_directory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "engine", "hunt.py")
PIVOT = os.path.join(ROOT, "engine", "pivot.py")
BREACH = os.path.join(ROOT, "engine", "breach.py")
REPORTS = os.path.join(ROOT, "reports")
STATIC = os.path.join(ROOT, "app", "static")
PORT = int(os.environ.get("ECHO_PORT") or (os.environ.get("PORT") if (os.environ.get("ECHO_BIND") or "").lower() in ("cloud", "public", "server") else None)
           or os.environ.get("ALIAS_PORT") or os.environ.get("XTH_PORT") or "8765")
# Default: loopback only. ECHO_BIND=lan opts in to phone access on the same Wi-Fi: listens on all interfaces but
# only answers private-network clients, and any non-loopback client must present a per-install access key
# (opened once via the "Open on phone" link, then kept in a cookie).
BIND = (os.environ.get("ECHO_BIND") or "local").strip().lower()
LAN = BIND in ("lan", "phone", "wifi")
# ECHO_BIND=cloud  -> hosted (Render/Railway/Docker): listens on 0.0.0.0:$PORT, every request needs the PIN.
# ECHO_BIND=tunnel -> stays on 127.0.0.1 behind Cloudflare Tunnel; proxied (remote) requests need the PIN,
#                     the PC's own app window does not.
CLOUD = BIND in ("cloud", "public", "server")
TUNNEL = BIND in ("tunnel", "cloudflare", "remote")
GATED = CLOUD or TUNNEL
HOST = "0.0.0.0" if (LAN or CLOUD) else "127.0.0.1"
TOKEN_FILE = os.path.join(ROOT, ".echo_access_key")


def access_key():
    try:
        with open(TOKEN_FILE) as f:
            k = f.read().strip()
            if len(k) >= 16:
                return k
    except OSError:
        pass
    k = secrets.token_urlsafe(18)
    with open(TOKEN_FILE, "w") as f:
        f.write(k)
    return k


KEY = access_key() if LAN else None

CONFIG_FILE = os.environ.get("ECHO_CONFIG") or os.path.join(ROOT, "echo.config.json")


def load_config():
    """PIN + session secret for the cloud/tunnel gate. ECHO_PIN / ECHO_SECRET env vars win; otherwise they live in
    echo.config.json next to the app (created on first gated start, never served)."""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    changed = False
    if not cfg.get("secret"):
        cfg["secret"] = secrets.token_hex(32); changed = True
    if not cfg.get("pin") and not os.environ.get("ECHO_PIN"):
        cfg["pin"] = "".join(secrets.choice("0123456789") for _ in range(8)); changed = True
    if changed:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            os.chmod(CONFIG_FILE, 0o600)
        except OSError:
            pass
    pin = (os.environ.get("ECHO_PIN") or cfg.get("pin") or "").strip()
    return pin, (os.environ.get("ECHO_SECRET") or cfg["secret"])


PIN, SECRET = load_config() if GATED else (None, None)
if GATED and len(PIN) < 6:
    sys.exit("ECHO: refusing to start publicly - ECHO_PIN must be at least 6 characters.")
PUBLIC_URL = (os.environ.get("ECHO_PUBLIC_URL") or "").strip().rstrip("/") or None
PUBLIC_URL_FILE = os.path.join(ROOT, ".echo_public_url")


def public_url():
    if PUBLIC_URL:
        return PUBLIC_URL
    try:
        with open(PUBLIC_URL_FILE) as f:
            u = f.read().strip().rstrip("/")
            return u if u.startswith("https://") else None
    except OSError:
        return None


FAILS = {}          # ip -> [timestamps] of wrong PINs
FAIL_LOCK = threading.Lock()


def lan_ip():
    """Best guess at this PC's Wi-Fi/Ethernet address. Prefers home-router ranges (192.168/172.16)
    over 10.x, because VPN tunnels (NordLynx, etc.) usually sit on 10.x. Override with ECHO_LAN_IP."""
    forced = (os.environ.get("ECHO_LAN_IP") or "").strip()
    if forced:
        return forced
    cands = []
    for probe in ("192.168.255.255", "172.31.255.255", "10.255.255.255"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((probe, 1))  # no packet is sent; just picks the outbound interface
            cands.append(s.getsockname()[0])
            s.close()
        except OSError:
            pass
    try:
        cands += [a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    def rank(ip):
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return 9
        if a.is_loopback or a.is_link_local or not a.is_private:
            return 9
        if ip.startswith("192.168."):
            return 0
        if a in ipaddress.ip_network("172.16.0.0/12"):
            return 1
        return 2
    good = sorted({c for c in cands if rank(c) < 9}, key=rank)
    return good[0] if good else None


MAX_CONCURRENT = 2

TARGET_RE = re.compile(r"^(?:@?[A-Za-z0-9_]{1,15}|\d{1,20})$")
X_URL_RE = re.compile(r"^(?:https?://)?(?:www\.|mobile\.)?(?:twitter|x)\.com/(?:i/user/(\d{1,20})|@?([A-Za-z0-9_]{1,15}))(?:[/?#].*)?$", re.I)
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,24}$")
PIVOT_RE = re.compile(r"^(?:[a-z]{1,10}:@?[A-Za-z0-9._\-]{1,60}|@?[A-Za-z0-9._\-]{1,40}|https?://[^\s<>\"']{4,300})$", re.I)
MODES = {"fast": ["--fast"], "thorough": [], "deep": ["--deep"]}
KINDS = ("auto", "x", "pivot", "breach")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 ().\-]{5,20}$")


def classify(target, kind):
    """-> (kind, cleaned target) or (None, error). X history stays the default for anything that is a valid X handle."""
    t = (target or "").strip()
    if kind == "breach":
        if PHONE_RE.match(t) and 7 <= len(re.sub(r"\D", "", t)) <= 15:
            return "breach", re.sub(r"[^0-9+]", "", t)
        if EMAIL_RE.match(t) or re.match(r"^@?[A-Za-z0-9._\-]{3,40}$", t):
            return "breach", t.lstrip("@")
        return None, "Leak check needs an email, a phone number (+44 7700 900123) or a username."
    if not t or len(t) > 320 or any(c.isspace() for c in t):
        return None, "Enter one handle, user id, email or profile link (no spaces)."
    m = X_URL_RE.match(t)
    if m and kind in ("auto", "x"):
        return "x", m.group(1) or m.group(2)
    if re.match(r"^(?:x|twitter|tw):@?([A-Za-z0-9_]{1,15})$", t, re.I) and kind in ("auto", "x"):
        return "x", t.split(":", 1)[1].lstrip("@")
    if kind == "x":
        if TARGET_RE.match(t):
            return "x", t.lstrip("@")
        return None, "X history needs an X handle (letters, digits, _; max 15), a numeric user id or an x.com link."
    if kind == "auto" and TARGET_RE.match(t):
        return "x", t.lstrip("@")
    if EMAIL_RE.match(t) or PIVOT_RE.match(t):
        return "pivot", t
    return None, "Not recognised. Try @handle, 12345 (X id), name@example.com, ig:username, gh:username or a profile URL."

app = Flask(__name__, static_folder=None)
if GATED:
    from datetime import timedelta
    app.secret_key = SECRET
    app.config.update(SESSION_COOKIE_NAME="echo_s", SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=timedelta(days=180),
                      SESSION_COOKIE_SECURE=(os.environ.get("ECHO_INSECURE_COOKIE") != "1"))
    if CLOUD:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
JOBS = {}
LOCK = threading.Lock()


def safe_base(q):
    return re.sub(r"[^A-Za-z0-9_]", "_", q.strip().lstrip("@"))


def render_md(text):
    html = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])
    # report content comes from untrusted public data (bios etc.) - sanitize before display
    return nh3.clean(html, link_rel="noopener noreferrer nofollow",
                     tags={"h1", "h2", "h3", "h4", "h5", "p", "br", "hr", "ul", "ol", "li", "strong", "em",
                           "code", "pre", "blockquote", "table", "thead", "tbody", "tr", "th", "td", "a", "img",
                           "details", "summary"},
                     attributes={"a": {"href", "title"}, "img": {"src", "alt", "title"},
                                 "th": {"align"}, "td": {"align"}},
                     url_schemes={"http", "https"})


def run_job(job):
    script = {"pivot": PIVOT, "breach": BREACH}.get(job.get("kind"), ENGINE)
    cmd = [sys.executable, "-u", script, job["target"], *MODES[job["mode"]], "--out", job["dir"]]
    if job.get("kind") == "breach" and job.get("phone"):
        cmd += ["--phone", job["phone"]]
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")  # UTF-8 files on Windows too
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=os.path.dirname(ENGINE),
                             env=env, text=True, encoding="utf-8", errors="replace", bufsize=1)
        job["proc"] = p
        with open(os.path.join(job["dir"], "run.log"), "w", encoding="utf-8") as lf:
            for line in p.stdout:
                line = line.rstrip("\n")
                job["log"].append(line)
                lf.write(line + "\n")
                lf.flush()
        p.wait()
        job["rc"] = p.returncode
    except Exception as e:  # pragma: no cover
        job["log"].append(f"[!] failed to start engine: {e}")
        job["rc"] = -1
    base = os.path.join(job["dir"], safe_base(job["target"]))
    if job.get("cancelled"):
        job["status"] = "cancelled"
    elif os.path.exists(base + "-report.md"):
        job["status"] = "done"
    else:
        job["status"] = "error"
    job["ended"] = time.time()
    meta = {k: job.get(k) for k in ("id", "target", "mode", "kind", "status", "started", "ended", "rc", "phone")}
    with open(os.path.join(job["dir"], "job.json"), "w") as f:
        json.dump(meta, f)


def load_job(jid):
    if not re.fullmatch(r"[0-9a-f\-]{8,40}", jid or ""):
        abort(404)
    if jid in JOBS:
        return JOBS[jid]
    d = os.path.join(REPORTS, jid)
    try:
        with open(os.path.join(d, "job.json")) as f:
            meta = json.load(f)
    except OSError:
        abort(404)
    meta["dir"] = d
    try:
        with open(os.path.join(d, "run.log"), encoding="utf-8") as f:
            meta["log"] = f.read().splitlines()
    except OSError:
        meta["log"] = []
    return meta


def files(job):
    base = os.path.join(job["dir"], safe_base(job["target"]))
    return base + "-report.md", base + ".json", base + "-report.html"


@app.after_request
def headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "no-referrer"
    r.headers["Content-Security-Policy"] = ("default-src 'self'; img-src * data:; style-src 'self' 'unsafe-inline'; "
                                            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
    return r


def _private(ip):
    try:
        a = ipaddress.ip_address((ip or "").split("%")[0])
        return a.is_loopback or a.is_private or a.is_link_local
    except ValueError:
        return False


def _loopback(ip):
    try:
        return ipaddress.ip_address(ip or "").is_loopback
    except ValueError:
        return False


PUBLIC_PATHS = ("/manifest.webmanifest", "/static/icon-192.png", "/static/icon-512.png", "/static/apple-touch-icon.png",
                "/login", "/healthz")


def client_ip():
    return (request.headers.get("CF-Connecting-IP") or request.remote_addr or "?").strip()


def proxied():
    return bool(request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For")
                or request.headers.get("Cf-Ray"))


def login_page(msg="", code=200):
    from html import escape
    body = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            "<meta name=theme-color content='#0e0f12'><meta name=apple-mobile-web-app-capable content=yes>"
            "<meta name=mobile-web-app-capable content=yes>"
            "<meta name=apple-mobile-web-app-status-bar-style content=black-translucent>"
            "<meta name=apple-mobile-web-app-title content=ECHO><link rel=manifest href='/manifest.webmanifest'>"
            "<link rel=apple-touch-icon href='/static/apple-touch-icon.png'><title>ECHO</title><style>"
            "html,body{height:100%;margin:0}body{display:grid;place-items:center;background:radial-gradient(1200px 700px at 30% 10%,#1d2128,#0e0f12 60%);"
            "color:#e9eaee;font:16px -apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',sans-serif}"
            ".card{width:min(340px,86vw);padding:34px 28px;border-radius:26px;background:rgba(255,255,255,.06);"
            "border:1px solid rgba(255,255,255,.12);backdrop-filter:blur(24px) saturate(140%);-webkit-backdrop-filter:blur(24px) saturate(140%);"
            "box-shadow:0 30px 80px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.08);text-align:center}"
            ".rings{position:relative;width:64px;height:64px;margin:0 auto 18px}.rings i{position:absolute;inset:0;border-radius:50%;"
            "border:1px solid rgba(255,255,255,.35);animation:r 3.2s ease-out infinite;opacity:0}.rings i:nth-child(2){animation-delay:1.06s}"
            ".rings i:nth-child(3){animation-delay:2.13s}.rings b{position:absolute;inset:26px;border-radius:50%;background:#e9eaee}"
            "@keyframes r{0%{transform:scale(.3);opacity:.9}100%{transform:scale(1.25);opacity:0}}"
            "h1{margin:0;font-weight:600;letter-spacing:.32em;font-size:22px;padding-left:.32em}p{color:#9a9ea8;font-size:13px;margin:8px 0 22px}"
            "input{width:100%;box-sizing:border-box;padding:14px 16px;border-radius:14px;border:1px solid rgba(255,255,255,.14);"
            "background:rgba(0,0,0,.25);color:#fff;font-size:20px;letter-spacing:.3em;text-align:center;outline:none}"
            "input:focus{border-color:rgba(255,255,255,.35)}button{margin-top:14px;width:100%;padding:13px;border:0;border-radius:14px;"
            "background:#e9eaee;color:#0e0f12;font-weight:600;font-size:15px}.err{color:#ff8a80;min-height:18px;font-size:13px;margin:12px 0 0}"
            "</style></head><body><form class=card method=post action='/login' autocomplete=off>"
            "<div class=rings aria-hidden=true><i></i><i></i><i></i><b></b></div><h1>ECHO</h1><p>Private. Enter your PIN.</p>"
            "<input name=pin type=password inputmode=numeric autocomplete=current-password autofocus required maxlength=64>"
            "<button type=submit>Unlock</button><div class=err>" + escape(msg) + "</div></form></body></html>")
    return body, code, {"Cache-Control": "no-store"}


def too_many(ip):
    now = time.time()
    with FAIL_LOCK:
        for k in list(FAILS):
            FAILS[k] = [t for t in FAILS[k] if now - t < 900]
            if not FAILS[k]:
                del FAILS[k]
        total = sum(len(v) for v in FAILS.values())
        return len(FAILS.get(ip, [])) >= 8 or total >= 60


@app.route("/login", methods=["GET", "POST"])
def login():
    from flask import redirect, session
    if not GATED:
        return redirect("/")
    if request.method == "GET":
        return login_page()
    ip = client_ip()
    if too_many(ip):
        return login_page("Too many tries. Wait 15 minutes.", 429)
    pin = (request.form.get("pin") or "").strip()
    if pin and secrets.compare_digest(pin.encode(), PIN.encode()):
        session.clear()
        session.permanent = True
        session["ok"] = True
        r = redirect("/")
        return r
    with FAIL_LOCK:
        FAILS.setdefault(ip, []).append(time.time())
    time.sleep(0.8)
    return login_page("Wrong PIN.", 401)


@app.post("/logout")
def logout():
    from flask import session
    session.clear()
    return jsonify(ok=True)


@app.get("/healthz")
def healthz():
    return "ok"


def gate():
    """Cloud / tunnel mode: everything except the icon + login page needs a PIN session."""
    from flask import redirect, session
    if TUNNEL and not proxied() and _loopback(request.remote_addr) and \
            (request.host or "").rsplit(":", 1)[0].strip("[]") in ("127.0.0.1", "localhost"):
        return None  # the PC's own window
    if request.path in PUBLIC_PATHS:
        return None
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != request.host:
            abort(403)
    if session.get("ok"):
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="Locked - open ECHO and enter your PIN."), 401
    return redirect("/login")


@app.before_request
def local_only():
    if GATED:
        return gate()
    # defence in depth against DNS rebinding: only accept localhost / private-IP Host headers
    host = (request.host or "").rsplit(":", 1)[0].strip("[]")
    if host not in ("127.0.0.1", "localhost") and not (LAN and _private(host)):
        abort(403)
    if not LAN:
        return None
    ra = request.remote_addr
    if _loopback(ra):
        return None
    if not _private(ra):
        abort(403)
    if request.args.get("k") and secrets.compare_digest(request.args.get("k"), KEY):
        if request.method == "GET" and request.path == "/":
            from flask import redirect
            r = redirect("/")
            r.set_cookie("echo_k", KEY, max_age=60 * 60 * 24 * 180, httponly=True, samesite="Strict")
            return r
        return None
    if secrets.compare_digest(request.cookies.get("echo_k", ""), KEY):
        return None
    return ("<!doctype html><meta name=viewport content='width=device-width'><body style='font:16px -apple-system,sans-serif;"
            "background:#111316;color:#ddd;padding:40px'><h2>ECHO</h2><p>This device isn't paired yet. On the PC, open ECHO and use "
            "<b>Open on phone</b> (scan the link / type it exactly, including <code>?k=…</code>).</p>"), 403


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/static/<path:p>")
def static_files(p):
    return send_from_directory(STATIC, p)


@app.post("/api/run")
def api_run():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode") or "fast"
    kind = data.get("kind") or "auto"
    if kind not in KINDS:
        return jsonify(error="kind must be auto, x, pivot or breach"), 400
    kind, target = classify(data.get("target"), kind)
    if not kind:
        return jsonify(error=target), 400
    if mode not in MODES:
        return jsonify(error="mode must be fast, thorough or deep"), 400
    with LOCK:
        running = sum(j["status"] == "running" for j in JOBS.values())
        if running >= MAX_CONCURRENT:
            return jsonify(error=f"{running} hunts already running - wait for one to finish (keeps Wayback from rate-limiting you)."), 429
        jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        d = os.path.join(REPORTS, jid)
        os.makedirs(d, exist_ok=True)
        phone = None
        if kind == "breach":
            ph = (data.get("phone") or "").strip()
            if ph:
                if not (PHONE_RE.match(ph) and 7 <= len(re.sub(r"\D", "", ph)) <= 15):
                    return jsonify(error="Phone should look like +44 7700 900123."), 400
                phone = re.sub(r"[^0-9+]", "", ph)
        job = dict(id=jid, target=target.lstrip("@") if kind == "x" else target, mode=mode, kind=kind, status="running", started=time.time(),
                   ended=None, rc=None, dir=d, log=[], phone=phone)
        JOBS[jid] = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return jsonify(id=jid)


@app.get("/api/jobs/<jid>")
def api_job(jid):
    job = load_job(jid)
    since = max(0, request.args.get("since", 0, type=int))
    lines = job["log"][since:]
    return jsonify(id=job["id"], target=job["target"], mode=job["mode"], kind=job.get("kind") or "x", phone=job.get("phone"), status=job["status"],
                   started=job["started"], ended=job.get("ended"), log=lines, next=since + len(lines))


@app.post("/api/jobs/<jid>/cancel")
def api_cancel(jid):
    job = load_job(jid)
    p = job.get("proc")
    if job["status"] == "running" and p:
        job["cancelled"] = True
        p.terminate()
    return jsonify(ok=True)


@app.get("/api/jobs/<jid>/report")
def api_report(jid):
    job = load_job(jid)
    md, js, htmlp = files(job)
    if not os.path.exists(md):
        abort(404)
    with open(md, encoding="utf-8") as f:
        text = f.read()
    return jsonify(html=render_md(text), has_html=os.path.exists(htmlp))


@app.get("/api/jobs/<jid>/download/<kind>")
def api_download(jid, kind):
    job = load_job(jid)
    md, js, htmlp = files(job)
    path = {"md": md, "json": js, "html": htmlp}.get(kind)
    if not path or not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


@app.get("/api/history")
def api_history():
    out = []
    for name in sorted(os.listdir(REPORTS), reverse=True):
        jf = os.path.join(REPORTS, name, "job.json")
        if os.path.isfile(jf):
            try:
                with open(jf) as f:
                    out.append(json.load(f))
            except (OSError, ValueError):
                pass
    for j in JOBS.values():
        if j["status"] == "running":
            out.insert(0, {k: j.get(k) for k in ("id", "target", "mode", "kind", "status", "started")})
    return jsonify(out[:50])


@app.get("/api/info")
def api_info():
    loop = _loopback(request.remote_addr)
    ip = lan_ip() if LAN else None
    pub = public_url() if GATED else None
    return jsonify(bind="lan" if LAN else ("cloud" if CLOUD else ("tunnel" if TUNNEL else "local")), lan_ip=ip if loop else None,
                   port=PORT, public_url=pub,
                   phone_url=(f"http://{ip}:{PORT}/?k={KEY}" if (LAN and ip and loop) else (pub if (TUNNEL and loop and not proxied()) else None)),
                   hibp_key=bool(os.environ.get("HIBP_API_KEY")))


@app.get("/manifest.webmanifest")
def manifest():
    r = send_from_directory(STATIC, "manifest.webmanifest")
    r.headers["Content-Type"] = "application/manifest+json"
    return r


@app.post("/api/detect")
def api_detect():
    data = request.get_json(silent=True) or {}
    kind, val = classify(data.get("target"), data.get("kind") or "auto")
    return jsonify(kind=kind, target=val if kind else None, error=None if kind else val)


def main():
    os.makedirs(REPORTS, exist_ok=True)
    url = f"http://127.0.0.1:{PORT}/"
    mode = ("phone mode: same Wi-Fi, paired devices" if LAN else "cloud: PIN-gated on 0.0.0.0" if CLOUD
            else "tunnel: PIN-gated behind Cloudflare" if TUNNEL else "local only")
    print(f"\n  ECHO online at {url}\n  ({mode} — Ctrl+C to shut it down)\n", flush=True)
    if LAN:
        print(f"  Phone mode ON (ECHO_BIND=lan): same Wi-Fi only, paired devices only.\n"
              f"  Phone link: http://{lan_ip()}:{PORT}/?k={KEY}\n", flush=True)
    # Browser is opened by the Desktop shortcut only (never here).
    app.run(host=HOST, port=PORT, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
