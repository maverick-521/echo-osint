#!/usr/bin/env bash
# Waits for `gh auth login` to finish, then creates PRIVATE maverick-521/echo-osint and pushes.
set -uo pipefail
LOG=/workspace/echo-push.log
for i in $(seq 1 200); do gh auth status >/dev/null 2>&1 && break; sleep 6; done
gh auth status >/dev/null 2>&1 || { echo "gh login never completed (code expired)" >>$LOG; exit 1; }
gh auth setup-git >>$LOG 2>&1
cd /workspace/ECHO
ME=$(gh api user -q .login); echo "gh user: $ME" >>$LOG
gh repo view "$ME/echo-osint" >/dev/null 2>&1 || gh repo create echo-osint --private --description "ECHO (private)" >>$LOG 2>&1
git remote remove origin 2>/dev/null; git remote add origin "https://github.com/$ME/echo-osint.git"
git push -u origin main >>$LOG 2>&1 && echo "PUSH_OK $(date)" >>$LOG || echo "PUSH_FAILED" >>$LOG
gh repo view echo-osint --json url,visibility >>$LOG 2>&1
