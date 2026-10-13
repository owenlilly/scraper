# Running the hosted API

The hosted service is two programs and one database:

- the web app (Next.js, on Vercel), which owns accounts, sign-in and invitations;
- this API (`python -m scraper`), which owns each person's sessions, runs and results;
- Postgres, with one schema for each program.

This page covers the API and the database. It is a single process on purpose: every signed-in user's live session is kept in memory, so do not run more than one copy until sessions are routed by user.

## 1. Database

Create the database and two roles. The API role owns only the `app` schema, so even a bug in the API cannot read password hashes in `auth`. Run this once as an administrator, with real passwords:

```sql
CREATE DATABASE scraper;
\c scraper
CREATE ROLE scraper_api LOGIN PASSWORD 'change-me';
CREATE ROLE scraper_web LOGIN PASSWORD 'change-me-too';
GRANT CONNECT ON DATABASE scraper TO scraper_api, scraper_web;
CREATE SCHEMA app AUTHORIZATION scraper_api;
CREATE SCHEMA auth AUTHORIZATION scraper_web;
CREATE SCHEMA pipeline AUTHORIZATION scraper_api;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
```

The API creates its own tables on start-up (the LangGraph checkpoint tables included) and brings the `pipeline` schema up to date. The web app creates the `auth` tables with Better Auth's migration. The spiders page needs the API to know whether someone is an administrator, so once the web app's first migration has created `auth."user"`, give the API role exactly three columns of it and nothing else:

```sql
GRANT USAGE ON SCHEMA auth TO scraper_api;
GRANT SELECT (id, role, banned) ON auth."user" TO scraper_api;
```

Check the separation at any time: connected as `scraper_api`, `SELECT email FROM auth."user"` must fail with `permission denied for table user`, and so must any read of `auth.account` (where password hashes live).

## 2. Settings

Set these in the API's environment (or a `.env` file next to where it runs). Real environment variables win over the file.

