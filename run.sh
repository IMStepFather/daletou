#!/usr/bin/env bash
# cron wrapper: cd to app dir, load .env, dispatch to the right entry point.
# cron provides no PATH / cwd context, so this wrapper must be self-contained.
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"
if [[ -f "$APP_DIR/.env" ]]; then
  set -a; . "$APP_DIR/.env"; set +a
fi
# fetch is handled by the standalone script (same implementation as CLI)
if [[ "${1:-}" == "fetch" ]]; then
  shift
  exec /usr/bin/env python3 "$APP_DIR/fetch_results.py" "$@"
fi
exec /usr/bin/env python3 "$APP_DIR/lotto.py" "$@"
