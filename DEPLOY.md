# ECHO from anywhere (iPhone on cellular, any Wi-Fi)

ECHO is an OSINT tool, so every remote option is **PIN-gated**: the only pages reachable without the PIN are the
unlock screen and the app icon. Wrong PINs are rate-limited (8 per IP / 60 total per 15 min). Sessions last 180 days.

## Option A - always-on cloud (PC can be off)  ← recommended
Files: `Dockerfile`, `render.yaml`, `railway.json`, `Procfile`. One worker, many threads (jobs live in memory).

**Render (free plan)**
1. Put this folder in a **private** GitHub repo (`deploy/push-to-github.sh` does it once GitHub is connected).
2. render.com → sign in with GitHub → **New + → Blueprint** → pick the repo → it reads `render.yaml`.
3. When asked, set **ECHO_PIN** (6+ characters; digits are easiest on a phone). Optional: `HIBP_API_KEY`.
4. Deploy → you get `https://echo-xxxx.onrender.com`. Free plan sleeps after ~15 min idle (first open takes ~1 min)
   and forgets old reports on restart; the paid Starter plan ($7/mo) keeps it awake.

**Railway**: New Project → Deploy from GitHub repo → Variables: `ECHO_BIND=cloud`, `ECHO_PIN=…` → Settings → Generate Domain.
**Any Docker host / Fly.io**: `docker build -t echo . && docker run -e ECHO_PIN=… -p 8080:8080 echo`.

Note: cloud scans come from datacenter IPs, so a few sources (Instagram/TikTok logged-out checks, some archives)
rate-limit sooner than from home.

## Option B - from the home PC (Cloudflare Tunnel, PC must be awake)
`powershell -ExecutionPolicy Bypass -File deploy\echo-remote.ps1`
- Quick tunnel: free, no account, `https://<random>.trycloudflare.com`; **the link changes every time it restarts**.
- Fixed link: Cloudflare account + a domain → Zero Trust → Tunnels → create → copy token, add a public hostname
  → `http://127.0.0.1:8765`; then set `ECHO_CF_TOKEN` and `ECHO_PUBLIC_URL` and run the same script.
The PC's own ECHO window skips the PIN; everything coming through the tunnel needs it.

## The PIN
Cloud: the `ECHO_PIN` variable in the host's settings. PC/tunnel: `echo.config.json` next to the app (created on first
remote start; edit `"pin"` and restart to change it). Never commit `echo.config.json`.

## iPhone install
Open the https link in **Safari** → enter PIN → **Share → Add to Home Screen** → "ECHO". It opens full-screen with
its own icon, like an app. (The Home Screen app keeps its own login, so enter the PIN once more the first time.)

## Desktop app (Windows)
`ECHO-app.vbs` opens ECHO in its own window (Edge WebView2 via `pywebview`) instead of a browser tab.
