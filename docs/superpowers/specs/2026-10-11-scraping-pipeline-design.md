# Scraping pipeline and data-side LLM: design

Date: 2026-10-11
Branch: `nextjs_auth_hosted`
Status: draft for review

## 1. Goal and intent

Replace the agent-driven scraper with a deterministic, production-style scraping pipeline, and move the LLM from fetching to working on the data the pipeline stores.

**Agreed with the owner**
- The pipeline is deterministic and agent-free: scheduling, polite fetching, raw archiving, parsing, validation, dedupe, versioned storage, monitoring.
- Each site is a hand-written spider class, built on Scrapling's spider framework (`scrapling.spiders`, 0.4.15). The pipeline layer around the spiders is ours.
- The LLM never touches the network. It reads stored data for two jobs: chat over the data, and enrichment plus quality checks.
- Scale target is "not decided": build for roughly 1 to 20 sources and tens of thousands of pages per run, behind seams that allow growth (fetch/queue/blob store).
- Pipeline workers are separate processes in this repo, sharing the existing Postgres. The existing FastAPI and Next.js apps become the read, admin and chat side.
- The existing agent, its tools and its chat UI are retired.

**Assumptions to confirm during review**
- No real target sites are chosen yet. The first reference spider is a placeholder until the owner names a site (see section 9).
- Hosted multi-user behaviour (invite-only accounts, per-person limits) stays as is. Pipeline data is shared across signed-in users, not per user. This is a change from the per-person session data model and needs confirming (section 9).

## 2. Non-goals

- A distributed broad-crawl system (millions of pages, many hundreds of sources). Interfaces leave room, nothing more.
- Embeddings and vector search. Postgres full-text search first; pgvector only when there is a demonstrated need.
- LLM-driven fetching, link choice or selector writing. The LLM may later help author spiders, but that is outside this spec.
- Letting the LLM modify scraped records or decide tombstoning.

## 3. Architecture

```
scheduler --enqueue--> pipeline.run (Postgres queue)
                              | claimed by a worker (SKIP LOCKED)
                              v
                  SourceSpider (one class per site)
                    | every response            | yielded items
                    v                           v
            raw archive (fetch)         item pipeline:
         body, headers, status,       validate > normalize > key+hash
         url, time, content hash      > upsert record + record_version
                              |
                              v
              run stats + parse-health metrics

LLM layer (read-only on scraped data, separate write path):
  chat <-- SQL + search over views of record / record_version / run stats
  enrichment + QA --> enrichment, alert tables
```

Processes: `scheduler` (one), `worker` (one or more), the existing API and web app. Each run executes in a child process of the worker, because `Spider.start()` owns its event loop and installs a SIGINT handler, and because browser sessions need memory containment.

## 4. Storage

New Postgres schema `pipeline`. Workers write; the API reads. The chat's SQL tool uses a separate read-only role with access to views only; it cannot see `auth` or `app`.

| Table | Holds |
|---|---|
| `source` | name, cron schedule, enabled, limits (concurrency, delay, proxy), config (jsonb), current schema version |
| `run` | source, status, queued/started/finished times, attempt, worker id, stats (jsonb), error |
| `fetch` | run, url, final url, status, headers, fetch time, duration, fetcher kind, blob sha256, size |
| `record` | source, natural key, current version, first seen, last seen, gone-at |
| `record_version` | record, run, fetch (provenance), data (jsonb), content hash, parser version, seen-at |
| `enrichment` | record version, kind, model, prompt version, output (jsonb) |
| `alert` | run or source, kind, detail, triage note, raised-at, resolved-at |

**Raw archive.** Metadata in Postgres. Bodies in a content-addressed blob store (zstd, keyed by sha256) behind a `BlobStore` interface. The default implementation is a directory on a mounted volume; an S3-compatible one can follow. Identical bodies are stored once. Bodies are not stored in `bytea`, to keep the database, backups and vacuum small.

**Versioning.** An upsert compares the content hash. Unchanged: only `last_seen` moves. Changed: a new `record_version` is added and `record.current_version` moves.

**Disappearing records.** A record not seen for N consecutive complete runs gets `gone_at` (a tombstone, not a delete); it is cleared if the record returns.

