# Spiders page: live runs, retries and trial runs

Status: design, awaiting review. Branch: `realtor_ca_spider`. Builds on `2026-10-11-scraping-pipeline-design.md`.

## 1. Goal

An administrator manages the pipeline's spiders from the web app instead of the terminal. One page lists the spiders. Each spider has a page where they can:

- **Run** it now and watch the run progress live.
- **Stop** a running run (it ends as `paused`; nothing saved is lost).
- **Retry** a failed or paused run.
- **Trial run** it: a small capped run that stores no records and shows what the spider parsed and rejected.

Success: an admin can start `books_toscrape`, see counters and fetched URLs move without reloading, stop it, retry it, and run a trial that shows the first 25 parsed items and the rejected samples, all without opening a terminal. A run that nothing is executing, or whose worker died, says so on the page and ends as `failed`.

## 2. Non-goals

- No scheduling (cron) and no automatic retry policy. Retry is a button.
- No resume of a crawl from where it stopped. A retry starts a new run from scratch. Resuming from a Scrapling crawl directory is a later change.
- No editing of source settings, no data browser, no alerts. Those stay in the pipeline spec's later plans.
- No Inngest. It was considered and declined for now: it would replace only the queue and scheduler code, while the run table, heartbeat and child process stay. The queue sits behind `RunRepository`, so it can be swapped later for the enrichment jobs or the scheduler without touching the API or the page.
- Spiders are not run inside the API process.

## 3. Architecture

```
web (Next.js, /spiders)  --bearer token-->  API (FastAPI, /pipeline/*)  --repositories-->  Postgres (pipeline schema)
                                                                                              ^
worker (python -m scraper.pipeline worker) --claims queued runs, one child process per run---+
   child: python -m scraper.pipeline exec-run <id>  (the spider; beats every ~2s into run.stats)
```

- The page reads and commands through the API. The API only reads and writes `pipeline` rows through repositories. It never imports or runs a spider.
- The worker is a separate process. It claims a queued run, starts a child process for it, and watches the child.
- The child runs the spider (`Spider.start()` owns its event loop and installs a SIGINT handler, and browser spiders need memory containment, as the pipeline spec says).
- The page polls. No streaming connection is held.

## 4. Data model

One Alembic migration (`0002`) adds to `pipeline.run`:

| Column | Type | Meaning |
|---|---|---|
| `kind` | text, default `live` | `live` or `trial` |
| `retry_of` | bigint, nullable, FK to `run.id` | the run this one retries |
| `requested_by` | text, nullable | the admin's user id; null for the CLI |
| `heartbeat_at` | timestamptz, nullable | last sign of life from the executing process |
| `cancel_requested` | boolean, default false | set by Stop |
| `options` | jsonb, default `{}` | per-run settings, e.g. `{"max_requests": 5}` for a trial |

Statuses: `queued`, `running`, `completed`, `paused`, `failed`, `cancelled`. `cancelled` is a run stopped while still queued. A run stopped while running ends `paused`, as Ctrl+C does today.

Live counters need no new table. The executing process writes a snapshot into `run.stats` on every beat, and `finish` replaces it with the final stats. `stats` holds Scrapling's `CrawlStats.to_dict()` (including `requests_count`, `blocked_requests_count`, `failed_requests_count`) merged with the pipeline counters (`records_created`, `records_changed`, `records_unchanged`, `items_rejected`, `rejected_by_field`, `rejected_samples`). A trial run adds `trial_items`: the first 25 validated items.

The migration is reviewed and committed like the first one (`make pipeline-revision MSG="..."`).

## 5. Repositories

All SQL stays in repositories. `RunRepository` gains:

- `enqueue(source_id, *, kind="live", retry_of=None, requested_by=None, options=None)`. Raises a typed `SourceBusy` if the source already has a `queued` or `running` run (checked in the same statement that inserts, backed by a partial unique index on `source_id` where status is `queued` or `running`, so two requests cannot both win).
- `claim_next(worker_id)`. Atomically picks the oldest `queued` run with `FOR UPDATE SKIP LOCKED`, sets it `running` with `started_at` and `heartbeat_at`, and returns it, or `None`.
- `heartbeat(run_id, stats) -> bool`. Writes the stats snapshot and `heartbeat_at`, and returns whether a cancel has been requested.
- `request_cancel(run_id)`. A `queued` run becomes `cancelled` at once. A `running` run gets `cancel_requested = true`. A finished run is left alone.
- `reap_stale(older_than)`. Marks `running` runs whose heartbeat is older than the threshold (default 60 s) as `failed` with `error = "worker lost"`.
- `recent(source_id, limit)` and `get(run_id)`.

`SourceRepository` gains `list()`. `FetchRepository` gains `since(run_id, after_id, limit)`, returning `id, url, final_url, status, fetched_at, duration_ms, size` in id order.

