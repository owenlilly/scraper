#!/usr/bin/env bash
# Start the web app and the Python API on one scratch database and run scripts/e2e.ts against them.
#
#   E2E_DATABASE_URL=postgresql://user@host/scraper_e2e_test npm run e2e
#
# The database must be a scratch one: the script drops its `auth` and `app` schemas first.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${E2E_DATABASE_URL:?set E2E_DATABASE_URL to a scratch database (its auth and app schemas are dropped)}"
case "$E2E_DATABASE_URL" in *test*) ;; *) echo "refusing: the database name must contain 'test'" >&2; exit 2;; esac

WEB_PORT=${WEB_PORT:-3100}; API_PORT=${API_PORT:-8180}
WEB_URL="http://localhost:$WEB_PORT"; API_URL="http://localhost:$API_PORT"
for port in "$WEB_PORT" "$API_PORT"; do  # a leftover server on these ports would be tested by mistake
  if lsof -ti "tcp:$port" -sTCP:LISTEN > /dev/null 2>&1; then echo "port $port is already in use; stop that server or set WEB_PORT / API_PORT" >&2; exit 2; fi
done
SECRET=$(node -e "console.log(require('crypto').randomBytes(24).toString('base64url'))")
LOGS=$(mktemp -d)
stop_servers() {
  # `uv run` and `npx` start the real server as a child; stop the children too
  for pid in $(jobs -p); do pkill -TERM -P "$pid" 2>/dev/null || true; kill "$pid" 2>/dev/null || true; done
  sleep 0.5; rm -rf "$LOGS"
}
trap stop_servers EXIT

psql "$E2E_DATABASE_URL" -q -c "DROP SCHEMA IF EXISTS auth CASCADE" -c "DROP SCHEMA IF EXISTS app CASCADE" -c "CREATE SCHEMA auth"
export DATABASE_URL="$E2E_DATABASE_URL" BETTER_AUTH_SECRET="$(node -e "console.log(require('crypto').randomBytes(32).toString('base64url'))")" \
  BETTER_AUTH_URL="$WEB_URL" API_URL="$API_URL" API_AUDIENCE="$API_URL" SERVICE_SECRET="$SECRET" \
  EMAIL_TRANSPORT=log ADMIN_EMAIL=owner@example.com

npm run --silent migrate
# The development server, not `next start`: a production server insists on https and a real email service, which is right
# for a deployment and wrong for a check over plain http on this machine.
npx next dev -p "$WEB_PORT" > "$LOGS/web.log" 2>&1 &
# Nothing here may reach a real model provider: the API is pointed at an address that refuses connections, given a placeholder
# key, and run from a directory that has no .env, so the key in the repository's .env cannot be picked up.
ROOT=$(cd .. && pwd)
(cd "$LOGS" && SCRAPER_HOSTED=true SCRAPER_DATABASE_URL="$E2E_DATABASE_URL" SCRAPER_JWKS_URL="$WEB_URL/api/auth/jwks" SCRAPER_JWT_ISSUER="$WEB_URL" \
  SCRAPER_JWT_AUDIENCE="$API_URL" SCRAPER_SERVICE_SECRET="$SECRET" SCRAPER_API_KEY=not-a-real-key SCRAPER_MISTRAL_BASE_URL="http://127.0.0.1:1/v1" \
  SCRAPER_CORS_ORIGINS="$WEB_URL" SCRAPER_HOST=127.0.0.1 SCRAPER_PORT="$API_PORT" exec uv run --project "$ROOT" python -m scraper_api > "$LOGS/api.log" 2>&1) &

for url in "$WEB_URL/" "$API_URL/health"; do
  for _ in $(seq 1 100); do curl -fs "$url" > /dev/null 2>&1 && break; sleep 0.2; done
  curl -fs "$url" > /dev/null 2>&1 || { echo "did not start: $url"; tail -20 "$LOGS"/*.log; exit 1; }
done
WEB_URL="$WEB_URL" API_URL="$API_URL" WEB_LOG="$LOGS/web.log" npx tsx scripts/e2e.ts || { echo "--- web log"; tail -15 "$LOGS/web.log"; echo "--- api log"; tail -15 "$LOGS/api.log"; exit 1; }