| Variable | Meaning |
|---|---|
| `SCRAPER_HOSTED` | `true`. The API refuses to start without it |
| `SCRAPER_DATABASE_URL` | `postgresql://scraper_api:...@host/scraper` |
| `SCRAPER_API_KEY` | Your Mistral key. It exists only on this host, never in Vercel |
| `SCRAPER_PROVIDER` | Optional. `mistral` (the default when hosted) or `lmstudio`, to use a model server you run yourself, such as LM Studio or any server with the same API. A local model needs no `SCRAPER_API_KEY`; set `SCRAPER_BASE_URL` to its address (default `http://localhost:1234/v1`). Page text then goes only to that server |
| `SCRAPER_PROVIDERS` | Optional. Comma-separated model servers people may choose between in the app, besides `SCRAPER_PROVIDER`, such as `lmstudio,mistral`. Each person's choice is saved for them and can change only while nothing is running. Listing `mistral` needs `SCRAPER_API_KEY`; listing `lmstudio` lets everyone who signs in use the server at `SCRAPER_BASE_URL`, so list it only when that is meant. The app tells people when page text goes to a hosted provider. `SCRAPER_ALLOWED_MODELS` applies to every provider |
| `SCRAPER_JWKS_URL` | `https://<web app>/api/auth/jwks` |
| `SCRAPER_JWT_ISSUER` | The web app's base URL, exactly as Better Auth issues it |
| `SCRAPER_JWT_AUDIENCE` | The audience the web app puts in its tokens |
| `SCRAPER_SERVICE_SECRET` | A long random string shared with the web app, for account deletion |
| `SCRAPER_CORS_ORIGINS` | Comma-separated browser origins allowed to call the API, such as `https://app.example.com` |
| `SCRAPER_CORS_ORIGIN_REGEX` | Optional pattern for preview deployments, such as `https://app-.*\.vercel\.app` |
| `SCRAPER_ALLOWED_MODELS` | Optional comma-separated model ids people may choose. Set it to keep costs predictable |
| `SCRAPER_DEBUG` | Defaults to `false`. Writes a local log for review: each fetch (address, mode, status, timing, size, the start of the page text), browser action and the requests it refused, search, tool call, fetch-mode switch, context summary, step limit and run start and end, one JSON line each, in `SCRAPER_DEBUG_DIR` (default `debug-logs`, one file a day, readable by its owner only). Keys, tokens and cookies are never written, but the log **does** hold what people searched for and which pages they read, so it is for the person running the server and should not be left on over other people's data without telling them. The API prints a notice at start-up when it is on. `SCRAPER_DEBUG_BODY_CHARS` (default 500) limits the text each line keeps, and 0 keeps none. The folder is not rotated or deleted for you |
| `SCRAPER_WEB_SEARCH` | Defaults to `true`. Gives the agent a `web_search` tool that searches the web through Exa's hosted MCP server (`SCRAPER_EXA_URL`, default `https://mcp.exa.ai/mcp`, open to use without a key; `SCRAPER_EXA_API_KEY` is optional and raises its rate limit). **The search words are sent to Exa**, which is a third party; page text is not. Each search costs a page of the page budget and of the person's daily allowance. Results are addresses and snippets: the agent still has to fetch a page to read it, and a site the person did not name asks them for approval first. Set `false` to remove the tool |
| `SCRAPER_BROWSER` | Defaults to `true`. Lets the agent use a real browser (Playwright, through Scrapling) for four things: `browser_submit_form` (type into a form and press its button), `browser_click` (click a button, link or tab, such as "Load more"), `browser_scroll` (scroll so lazy content loads) and `browser_network` (list the background requests a page makes, such as the JSON behind a list). It works whatever `SCRAPER_FETCH_MODES` allows. It needs browsers installed on the host (`scrapling install`; the container image does not include them, so there it answers that the browser is missing until you add them), and each use opens a fresh browser, so mind the memory limit. The browser may load only public addresses and move only between sites the person named or approved, and every use costs a page of the daily allowance. Each person has a "Use the browser" switch in the app; this setting is the operator's override. Set `false` to remove the tools |
| `SCRAPER_COMPACT_CONTEXT` | Defaults to `true`. When a run outgrows the model's context window, the model writes notes on the older work (the instruction, the pages fetched, the rows added, the person's answers, what remains) and the last two steps stay word for word. It costs one model call of the daily allowance each time. If the notes cannot be made, or are not enough, it falls back to replacing old tool results with a stub, and a run that still does not fit ends as before. Set `false` to use the stub only |
| `SCRAPER_FETCH_FALLBACK` | Defaults to `true`. When a page is blocked (HTTP 401, 403 or 429) or comes back nearly empty, `fetch_page` retries it in the more capable modes that `SCRAPER_FETCH_MODES` allows (`fast`, then `dynamic`, then `stealthy`) before giving up, and tells the model which it used. Each attempt uses a page of the page budget and of the daily allowance. With only `fast` allowed there is nothing to retry with. A missing page, a dead address or a timeout is not retried. Set `false` to leave the choice to the model |
| `SCRAPER_FETCH_MODES` | Defaults to `fast` when hosted. Browser modes need browsers installed and per-user limits first |
| `SCRAPER_DAILY_MODEL_CALLS`, `SCRAPER_DAILY_PAGES` | Per person per UTC day. Defaults 300 and 300 |
| `SCRAPER_MAX_CONCURRENT_RUNS` | Streams in flight across everyone. Default 5 |
| `SCRAPER_HOST`, `SCRAPER_PORT` | Where it listens. Default `127.0.0.1:8080`. The container image listens on `0.0.0.0` |

## 3. Network egress (required)

The API fetches addresses people give it. The application refuses private, loopback, link-local and metadata addresses and redirects to them, but a hostname can answer differently between the check and the connection (DNS rebinding), and the check cannot see inside a browser or a library. **The host must also refuse to send traffic from the API to private ranges.** Treat a public launch without this as unsafe.

Block outbound traffic from the API's container or machine to:

