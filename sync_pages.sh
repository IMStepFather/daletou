#!/usr/bin/env bash
# Sync results.html to a Cloudflare Pages project (Direct Upload).
# Standalone, non-interactive, cron friendly. Safe to run with no arguments.
# Missing credentials, missing wrangler or unchanged content never breaks
# the main lotto job: they only produce a warning and exit 0.
set -uo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR" || exit 1

# Preserve externally injected credentials (manual runs / CI) if .env leaves
# the corresponding keys empty; .env stays the primary source on the VPS.
_prior_token="${CLOUDFLARE_API_TOKEN:-}"
_prior_account="${CLOUDFLARE_ACCOUNT_ID:-}"
if [[ -f "$APP_DIR/.env" ]]; then
  set -a
  . "$APP_DIR/.env"
  set +a
fi

PROJECT="${PAGES_PROJECT:-daletou}"
DOMAIN="${PAGES_DOMAIN:-daletou.680868.xyz}"
TIMEOUT="${PAGES_TIMEOUT:-180}"
TOKEN="${CLOUDFLARE_API_TOKEN:-$_prior_token}"
ACCOUNT="${CLOUDFLARE_ACCOUNT_ID:-$_prior_account}"
SOURCE="$APP_DIR/results.html"
STATE="$APP_DIR/.pages.sha256"
LOCK="$APP_DIR/.pages.lock"

if [[ -z "$TOKEN" || -z "$ACCOUNT" ]]; then
  echo "[pages-sync] skip: CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID not set"
  exit 0
fi
if [[ ! -f "$SOURCE" ]]; then
  echo "[pages-sync] skip: results.html not found"
  exit 0
fi

# Idempotency: deploy only when the rendered report actually changed.
# Silent on skip so a 5-minute cron produces no log noise.
# Read via stdin: sha256sum prefixes the hash with a backslash when the
# filename contains special characters (Windows/Git Bash paths), which would
# break the comparison. "hash  -" is stable on every platform.
current="$(sha256sum < "$SOURCE" | cut -d' ' -f1)"
previous=""
if [[ -f "$STATE" ]]; then
  previous="$(cat "$STATE")"
fi
if [[ "$current" == "$previous" ]]; then
  exit 0
fi

# Best-effort concurrency guard. Not all minimal images ship flock.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK" || exit 0
  if ! flock -n 9; then
    echo "[pages-sync] skip: another sync is running"
    exit 0
  fi
fi

if [[ -n "${WRANGLER_BIN:-}" ]]; then
  W=("$WRANGLER_BIN")
elif command -v wrangler >/dev/null 2>&1; then
  W=(wrangler)
elif command -v npx >/dev/null 2>&1; then
  W=(npx --yes wrangler)
else
  echo "[pages-sync] FAIL: wrangler and npx not found; install Node.js + wrangler"
  exit 1
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp "$SOURCE" "$STAGE/index.html"
cp "$SOURCE" "$STAGE/results.html"
printf '/*\n  Cache-Control: no-cache, must-revalidate\n' > "$STAGE/_headers"

echo "[pages-sync] deploying to Pages project $PROJECT"
if timeout "$TIMEOUT" env \
    CLOUDFLARE_API_TOKEN="$TOKEN" \
    CLOUDFLARE_ACCOUNT_ID="$ACCOUNT" \
    "${W[@]}" pages deploy "$STAGE" \
    --project-name="$PROJECT" --branch=main; then
  printf '%s\n' "$current" > "$STATE"
  echo "[pages-sync] OK: https://$DOMAIN (fallback https://$PROJECT.pages.dev)"
  exit 0
fi

echo "[pages-sync] FAIL: deploy failed; hash not recorded, next run will retry"
exit 1
