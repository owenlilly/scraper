# Page Chat

Tell a model in plain English what to collect from a website. It fetches the pages with [Scrapling](https://github.com/D4Vinci/Scrapling), reads them, asks you when something is unclear, and hands back an answer plus a table you can download.

Page Chat is a hosted, invite-only service. An administrator invites people by email; each person has their own account, session, results and daily allowance, kept apart from everyone else's.

## What it does

- **Tell it what to collect.** "Go to shop.example/plans and collect every plan with its price." Say how, too: "use the `.price` selector", "only the /products pages".
- **It asks when it is stuck.** The agent pauses with a question when your instruction is ambiguous, a login or CAPTCHA blocks a page, or a choice changes the outcome. It also pauses when it hits its step, page or repeat limit (continue, wrap up, or stop) and before it fetches a site you did not name (allow or do not allow).
- **Talk to it while it works.** Send a note ("skip the blog pages", "also grab the SKU") and the agent reads it before its next step, keeping what it has gathered. The note shows as waiting until it is read. Press Stop and anything unread goes back into the box.
- **Nothing is lost.** Your session (pages, results, conversation) and any paused, stopped or interrupted run are saved. Close the tab, come back, and press Resume or answer the question that was waiting.
- **Results you can use.** Collected rows appear in a table, in the column order the agent chose, with CSV and JSON download.
- **Ask about the pages.** Switch to "Ask about the pages" to read a page yourself and ask questions about it, with no tools. Use this for models without tool calling.

## How it is built

| Part | Where | Does |
|---|---|---|
| Web app | `web/` (Next.js, Better Auth) | The screens, accounts, email confirmation, invitations, and the short-lived tokens the browser sends to the API |
| API | `src/scraper_api/` (FastAPI) | One live session per signed-in person, runs streamed to the browser as server-sent events, daily limits |
| Agent library | `src/scraper/` | The agent, its tools, fetching, the guards against reaching private networks, the model clients |
| Database | Postgres | A schema `auth` for the web app and a schema `app` for the API, so the API cannot read password hashes |

## Run it on your machine

You need [uv](https://docs.astral.sh/uv/) with Python 3.14, Node 20.9 or later, Postgres, and a model server. For development use [LM Studio](https://lmstudio.ai/) with a chat model that supports tool calling (a Ministral 3 8B build works), so no page text leaves your machine and nothing is billed.

1. Make a database and read [docs/hosted-deploy.md](docs/hosted-deploy.md) for the two roles used in production. For a first run, one database and one user are enough.
2. Start the API, from a directory of your choice:

   ```bash
   SCRAPER_HOSTED=true SCRAPER_PROVIDER=lmstudio SCRAPER_BASE_URL=http://127.0.0.1:1234/v1 \
   SCRAPER_DATABASE_URL=postgresql://user@localhost/scraper \
   SCRAPER_JWKS_URL=http://localhost:3000/api/auth/jwks SCRAPER_JWT_ISSUER=http://localhost:3000 SCRAPER_JWT_AUDIENCE=http://localhost:8080 \
   SCRAPER_SERVICE_SECRET=<a long random string> SCRAPER_CORS_ORIGINS=http://localhost:3000 \
   uv run python -m scraper_api
   ```

3. Start the web app, with the matching settings in `web/.env.local` (copy `web/.env.example`; the table in [docs/hosted-deploy.md](docs/hosted-deploy.md) lists which setting pairs with which):

   ```bash
   cd web && npm install && npm run migrate && npm run dev
   ```

   With `EMAIL_TRANSPORT=log` (the development default) invitation and confirmation links are printed in the terminal running `npm run dev`.
4. Open http://localhost:3000 and sign up with the address in `ADMIN_EMAIL`. That first account is the administrator and needs no invitation; everyone after it does.

`compose.yaml` runs Postgres and the API in containers (`podman-compose up --build`); the web app runs separately.

## Instruction cookbook

| You want | Say something like |
|---|---|
| A table of items | "Go to https://shop.example/products, open each product page, and collect name, price and url." |
| A specific selector | "On https://example.com/list use the selector `.row .title` and give me the first 20 titles." |
| To be consulted | "Find the contact email for the support team. Ask me if there is more than one." |
| A follow-up | In "Ask about the pages": "Which of those costs less than 50 dollars?" (it reuses the pages already read) |
| To change course mid-run | While it works, send: "Only the cheapest plan, please." |

The agent may fetch only sites you named in the instruction, the "Start from" address, or pages already read. Anything else asks you first.

## Privacy and safety

- **With the hosted Mistral provider, the text of every page the model reads is sent to Mistral.** With a local model server, it goes only to that server. The API turns LangSmith and other cloud tracing off at start-up.
- Page text and prompts are never written to a log.
- The API refuses addresses on private networks, and the host must also block them at the network level: see [docs/hosted-deploy.md](docs/hosted-deploy.md), section 3.
- Scraped pages are untrusted: the agent is told never to follow instructions found inside them, and its allowlist and step budgets limit what a hostile page could make it do. That reduces the risk of prompt injection but does not remove it.
- The agent's answers are shown as formatted text only: no images, no scripts, and only ordinary links.

## Configuration

The API reads environment variables (`SCRAPER_<NAME>`), or a `.env` file in the directory it runs from; real variables win over the file. The settings for a hosted deployment are in [docs/hosted-deploy.md](docs/hosted-deploy.md). These tune the agent:

| Variable | Default | Meaning |
|---|---|---|
| `SCRAPER_AGENT_MAX_STEPS` | `25` | Model steps per round before the agent pauses to ask |
| `SCRAPER_AGENT_MAX_PAGES` | `30` | Page fetches per round before it pauses to ask |
| `SCRAPER_TOOL_RESULT_TOKENS` | `3000` | Size cap for each tool result |
| `SCRAPER_FALLBACK_CONTEXT` | `16384` | Context size assumed when the model does not report one |
| `SCRAPER_CONTEXT_FRACTION` / `SCRAPER_REPLY_RESERVE` / `SCRAPER_HISTORY_RESERVE` | `0.7` / `2048` / `3000` | How much of the context window each part of a prompt may use |
| `SCRAPER_CRAWL_MAX_PAGES` / `SCRAPER_CRAWL_DEPTH` / `SCRAPER_REQUEST_DELAY` | `20` / `1` / `0.5` | Limits for reading linked pages. `SCRAPER_REQUEST_DELAY` is also the minimum pause between the agent's fetches: the model can raise it with `set_fetch_delay` (up to 30s) when a site pushes back, but never go below it |
| `SCRAPER_FETCH_TIMEOUT` | `20` | Seconds per page |

## Tests

`make help` lists the common tasks (`make test`, `make e2e-ui`, `make api`, `make web`, and so on). `make test-db` starts a throwaway Postgres in Podman with the scratch databases the tests use.

```bash
uv run pytest
```

The Postgres tests are skipped unless `SCRAPER_TEST_DATABASE_URL` points at a database they may fill and empty (use a scratch one). A run with it unset still reports green, so check the summary says `0 skipped` before you trust it:

```bash
podman run -d --name scraper-test-db -e POSTGRES_PASSWORD=test -e POSTGRES_DB=scraper_test -p 127.0.0.1:55432:5432 docker.io/library/postgres:17
export SCRAPER_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/scraper_test
uv run pytest -rs
```

The web app has its own tests, and two end-to-end checks that start the real servers: see [web/README.md](web/README.md).
