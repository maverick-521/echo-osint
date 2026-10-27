#!/usr/bin/env bash
# One-time: push ECHO to a PRIVATE GitHub repo for Render/Railway. Needs `gh auth login` first.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="${1:-echo-osint}"
[ -d .git ] || git init -q -b main
git add -A && git -c user.name=ECHO -c user.email=echo@localhost commit -qm "ECHO cloud package" || true
gh repo view "$REPO" >/dev/null 2>&1 || gh repo create "$REPO" --private --source . --remote origin
git push -u origin main
echo "Now: https://dashboard.render.com/blueprints → New Blueprint → $REPO → set ECHO_PIN"
