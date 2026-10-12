# Hosted Page Chat: Next.js UI and invite-only auth

Date: 2026-10-09
Status: draft for review

## Goal

Replace the NiceGUI UI with a Next.js app and turn the local single-user tool into a hosted, invite-only, multi-user service. Each invited person has their own account, session, runs, results and preferences, isolated from everyone else. Inference uses the operator's Mistral API key.

Success looks like:

- An invited person can sign up, verify their email, sign in, and use every feature the local app has today (scrape, agent runs, pause cards, follow-up steering, results table with CSV/JSON download, model picker).
- No person can read, stop, resume or discard another person's data or runs.
- A person who was not invited cannot create an account.
- The server cannot be used to reach internal network addresses.
- One user cannot run up the Mistral bill without bound.

## Decisions already made

| Topic | Decision |
|---|---|
| Audience | Hosted, many users, invite-only |
| Tenancy | Per-user isolation only. No organizations in v1 |
| Model backend | Mistral API with a server-side key. LM Studio stays available for local development only |
| Auth | Better Auth in Next.js. Python API verifies its JWTs through JWKS |
| Backend | FastAPI wrapping the existing `scraper` library |
| Database | One Postgres, two schemas: `auth` (Better Auth) and `app` (Python) |
| Hosting | Next.js on Vercel, Python API on a separate container host |
| Rejected | Django and django-allauth (cannot run without Django), fastapi-users (maintenance mode), Supabase Auth and fastapi-users (no built-in tenants), Ory Kratos (no organizations in open source) |

## Assumptions

Items 1 and 3 were confirmed by the user on 2026-10-10. Items 2 and 4 are still assumptions.

1. **The NiceGUI UI is removed completely** (confirmed): `src/scraper/app.py`, `theme.py`, the local `__main__.py`, their tests, the `nicegui` dependency and every mention in the docs. The removal is the last task of the UI plan, after the web app works, so the repository never has a stretch with no working UI. Running locally means the same stack started with `podman-compose` (Postgres, API, web), optionally with LM Studio as the provider. No separate single-user mode is kept.
2. **UI direction.** The new UI keeps today's layout and full feature set. Whether to keep the "reading desk" look or redesign it is not decided; the visual pass happens at plan time with the frontend-design skill, using your feedback on what feels clunky.
3. **Email provider** is Resend (confirmed), behind one `sendEmail()` function so it is swappable.
4. **Organizations are out of scope.** Adding them later means an `org_id` column and a data migration.

## Architecture

```
Browser --cookie--> Next.js on Vercel (UI + Better Auth at /api/auth/*)
   |                          | users, sessions, invites
   |                          v
   |                     Postgres  <-- app tables (sessions, pages, rows, history, runs, prefs, usage)
   |                                          ^
   +--Bearer JWT (15 min)--> FastAPI (Python host) -- scraper library -- Mistral API
        REST + SSE                verifies via /api/auth/jwks
```

- `web/` is the Next.js app. It owns passwords, sessions and invites. It has no scraping logic.
- `src/scraper/api/` is the FastAPI service. It has no user table. It verifies the JWT, takes `sub` as `user_id`, and scopes every query to it.
- `src/scraper/` stays the library (agent, tools, fetch, guards, LLM clients). `Session` is already UI-independent.
- Postgres has one role per schema so the API cannot read password hashes.

### Token flow

The browser asks Better Auth for a JWT with `authClient.token()` (15 minute lifetime, EdDSA by default). FastAPI fetches `/api/auth/jwks`, caches it, selects the key by `kid`, refetches on an unknown `kid`, and checks signature, `iss`, `aud` and expiry. A request with no valid token gets `401`. The browser calls FastAPI directly because agent event streams would hit Vercel's function duration limits if proxied.

The JWT plugin docs show only JavaScript verification examples; the Python side (PyJWT with `cryptography`) follows the same steps and is verified by tests against a locally generated JWKS.

