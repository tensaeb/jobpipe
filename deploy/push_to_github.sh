#!/usr/bin/env bash
# Create a PRIVATE GitHub repo and push this project to it.
#
#   ./deploy/push_to_github.sh [repo-name]
#
# Needs a GitHub token with the "repository: create" + "contents: write"
# permissions. Create a fine-grained token here (set an expiry — 7 days is
# plenty for this):
#   https://github.com/settings/personal-access-tokens/new
#
# The token is read from GITHUB_TOKEN, or prompted for. It is never written
# to disk, never committed, and never added to the git remote URL.

set -euo pipefail

REPO="${1:-jobpipe}"
OWNER="${GITHUB_OWNER:-tensaeb}"
VISIBILITY="${VISIBILITY:-private}"

cd "$(dirname "$0")/.."

# --- 1. refuse to push secrets -------------------------------------------
echo "==> checking for sensitive files in the index"
LEAKS=$(git ls-files | grep -E 'profile\.yaml|(^|/)\.env$|\.session|\.db$|^cv/|^outbox/|^uploads/|credentials/|\.bundle$' || true)
if [ -n "$LEAKS" ]; then
  echo "ABORT — these would be published:"
  echo "$LEAKS" | sed 's/^/    /'
  echo "Fix .gitignore, then: git rm --cached <file>"
  exit 1
fi
echo "    clean: no secrets tracked"

# --- 2. make sure there is something to push ------------------------------
if ! git rev-parse HEAD >/dev/null 2>&1; then
  echo "==> no commits yet, creating one"
  git add -A
  git commit -q -m "initial commit"
fi

# --- 3. token --------------------------------------------------------------
if [ -z "${GITHUB_TOKEN:-}" ]; then
  read -rsp "GitHub token (input hidden): " GITHUB_TOKEN
  echo
fi
[ -n "$GITHUB_TOKEN" ] || { echo "ABORT — no token given"; exit 1; }

AUTH="Authorization: Bearer $GITHUB_TOKEN"
API="https://api.github.com"

echo "==> verifying token"
WHO=$(curl -fsS -H "$AUTH" -H "Accept: application/vnd.github+json" "$API/user" \
      | sed -n 's/.*"login": *"\([^"]*\)".*/\1/p' | head -1)
[ -n "$WHO" ] || { echo "ABORT — token rejected by GitHub"; exit 1; }
echo "    authenticated as $WHO"
OWNER="$WHO"

# --- 4. create the repo (idempotent) --------------------------------------
if curl -fsS -o /dev/null -H "$AUTH" "$API/repos/$OWNER/$REPO" 2>/dev/null; then
  echo "==> repo $OWNER/$REPO already exists, reusing it"
else
  echo "==> creating $VISIBILITY repo $OWNER/$REPO"
  PRIVATE=true; [ "$VISIBILITY" = "public" ] && PRIVATE=false
  curl -fsS -X POST -H "$AUTH" -H "Accept: application/vnd.github+json" \
    "$API/user/repos" \
    -d "{\"name\":\"$REPO\",\"private\":$PRIVATE,\"has_issues\":true,\"has_wiki\":false,\"auto_init\":false}" \
    >/dev/null
  echo "    created"
fi

# --- 5. push ---------------------------------------------------------------
echo "==> pushing"
git branch -M main
git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/$OWNER/$REPO.git"

# Token passed via an askpass helper so it never lands in .git/config,
# the remote URL, or your shell history.
ASKPASS=$(mktemp)
printf '#!/bin/sh\ncase "$1" in *Username*) echo "%s";; *) echo "%s";; esac\n' \
  "$OWNER" "$GITHUB_TOKEN" > "$ASKPASS"
chmod 700 "$ASKPASS"
trap 'rm -f "$ASKPASS"' EXIT

GIT_ASKPASS="$ASKPASS" GIT_TERMINAL_PROMPT=0 git push -u origin main

echo
echo "==> done: https://github.com/$OWNER/$REPO"
echo "    the token was not stored; rotate or let it expire when you're finished"