**Tombstone guard.** Tombstoning only happens after a run that completed and returned at least X% of the previous run's item count. A smaller run raises an `alert` and leaves records untouched. X and N are per-source settings with defaults chosen in the plan.

**Provenance and re-parsing.** Every version points to the fetch it came from. A re-parse reads archived bodies and writes new `record_version` rows with a bumped `parser_version`; no network is involved.

### 4.1 Repository layer

All access to pipeline data goes through repositories, and a repository is the class that talks to the database. Each one is a concrete class in `scraper/pipeline/repositories/` that holds the SQL, the `ON CONFLICT` clauses and the row mapping for its aggregate, and runs them on a psycopg 3 connection. There are no separate interface and implementation layers, and no in-memory twins. Spiders, the item pipeline, the scheduler, the worker, the LLM jobs and the API routes call repositories and write no SQL themselves.

| Repository | Owns | Notable methods |
|---|---|---|
| `SourceRepository` | `source` | `get`, `list_enabled`, `list_due(now)`, `update_settings` |
| `RunRepository` | `run` | `enqueue`, `claim_next(worker_id)`, `heartbeat`, `finish(stats, status)`, `previous_complete(source)` |
| `FetchRepository` | `fetch` plus the `BlobStore` | `save(response) -> FetchRef`, `get`, `open_body(ref)` |
| `RecordRepository` | `record`, `record_version` | `upsert_batch(items) -> UpsertResult`, `current(source, key)`, `history(record_id)`, `tombstone_unseen(source, run, guard)`, `reparse_candidates(source, parser_version)` |
| `EnrichmentRepository` | `enrichment` | `pending_versions(source, kind, prompt_version, model, limit)`, `save` |
| `AlertRepository` | `alert` | `raise_`, `resolve`, `open_for(source)`, `attach_triage` |

**Unit of work.** A `UnitOfWork` context manager owns one connection and one transaction, and constructs the repositories on it (`with uow: uow.records.upsert_batch(...); uow.runs.finish(...)`). It commits on clean exit and rolls back on an exception. A repository method never commits by itself, so a batch of record upserts and the run's progress counters commit atomically. This replaces the existing habit of opening a pooled connection inside every method.

**Read side.** The API and the chat read through the same repositories, using read methods that return view-shaped DTOs, or through read-only methods on dedicated repositories where a query spans aggregates (for example `RecordRepository.search` for full-text). There is no second data-access path. The one place that accepts SQL from outside the repository code is the chat's `query_sql` tool, and that is a method on a `ReadOnlySqlRepository` that runs on a connection using the read-only role, over the documented views, with a statement timeout and a row cap.

**Domain types.** Repositories take and return frozen dataclasses (`SourceConfig`, `Run`, `FetchRef`, `RecordItem`, `RecordVersion`, `Alert`), not dict rows or cursors.

**Tests.** Repositories are tested against a real Postgres, so queue claiming (`SKIP LOCKED`), versioning, the tombstone guard and re-parse are checked on the database that will run them. Spider and pipeline tests also use Postgres through the unit of work. See section 8.

## 5. Spider contract and item pipeline

A source author writes:

```python
class AcmePrices(SourceSpider):
    name = "acme_prices"
    allowed_domains = {"acme.example"}
    start_urls = ["https://acme.example/catalog"]
    item_schema = AcmeProduct        # pydantic model: types, required fields, normalizers
    natural_key = ("sku",)
    parser_version = "3"

    async def parse(self, response):  # plain Scrapling: yield dicts or Requests
        ...
```

`SourceSpider` extends `scrapling.spiders.Spider` and sets production defaults: `robots_txt_obey = True`, auto-throttle on, and per-domain concurrency, delay and proxy read from the `source` row (overriding class defaults, so operators tune a site without a deploy).

**Archiving.** `is_blocked(response)` is the one hook that sees every response, blocked ones included. The base class implements it as "archive the response, then call an overridable `looks_blocked()`". Blocked pages are kept as evidence. (Verify in the plan that the engine calls it for every response.)

**Item to fetch link.** The base class wraps each callback so that every dict it yields is stamped with the fetch id before the engine receives it. Stamping the yielded dict avoids context variables and is safe under concurrency.