`duration_ms` is already a column but the spider does not fill it. The archive step records it from the response timing if Scrapling exposes it; otherwise the page omits the column. (Open item 1.)

## 6. Worker and run execution

`python -m scraper.pipeline worker [--concurrency N]` (default 1), also `make pipeline-worker`:

1. Loop: `reap_stale`, then `claim_next`. When there is nothing to claim, sleep 2 s.
2. For a claimed run, start `python -m scraper.pipeline exec-run <run_id>` as a child in its own process group.
3. Wait for the child. If it exits without having finished the run (non-zero exit, killed), mark the run `failed` with the exit code.
4. On SIGTERM or SIGINT the worker sends the child a graceful stop (SIGINT, which Scrapling treats as pause), waits a bounded time, and exits.

`exec-run <id>` loads the run, builds the spider with a `RunContext` that now also carries `kind` and `options`, and runs it. A background thread beats every 2 s: it calls `heartbeat(run_id, snapshot)` and, if the answer is "cancel requested", calls `spider.pause()` once. `pause()` is called at most once, because Scrapling treats a second call as a force stop. The final result goes through `finish` exactly as `run_source` does today.

The existing CLI `run` command keeps working and runs in-process as now. It records its run through the same `enqueue` and `start`, so the one-active-run rule applies to it too: the CLI refuses with "a run is already queued or running for this source" if the page or a worker holds one.

A source never has two runs at once (section 5), so a worker with `--concurrency 2` runs two different sources in parallel.

## 7. Trial runs

A trial is a run with `kind = 'trial'` and `options = {"max_requests": N}` (default 5, allowed 1 to 20):

- The spider pauses itself when `requests_count` reaches `max_requests`, checked where every response already passes (`is_blocked`).
- Every response is still archived. A blocked page is the evidence you want most when a site fights back.
- Items go through the same `prepare` validation. Valid ones are not buffered for the record tables; the first 25 are kept in `stats.trial_items`. Invalid ones are counted and sampled exactly as in a live run.
- `record`, `record_version` and the run's record counters are untouched. The run ends `completed` when it stopped at the cap, and the page labels it "stopped at the page cap".
- A trial cannot be retried as a live run. "Retry" on a trial starts another trial with the same options.

## 8. API

A new router, `/pipeline`, mounted next to the existing ones. Every route depends on `require_admin`.

`require_admin` verifies the bearer token as `current_user` does, then reads `role` and `banned` from `auth."user"` for that id. The token carries only `sub` and stays that way, and a banned or demoted admin loses access at once instead of when the 15-minute token expires. A non-admin gets 403 `forbidden`, a missing or bad token 401, as elsewhere.

The hosted setup deliberately gives the API's database role only the `app` schema, so that a bug in the API cannot read password hashes. The lookup therefore needs one narrow grant, added to `docs/hosted-deploy.md`: `GRANT USAGE ON SCHEMA auth TO scraper_api; GRANT SELECT (id, role, banned) ON auth."user" TO scraper_api;`. That exposes three columns of one table and nothing else (not emails, names, sessions or passwords). The pipeline's own tables live in a `pipeline` schema that the same role owns (`CREATE SCHEMA pipeline AUTHORIZATION scraper_api`), because the API and the worker run the pipeline's migrations at start-up.

| Route | Result |
|---|---|
| `GET /pipeline/sources` | The names in `SPIDERS`, each with its latest run (creating missing `source` rows). |
| `GET /pipeline/sources/{name}/runs?limit=` | Run history, newest first (default 30, max 100). 404 for an unknown name. |
| `POST /pipeline/sources/{name}/runs` | Body `{kind: "live" or "trial", max_requests?}`. Returns the new run, 201. 409 `source_busy` if the source has a queued or running run. |
| `GET /pipeline/runs/{id}` | One run including `stats`, `error`, `options`. |
| `GET /pipeline/runs/{id}/fetches?after=` | Archived responses with id greater than `after`, oldest first, at most 200. |
| `POST /pipeline/runs/{id}/stop` | 202. Idempotent. 409 `not_active` if the run has already finished. |
| `POST /pipeline/runs/{id}/retry` | New run with `retry_of` set. 409 `not_retryable` unless the run is `failed` or `paused`; 409 `source_busy` as above. |

A run is returned as `{id, source, kind, status, queued_at, started_at, finished_at, heartbeat_at, retry_of, cancel_requested, options, stats, error}`. Errors use the existing `{error: {code, message}}` shape.

The API lists spiders from the `SPIDERS` registry, which imports Scrapling (already a dependency). It only reads each class's `name`.

## 9. The page

Reuses the app's design system (tokens in `globals.css`, Literata for headings, Schibsted Grotesk for the interface, `.plain` tables, `.pill`, `.btn`, `Dialog`, `Toast`). No new palette. The yellow highlighter stays reserved for "the model used this page".

**Routes** (admin only: `requireAdmin` in the page, `/spiders/:path*` added to the proxy matcher): `/spiders` and `/spiders/[name]`. Admins get a "Spiders" link in the top bar.