- `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` (private networks)
- `100.64.0.0/10` (carrier-grade NAT, which Tailscale and some clouds use)
- `169.254.0.0/16` (link-local, including the cloud metadata address `169.254.169.254`)
- `127.0.0.0/8`, `::1`, `fc00::/7`, `fe80::/10`

and allow only: DNS, the public internet, and the one private address of your database. On a cloud host also require the hardened metadata service (on AWS, IMDSv2 with a hop limit of 1) so a container cannot reach credentials even if a rule is missed.

An example for a Linux host that forwards a container network (`10.88.0.0/16`). It is an example to adapt, not a tested drop-in, because chains, interface names and the container runtime differ:

```
table inet scraper_egress {
  chain forward {
    type filter hook forward priority -10; policy accept;
    ip saddr 10.88.0.0/16 ip daddr 10.0.5.20 tcp dport 5432 accept   # the database
    ip saddr 10.88.0.0/16 ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 169.254.0.0/16, 127.0.0.0/8 } drop
    ip6 saddr fd00::/8 ip6 daddr { fc00::/7, fe80::/10, ::1 } drop
  }
}
```

**Prove it works.** From the API's host (inside the container if it runs in one), give `scripts/check_egress.py` a few private addresses that really would answer, such as the database's address on a port you did not allow and a service on your network. The check passes only when every connection fails:

```bash
python scripts/check_egress.py 10.0.5.20:22 169.254.169.254:80
```

## 4. Running

```bash
podman build -t page-chat-api -f Containerfile .
podman run --env-file .env -p 8080:8080 page-chat-api
```

For a local stack with Postgres, copy `.env.example` to `.env`, set `POSTGRES_PASSWORD` and the hosted settings, and run `podman-compose up --build`. The local compose file uses one database user for both schemas; production should use the two roles above.

To develop or test on a local model (no Mistral account, no cost), start LM Studio with a model loaded, then run the API with `SCRAPER_PROVIDER=lmstudio SCRAPER_BASE_URL=http://127.0.0.1:1234/v1` and, if you want to limit the choice, `SCRAPER_ALLOWED_MODELS` set to the model ids to offer. Choose a model that supports tool calling, such as a Ministral 3 8B build. The web app shows the "pages are sent to" notice only for a hosted provider, so with a local model it stays out of the way.

Health check: `GET /health` returns `{"ok": true}` with no sign-in.

## 5. Things to know

- **Memory limit.** The API refuses request bodies over 64 KB, but it cannot limit how large a page it downloads for someone. Run the container with a memory limit (for example `podman run --memory 2g`) so one hostile page cannot take the host down.