**Item pipeline** (run from `on_scraped_item`; spiders contain no storage code):
1. Validate against `item_schema`. Failures are counted per field, with a capped sample of payloads kept in `run.stats`.
2. Normalize with the schema's field normalizers (prices, whitespace, URLs, dates).
3. Compute the natural key and the content hash.
4. Upsert through the injected `RecordRepository` (inside a `UnitOfWork`), batched per transaction.

**Runs.** The scheduler enqueues a run when a source is due. A worker claims it and runs it in a child process with a per-run checkpoint directory, so a crashed run resumes if the worker restarts on the same volume. At the end, `CrawlStats` is copied into `run.stats` and the tombstone guard runs.

## 6. LLM layer

All three jobs are downstream of stored data and outside the crawl's critical path.

**Chat over the data.** A tool-using loop with read-only tools: `query_sql` (read-only role, documented views, statement timeout, row cap), `search_records` (Postgres full-text), `record_history`, `run_stats`, `page_excerpt` (excerpt of an archived page). Answers cite record and version ids. Scraped text reaches the model as data and is treated as untrusted; the chat has no write or network tools, so a hostile page can at worst cause a wrong answer.

**Enrichment.** Batch jobs on the same Postgres queue. Each source configures which kinds run (classify, tag, summarize, normalize), each with a prompt version and a JSON output schema. A job only processes `record_version`s with no result for that `(kind, prompt version, model)`, so re-runs are idempotent. Outputs are schema-validated and failures counted. A per-source daily cap bounds cost.

**Quality checks.** Deterministic checks come first and use no model: item-count drop, field fill-rate drop, status-code shift, validation-failure rate, price outliers. They raise an `alert`. The LLM then triages it, reading the stats diff and sample archived pages against parsed output, and attaches a likely cause to the alert. It never edits data and never tombstones.

**Models.** Reuse the existing OpenAI-compatible clients (LM Studio locally, Mistral hosted) and the existing usage and daily-limit code for chat.

## 7. Existing code

Decided from file names; the plan confirms each by checking imports.
- Removed: `agent.py`, `tools/`, `browser.py`, `search.py`, `session.py`, the SQLite `store.py`, `crawl.py`, the agent chat screens in `web/`.
- Kept: auth, accounts, limits, API and DB scaffolding, `llm.py` / `chat_model.py`, `usage.py`.
- Undecided: `fetch.py`, `netguard.py`, `guards.py` (may still serve spiders).

## 8. Testing

- Spiders are tested against fixture HTML served by a local test server; no live sites in tests.
- Item pipeline, normalizers, hashing and versioning: unit tests.
- Repositories, the item pipeline, the scheduler and the LLM jobs are tested against a real Postgres, using the existing convention of skipping when none is available. Behaviours such as queue claiming (`SKIP LOCKED`), versioning on an unchanged hash, the tombstone guard and re-parse are tested on the database that will run them. In CI and on a dev machine without Postgres, a Podman-run Postgres is the intended setup.
- LLM jobs use fake model clients and recorded fixtures. Live Mistral is never used for tests; LM Studio is for manual checks only.

## 9. Delivery and open items

Three plans, each with its own spec-to-plan cycle, in this order:
1. **Pipeline core:** the `pipeline` schema, blob store, `SourceSpider` base, item pipeline, queue, scheduler, worker, a reference spider, run stats, deterministic checks.
2. **LLM layer:** chat tools, enrichment jobs, alert triage.
3. **Web UI and agent removal:** sources and runs (status, run now, pause), data browser with raw-page view, alerts, chat; delete the old agent code.

**Open items for review**
- Is pipeline data shared across all signed-in users (assumed), or scoped per account?
- Which real site should the first spider target? Until named, the reference spider is a placeholder.
- Queue implementation: a library such as Procrastinate, or a small queue table of our own. Decide in plan 1.
- Cron parsing and scheduler library. Decide in plan 1.
- Default values for the tombstone guard (X%, N runs).
- Confirm `is_blocked` is called for every response, and the checkpoint resume behaviour, against Scrapling 0.4.15's engine.
- Fate of `fetch.py`, `netguard.py`, `guards.py`.
- The existing `PgSessionStore` follows the same shape informally (same methods as the SQLite store, no declared interface). It is deleted with the agent, so it is not migrated to this layer.