**List.** A table: spider name, status pill, last run (relative), new and changed records of that run. Actions by state: Run now and Trial run when idle; Stop when running; Retry after a failure. A row opens the spider.

**Spider page.** Master-detail.

- Left: the run list (id, status, kind, relative time). It selects what the right side shows and defaults to the latest run. On narrow screens it becomes a select above the main pane.
- Header: `Run now` and `Trial run` with a small page-cap select (default 5).
- Right, for the selected run: a header with status pill, Trial badge, "retry of #N", started time, and worker freshness; a single ruled counters strip (requests, new, changed, unchanged, rejected, blocked); the error in an error box when there is one; the page ledger, a ruled, numbered table of fetched URLs with status, time and size, newest first, status colours as pills (2xx ok, 3xx muted, 4xx/5xx and blocked red); for trial runs, `Parsed items` and `Rejected items` tabs.
- One motion: the pulsing dot on a running pill (disabled under `prefers-reduced-motion`).

**Problem states, each naming the fix:**

| Situation | Message |
|---|---|
| Queued for over 30 s | "No worker has picked this up. Start one with `make pipeline-worker`." |
| Heartbeat older than 15 s while running | Amber "worker last seen 40s ago" in the run header |
| Reaped run | Failed, error "worker lost" |
| 409 `source_busy` | Toast "A run is already queued or running for this spider" |
| Stop requested | Stop disabled, shows "Stopping…" until the run ends |
| Never run | "No runs yet. Run a trial first to check this spider against the site." |
| API unreachable | The client's existing network error message, with a retry-in-place button |

**Live behaviour.** TanStack Query polls `GET /pipeline/runs/{id}` and `fetches?after=` every 2 s while the selected run is `queued` or `running`, and stops when it finishes. The run list refreshes every 6 s, not every 2 s, so a page left open stays near 70 requests a minute, under the API's per-user limit of 120. Status changes are announced in an `aria-live="polite"` region, not each ledger row. The ledger shows the latest 200 rows.

**Files.** `web/src/app/spiders/page.tsx`, `web/src/app/spiders/[name]/page.tsx`; `web/src/components/spiders/` (`SpiderList`, `SpiderPage`, `RunList`, `RunHeader`, `RunLedger`, `TrialResults`, `spiders.css`); `web/src/lib/spiders/` (`types.ts`, `queries.ts`, `derive.ts`). Before any page code, read `node_modules/next/dist/docs/` for the installed Next.js version, as `web/AGENTS.md` requires.

## 10. Testing

Python, on the existing real-Postgres fixtures (skipped without one, as now):

- **Queue:** `claim_next` hands one run to exactly one of two concurrent claimers; the partial unique index and `SourceBusy` refuse a second active run for a source, including one from the CLI; `claim_next` returns the oldest queued run first and `None` when the queue is empty.
- **Heartbeat and cancel:** `heartbeat` stores stats and reports a cancel; `request_cancel` cancels a queued run and flags a running one; `reap_stale` fails only stale `running` runs.
- **Trial:** writes no `record` or `record_version` rows; stops at the cap; keeps at most 25 items; still archives responses; counts and samples rejections.
- **Execution:** `exec-run` against the local fixture server (no live sites): a full run completes with counters; a stop request ends it `paused`; `pause()` is called once; a child killed mid-run is marked `failed` by the worker.
- **API:** admin allowed, non-admin 403, banned admin 403, no token 401; 404, 409 `source_busy`, 409 `not_active`, 409 `not_retryable`; retry sets `retry_of`; `fetches?after=` returns only newer rows.
- **Migration:** `0002` upgrades a database at `0001` and the models match it (the existing migration test).

Web: Vitest for `derive.ts` (status labels, elapsed time, stale heartbeat, no-worker, can-retry, can-stop) and for the query hooks' polling start and stop; a Playwright spec for the admin-only guard and the list and detail pages against a stubbed API. No live model calls and no live sites anywhere.

## 11. Open items

1. **Fetch duration.** Confirm whether Scrapling gives per-response timing to record in `fetch.duration_ms`; the ledger drops the column if not.
2. **Realtor spider.** `SPIDERS` currently registers the uncommitted `RealtorSpider` (a plain Scrapling `Spider` named `demo`). It must become a `SourceSpider` named `realtor_ca` before it is runnable. This work builds and tests on `books_toscrape` and does not depend on it; until it is converted the API should list only classes that are `SourceSpider` subclasses.
3. **Deployment.** Add a `worker` service to `compose.yaml`, the `pipeline` schema and the column grant above to the hosted-deploy notes, and change that page's check from `SELECT 1 FROM auth."user"` to `SELECT email FROM auth."user"`, which must still fail.
4. **Thresholds.** The stale-heartbeat limit (60 s reap, 15 s warning) and the no-worker warning (30 s) are starting values; they become settings if they prove wrong.
