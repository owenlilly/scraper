#!/usr/bin/env bash
# Start the web app and the Python API on one scratch database and run scripts/e2e.ts against them.
#
#   E2E_DATABASE_URL=postgresql://user@host/scraper_e2e_test npm run e2e
#
# The database must be a scratch one: the script drops its `auth` and `app` schemas first.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/stack.sh
trap stack_down EXIT
STACK_PROVIDER=mistral stack_up

WEB_URL="$WEB_URL" API_URL="$API_URL" WEB_LOG="$LOGS/web.log" npx tsx scripts/e2e.ts || { echo "--- web log"; tail -15 "$LOGS/web.log"; echo "--- api log"; tail -15 "$LOGS/api.log"; exit 1; }
