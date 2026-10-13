# Common tasks. Run `make` to list them. Containers use Podman.
.DEFAULT_GOAL := help
MAIL_NAME ?= scraper-mailpit
.PHONY: debug-log help install api web mail mail-stop migrate pipeline-migrate pipeline-revision pipeline-run test test-py test-web typecheck e2e e2e-ui test-db test-db-stop up down logs

# Scratch databases for the tests. `make test-db` starts a Postgres that has all three. Override any of these to use your own;
# each database's schemas are dropped and recreated, so never point them at data you want to keep.
TEST_PG ?= postgresql://postgres:test@127.0.0.1:55432
export SCRAPER_TEST_DATABASE_URL ?= $(TEST_PG)/scraper_test
export WEB_TEST_DATABASE_URL ?= $(TEST_PG)/scraper_web_test
export E2E_DATABASE_URL ?= $(TEST_PG)/scraper_e2e_test
TEST_DB_NAME ?= scraper-test-db

help: ## List the tasks
	@awk 'BEGIN {FS = ":.*## "} /^[a-z0-9-]+:.*## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install the Python and web dependencies
	uv sync
	cd web && npm ci

api: ## Run the Python API (reads .env in this directory)
	uv run python -m scraper

web: ## Run the web app in development mode (reads web/.env.local)
	cd web && npm run dev

mail: ## Start Mailpit, the development email inbox, in Podman at http://127.0.0.1:8025
	podman run -d --name $(MAIL_NAME) -p 127.0.0.1:8025:8025 docker.io/axllent/mailpit

mail-stop: ## Remove the Mailpit container
	podman rm -f $(MAIL_NAME)

migrate: ## Create or update the web app's tables in the auth schema
	cd web && npm run migrate

pipeline-migrate: ## Bring the scraping pipeline's schema up to date (reads SCRAPER_DATABASE_URL)
	uv run python -m scraper.pipeline migrate

pipeline-revision: pipeline-migrate ## Write a migration from model changes: make pipeline-revision MSG="what changed" (then review it)
	@test -n "$(MSG)" || (echo 'usage: make pipeline-revision MSG="what changed"'; exit 2)
	uv run alembic -c src/scraper/pipeline/alembic.ini revision --autogenerate -m "$(MSG)"

pipeline-run: ## Crawl one source once: make pipeline-run SOURCE=books_toscrape
	uv run python -m scraper.pipeline run $(or $(SOURCE),books_toscrape)

test: test-py test-web ## Run the Python and web tests

test-py: ## Run the Python tests (needs a scratch database: see test-db)
	uv run pytest -q -rfEs

test-web: typecheck ## Typecheck, then run the web tests
	cd web && npm test

typecheck: ## Typecheck the web app
	cd web && npm run typecheck

e2e: ## Run the real web app and API together through sign-up, tokens and deletion
	cd web && npm run e2e

e2e-ui: ## Run the browser tests (starts the whole stack with a scripted model; clears web/.next)
	cd web && npx playwright install chromium
	cd web && npm run e2e:ui

test-db: ## Start a throwaway Postgres in Podman on port 55432 with the three scratch databases
	podman run -d --name $(TEST_DB_NAME) -e POSTGRES_PASSWORD=test -e POSTGRES_DB=scraper_test -p 127.0.0.1:55432:5432 docker.io/library/postgres:17
	@until podman exec $(TEST_DB_NAME) pg_isready -U postgres -d scraper_test >/dev/null 2>&1; do sleep 1; done
	podman exec $(TEST_DB_NAME) createdb -U postgres scraper_web_test
	podman exec $(TEST_DB_NAME) createdb -U postgres scraper_e2e_test

test-db-stop: ## Remove the throwaway Postgres
	podman rm -f $(TEST_DB_NAME)

debug-log: ## Show the most recent debug log lines (needs SCRAPER_DEBUG=true), newest last
	@ls debug-logs/debug-*.jsonl >/dev/null 2>&1 || { echo "No debug log yet. Set SCRAPER_DEBUG=true in .env and restart the API."; exit 1; }
	@tail -n 60 $$(ls -t debug-logs/debug-*.jsonl | head -1)

up: ## Start Postgres and the API in containers (needs POSTGRES_PASSWORD and the hosted settings in .env)
	podman-compose up --build -d

down: ## Stop the containers
	podman-compose down

logs: ## Follow the containers' logs
	podman-compose logs -f
