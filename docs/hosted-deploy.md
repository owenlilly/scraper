# Running the hosted API

The hosted service is two programs and one database:

- the web app (Next.js, on Vercel), which owns accounts, sign-in and invitations;
- this API (`python -m scraper_api`), which owns each person's sessions, runs and results;
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
REVOKE ALL ON SCHEMA public FROM PUBLIC;
```

The API creates its own tables on start-up (the LangGraph checkpoint tables included). The web app creates the `auth` tables with Better Auth's migration. Check the separation at any time: connected as `scraper_api`, `SELECT 1 FROM auth."user"` must fail with `permission denied for schema auth`.

## 2. Settings

Set these in the API's environment (or a `.env` file next to where it runs). Real environment variables win over the file.

| Variable | Meaning |
|---|---|
| `SCRAPER_HOSTED` | `true`. The API refuses to start without it |
| `SCRAPER_DATABASE_URL` | `postgresql://scraper_api:...@host/scraper` |
| `SCRAPER_API_KEY` | Your Mistral key. It exists only on this host, never in Vercel |
| `SCRAPER_JWKS_URL` | `https://<web app>/api/auth/jwks` |
| `SCRAPER_JWT_ISSUER` | The web app's base URL, exactly as Better Auth issues it |
| `SCRAPER_JWT_AUDIENCE` | The audience the web app puts in its tokens |
| `SCRAPER_SERVICE_SECRET` | A long random string shared with the web app, for account deletion |
| `SCRAPER_CORS_ORIGINS` | Comma-separated browser origins allowed to call the API, such as `https://app.example.com` |
| `SCRAPER_CORS_ORIGIN_REGEX` | Optional pattern for preview deployments, such as `https://app-.*\.vercel\.app` |
| `SCRAPER_ALLOWED_MODELS` | Optional comma-separated model ids people may choose. Set it to keep costs predictable |
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

Health check: `GET /health` returns `{"ok": true}` with no sign-in.

## 5. Things to know

- **Memory limit.** The API refuses request bodies over 64 KB, but it cannot limit how large a page it downloads for someone. Run the container with a memory limit (for example `podman run --memory 2g`) so one hostile page cannot take the host down.

- **No access log.** The API does not log requests, so page addresses and prompts never reach a log file. It logs failures by exception type only.
- **One process.** Restarting it ends any run that is streaming. The run is saved and the person presses Resume.
- **Rotating the Mistral key.** Change `SCRAPER_API_KEY` and restart.
- **Deleting a person.** The web app calls `DELETE /account-data` with `X-Service-Secret`. That removes their session, pages, results, history, runs, preferences, usage counts and agent checkpoints.
- **Costs.** The per-person daily limits count model calls and pages, not tokens. A call's size is bounded by the model's context window, but set `SCRAPER_ALLOWED_MODELS` to cheap models to keep the worst case small.
