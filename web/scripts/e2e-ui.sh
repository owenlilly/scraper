#!/usr/bin/env bash
# Run the browser tests (Playwright) against the whole stack, with a scripted fake model standing in for the model server.
#
#   E2E_DATABASE_URL=postgresql://user@host/scraper_e2e_test npm run e2e:ui [-- playwright arguments]
#
# The database must be a scratch one: the script drops its `auth` and `app` schemas first. Nothing here contacts a real model.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/stack.sh
trap stack_down EXIT
STACK_PROVIDER=lmstudio stack_up

status=0
E2E_RESULTS_DIR="$LOGS/test-results" WEB_URL="$WEB_URL" API_URL="$API_URL" FAKE_MODEL_URL="$MODEL_URL" WEB_LOG="$LOGS/web.log" npx playwright test "$@" || status=$?
if [ "$status" != 0 ] || [ -n "${E2E_SHOW_LOGS:-}" ]; then echo "--- web log"; tail -${E2E_LOG_LINES:-15} "$LOGS/web.log"; echo "--- api log"; tail -15 "$LOGS/api.log"; fi
exit $status
