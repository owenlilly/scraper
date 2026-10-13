# Starts the whole stack on one scratch database: the web app, the Python API and, for the browser tests, a scripted fake model.
# Not run directly: scripts/e2e.sh and scripts/e2e-ui.sh source it.
#
#   STACK_PROVIDER=mistral   the API is given a placeholder key and an address that refuses connections (nothing can reach Mistral)
#   STACK_PROVIDER=lmstudio  the API talks to the fake model server this script starts (nothing can reach a real model either)
#
# After `stack_up`: WEB_URL, API_URL, MODEL_URL (lmstudio only) and LOGS are set. Call `stack_down` on exit.

stack_up() {
  if [ -z "${E2E_DATABASE_URL:-}" ]; then
    echo "set E2E_DATABASE_URL to a scratch database (its auth and app schemas are dropped)" >&2
    exit 2
  fi
  # Look at the database NAME only (between the first / after the host and any ?): a user, host or password that happens to say "test"
  # must not unlock a script that drops schemas.
  local rest=${E2E_DATABASE_URL#*://} name
  case "$rest" in */*) name=${rest#*/}; name=${name%%[?#]*} ;; *) name="" ;; esac
  case "$name" in *test*) ;; *) echo "refusing: the database name (\"$name\") must contain 'test', because this script drops its schemas" >&2; exit 2;; esac

  WEB_PORT=${WEB_PORT:-3100}; API_PORT=${API_PORT:-8180}; MODEL_PORT=${MODEL_PORT:-8190}
  WEB_URL="http://localhost:$WEB_PORT"; API_URL="http://localhost:$API_PORT"; MODEL_URL="http://127.0.0.1:$MODEL_PORT"
  for port in "$WEB_PORT" "$API_PORT" "$MODEL_PORT"; do  # a leftover server on these ports would be tested by mistake
    if lsof -ti "tcp:$port" -sTCP:LISTEN > /dev/null 2>&1; then echo "port $port is already in use; stop that server or set WEB_PORT / API_PORT / MODEL_PORT" >&2; exit 2; fi
  done
  ROOT=$(cd .. && pwd)
  LOGS=$(mktemp -d)
  SECRET=$(node -e "console.log(require('crypto').randomBytes(24).toString('base64url'))")

  psql "$E2E_DATABASE_URL" -q -c "DROP SCHEMA IF EXISTS auth CASCADE" -c "DROP SCHEMA IF EXISTS app CASCADE" -c "DROP SCHEMA IF EXISTS pipeline CASCADE" -c "CREATE SCHEMA auth"
  export DATABASE_URL="$E2E_DATABASE_URL" BETTER_AUTH_SECRET="$(node -e "console.log(require('crypto').randomBytes(32).toString('base64url'))")" \
    BETTER_AUTH_URL="$WEB_URL" API_URL="$API_URL" API_AUDIENCE="$API_URL" SERVICE_SECRET="$SECRET" \
    EMAIL_TRANSPORT=log ADMIN_EMAIL=owner@example.com
  npm run --silent migrate

  # The development server, not `next start`: a production server insists on https and a real email service, which is right
  # for a deployment and wrong for a check over plain http on this machine.
  # It starts from an empty build cache: a cache left by an earlier session can make the page reload over and over under a test.
  rm -rf .next
  npx next dev -p "$WEB_PORT" > "$LOGS/web.log" 2>&1 &

  # Nothing here may reach a real model provider, and the API runs from a directory with no .env, so the key in the repository's
  # .env cannot be picked up.
  local provider_env
  if [ "${STACK_PROVIDER:-mistral}" = lmstudio ]; then
    FAKE_MODEL_PORT="$MODEL_PORT" npx tsx e2e/support/fake-model-main.ts > "$LOGS/model.log" 2>&1 &
    # Mistral is offered too, so the provider picker has something to pick; its address goes nowhere, so nothing can be sent.
    provider_env="SCRAPER_PROVIDER=lmstudio SCRAPER_BASE_URL=$MODEL_URL/v1 SCRAPER_PROVIDERS=lmstudio,mistral SCRAPER_API_KEY=not-a-real-key SCRAPER_MISTRAL_BASE_URL=http://127.0.0.1:1/v1"
  else
    provider_env="SCRAPER_API_KEY=not-a-real-key SCRAPER_MISTRAL_BASE_URL=http://127.0.0.1:1/v1"
  fi
  (cd "$LOGS" && exec env $provider_env SCRAPER_HOSTED=true SCRAPER_DATABASE_URL="$E2E_DATABASE_URL" SCRAPER_JWKS_URL="$WEB_URL/api/auth/jwks" \
    SCRAPER_JWT_ISSUER="$WEB_URL" SCRAPER_JWT_AUDIENCE="$API_URL" SCRAPER_SERVICE_SECRET="$SECRET" SCRAPER_CORS_ORIGINS="$WEB_URL" \
    SCRAPER_HOST=127.0.0.1 SCRAPER_PORT="$API_PORT" uv run --project "$ROOT" python -m scraper > "$LOGS/api.log" 2>&1) &

  local urls=("$WEB_URL/" "$API_URL/health")
  [ "${STACK_PROVIDER:-mistral}" = lmstudio ] && urls+=("$MODEL_URL/v1/models")
  for url in "${urls[@]}"; do
    for _ in $(seq 1 150); do curl -fs -o /dev/null "$url" 2>/dev/null && break; sleep 0.2; done
    curl -fs -o /dev/null "$url" 2>/dev/null || { echo "did not start: $url"; tail -20 "$LOGS"/*.log; exit 1; }
  done
}

stack_down() {
  local status=$?  # the script's own exit status: cleanup must hand it on unchanged
  trap - EXIT
  # `uv run` and `npx` start the real server as a child; stop the children too
  for pid in $(jobs -p); do pkill -TERM -P "$pid" 2>/dev/null || true; kill "$pid" 2>/dev/null || true; done
  sleep 0.5
  if [ -n "${LOGS:-}" ]; then rm -rf "$LOGS"; fi
  exit "$status"
}