## Data model (Postgres, `app` schema)

`SessionStore` (SQLite) is replaced by `PgSessionStore` with the same method surface, every query scoped by `user_id`.

| Today (SQLite) | Hosted |
|---|---|
| `meta` (session id, run counter) | `sessions(user_id PK, session_id, run_counter)`. One current session per user; New Session wipes it, as today |
| `pages`, `rows`, `history` | Same columns plus `user_id`, indexed `(user_id, position)` |
| `run` (single row) | `runs(user_id PK, data jsonb)` |
| `prefs` | `prefs(user_id, key, value)`, PK on both |
| none | `usage(user_id, day, model_calls, pages_fetched)` |
| `checkpoints.db` | `AsyncPostgresSaver`. Thread ids are `user_id:session_id:run` |

Stored page size, stored pages per user and result rows per user are capped.

## Process model

`Session` keeps its busy flag, cancel event and follow-up inbox in memory, so v1 runs **one API process** holding a `Session` per active user. A session loads from Postgres on first request and is evicted when idle and not running. Scaling out later requires sticky routing by user.

The store stays synchronous (psycopg pool). Its queries are single-row and small. If load tests show event-loop stalls, store calls move to a thread pool; that change is contained in the store.

## API

All routes require the bearer JWT and act only on the caller's session.

- `GET /state`: pages, rows, history, run, prefs, models.
- `POST /scrape`, `POST /session/new`, `PUT /prefs/{key}`.
- Streaming (SSE): `POST /ask`, `POST /agent/run`, `POST /agent/resume`, `POST /agent/proceed`. Events are today's `Event` and `AgentEvent` serialized as JSON.
- `POST /agent/stop`, `POST /agent/discard`, `POST /agent/steer`.
- `GET /rows.csv`, `GET /rows.json`.
- `DELETE /account-data`: called by Next.js when a user is deleted; deletes all that user's `app` rows. Authorized with a shared service secret, not a user JWT.
- `409` when the session is busy (replaces the `SessionBusy` banner). `429` when a limit is hit.

If the browser disconnects mid-run, the run stops and is marked resumable, and the user presses Resume. The existing crash and stop recovery covers this. Running to completion in the background with a reattachable event log is out of scope for v1.

Hosted mode exposes only the Mistral provider. The provider picker is removed from the UI.

## Hosting protections

### SSRF

A `hosted` config flag turns on a public-only guard in `fetch.py`:

- Allow only http and https. Reject embedded credentials. Allow only ports 80, 443, 8080 and 8443.
- Resolve the hostname and reject private, loopback, link-local, multicast and reserved addresses, including IPv6 and IPv4-mapped forms and numeric host tricks (`0x7f.1`, `2130706433`). `localhost` is no longer treated as a default-http host.
- Redirects: Scrapling's `Fetcher` takes `follow_redirects="safe"` (its default), which makes curl refuse a redirect to a private or loopback address; this was confirmed by running it against a local redirecting server. The fetcher passes it explicitly, with a redirect cap of 5, and a test pins both.
- Hosts written as numbers are rejected unless they are plain dotted-decimal (`0177.0.0.1`, `0x7f.1` and `2130706433` are refused), because the system resolver and curl can read them differently.
- DNS rebinding cannot be fully closed in application code, so the Python host must also run with network egress rules that block private ranges and the cloud metadata address. This is required, not optional.

### Fetch modes

`Config.allowed_modes` is `("fast",)` when hosted. `fetch()` and the agent's `fetch_page` tool reject other modes, and the agent prompt stops suggesting dynamic and stealthy fallbacks.

### Limits

All counted per user per day in `usage`, plus a global cap on simultaneous runs:

