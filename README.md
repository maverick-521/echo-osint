# ECHO

**Evidence Continuum of Handle Origins**

Old usernames leave echoes. Type an X/Twitter **@username** (or numeric id), pick **Fast / Thorough / Deep**, hit **Run scan**, get the dossier.

Local only: `http://127.0.0.1:8765`. No API key.

## Start
Need **Python 3.9+**.

| OS | Start |
|---|---|
| **macOS** | Double-click `run.command` (or right-click → Open) |
| **Linux** | `./run.sh` |
| **Windows** | Double-click `run.bat` |

Env: `ECHO_PORT=9000`, `ECHO_NO_BROWSER=1`.

## Modes
Fast ~3 min · Thorough ~8–12 · Deep ~15–25 (rename recovery).

Reports in `reports/`. Public sources only; binds localhost only.

## Leak check (new)
Pick the **Leak check** tab, enter an email (and/or a phone with +country code, or a username) → **Check for leaks**.
Free sources: XposedOrNot, LeakCheck public, COMB (count only), Gravatar exposure; HIBP public catalogue enriches details.
Set `HIBP_API_KEY` to add the official Have I Been Pwned per-email lookup. Metadata only — no leaked passwords are stored.
CLI: `python engine/breach.py you@example.com [--phone +447700900123]`.

## Phone (iPhone) access
Default bind is `127.0.0.1` only. Start with `ECHO_BIND=lan` (Windows: `ECHO-phone.vbs`) to listen on your Wi-Fi.
Only private-network clients are answered, and each phone must pair once via the **Open on phone** link (it carries a
private key, stored in `.echo_access_key`). In Safari: Share → Add to Home Screen.

## From anywhere / app versions
See **DEPLOY.md**: PIN-gated cloud deploy (Render/Railway/Docker), Cloudflare Tunnel from the PC, iPhone Home Screen install, and the Windows desktop window (`ECHO-app.vbs`).