- **Access log.** The API logs one line per request (method, path, status, and the caller's address). Page addresses and prompts travel in request bodies, never in paths, so they do not reach the log; the API itself logs failures by exception type only. Set `SCRAPER_ACCESS_LOG=false` to turn the request lines off.
- **One process.** Restarting it ends any run that is streaming. The run is saved and the person presses Resume.
- **Rotating the Mistral key.** Change `SCRAPER_API_KEY` and restart.
- **Deleting a person.** The web app calls `DELETE /account-data` with `X-Service-Secret`. That removes their session, pages, results, history, runs, preferences, usage counts and agent checkpoints.
- **Costs.** The per-person daily limits count model calls and pages, not tokens. A call's size is bounded by the model's context window, but set `SCRAPER_ALLOWED_MODELS` to cheap models to keep the worst case small.

## 6. The web app

The web app (`web/`, Next.js) is deployed separately, usually on Vercel with the project's root directory set to `web`. It uses the same database, in the `auth` schema, through the `scraper_web` role from section 1.

Create its tables once, and again after upgrading Better Auth (it is safe to repeat):

```bash
cd web && DATABASE_URL=... BETTER_AUTH_SECRET=... BETTER_AUTH_URL=... API_URL=... SERVICE_SECRET=... EMAIL_TRANSPORT=resend RESEND_API_KEY=... EMAIL_FROM=... npm run migrate
```

Email goes through Resend: verify the sending domain there, create an API key, and set `RESEND_API_KEY` and `EMAIL_FROM` (such as `Page Chat <no-reply@your-domain>`). The app refuses to start in production with the `log` email transport, because that prints invitation links to the server log.

Settings that must agree between the web app and the API (the most common mistake is a trailing slash or a different scheme):

| Web app | Python API | Notes |
|---|---|---|
| `BETTER_AUTH_URL` | `SCRAPER_JWT_ISSUER` | Exactly equal, no trailing slash. Also the base of `SCRAPER_JWKS_URL`, which is `<BETTER_AUTH_URL>/api/auth/jwks` |
| `API_AUDIENCE` (default `API_URL`) | `SCRAPER_JWT_AUDIENCE` | Exactly equal |
| `SERVICE_SECRET` | `SCRAPER_SERVICE_SECRET` | The same long random string |
| `API_URL` | the API's public address | Where account deletions are sent |
| the app's own origin | `SCRAPER_CORS_ORIGINS` | So the browser may call the API directly |

Rate limits (sign-in, sign-up and password reset attempts) are kept per visitor, using the address in the `x-forwarded-for` header. Better Auth trusts that header only when it holds exactly one address. That is the case when the app is reached directly on Vercel. **If another proxy or CDN sits in front of the app** (Cloudflare, for example), the header holds more than one address and, unless you name those proxies, every visitor shares one bucket of three sign-ins per ten seconds, which would lock everyone out. Set `TRUSTED_PROXIES` to the proxies' addresses or ranges (comma-separated, such as `10.0.0.0/8, 192.0.2.7`) and the limits go back to being per visitor.

The first administrator: set `ADMIN_EMAIL` and sign up with that address. It needs no invitation and becomes the administrator. While an administrator exists, `ADMIN_EMAIL` has no special power and everyone needs an invitation (sent from the administrator's invitation routes). If the last administrator is ever deleted or demoted, that address can sign up without an invitation again.

**Sign up as the administrator as soon as you deploy.** Until you do, anyone who knows `ADMIN_EMAIL` can start that sign-up with a password of their own. The confirmation email goes to the real owner of the address, so the account is only usable if the owner clicks a confirmation link they did not ask for. If an unexpected confirmation email arrives for that address, do not click it.

### Known limitations

- **A token outlives the account for up to 15 minutes.** After someone is banned or deleted, a token they already hold still passes the API's check until it expires. A ban ends their sessions at once so they cannot get a new token, and deletion erases the API's data first, but a token in hand could create an empty session row again in that window. It holds no data.
- **Response timing can show whether an address has an account.** Password reset sends its email before answering, so a request for a real address takes longer than one for an unknown address. The responses are identical; the timing is not hidden.
- **An invitation link is a credential.** Anyone who holds the link can set the password for that address (and the confirmation email then goes to the real owner). Treat it like a password reset link.

## The spiders worker

The spiders page (`/spiders`, administrators only) queues runs; a separate worker process runs them. Without a worker a run stays queued, and the page says how to start one after 30 seconds.

- Start it with the API's environment: `python -m scraper.pipeline worker` (`make pipeline-worker` locally). `--concurrency N` runs up to N sources at once; a source never has two runs at the same time.
- It needs `SCRAPER_DATABASE_URL` and `SCRAPER_PIPELINE_BLOB_DIR`, a directory it can write (every fetched page is archived there, compressed). The API only reads the database, so it needs no blob directory. Run one worker per blob directory; the compose file mounts a volume at `/data/blobs`.
- Each run is a child process of the worker. Stopping the worker (SIGTERM or Ctrl+C) asks every run to stop, which ends them as stopped with what they saved. A worker or child that dies without finishing leaves a run that the next worker pass fails as `worker lost` after 60 seconds without a heartbeat.
- A browser-backed spider (`AsyncStealthySession`) needs the browsers installed on the worker host (`scrapling install`). The container image does not include them.