- Daily model calls and daily pages fetched. Hitting either ends the run with a "daily limit, resets 00:00 UTC" error, and the run stays resumable. Real token counts from Mistral usage data are a later refinement.
- Caps on stored pages, page size and result rows.
- Per-user request rate limit on non-streaming routes. `429` when the global run cap is full.
- The 0.5 second fetch delay floor stays. The terms include an acceptable-use line because the server's IP address is the one visiting target sites.

### Secrets and privacy

- The Mistral key exists only in the Python host environment. The Better Auth secret and auth database credentials exist only in Vercel.
- The FastAPI entry point repeats the forced tracing-off from `__main__.py` so LangSmith never receives page text.
- Page content and prompts are never logged. Errors log ids and statuses only.
- The UI always states that page text is sent to Mistral.
- Deleting an account deletes that user's `app` rows through `DELETE /account-data`.

Prompt-injection defenses (allowlist, step and page budgets, untrusted-content instructions) are unchanged.

## Auth and invites (Next.js)

- `web/lib/auth.ts`: Better Auth with email and password, mandatory email verification, plugins `jwt`, `admin`, `nextCookies`, Postgres through the Kysely adapter, mounted at `/api/auth/*`.
- **Invite-only signup.** An admin creates an invite: a normalized email, a single-use token and an expiry. The invite email links to `/sign-up?invite=<token>`. A `hooks.before` check on `/sign-up/email` rejects the request unless the token is valid, unexpired, unused and matches the submitted email. An `after` hook marks the invite used. The token (not just the email) is required so nobody can register an invitee's address first. The organization plugin is not used: its invitations control membership, not account creation. The exact hook API is confirmed during planning.
- **First admin.** `ADMIN_EMAIL` seeds the first invite and the admin role.
- **Routes:** `/sign-in`, `/sign-up`, `/forgot-password`, `/reset-password`, `/verify-email`, `/` (the app), `/admin`.
- **Gating.** Next.js middleware does a fast cookie check; server components verify the session.
- **Admin page.** Create and revoke invites; list, ban and unban users (admin plugin).
- **Email.** `sendEmail()` for invites, verification and password reset, backed by Resend.

## UI

Everything in the current screen carries over:

- Header: URL bar, fetch mode, follow links, depth, Scrape and Cancel, New Session, account menu (replaces the provider dropdown), standing "pages are sent to Mistral" notice.
- Left pane: scraped pages list; "what the model reads" preview.
- Right pane: chat thread with collapsible tool log and reasoning, queued-message bubbles, results table with CSV and JSON download, notices, pause card (ask with options, limit continue/wrap up/stop, allow/deny), composer with Agent switch, send and stop.
- Footer: status, model picker, context size, daily usage meter (calls and pages used).
- Resume card after a dropped connection or interrupted run.

Implementation notes:

- The API client caches the JWT until one minute before expiry and retries once on `401`. Streaming uses `fetch` with a stream reader because `EventSource` cannot send a bearer header.
- Server state through TanStack Query from `GET /state`. The live run is a reducer fed by the event stream.
- CORS allows the production origin and a pattern for Vercel preview URLs.

## Testing

- Python (pytest): fake JWKS; user isolation (user A can never read or control user B's session or run); SSRF cases (private IPs, redirects to private hosts, numeric host forms); usage limits; existing agent and session tests run unchanged against `PgSessionStore`.
- Web: Vitest for the event reducer, stream parser and token cache. Playwright end to end: invite signup, sign in, a full run against a fake model, a pause card, stop and resume, and an uninvited signup being rejected.

## Out of scope for v1

Organizations and shared teams; saved-session history; background runs that survive disconnects; dynamic and stealthy fetch modes; multi-process scaling; real token-based billing; social login.

## Risks

- Verifying Better Auth JWTs from Python is a pattern, not a documented recipe (its docs show only JavaScript). Covered by tests against a locally generated JWKS.
- Network egress rules are an operations requirement outside the code. If the host cannot provide them, the SSRF guard alone is not sufficient for a public launch.
- Single-process design caps concurrent load. The global run cap protects it.
