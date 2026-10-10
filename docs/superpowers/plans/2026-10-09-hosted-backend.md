# Hosted Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the scraper's agent into a multi-user, Postgres-backed FastAPI service that verifies the web app's bearer tokens, isolates every user, limits cost and abuse, and streams runs to a browser.

**Architecture:** `src/scraper/` stays the library; this plan adds a hosted layer to it (a public-address guard, a usage meter, a hosted fetch function) and a new package `src/scraper_api/` holding Postgres persistence, JWT verification, a per-user session registry and the HTTP routes. The existing NiceGUI app is untouched and keeps working; it is removed by the later UI plan. Auth itself (accounts, invitations) belongs to the web app, in a separate plan; here the API only verifies the tokens it issues.

**Tech Stack:** Python 3.14, FastAPI, psycopg 3 with a connection pool, LangGraph's Postgres checkpointer, PyJWT with `cryptography` (EdDSA), pytest and httpx for tests, Podman for containers.

**Spec:** `docs/superpowers/specs/2026-10-09-nextjs-auth-hosted-design.md`. This is the first of three plans: (1) this backend, (2) the web app's sign-in and invitations (Better Auth), (3) the web app's UI and removal of NiceGUI. The API in this plan is the contract the other two build on.

**Where this plan departs from the spec:** it resolves the spec's one open question (Scrapling can restrict redirects, so no egress proxy is needed for them; Task 2 updates the spec), adds a numeric-host rule found while testing, and adds an `allowed_models` setting so users cannot pick an expensive model on the operator's key.

## Global Constraints

- Python `>=3.14`, as `pyproject.toml` already says.
- Per-user isolation only: every table the API owns has a `user_id`, and every query is scoped by it. No organizations in v1.
- Hosted mode offers only the Mistral provider; LM Studio is for local development.
- Tokens are bearer JWTs from Better Auth, 15 minutes long, signed with EdDSA, verified through the web app's JWKS: signature, `iss`, `aud` and `exp` are all required.
- One API process (`workers=1`): each user's live `Session` is held in memory.
- Fetchable URLs are http or https only, with no credentials, on ports 80, 443, 8080 or 8443, resolving only to public addresses. Hosted mode allows the `fast` fetch mode only.
- Per-user daily limits on model calls and fetched pages, caps on stored pages, page size and result rows, a global cap on simultaneous runs, and a per-user request rate limit.
- Checkpoint thread ids start with `<user id>:`, so they cannot collide between users and can be deleted by prefix.
- The Mistral key lives only in the API's environment. Page content and prompts are never logged. LangChain and LangSmith tracing are forced off in the new entry point.
- CORS allows only the configured web origins and sends no credentials; tokens travel in the `Authorization` header.
- Status codes: `401` no or bad token, `409` session busy, `429` any limit, `422` invalid body. Every error body is `{"error": {"code", "message"}}`.
- `DELETE /account-data` is authenticated by `X-Service-Secret`, never by a user token.
- Never write an em dash (the long dash) in code comments, documents or messages; use a colon, comma or parentheses.
- Commit messages are short imperative sentences (as in `git log`), with no attribution lines.
- Work on branch `nextjs_auth_hosted`. Never commit to `main`.

## Review Focus

These are the failure modes the spec implies that no single task's tests would otherwise hold in view, most likely first.

1. **A browser disconnect at the instant a follow-up message is delivered can lose that message.** An existing test (`tests/test_session.py::test_a_follow_up_is_not_injected_twice_when_the_delivered_flag_is_lost_in_a_crash`) fails intermittently on `main` for this reason (about 1 run in 20 on a quiet machine, more under load; reproduced outside pytest: the saved checkpoint can lag the event stream, and the message is flagged delivered when the event is emitted, not when it is durable). Hosted mode makes disconnects routine. This plan does **not** fix it. Expected behaviour: a follow-up is either read by the model exactly once or handed back to the user as a draft, never dropped. Pinned by running that test 30 times in Task 13 and reporting the count.
2. **A hostname that resolves to a public address when checked and a private one when connected (DNS rebinding).** Application code cannot close this. Expected behaviour: the host's network rules refuse the connection. Pinned by `scripts/check_egress.py` (Task 13), which must be run on the real host before launch.
3. **A daily limit reached in the middle of a run.** Expected: the run ends with a plain message giving the reset time, stays resumable, and continues the next UTC day. Pinned by `test_a_spent_allowance_ends_the_round_with_the_plain_message_and_the_thread_resumes_tomorrow` (Task 5) and `test_the_daily_model_limit_ends_an_agent_run_with_an_error_event_and_keeps_it_resumable` (Task 12).
4. **The same person using two tabs or devices.** Expected: the second request gets `409 busy` and the first run is unharmed. Pinned by `test_a_busy_session_refuses_a_second_scrape_with_a_409` (Task 12).
5. **The key server being unreachable, or its keys being rotated.** Expected: tokens signed with already-seen keys keep working, an unknown key triggers at most one refresh every 30 seconds, and an outage with nothing cached gives `503`, not `401`. Pinned by the rotation, outage and rate-limited-refresh tests in `tests/test_auth.py` (Task 8) and `test_an_unreachable_key_server_is_a_503_not_a_401` (Task 11).

---

### Task 0: Branch, dependencies and a test database

Set up everything the later tasks stand on. Nothing here changes behaviour.

**Files:**
- Modify: `pyproject.toml`, `uv.lock`
- Create: `src/scraper_api/__init__.py`

**Interfaces:**
- Consumes: Nothing.
- Produces: The `scraper_api` package exists and imports; the new dependencies are installed; a Postgres the tests may use is reachable through `SCRAPER_TEST_DATABASE_URL`.

- [ ] **Step 1: Check the branch**

This plan is implemented on the branch that already holds the spec. If the output is `main`, stop and switch to `nextjs_auth_hosted`; never commit to `main`.

```bash
git branch --show-current
```

Expected: PASS (output contains `nextjs_auth_hosted`)

- [ ] **Step 2: Run the existing tests to get a baseline**

Everything passes before you start. One test, `test_a_follow_up_is_not_injected_twice_when_the_delivered_flag_is_lost_in_a_crash`, fails intermittently on `main` (see Review Focus 1). If only that test fails, run it again; if it passes on rerun, carry on. Any other failure means stop and report it.

```bash
uv run pytest -q
```

Expected: PASS (output contains `passed`)

- [ ] **Step 3: Add the dependencies**

`fastapi` and `uvicorn` are already installed through NiceGUI, but this service must not depend on that, so they are listed directly.

```bash
uv add 'psycopg[binary,pool]>=3.2,<4' 'langgraph-checkpoint-postgres>=3,<4' 'pyjwt[crypto]>=2.10,<3' 'fastapi>=0.115' 'uvicorn>=0.30'
```

- [ ] **Step 4: Package the new code with the old**

In `pyproject.toml`, replace:

```toml
packages = ["src/scraper"]
```

with:

```toml
packages = ["src/scraper", "src/scraper_api"]
```

- [ ] **Step 5: Create the package**

```python
"""The hosted API: a FastAPI service that wraps the scraper library for many signed-in users."""
```

- [ ] **Step 6: Get a Postgres for the tests**

The database tests need a scratch Postgres that they may fill and empty. Use Podman (this repository prefers it to Docker):

```bash
podman run -d --name scraper-test-db -e POSTGRES_PASSWORD=test -e POSTGRES_DB=scraper_test -p 127.0.0.1:55432:5432 docker.io/library/postgres:17
export SCRAPER_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/scraper_test
```

A Postgres you already run works too: create an empty database such as `scraper_test` and point the variable at it. Never point it at a database that holds real data: the tests truncate every table in the `app` schema.

**The trap:** when `SCRAPER_TEST_DATABASE_URL` is unset, those tests are skipped and the run still ends green. Every time this plan says to run the database tests, read the summary line and confirm it says **0 skipped**. Run `uv run pytest -rs` to see which were skipped and why.


- [ ] **Step 7: Confirm the database is reachable**

Expected: it prints `database reachable`. A connection error means the variable is unset in this shell or the server is not running.

```bash
uv run python -c "import os, psycopg; psycopg.connect(os.environ['SCRAPER_TEST_DATABASE_URL']).close(); print('database reachable')"
```

Expected: PASS (output contains `database reachable`)

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/scraper_api/__init__.py
git commit -m "Add the scraper_api package and its dependencies"
```

### Task 1: Hosted configuration

Everything the hosted service needs to know comes from the environment, through the same `SCRAPER_` mechanism as today. Hosted mode refuses to start half-configured. One bug to avoid: `bool('false')` is `True` in Python, so booleans are parsed from spelled-out values.

**Files:**
- Modify: `src/scraper/config.py`
- Test: `tests/test_config.py`, `tests/test_fetch.py`

**Interfaces:**
- Consumes: `scraper.config.Config`, `load(env)` as they are.
- Produces: `Config` gains `hosted`, `database_url`, `jwks_url`, `jwt_issuer`, `jwt_audience`, `service_secret`, `cors_origins`, `cors_origin_regex`, `fetch_modes`, `allowed_models`, `daily_model_calls`, `daily_pages`, `max_stored_pages`, `max_page_chars`, `max_result_rows`, `max_concurrent_runs`, `requests_per_minute`, and properties `allowed_modes`, `allowed_model_list`, `cors_origin_list`. `load` parses booleans, forces the Mistral provider when hosted, and refuses to start when a required hosted setting is missing. `config.FETCH_MODES` equals `fetch.MODES`.

- [ ] **Step 1: Add to the end of `tests/test_config.py`**

```python
# ---- hosted mode --------------------------------------------------------------

HOSTED_ENV = {
    "SCRAPER_HOSTED": "true",
    "SCRAPER_DATABASE_URL": "postgresql://api@db/scraper",
    "SCRAPER_JWKS_URL": "https://web.test/api/auth/jwks",
    "SCRAPER_JWT_ISSUER": "https://web.test",
    "SCRAPER_JWT_AUDIENCE": "https://web.test",
    "SCRAPER_SERVICE_SECRET": "s3cret",
    "SCRAPER_API_KEY": "mistral-key",
}


def test_bool_env_values_are_parsed_not_truthy_strings():
    assert load({"SCRAPER_HOSTED": "false"}).hosted is False
    assert load({"SCRAPER_HOSTED": "0"}).hosted is False
    assert load({**HOSTED_ENV, "SCRAPER_HOSTED": "1"}).hosted is True
    with pytest.raises(ValueError, match="SCRAPER_HOSTED must be true or false"):
        load({"SCRAPER_HOSTED": "maybe"})


def test_hosted_mode_forces_mistral_and_only_the_fast_fetch_mode():
    cfg = load(HOSTED_ENV)
    assert cfg.hosted and cfg.provider == "mistral"
    assert cfg.available_providers == ("mistral",)
    assert cfg.allowed_modes == ("fast",)


def test_local_mode_allows_every_fetch_mode():
    assert load({}).allowed_modes == ("fast", "dynamic", "stealthy")


def test_fetch_modes_can_be_widened_and_are_validated():
    assert load({**HOSTED_ENV, "SCRAPER_FETCH_MODES": "fast, dynamic"}).allowed_modes == ("fast", "dynamic")
    with pytest.raises(ValueError, match="SCRAPER_FETCH_MODES"):
        load({**HOSTED_ENV, "SCRAPER_FETCH_MODES": "fast,teleport"})


@pytest.mark.parametrize(
    "missing",
    ["SCRAPER_DATABASE_URL", "SCRAPER_JWKS_URL", "SCRAPER_JWT_ISSUER", "SCRAPER_JWT_AUDIENCE", "SCRAPER_SERVICE_SECRET", "SCRAPER_API_KEY"],
)
def test_hosted_mode_refuses_to_start_without_its_secrets_and_endpoints(missing):
    env = {k: v for k, v in HOSTED_ENV.items() if k != missing}
    with pytest.raises(ValueError, match=missing):
        load(env)


def test_hosted_secrets_are_not_shown_in_repr():
    text = repr(load(HOSTED_ENV))
    for secret in ("s3cret", "mistral-key", "postgresql://api@db/scraper"):
        assert secret not in text


def test_cors_origins_are_split_and_trimmed():
    cfg = load({**HOSTED_ENV, "SCRAPER_CORS_ORIGINS": "https://a.test, https://b.test ,"})
    assert cfg.cors_origin_list == ("https://a.test", "https://b.test")
    assert load({}).cors_origin_list == ()


def test_hosted_limit_defaults_are_set():
    cfg = load(HOSTED_ENV)
    assert (cfg.daily_model_calls, cfg.daily_pages, cfg.max_concurrent_runs) == (300, 300, 5)
    assert (cfg.max_stored_pages, cfg.max_page_chars, cfg.max_result_rows, cfg.requests_per_minute) == (60, 200_000, 2000, 120)


def test_allowed_models_are_split_and_trimmed_and_default_to_any():
    assert load({}).allowed_model_list == ()
    cfg = load({**HOSTED_ENV, "SCRAPER_ALLOWED_MODELS": "mistral-small-latest, ministral-8b-latest,"})
    assert cfg.allowed_model_list == ("mistral-small-latest", "ministral-8b-latest")


def test_a_missing_api_key_in_hosted_mode_is_reported_as_a_hosted_problem():
    env = {k: v for k, v in HOSTED_ENV.items() if k != "SCRAPER_API_KEY"}
    with pytest.raises(ValueError) as caught:
        load(env)
    assert "SCRAPER_HOSTED needs SCRAPER_API_KEY" in str(caught.value) and "SCRAPER_PROVIDER" not in str(caught.value)
```

- [ ] **Step 2: Add a test that keeps the two mode lists equal**

`config.py` repeats the list of fetch modes rather than importing the fetcher (importing it would load Scrapling just to read settings), so a test keeps the two in step.

```python
def test_config_fetch_modes_match_the_fetcher():
    from scraper.config import FETCH_MODES
    from scraper.fetch import MODES

    assert FETCH_MODES == MODES
```

- [ ] **Step 3: Run the tests to see them fail**

```bash
uv run pytest tests/test_config.py tests/test_fetch.py -q
```

Expected: FAIL (`failed`)

- [ ] **Step 4: Edit `src/scraper/config.py`**

In `src/scraper/config.py`, replace:

```python
MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
```

with:

```python
MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
FETCH_MODES = ("fast", "dynamic", "stealthy")  # kept equal to fetch.MODES by a test
HOSTED_REQUIRED = {
    "database_url": "SCRAPER_DATABASE_URL",
    "jwks_url": "SCRAPER_JWKS_URL",
    "jwt_issuer": "SCRAPER_JWT_ISSUER",
    "jwt_audience": "SCRAPER_JWT_AUDIENCE",
    "service_secret": "SCRAPER_SERVICE_SECRET",
    "api_key": "SCRAPER_API_KEY",
}
```

- [ ] **Step 5: Edit `src/scraper/config.py`**

In `src/scraper/config.py`, replace:

```python
    data_dir: str = "~/.page-chat"

    @property
    def available_providers(self) -> tuple[str, ...]:
        """LM Studio is always offered; Mistral once a key is set."""
        return ("lmstudio", "mistral") if self.api_key else ("lmstudio",)
```

with:

```python
    data_dir: str = "~/.page-chat"
    # Hosted, multi-user mode (the FastAPI service). Everything below is ignored when hosted is False.
    hosted: bool = False
    database_url: str = field(default="", repr=False)
    jwks_url: str = ""  # where the web app publishes its signing keys
    jwt_issuer: str = ""
    jwt_audience: str = ""
    service_secret: str = field(default="", repr=False)  # lets the web app call the account-deletion route
    cors_origins: str = ""  # comma-separated browser origins allowed to call the API
    cors_origin_regex: str = ""  # optional pattern, for preview deployments
    fetch_modes: str = ""  # comma-separated; empty means every mode locally and only "fast" when hosted
    allowed_models: str = ""  # comma-separated model ids users may pick when hosted; empty means any the provider lists
    daily_model_calls: int = 300  # per user per UTC day
    daily_pages: int = 300  # per user per UTC day
    max_stored_pages: int = 60  # per user
    max_page_chars: int = 200_000  # stored per page; longer text is cut
    max_result_rows: int = 2000  # per user
    max_concurrent_runs: int = 5  # across all users: the API is one process
    requests_per_minute: int = 120  # per user, on routes that do not stream

    @property
    def available_providers(self) -> tuple[str, ...]:
        """LM Studio is always offered locally; Mistral once a key is set. Hosted offers only Mistral."""
        if self.hosted:
            return ("mistral",)
        return ("lmstudio", "mistral") if self.api_key else ("lmstudio",)

    @property
    def allowed_modes(self) -> tuple[str, ...]:
        if self.fetch_modes.strip():
            return tuple(m.strip() for m in self.fetch_modes.split(",") if m.strip())
        return ("fast",) if self.hosted else FETCH_MODES

    @property
    def allowed_model_list(self) -> tuple[str, ...]:
        return tuple(m.strip() for m in self.allowed_models.split(",") if m.strip())

    @property
    def cors_origin_list(self) -> tuple[str, ...]:
        return tuple(o.strip() for o in self.cors_origins.split(",") if o.strip())


def _parse(kind: type, raw: str, key: str):
    if kind is bool:  # bool("false") is True, so spell the values out
        lowered = raw.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off", ""):
            return False
        raise ValueError(f"{key} must be true or false, got {raw!r}")
    try:
        return kind(raw)
    except ValueError:
        raise ValueError(f"{key} must be {kind.__name__}, got {raw!r}") from None
```

- [ ] **Step 6: Edit `src/scraper/config.py`**

In `src/scraper/config.py`, replace:

```python
            continue
        kind = type(getattr(defaults, f.name))
        try:
            overrides[f.name] = kind(raw)
        except ValueError:
            raise ValueError(f"{key} must be {kind.__name__}, got {raw!r}") from None
    provider = overrides.get("provider", defaults.provider)
```

with:

```python
            continue
        overrides[f.name] = _parse(type(getattr(defaults, f.name)), raw, key)
    if overrides.get("hosted"):
        overrides["provider"] = "mistral"  # LM Studio runs on one person's machine, not on the server
    provider = overrides.get("provider", defaults.provider)
```

- [ ] **Step 7: Edit `src/scraper/config.py`**

In `src/scraper/config.py`, replace:

```python
        overrides["api_key"] = env["MISTRAL_API_KEY"]
    if provider == "mistral":
        if not overrides.get("api_key"):
            raise ValueError(f"{ENV_PREFIX}PROVIDER=mistral needs {ENV_PREFIX}API_KEY (or MISTRAL_API_KEY)")
    return Config(**overrides)
```

with:

```python
        overrides["api_key"] = env["MISTRAL_API_KEY"]
    if provider == "mistral" and not overrides.get("hosted"):  # hosted mode reports its own, clearer, missing-key error below
        if not overrides.get("api_key"):
            raise ValueError(f"{ENV_PREFIX}PROVIDER=mistral needs {ENV_PREFIX}API_KEY (or MISTRAL_API_KEY)")
    if overrides.get("hosted"):
        for name, variable in HOSTED_REQUIRED.items():
            if not overrides.get(name):
                raise ValueError(f"{ENV_PREFIX}HOSTED needs {variable}")
    cfg = Config(**overrides)
    unknown = [m for m in cfg.allowed_modes if m not in FETCH_MODES]
    if unknown:
        raise ValueError(f"{ENV_PREFIX}FETCH_MODES has unknown mode(s) {', '.join(unknown)}; use {', '.join(FETCH_MODES)}")
    return cfg
```

- [ ] **Step 8: Run the tests to see them pass**

```bash
uv run pytest tests/test_config.py tests/test_fetch.py -q
```

Expected: PASS (output contains `passed`)

- [ ] **Step 9: Commit**

```bash
git add src/scraper/config.py tests/test_config.py tests/test_fetch.py
git commit -m "Add hosted-mode settings to the configuration"
```

### Task 2: SSRF guard and explicit safe redirects

The server will fetch addresses strangers type. This task adds the application-level check. It cannot be the only defence (see Review Focus 2), but it must be right.

**Files:**
- Create: `src/scraper/netguard.py`, `tests/test_netguard.py`
- Modify: `src/scraper/fetch.py`, `tests/test_fetch.py`, `docs/superpowers/specs/2026-10-09-nextjs-auth-hosted-design.md`

**Interfaces:**
- Consumes: `scraper.fetch.FetchError`.
- Produces: `scraper.netguard.check_public_url(url, *, resolver=socket.getaddrinfo, ports=ALLOWED_PORTS) -> None`, which raises `FetchError` unless the URL is http or https, has no credentials, uses an allowed port and resolves only to public addresses. `fetch._fast` passes `follow_redirects="safe"` and `max_redirects=5` explicitly.

- [ ] **Step 1: Create `tests/test_netguard.py`**

```python
import socket

import pytest

from scraper.fetch import FetchError
from scraper.netguard import check_public_url


def resolver_for(*addresses):
    """A getaddrinfo stand-in that answers every lookup with the given addresses."""

    def resolve(host, port, *args, **kwargs):
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]

    return resolve


PUBLIC = resolver_for("93.184.216.34")


@pytest.mark.parametrize("url", ["https://example.test/", "http://example.test/a?b=1", "https://example.test:8443/x", "http://example.test:8080/"])
def test_public_urls_on_allowed_ports_pass(url):
    check_public_url(url, resolver=PUBLIC)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "10.0.0.5", "172.16.0.1", "192.168.1.1", "169.254.169.254",  # loopback, private, cloud metadata
        "100.64.0.1",  # carrier-grade NAT (Tailscale and friends)
        "0.0.0.0", "224.0.0.1", "240.0.0.1",
        "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1",  # IPv6 loopback, link-local, unique-local, IPv4-mapped
    ],
)
def test_non_public_addresses_are_rejected(address):
    with pytest.raises(FetchError, match="not a public address"):
        check_public_url("https://example.test/", resolver=resolver_for(address))


def test_any_non_public_answer_rejects_the_host_even_when_others_are_public():
    with pytest.raises(FetchError, match="not a public address"):
        check_public_url("https://example.test/", resolver=resolver_for("93.184.216.34", "10.0.0.5"))


@pytest.mark.parametrize("url", ["ftp://example.test/", "file:///etc/passwd", "gopher://example.test/", "javascript:alert(1)"])
def test_only_http_and_https_are_allowed(url):
    with pytest.raises(FetchError, match="Only http and https"):
        check_public_url(url, resolver=PUBLIC)


@pytest.mark.parametrize("url", ["https://user:pw@example.test/", "https://user@example.test/", "http://example.test@evil.test/"])
def test_urls_with_credentials_are_rejected(url):
    with pytest.raises(FetchError, match="credentials"):
        check_public_url(url, resolver=PUBLIC)


@pytest.mark.parametrize("url", ["http://example.test:22/", "https://example.test:5432/", "http://example.test:6379/", "http://example.test:9200/"])
def test_unusual_ports_are_rejected(url):
    with pytest.raises(FetchError, match="Port"):
        check_public_url(url, resolver=PUBLIC)


def test_unresolvable_hosts_are_rejected_with_a_plain_message():
    def broken(*args, **kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    with pytest.raises(FetchError, match="Could not find"):
        check_public_url("https://nope.invalid/", resolver=broken)


def test_a_host_that_resolves_to_nothing_is_rejected():
    with pytest.raises(FetchError, match="Could not find"):
        check_public_url("https://example.test/", resolver=lambda *a, **k: [])


@pytest.mark.parametrize("url", ["http://", "https:///path", "https://:443/"])
def test_urls_without_a_host_are_rejected(url):
    with pytest.raises(FetchError):
        check_public_url(url, resolver=PUBLIC)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/", "http://LOCALHOST:8080/", "http://localhost./", "http://127.0.0.1/", "http://[::1]/",
        "http://2130706433/", "http://0x7f.1/", "http://0177.0.0.1/", "http://127.1/", "http://0/",
        "http://169.254.169.254/latest/meta-data/", "http://[::ffff:127.0.0.1]/",
    ],
)
def test_loopback_in_every_spelling_is_rejected_using_the_real_resolver(url):
    with pytest.raises(FetchError):
        check_public_url(url)


def test_the_error_names_the_host_but_never_the_resolved_address():
    with pytest.raises(FetchError) as caught:
        check_public_url("https://internal.example.test/", resolver=resolver_for("10.1.2.3"))
    assert "internal.example.test" in caught.value.message
    assert "10.1.2.3" not in caught.value.message


@pytest.mark.parametrize("host", ["0177.0.0.1", "0x7f.0.0.1", "2130706433", "127.1", "0x7f000001", "0300.0250.0.1", "1.2.3"])
def test_ambiguous_numeric_hosts_are_rejected_even_when_the_resolver_calls_them_public(host):
    # macOS reads 0177.0.0.1 as 177.0.0.1 while curl reads it as 127.0.0.1, so the guard must not rely on the resolver here
    with pytest.raises(FetchError, match="plain address"):
        check_public_url(f"http://{host}/", resolver=PUBLIC)


def test_a_plain_public_ip_literal_is_accepted():
    check_public_url("https://93.184.216.34/", resolver=resolver_for("93.184.216.34"))


@pytest.mark.parametrize("url", ["http://example.test\\@127.0.0.1/", "http://exam ple.test/", "http://example.test/\x00", "http://example.test/\nHost: x"])
def test_backslashes_spaces_and_control_characters_are_rejected(url):
    with pytest.raises(FetchError, match="valid URL"):
        check_public_url(url, resolver=PUBLIC)


def test_non_ascii_hosts_must_be_written_as_punycode():
    with pytest.raises(FetchError, match="punycode"):
        check_public_url("https://exämple.test/", resolver=PUBLIC)
    check_public_url("https://xn--exmple-cua.test/", resolver=PUBLIC)


def test_hosts_that_merely_look_hexadecimal_are_ordinary_names():
    check_public_url("https://deadbeef.cafe/", resolver=PUBLIC)
    check_public_url("https://123abc.test/", resolver=PUBLIC)
```

- [ ] **Step 2: Add to the end of `tests/test_fetch.py`**

The first test starts a local server that redirects to `127.0.0.1` and proves Scrapling's safe mode refuses it. The second pins that the fetcher passes the safe setting and a cap explicitly, so a future change of the library's default cannot silently reopen the hole.

```python
# ---- redirects never reach private addresses -----------------------------------


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        port = self.server.server_port
        if self.path == "/to-loopback":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{port}/secret")
            self.end_headers()
        elif self.path == "/loop":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{port}/loop")
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><title>secret</title><body>internal data</body></html>")

    def log_message(self, *args):
        pass


@pytest.fixture
def redirecting_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _RedirectHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_fast_mode_refuses_a_redirect_to_a_private_address(redirecting_server):
    from scraper.fetch import _fast

    with pytest.raises(Exception, match="Redirect to internal IP"):
        _fast(f"{redirecting_server}/to-loopback", 5.0)


def test_fast_mode_passes_safe_redirects_and_a_redirect_cap_explicitly(monkeypatch):
    seen = {}

    class FakeFetcher:
        @staticmethod
        def get(url, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(status=200)

    monkeypatch.setattr("scrapling.fetchers.Fetcher", FakeFetcher)
    from scraper.fetch import _fast

    _fast("https://example.test/", 5.0)
    assert seen["follow_redirects"] == "safe"
    assert seen["max_redirects"] == 5
```

- [ ] **Step 3: Run the guard tests to see them fail**

```bash
uv run pytest tests/test_netguard.py -q
```

Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 4: Run the redirect test to see it fail**

The server-based test already passes (the library default is safe); the second one fails because the argument is not passed yet.

```bash
uv run pytest tests/test_fetch.py -q -k explicitly
```

Expected: FAIL (`KeyError`)

- [ ] **Step 5: Create `src/scraper/netguard.py`**

Two details came from running the real resolver against tricky inputs, and each has a test. `http://0177.0.0.1/` is read by macOS's resolver as the public address `177.0.0.1` but by curl as octal `127.0.0.1`, so any host written as numbers must be plain dotted-decimal or it is refused. Backslashes, spaces and control characters are refused because URL parsers disagree about them.

```python
"""Hosted mode: refuse URLs that would make the server call something that is not on the public internet."""

import ipaddress
import re
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

from .fetch import FetchError

ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
DEFAULT_PORTS = {"http": 80, "https": 443}
_NUMBER = r"(?:0[xX][0-9a-fA-F]*|[0-9]+)"  # one label written as decimal, octal or hex
_NUMERIC_HOST = re.compile(rf"^{_NUMBER}(?:\.{_NUMBER})*\.?$")
_ODD_CHARACTERS = re.compile(r"[\x00-\x20\x7f\\]")  # control characters, spaces and backslashes: parsers disagree on these


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop an IPv6 zone id such as fe80::1%en0
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_public_url(url: str, *, resolver: Callable = socket.getaddrinfo, ports: frozenset[int] = ALLOWED_PORTS) -> None:
    """Raise FetchError unless `url` is http(s), has no credentials, uses an allowed port and resolves only to public addresses.

    This closes the direct cases. A hostname can still answer differently between this check and the connection
    (DNS rebinding), so the host must also block private ranges at the network level."""
    if _ODD_CHARACTERS.search(url):
        raise FetchError("Enter a valid URL")
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise FetchError("Enter a valid URL") from None
    if parts.scheme not in DEFAULT_PORTS:
        raise FetchError("Only http and https URLs are supported")
    if parts.username is not None or parts.password is not None:
        raise FetchError("URLs with credentials are not allowed")
    if not host:
        raise FetchError("Enter a valid URL")
    if not host.isascii():
        raise FetchError("Use the punycode form of this address (xn--...)")
    if _NUMERIC_HOST.match(host):  # 2130706433, 0x7f.1, 0177.0.0.1: the resolver and curl can read these differently
        try:
            ipaddress.IPv4Address(host)  # only plain dotted-decimal, such as 93.184.216.34, is accepted
        except ValueError:
            raise FetchError(f"{host} is not a plain address. Write IP addresses as four decimal numbers.") from None
    port = port or DEFAULT_PORTS[parts.scheme]
    if port not in ports:
        raise FetchError(f"Port {port} is not allowed. Use one of: {', '.join(str(p) for p in sorted(ports))}.")
    try:
        answers = resolver(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        raise FetchError(f"Could not find {host}") from None
    addresses = [answer[4][0] for answer in answers]
    if not addresses:
        raise FetchError(f"Could not find {host}")
    if not all(_is_public(address) for address in addresses):
        raise FetchError(f"{host} is not a public address, so it cannot be fetched")
```

- [ ] **Step 6: Edit `src/scraper/fetch.py`**

In `src/scraper/fetch.py`, replace:

```python
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
```

with:

```python
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
MAX_REDIRECTS = 5
```

- [ ] **Step 7: Edit `src/scraper/fetch.py`**

In `src/scraper/fetch.py`, replace:

```python

    return Fetcher.get(url, timeout=timeout, retries=2)
```

with:

```python

    # "safe" makes curl refuse a redirect to a private or loopback address. It is the library default; spelled out so a
    # change of default cannot quietly turn it off.
    return Fetcher.get(url, timeout=timeout, retries=2, follow_redirects="safe", max_redirects=MAX_REDIRECTS)
```

- [ ] **Step 8: Run the tests to see them pass**

```bash
uv run pytest tests/test_netguard.py tests/test_fetch.py -q
```

Expected: PASS (output contains `passed`)

- [ ] **Step 9: Record what was found in the spec**

The spec said it was unverified whether redirects can be restricted. It can; update it so the document matches what is built.

In `docs/superpowers/specs/2026-10-09-nextjs-auth-hosted-design.md`, replace:

```markdown
- Follow redirects manually and re-validate every hop. Whether Scrapling's `Fetcher` can disable automatic redirects is **unverified**; the plan checks this first. If it cannot, redirects are validated by an egress proxy.
```

with:

```markdown
- Redirects: Scrapling's `Fetcher` takes `follow_redirects="safe"` (its default), which makes curl refuse a redirect to a private or loopback address; this was confirmed by running it against a local redirecting server. The fetcher passes it explicitly, with a redirect cap of 5, and a test pins both.
- Hosts written as numbers are rejected unless they are plain dotted-decimal (`0177.0.0.1`, `0x7f.1` and `2130706433` are refused), because the system resolver and curl can read them differently.
```

- [ ] **Step 10: Commit**

```bash
git add src/scraper/netguard.py src/scraper/fetch.py tests/test_netguard.py tests/test_fetch.py docs/superpowers/specs/2026-10-09-nextjs-auth-hosted-design.md
git commit -m "Refuse private addresses and pin safe redirects for hosted fetching"
```

### Task 3: Per-user daily usage meter

The meter is storage-agnostic: an in-memory store here, Postgres in Task 7. `try_add` is atomic (add one unless the count is already at the limit), which is what makes the limit hold when requests race.

**Files:**
- Create: `src/scraper/usage.py`, `tests/test_usage.py`

**Interfaces:**
- Consumes: `scraper.config.Config` (`daily_model_calls`, `daily_pages`).
- Produces: `UsageLimitReached(Exception)`; `UsageStore` protocol (`try_add(user_id, day, field, limit) -> bool`, `get(user_id, day) -> dict`); `MemoryUsageStore`; `UsageMeter(user_id, store, cfg, *, today)` with `charge_model_call()`, `charge_page()`, `exhausted() -> str | None`, `snapshot() -> dict`; `FIELDS = ("model_calls", "pages")`.

- [ ] **Step 1: Create `tests/test_usage.py`**

```python
from datetime import date

import pytest

from scraper.config import Config
from scraper.usage import MemoryUsageStore, UsageLimitReached, UsageMeter

CFG = Config(daily_model_calls=3, daily_pages=2)


def meter(user="u1", store=None, day=date(2026, 10, 9)):
    return UsageMeter(user, store or MemoryUsageStore(), CFG, today=lambda: day)


def test_model_calls_are_counted_up_to_the_daily_limit_then_refused():
    m = meter()
    for _ in range(3):
        m.charge_model_call()
    with pytest.raises(UsageLimitReached, match=r"model-call limit .*3"):
        m.charge_model_call()
    assert m.snapshot()["model_calls"] == 3  # the refused call is not counted


def test_pages_are_counted_separately_from_model_calls():
    m = meter()
    m.charge_page()
    m.charge_page()
    with pytest.raises(UsageLimitReached, match="page limit"):
        m.charge_page()
    m.charge_model_call()  # unaffected


def test_the_message_says_when_it_resets_and_that_the_run_is_kept():
    m = meter()
    for _ in range(2):
        m.charge_page()
    with pytest.raises(UsageLimitReached) as caught:
        m.charge_page()
    assert "00:00 UTC" in str(caught.value) and "saved" in str(caught.value)


def test_counts_start_again_the_next_utc_day():
    store = MemoryUsageStore()
    for _ in range(2):
        meter(store=store).charge_page()
    with pytest.raises(UsageLimitReached):
        meter(store=store).charge_page()
    meter(store=store, day=date(2026, 10, 10)).charge_page()


def test_users_are_counted_separately():
    store = MemoryUsageStore()
    for _ in range(2):
        meter("alice", store).charge_page()
    meter("bob", store).charge_page()
    with pytest.raises(UsageLimitReached):
        meter("alice", store).charge_page()


def test_snapshot_reports_used_limits_and_the_reset_time():
    m = meter()
    m.charge_model_call()
    m.charge_page()
    assert m.snapshot() == {
        "day": "2026-10-09",
        "model_calls": 1, "model_calls_limit": 3,
        "pages": 1, "pages_limit": 2,
        "resets_at": "2026-10-10T00:00:00+00:00",
    }


def test_exhausted_names_the_first_spent_allowance_or_none():
    m = meter()
    assert m.exhausted() is None
    for _ in range(2):
        m.charge_page()
    assert "page limit" in m.exhausted()
    for _ in range(3):
        m.charge_model_call()
    assert "model-call limit" in m.exhausted()
```

- [ ] **Step 2: Run the tests to see them fail**

```bash
uv run pytest tests/test_usage.py -q
```

Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Create `src/scraper/usage.py`**

```python
"""Hosted mode: per-user daily allowances for model calls and fetched pages."""

import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol

from .config import Config


class UsageLimitReached(Exception):
    """A daily allowance is spent. The message is written for the person to read."""


class UsageStore(Protocol):
    def try_add(self, user_id: str, day: date, field: str, limit: int) -> bool:
        """Add one to the count unless it is already at `limit`. Atomic. Returns whether it was added."""

    def get(self, user_id: str, day: date) -> dict[str, int]:
        """The counts so far for that day, with every field present."""


FIELDS = ("model_calls", "pages")


class MemoryUsageStore:
    def __init__(self) -> None:
        self._counts: dict[tuple[str, date, str], int] = defaultdict(int)
        self._lock = threading.Lock()

    def try_add(self, user_id: str, day: date, field: str, limit: int) -> bool:
        with self._lock:
            if self._counts[(user_id, day, field)] >= limit:
                return False
            self._counts[(user_id, day, field)] += 1
            return True

    def get(self, user_id: str, day: date) -> dict[str, int]:
        with self._lock:
            return {field: self._counts[(user_id, day, field)] for field in FIELDS}


def _utc_today() -> date:
    return datetime.now(UTC).date()


class UsageMeter:
    def __init__(self, user_id: str, store: UsageStore, cfg: Config, *, today: Callable[[], date] = _utc_today) -> None:
        self.user_id = user_id
        self.store = store
        self._limits = {"model_calls": cfg.daily_model_calls, "pages": cfg.daily_pages}
        self._today = today

    def _refused(self, what: str, limit: int) -> UsageLimitReached:
        return UsageLimitReached(
            f"Daily {what} limit reached ({limit} a day). It resets at 00:00 UTC. Your session and run are saved, so you can resume then."
        )

    def charge_model_call(self) -> None:
        if not self.store.try_add(self.user_id, self._today(), "model_calls", self._limits["model_calls"]):
            raise self._refused("model-call", self._limits["model_calls"])

    def charge_page(self) -> None:
        if not self.store.try_add(self.user_id, self._today(), "pages", self._limits["pages"]):
            raise self._refused("page", self._limits["pages"])

    def exhausted(self) -> str | None:
        """The message for the first spent allowance, or None while there is room in both."""
        used = self.store.get(self.user_id, self._today())
        if used["model_calls"] >= self._limits["model_calls"]:
            return str(self._refused("model-call", self._limits["model_calls"]))
        if used["pages"] >= self._limits["pages"]:
            return str(self._refused("page", self._limits["pages"]))
        return None

    def snapshot(self) -> dict:
        day = self._today()
        used = self.store.get(self.user_id, day)
        return {
            "day": day.isoformat(),
            "model_calls": used["model_calls"],
            "model_calls_limit": self._limits["model_calls"],
            "pages": used["pages"],
            "pages_limit": self._limits["pages"],
            "resets_at": datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC).isoformat(),
        }
```

- [ ] **Step 4: Run the tests to see them pass**

```bash
uv run pytest tests/test_usage.py -q
```

Expected: PASS (output contains `passed`)

- [ ] **Step 5: Commit**

```bash
git add src/scraper/usage.py tests/test_usage.py
git commit -m "Add a per-user daily usage meter"
```

### Task 4: Hosted fetch function and fetch-mode awareness

`hosted_fetch` is what every user's `Session` receives as its `fetch_fn`. The tool layer and the agent prompt also learn which modes exist, so the model is not told to use ones the server refuses.

**Files:**
- Create: `src/scraper/hosted.py`, `tests/test_hosted.py`
- Modify: `src/scraper/tools.py`, `src/scraper/session.py`, `tests/test_tools.py`, `tests/test_session.py`

**Interfaces:**
- Consumes: `scraper.netguard.check_public_url`, `scraper.usage.UsageMeter`, `Config.allowed_modes`.
- Produces: `scraper.hosted.make_hosted_fetch(cfg, meter, *, base=fetch, check=check_public_url)` returning a function with the same signature as `fetch`. `ToolBox(..., allowed_modes=MODES)`. `agent_system_prompt(cfg)` stops suggesting other modes when only `fast` is offered.

- [ ] **Step 1: Create `tests/test_hosted.py`**

```python
import pytest

from fakes import make_page
from scraper.config import Config
from scraper.fetch import FetchError
from scraper.hosted import make_hosted_fetch
from scraper.usage import MemoryUsageStore, UsageLimitReached, UsageMeter

CFG = Config(hosted=True, daily_pages=2)


class Spy:
    def __init__(self, result=None, error=None):
        self.calls, self.result, self.error = [], result or make_page("https://example.test/"), error

    def __call__(self, url, mode="fast", *, timeout=20.0):
        self.calls.append((url, mode, timeout))
        if self.error:
            raise self.error
        return self.result


def build(base=None, check=None, cfg=CFG):
    meter = UsageMeter("u1", MemoryUsageStore(), cfg)
    checks = []
    return make_hosted_fetch(cfg, meter, base=base or Spy(), check=check or checks.append), meter, checks


def test_a_valid_fetch_is_checked_charged_and_passed_on():
    base = Spy()
    fetch_fn, meter, checks = build(base)
    page = fetch_fn("example.test/a", "fast", timeout=7.0)
    assert page is base.result
    assert checks == ["https://example.test/a"]  # the scheme is added before the address check
    assert base.calls == [("https://example.test/a", "fast", 7.0)]
    assert meter.snapshot()["pages"] == 1


def test_a_mode_this_server_does_not_offer_is_refused_before_anything_else():
    base = Spy()
    fetch_fn, meter, checks = build(base)
    with pytest.raises(FetchError, match="dynamic mode is not available.*fast"):
        fetch_fn("https://example.test/", "dynamic")
    assert base.calls == [] and checks == [] and meter.snapshot()["pages"] == 0


def test_widened_modes_are_honoured():
    base = Spy()
    fetch_fn, _, _ = build(base, cfg=Config(hosted=True, fetch_modes="fast,dynamic"))
    fetch_fn("https://example.test/", "dynamic")
    assert base.calls[0][1] == "dynamic"


def test_a_non_public_address_is_refused_and_costs_nothing():
    base = Spy()

    def refuse(url):
        raise FetchError("localhost is not a public address, so it cannot be fetched")

    fetch_fn, meter, _ = build(base, check=refuse)
    with pytest.raises(FetchError, match="not a public address"):
        fetch_fn("http://localhost/")
    assert base.calls == [] and meter.snapshot()["pages"] == 0


def test_an_invalid_url_is_refused_before_the_address_check():
    base = Spy()
    fetch_fn, _, checks = build(base)
    with pytest.raises(FetchError, match="valid URL"):
        fetch_fn("not a url")
    assert checks == [] and base.calls == []


def test_the_daily_page_limit_raises_usage_limit_not_a_fetch_error():
    base = Spy()
    fetch_fn, _, _ = build(base)
    fetch_fn("https://example.test/1")
    fetch_fn("https://example.test/2")
    with pytest.raises(UsageLimitReached):
        fetch_fn("https://example.test/3")
    assert len(base.calls) == 2


def test_a_retry_suggestion_for_a_mode_that_is_off_is_dropped():
    blocked = FetchError("HTTP 403 from https://example.test/", hint="The site may be blocking simple requests. Try Stealthy mode.", retry_mode="stealthy")
    fetch_fn, _, _ = build(Spy(error=blocked))
    with pytest.raises(FetchError) as caught:
        fetch_fn("https://example.test/")
    assert caught.value.retry_mode is None and caught.value.hint is None
    assert caught.value.message == "HTTP 403 from https://example.test/"


def test_a_retry_suggestion_for_an_available_mode_is_kept():
    blocked = FetchError("HTTP 403", hint="Try Stealthy mode.", retry_mode="stealthy")
    fetch_fn, _, _ = build(Spy(error=blocked), cfg=Config(hosted=True, fetch_modes="fast,stealthy"))
    with pytest.raises(FetchError) as caught:
        fetch_fn("https://example.test/")
    assert caught.value.retry_mode == "stealthy"
```

- [ ] **Step 2: Add to the end of `tests/test_tools.py`**

```python
# ---- fetch modes the server offers --------------------------------------------


def test_a_mode_the_server_does_not_offer_is_explained_and_nothing_is_fetched():
    box, pages, _, calls = make_box({URL: PAGE}, allowed_modes=("fast",))
    result = box.execute("fetch_page", {"url": URL, "mode": "dynamic"})
    assert result.is_error
    assert "not available" in result.text and "fast" in result.text
    assert calls == [] and pages == []


def test_aliases_resolve_before_the_availability_check():
    box, _, _, calls = make_box({URL: PAGE}, allowed_modes=("fast",))
    assert box.execute("fetch_page", {"url": URL, "mode": "static"}).is_error is False
    assert box.execute("fetch_page", {"url": URL, "mode": "js"}).is_error is True  # js means dynamic, which is off
    assert [mode for _, mode in calls] == ["fast"]


def test_the_empty_page_hint_only_suggests_dynamic_when_it_is_available():
    tiny = parse_html("<html><head><title>T</title></head><body><div id='app'></div></body></html>", URL)
    tiny = tiny.__class__(url=tiny.url, title=tiny.title, markdown=tiny.markdown, links=tiny.links, looks_empty=True)
    box, _, _, _ = make_box({URL: tiny}, allowed_modes=("fast",))
    assert "dynamic" not in box.execute("fetch_page", {"url": URL}).text
    box, _, _, _ = make_box({URL: tiny})
    assert "dynamic" in box.execute("fetch_page", {"url": URL}).text
```

- [ ] **Step 3: Add to the end of `tests/test_session.py`**

```python
# ---- the agent prompt follows the fetch modes on offer ---------------------------


def test_the_agent_prompt_advises_other_modes_only_when_they_exist():
    from scraper.session import agent_system_prompt

    local = agent_system_prompt(Config())
    hosted = agent_system_prompt(Config(hosted=True))
    assert "dynamic, then stealthy" in local
    assert "dynamic, then stealthy" not in hosted
    assert "Only the fast fetch mode is available" in hosted
```

- [ ] **Step 4: Run the new fetch tests to see them fail**

```bash
uv run pytest tests/test_hosted.py -q
```

Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 5: Run the tool and prompt tests to see them fail**

```bash
uv run pytest tests/test_tools.py tests/test_session.py -q -k 'server_does_not_offer or aliases_resolve or empty_page_hint or prompt_advises'
```

Expected: FAIL (`failed`)

- [ ] **Step 6: Create `src/scraper/hosted.py`**

The order matters and is tested: refuse a mode the server does not offer, normalise the URL, check the address, then charge the page, so a refused request costs nothing. When the site blocks simple requests, `fetch` suggests retrying in `stealthy` mode; here that mode is off, so the suggestion is dropped (otherwise the model would waste calls obeying it).

```python
"""Hosted mode: the fetch function every user's session gets. Restricts modes, blocks private addresses, counts pages."""

from collections.abc import Callable

from . import fetch as fetch_mod
from .config import Config
from .netguard import check_public_url
from .usage import UsageMeter


def make_hosted_fetch(
    cfg: Config,
    meter: UsageMeter,
    *,
    base: Callable[..., fetch_mod.Page] = fetch_mod.fetch,
    check: Callable[[str], None] = check_public_url,
) -> Callable[..., fetch_mod.Page]:
    modes = cfg.allowed_modes

    def hosted_fetch(url: str, mode: str = "fast", *, timeout: float = 20.0) -> fetch_mod.Page:
        if mode not in modes:
            raise fetch_mod.FetchError(f"The {mode} mode is not available on this server. Use: {', '.join(modes)}.")
        url = fetch_mod.validate_url(url)
        check(url)
        meter.charge_page()  # only a fetch that will really happen costs allowance
        try:
            return base(url, mode, timeout=timeout)
        except fetch_mod.FetchError as exc:
            if exc.retry_mode and exc.retry_mode not in modes:  # do not send the model to a mode it cannot use
                raise fetch_mod.FetchError(exc.message) from exc
            raise

    return hosted_fetch
```

- [ ] **Step 7: Edit `src/scraper/tools.py`**

In `src/scraper/tools.py`, replace:

```python
        grant: int | None = None,
        min_delay: float = 0.0,
```

with:

```python
        grant: int | None = None,
        allowed_modes: tuple[str, ...] = MODES,
        min_delay: float = 0.0,
```

- [ ] **Step 8: Edit `src/scraper/tools.py`**

In `src/scraper/tools.py`, replace:

```python
        self.fetched = fetched
        self.approve_host = approve_host
```

with:

```python
        self.fetched = fetched
        self.allowed_modes = allowed_modes
        self.approve_host = approve_host
```

- [ ] **Step 9: Edit `src/scraper/tools.py`**

In `src/scraper/tools.py`, replace:

```python

    @staticmethod
    def _mode(value) -> str:
        if value is None or value == "":
```

with:

```python

    def _mode(self, value) -> str:
        if value is None or value == "":
```

- [ ] **Step 10: Edit `src/scraper/tools.py`**

In `src/scraper/tools.py`, replace:

```python
        if mode not in MODES:
            raise ToolArgError(f"Unknown mode {value!r}. Use one of: {', '.join(MODES)}.")
        return mode
```

with:

```python
        if mode not in MODES:
            raise ToolArgError(f"Unknown mode {value!r}. Use one of: {', '.join(self.allowed_modes)}.")
        if mode not in self.allowed_modes:
            raise ToolArgError(f"The {mode} mode is not available on this server. Use one of: {', '.join(self.allowed_modes)}.")
        return mode
```

- [ ] **Step 11: Edit `src/scraper/tools.py`**

In `src/scraper/tools.py`, replace:

```python
        note = ""
        if page.looks_empty and mode == "fast":
            note = " The page looks nearly empty; it may need mode='dynamic'."
```

with:

```python
        note = ""
        if page.looks_empty and mode == "fast" and "dynamic" in self.allowed_modes:
            note = " The page looks nearly empty; it may need mode='dynamic'."
```

- [ ] **Step 12: Edit `src/scraper/session.py`**

In `src/scraper/session.py`, replace:

```python
- When you are done, reply with a short plain-text summary of what you found. Do not call a tool in your final reply.
- If a page is blocked or empty, try another fetch mode (dynamic, then stealthy) before giving up.
```

with:

```python
- When you are done, reply with a short plain-text summary of what you found. Do not call a tool in your final reply.
{mode_advice}
```

- [ ] **Step 13: Edit `src/scraper/session.py`**

In `src/scraper/session.py`, replace:

```python

def agent_system_prompt(cfg: Config) -> str:
    return AGENT_PROMPT.format(max_steps=cfg.agent_max_steps, max_pages=cfg.agent_max_pages)
```

with:

```python

MODE_ADVICE = {
    True: "- If a page is blocked or empty, try another fetch mode (dynamic, then stealthy) before giving up.",
    False: "- Only the fast fetch mode is available here. If a page is blocked or empty, say so instead of retrying with another mode.",
}


def agent_system_prompt(cfg: Config) -> str:
    other_modes = any(mode != "fast" for mode in cfg.allowed_modes)
    return AGENT_PROMPT.format(max_steps=cfg.agent_max_steps, max_pages=cfg.agent_max_pages, mode_advice=MODE_ADVICE[other_modes])
```

- [ ] **Step 14: Edit `src/scraper/session.py`**

In `src/scraper/session.py`, replace:

```python
            grant=self.cfg.agent_max_pages,
            result_tokens=self.cfg.tool_result_tokens,
```

with:

```python
            grant=self.cfg.agent_max_pages,
            allowed_modes=self.cfg.allowed_modes,
            result_tokens=self.cfg.tool_result_tokens,
```

- [ ] **Step 15: Run the tests to see them pass**

```bash
uv run pytest tests/test_hosted.py tests/test_tools.py tests/test_session.py -q
```

Expected: PASS (output contains `passed`)

- [ ] **Step 16: Commit**

```bash
git add src/scraper/hosted.py src/scraper/tools.py src/scraper/session.py tests/test_hosted.py tests/test_tools.py tests/test_session.py
git commit -m "Give hosted sessions a fetch that blocks private addresses and counts pages"
```

### Task 5: Charge model calls, and end a run cleanly when the allowance is spent

`UsageGuard` runs before every model call in an agent run. When the allowance is spent it raises; the runner turns any exception into an `error` event and the thread stays resumable (this was checked against LangChain's tool node first: a custom exception ends the stream, it is not swallowed). The guard goes first in the middleware list so a run paused inside `RunGuard` is not charged twice when it resumes.

**Files:**
- Create: `tests/test_usage_wiring.py`
- Modify: `src/scraper/guards.py`, `src/scraper/agent.py`, `src/scraper/session.py`

**Interfaces:**
- Consumes: `UsageMeter.charge_model_call`, `UsageLimitReached`.
- Produces: `guards.UsageGuard(charge)` (agent middleware); `build_agent(..., usage_charge=None)`; `describe_error` shows a `UsageLimitReached` message as written; `Session(..., usage: UsageMeter | None = None)`, and `Session.ask` refuses with an `error` event, without calling the model, when the allowance is spent.

- [ ] **Step 1: Create `tests/test_usage_wiring.py`**

```python
from datetime import date

from fakes import FakeLLM, ScriptedChat, ai, call, make_fetch, make_page
from langgraph.checkpoint.memory import InMemorySaver

from scraper.agent import AgentRunner, build_agent, describe_error
from scraper.config import Config
from scraper.llm import Event
from scraper.session import Session
from scraper.tools import ToolBox, build_tools
from scraper.usage import MemoryUsageStore, UsageLimitReached, UsageMeter

URL = "https://site.test/pricing"
WEB = {URL: make_page(URL, markdown="# Pricing\n\nStarter ten dollars.")}
MESSAGES = [{"role": "user", "content": "go"}]


class Clock:
    def __init__(self):
        self.day = date(2026, 10, 9)

    def __call__(self):
        return self.day


def agent_with_meter(script, *, model_calls):
    clock = Clock()
    meter = UsageMeter("u1", MemoryUsageStore(), Config(daily_model_calls=model_calls), today=clock)
    box = ToolBox(pages=[], rows=[], fetch_fn=make_fetch(WEB), allowed_hosts={"site.test"})
    agent = build_agent(
        ScriptedChat(script=list(script)),
        build_tools(box),
        system_prompt="x",
        checkpointer=InMemorySaver(),
        max_steps=25,
        max_context_tokens=100_000,
        usage_charge=meter.charge_model_call,
    )
    return AgentRunner(agent, max_steps=25, base_url="http://x/v1", provider="Mistral"), meter, clock


async def collect(stream):
    return [event async for event in stream]


async def test_a_spent_allowance_ends_the_round_with_the_plain_message_and_the_thread_resumes_tomorrow():
    runner, meter, clock = agent_with_meter([ai("", call("fetch_page", url=URL)), ai("Starter is ten dollars.")], model_calls=1)
    first = await collect(runner.run("t1", MESSAGES))
    errors = [e for e in first if e.kind == "error"]
    assert len(errors) == 1 and "model-call limit" in errors[0].text and "UsageLimitReached" not in errors[0].text
    assert any(e.kind == "tool_result" for e in first)  # the first call and its tool ran before the limit bit
    clock.day = date(2026, 10, 10)  # the next UTC day
    second = await collect(runner.proceed("t1"))
    assert second[-1].kind == "done"


async def test_every_model_call_is_charged_once():
    runner, meter, _ = agent_with_meter([ai("", call("fetch_page", url=URL)), ai("done now")], model_calls=10)
    await collect(runner.run("t1", MESSAGES))
    assert meter.snapshot()["model_calls"] == 2


def test_describe_error_shows_a_usage_limit_message_as_written():
    assert describe_error(UsageLimitReached("Daily page limit reached (5 a day)."), "http://x", "Mistral") == "Daily page limit reached (5 a day)."


async def test_ask_is_charged_and_refused_at_the_limit_without_calling_the_model():
    meter = UsageMeter("u1", MemoryUsageStore(), Config(daily_model_calls=1), today=Clock())
    llm = FakeLLM(events=[Event("answer", "Ten dollars.")])
    session = Session(Config(request_delay=0.0), llm, make_fetch(WEB), usage=meter)
    await session.scrape(URL)
    first = [e async for e in session.ask("price?", "qwen-test")]
    assert first[-1] == Event("answer", "Ten dollars.")
    second = [e async for e in session.ask("again?", "qwen-test")]
    assert [e.kind for e in second] == ["error"] and "model-call limit" in second[0].text
    assert len(llm.calls) == 1  # the refused question never reached the model
    assert len(session.history) == 2  # nor was it recorded


async def test_a_question_that_fails_before_the_model_is_not_charged():
    meter = UsageMeter("u1", MemoryUsageStore(), Config(daily_model_calls=5), today=Clock())
    session = Session(Config(request_delay=0.0), FakeLLM(), make_fetch(WEB), usage=meter)
    events = [e async for e in session.ask("price?", "qwen-test")]  # no page scraped yet
    assert events[0].kind == "error" and meter.snapshot()["model_calls"] == 0
```

- [ ] **Step 2: Run the tests to see them fail**

```bash
uv run pytest tests/test_usage_wiring.py -q
```

Expected: FAIL (`failed`)

- [ ] **Step 3: Edit `src/scraper/guards.py`**

In `src/scraper/guards.py`, replace:

```python
    async def abefore_model(self, state, runtime):
        return self.before_model(state, runtime)
```

with:

```python
    async def abefore_model(self, state, runtime):
        return self.before_model(state, runtime)


class UsageGuard(AgentMiddleware):
    """Hosted mode: every model call spends the user's daily allowance. A spent allowance raises, which ends the round
    with an error event and leaves the thread resumable."""

    def __init__(self, charge: Callable[[], None]) -> None:
        self.charge = charge

    def before_model(self, state, runtime):
        self.charge()
        return None

    async def abefore_model(self, state, runtime):
        self.charge()
        return None
```

- [ ] **Step 4: Edit `src/scraper/agent.py`**

In `src/scraper/agent.py`, replace:

```python

from .guards import FOLLOWUP_KEY, ContextTrim, FollowupInbox, RunGuard
```

with:

```python

from .guards import FOLLOWUP_KEY, ContextTrim, FollowupInbox, RunGuard, UsageGuard
from .usage import UsageLimitReached
```

- [ ] **Step 5: Edit `src/scraper/agent.py`**

In `src/scraper/agent.py`, replace:

```python

def build_agent(model, tools, *, system_prompt, checkpointer, max_steps, max_context_tokens, on_grant=None, followups=None, on_inject=None, on_present=None):
    middleware = [ContextTrim(max_context_tokens), RunGuard(max_steps=max_steps, on_grant=on_grant)]
    if followups is not None:
```

with:

```python

def build_agent(model, tools, *, system_prompt, checkpointer, max_steps, max_context_tokens, on_grant=None, followups=None, on_inject=None, on_present=None, usage_charge=None):
    middleware = [ContextTrim(max_context_tokens), RunGuard(max_steps=max_steps, on_grant=on_grant)]
    if usage_charge is not None:
        middleware.insert(0, UsageGuard(usage_charge))  # first: a run paused inside RunGuard must not be charged again when it resumes
    if followups is not None:
```

- [ ] **Step 6: Edit `src/scraper/agent.py`**

In `src/scraper/agent.py`, replace:

```python
def describe_error(exc: Exception, base_url: str, provider: str = "LM Studio") -> str:
    if isinstance(exc, openai.APIStatusError):
```

with:

```python
def describe_error(exc: Exception, base_url: str, provider: str = "LM Studio") -> str:
    if isinstance(exc, UsageLimitReached):
        return str(exc)
    if isinstance(exc, openai.APIStatusError):
```

- [ ] **Step 7: Edit `src/scraper/session.py`**

In `src/scraper/session.py`, replace:

```python
from .tools import ToolBox, build_tools, host_of, hosts_in
```

with:

```python
from .tools import ToolBox, build_tools, host_of, hosts_in
from .usage import UsageLimitReached, UsageMeter
```

- [ ] **Step 8: Edit `src/scraper/session.py`**

In `src/scraper/session.py`, replace:

```python
        providers: dict | None = None,
    ) -> None:
        self.cfg = cfg
        self.providers: dict = dict(providers) if providers else {llm.key: llm}
```

with:

```python
        providers: dict | None = None,
        usage: UsageMeter | None = None,
    ) -> None:
        self.cfg = cfg
        self.usage = usage
        self.providers: dict = dict(providers) if providers else {llm.key: llm}
```

- [ ] **Step 9: Edit `src/scraper/session.py`**

In `src/scraper/session.py`, replace:

```python
        ]
        answer, reasoning, failed = "", "", False
```

with:

```python
        ]
        if self.usage is not None:
            try:
                self.usage.charge_model_call()
            except UsageLimitReached as exc:
                yield Event("error", str(exc))
                return
        answer, reasoning, failed = "", "", False
```

- [ ] **Step 10: Edit `src/scraper/session.py`**

In `src/scraper/session.py`, replace:

```python
            on_grant=lambda reason, toolbox=self._toolbox, run=run: self._grant_pages(toolbox, run),
            followups=lambda: list(self.run.followups) if self.run is not None else [],
```

with:

```python
            on_grant=lambda reason, toolbox=self._toolbox, run=run: self._grant_pages(toolbox, run),
            usage_charge=self.usage.charge_model_call if self.usage is not None else None,
            followups=lambda: list(self.run.followups) if self.run is not None else [],
```

- [ ] **Step 11: Run the new tests to see them pass**

```bash
uv run pytest tests/test_usage_wiring.py -q
```

Expected: PASS (output contains `passed`)

- [ ] **Step 12: Run the whole suite**

This task touches the agent and the session, so the existing tests matter. Same note as in Task 0 about the one intermittent test.

```bash
uv run pytest -q
```

Expected: PASS (output contains `passed`)

- [ ] **Step 13: Commit**

```bash
git add src/scraper/guards.py src/scraper/agent.py src/scraper/session.py tests/test_usage_wiring.py
git commit -m "Charge model calls and end a run cleanly when the daily allowance is spent"
```

### Task 6: A dropped connection stops the run and keeps it resumable

When the browser goes away mid-stream, Python cancels the generator at its next `await`. The session's own `finally` frees the busy flag, but the run record still says `running`. The API layer (Task 12) calls this method in its own `finally` so the record tells the truth, exactly as after a crash.

**Files:**
- Modify: `src/scraper/session.py`, `tests/test_session.py`

**Interfaces:**
- Consumes: `Session` as built so far.
- Produces: `Session.release_after_disconnect()`: if the run is `running`, mark it `stopped`, hand back unread follow-ups as drafts, and save. Does nothing for a paused, finished or absent run.

- [ ] **Step 1: Import `asyncio` in the session tests**

The new tests pause for a moment after closing a stream, so LangChain's error callback can finish before the test's event loop closes (otherwise pytest prints `Task was destroyed but it is pending`).

In `tests/test_session.py`, replace:

```python
from contextlib import AsyncExitStack
from dataclasses import replace
```

with:

```python
import asyncio
from contextlib import AsyncExitStack
from dataclasses import replace
```

- [ ] **Step 2: Add to the end of `tests/test_session.py`**

```python
# ---- the browser goes away mid-run ---------------------------------------------


async def test_a_dropped_connection_leaves_the_run_stopped_and_resumable():
    web = {A: make_page(A, markdown="# A\n\nText.")}
    session, _ = agent_session(web, [ai("", call("fetch_page", url=A)), ai("All done.")])
    stream = session.run_agent(f"read {A}", "qwen-test")
    first = await anext(stream)  # the browser read one event, then went away
    assert first.kind in ("step", "reasoning", "answer", "tool_start")
    await stream.aclose()
    await asyncio.sleep(0.05)  # let LangChain's error callback finish before the test's loop closes
    assert session.busy is False  # nothing else is blocked behind the dead stream
    assert session.run.status == "running"  # nobody has said otherwise yet
    session.release_after_disconnect()
    assert session.run.status == "stopped" and session.resumable
    events = await proceed_all(session)
    assert events[-1].kind == "done" and session.run.status == "done"


async def test_messages_the_agent_never_read_are_handed_back_when_the_connection_drops():
    web = {A: make_page(A)}
    session, _ = agent_session(web, [ai("", call("fetch_page", url=A)), ai("All done.")])
    stream = session.run_agent(f"read {A}", "qwen-test")
    await anext(stream)
    assert session.steer("also the footer").status == "queued"
    await stream.aclose()
    await asyncio.sleep(0.05)  # let LangChain's error callback finish before the test's loop closes
    session.release_after_disconnect()
    assert session.take_stopped_drafts() == ["also the footer"]


async def test_releasing_after_a_disconnect_leaves_finished_and_paused_runs_alone():
    session = await paused_session()
    session.release_after_disconnect()
    assert session.run.status == "paused"
    done, _ = agent_session({}, [ai("Nothing to fetch.")])
    await run_all(done, "say hi")
    done.release_after_disconnect()
    assert done.run.status == "done"
    Session(CFG, FakeLLM(), make_fetch({})).release_after_disconnect()  # no run at all: nothing to do
```

- [ ] **Step 3: Run the tests to see them fail**

```bash
uv run pytest tests/test_session.py -q -k 'dropped or releasing or handed_back'
```

Expected: FAIL (`AttributeError`)

- [ ] **Step 4: Edit `src/scraper/session.py`**

In `src/scraper/session.py`, replace:

```python
            self._stopped_drafts.extend(self._take_undelivered())  # Stop means stop: hand unread messages back
```

with:

```python
            self._stopped_drafts.extend(self._take_undelivered())  # Stop means stop: hand unread messages back

    def release_after_disconnect(self) -> None:
        """The browser went away while a run was streaming. The run stops here and can be resumed, as after a crash."""
        run = self.run
        if run is None or run.status != "running":
            return
        self._stopped_drafts.extend(self._take_undelivered())
        run.status = "stopped"
        run.pending = None
        self._save_run()
```

- [ ] **Step 5: Run the tests to see them pass**

```bash
uv run pytest tests/test_session.py -q
```

Expected: PASS (output contains `passed`)

- [ ] **Step 6: Commit**

```bash
git add src/scraper/session.py tests/test_session.py
git commit -m "Stop a run and keep it resumable when the browser disconnects"
```

### Task 7: Postgres store and usage counters

`PgSessionStore` is a drop-in for the SQLite `SessionStore`, scoped to one user. The existing `Session` needs no change to use it. The tests are the SQLite store's tests ported, plus isolation between users, the caps, and usage counting under racing threads.

**Files:**
- Create: `src/scraper_api/db.py`, `src/scraper_api/pgstore.py`, `tests/conftest.py`, `tests/test_pgstore.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: `scraper.store.RunRecord`, `LoadedSession`; `scraper.models.Message`, `TraceEntry`; `scraper.usage.FIELDS`, `UsageLimitReached`; `scraper.config.Config`.
- Produces: `db.make_pool(url, *, max_size=10) -> ConnectionPool`; `db.migrate(pool)`; `db.open_checkpointer(url, stack)` (async); `db.USER_TABLES`; `pgstore.PgSessionStore(pool, user_id, cfg)` with the same methods as `SessionStore` (`session_id`, `run_counter`, `next_run_number`, `get_pref`, `set_pref`, `save_page`, `append_rows`, `append_history`, `save_run`, `load_run`, `load`, `wipe`, `recovered`); `pgstore.PgUsageStore(pool)` implementing `UsageStore`. Test fixtures `pg_url`, `pg_pool`, `pg`.

- [ ] **Step 1: Create `tests/conftest.py`**

Three fixtures: the database URL (skipping when unset), a migrated pool, and `pg`, which empties every per-user table before a test.

```python
import os

import pytest

DATABASE_URL = os.environ.get("SCRAPER_TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def pg_url():
    if not DATABASE_URL:
        pytest.skip("set SCRAPER_TEST_DATABASE_URL to run the Postgres tests")
    return DATABASE_URL


@pytest.fixture(scope="session")
def pg_pool(pg_url):
    from scraper_api.db import make_pool, migrate

    pool = make_pool(pg_url)
    migrate(pool)
    yield pool
    pool.close()


@pytest.fixture
def pg(pg_pool):
    """The migrated pool with every per-user table emptied."""
    from scraper_api.db import USER_TABLES

    with pg_pool.connection() as conn:
        conn.execute("TRUNCATE " + ", ".join(f"app.{t}" for t in USER_TABLES))
    return pg_pool
```

- [ ] **Step 2: Create `tests/test_db.py`**

```python
from contextlib import contextmanager

from scraper_api.db import migrate


class FakeConn:
    def __init__(self, schema_exists: bool):
        self.schema_exists = schema_exists
        self.statements: list[str] = []

    def execute(self, sql, *args):
        self.statements.append(sql.strip().split("\n")[0][:40])
        outer = self

        class Result:
            def fetchone(self):
                return (1,) if outer.schema_exists else None

        return Result()


class FakePool:
    def __init__(self, schema_exists: bool):
        self.conn = FakeConn(schema_exists)

    @contextmanager
    def connection(self):
        yield self.conn


def test_migrate_creates_the_schema_only_when_it_is_missing():
    missing = FakePool(schema_exists=False)
    migrate(missing)
    assert "CREATE SCHEMA app" in missing.conn.statements
    present = FakePool(schema_exists=True)
    migrate(present)  # a role that owns only the schema cannot run CREATE SCHEMA, even IF NOT EXISTS
    assert "CREATE SCHEMA app" not in present.conn.statements


def test_migrate_can_run_again_against_a_real_database(pg_pool):
    migrate(pg_pool)
    migrate(pg_pool)
```

- [ ] **Step 3: Create `tests/test_pgstore.py`**

```python
import threading
from datetime import date

import pytest

from fakes import make_page
from scraper.config import Config
from scraper.models import Message, TraceEntry
from scraper.store import RunRecord
from scraper.usage import UsageLimitReached, UsageMeter
from scraper_api.pgstore import PgSessionStore, PgUsageStore

CFG = Config(max_stored_pages=3, max_page_chars=50, max_result_rows=4)


@pytest.fixture
def store(pg):
    return PgSessionStore(pg, "alice", CFG)


def test_a_user_id_is_required(pg):
    with pytest.raises(ValueError):
        PgSessionStore(pg, "", CFG)


def test_pages_keep_their_order_and_upsert_by_normalized_url(store):
    store.save_page(make_page("https://a.test/x", title="A", markdown="# A", links=["https://a.test/y"], html="<h1>A</h1>"))
    store.save_page(make_page("https://a.test/z", title="Z"))
    store.save_page(make_page("https://a.test/x/", title="A again", markdown="# A2"))
    assert [p.title for p in store.load().pages] == ["A again", "Z"]
    store.save_page(make_page("https://a.test/x", title="A", markdown="# A", links=["https://a.test/y"], html="<h1>A</h1>"))
    first = store.load().pages[0]
    assert first.links == ("https://a.test/y",) and first.html == "<h1>A</h1>" and first.looks_empty is False


def test_rows_and_history_round_trip(store):
    store.append_rows([{"name": "Starter", "price": "$10"}])
    store.append_rows([{"name": "Team", "tags": ["a", "b"]}])
    trace = (TraceEntry("fetch_page", {"url": "u"}, "page text", True),)
    store.append_history(
        [
            Message("user", "scrape it"),
            Message("assistant", "done", "thought", ("https://a.test/",), interrupted=False, note="2 rows added to the results table", trace=trace),
        ]
    )
    loaded = store.load()
    assert loaded.rows == [{"name": "Starter", "price": "$10"}, {"name": "Team", "tags": ["a", "b"]}]
    user, assistant = loaded.history
    assert (user.role, user.content) == ("user", "scrape it")
    assert assistant.reasoning == "thought" and assistant.sources == ("https://a.test/",)
    assert assistant.note == "2 rows added to the results table" and assistant.trace == trace


def test_run_record_round_trip(store):
    assert store.load_run() is None
    record = RunRecord(number=1, instruction="do it", model="m", system_prompt="sys", seed_url="a.test", status="paused",
                       pending={"kind": "ask", "question": "Which?"}, allowed_hosts=["a.test"], fetched=3,
                       fetched_urls=["https://a.test/"], context_sources=["https://a.test/"], rows_added=2,
                       trace=[{"name": "fetch_page", "args": {"url": "u"}, "text": "t", "ok": True}],
                       followups=[{"id": "f1", "text": "also the SKU", "delivered": False}])
    store.save_run(record)
    assert store.load_run() == record
    store.save_run(RunRecord(number=2, instruction="again"))
    assert store.load_run().number == 2  # saving replaces, it does not add


def test_session_id_is_stable_starts_with_the_user_and_run_numbers_count_up(store, pg):
    first = store.session_id()
    assert first == store.session_id() == PgSessionStore(pg, "alice", CFG).session_id()
    assert first.startswith("alice:")
    assert (store.run_counter(), store.next_run_number(), store.next_run_number(), store.run_counter()) == (0, 1, 2, 2)


def test_wipe_forgets_the_session_but_keeps_preferences(store):
    old = store.session_id()
    store.set_pref("provider", "mistral")
    store.save_page(make_page("https://a.test/"))
    store.append_rows([{"a": 1}])
    store.append_history([Message("user", "hi"), Message("assistant", "yo")])
    store.next_run_number()
    store.save_run(RunRecord(number=1, instruction="x"))
    store.wipe()
    loaded = store.load()
    assert (loaded.pages, loaded.rows, loaded.history, loaded.run) == ([], [], [], None)
    assert store.run_counter() == 0 and store.session_id() != old
    assert store.get_pref("provider") == "mistral"


def test_preferences_persist_and_are_replaced(store):
    assert store.get_pref("provider") is None
    store.set_pref("provider", "mistral")
    store.set_pref("provider", "other")
    assert store.get_pref("provider") == "other"


def test_writes_from_several_threads_do_not_fail(store):
    errors = []

    def work(n):
        try:
            store.append_rows([{"n": n}])
        except Exception as exc:  # pragma: no cover - the assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and len(store.load().rows) == 4


# ---- users never see each other ---------------------------------------------------


def test_two_users_have_completely_separate_data(pg):
    alice, bob = PgSessionStore(pg, "alice", CFG), PgSessionStore(pg, "bob", CFG)
    alice.save_page(make_page("https://a.test/", title="Alice page"))
    alice.append_rows([{"owner": "alice"}])
    alice.append_history([Message("user", "alice question")])
    alice.save_run(RunRecord(number=1, instruction="alice task"))
    alice.set_pref("provider", "mistral")
    assert alice.session_id() != bob.session_id()
    loaded = bob.load()
    assert (loaded.pages, loaded.rows, loaded.history, loaded.run) == ([], [], [], None)
    assert bob.get_pref("provider") is None and bob.run_counter() == 0


def test_wiping_one_user_leaves_the_other_alone(pg):
    alice, bob = PgSessionStore(pg, "alice", CFG), PgSessionStore(pg, "bob", CFG)
    for who in (alice, bob):
        who.save_page(make_page("https://a.test/"))
        who.append_rows([{"n": 1}])
        who.save_run(RunRecord(number=1, instruction="x"))
    alice.wipe()
    assert alice.load().pages == [] and len(bob.load().pages) == 1
    assert bob.load_run() is not None and len(bob.load().rows) == 1


def test_the_same_page_url_can_be_stored_by_two_users(pg):
    alice, bob = PgSessionStore(pg, "alice", CFG), PgSessionStore(pg, "bob", CFG)
    alice.save_page(make_page("https://a.test/", title="Alice's copy"))
    bob.save_page(make_page("https://a.test/", title="Bob's copy"))
    assert [p.title for p in alice.load().pages] == ["Alice's copy"]
    assert [p.title for p in bob.load().pages] == ["Bob's copy"]


# ---- caps ---------------------------------------------------------------------------


def test_stored_page_text_is_cut_to_the_cap(store):
    store.save_page(make_page("https://a.test/", markdown="m" * 500, html="h" * 500))
    page = store.load().pages[0]
    assert len(page.markdown) == 50 and len(page.html) == 50


def test_a_page_beyond_the_stored_page_cap_is_refused_but_an_existing_one_can_be_updated(store):
    for n in range(3):
        store.save_page(make_page(f"https://a.test/{n}"))
    with pytest.raises(UsageLimitReached, match="3 pages stored"):
        store.save_page(make_page("https://a.test/3"))
    store.save_page(make_page("https://a.test/1", title="updated"))  # not a new page
    assert len(store.load().pages) == 3


def test_rows_beyond_the_cap_are_refused_without_storing_any_of_the_batch(store):
    store.append_rows([{"n": 1}, {"n": 2}, {"n": 3}])
    with pytest.raises(UsageLimitReached, match="most allowed"):
        store.append_rows([{"n": 4}, {"n": 5}])
    assert len(store.load().rows) == 3
    store.append_rows([{"n": 4}])
    assert len(store.load().rows) == 4


def test_the_caps_are_per_user(pg):
    alice, bob = PgSessionStore(pg, "alice", CFG), PgSessionStore(pg, "bob", CFG)
    alice.append_rows([{"n": n} for n in range(4)])
    bob.append_rows([{"n": 1}])  # bob is nowhere near his own cap
    with pytest.raises(UsageLimitReached):
        alice.append_rows([{"n": 9}])


# ---- usage counters -----------------------------------------------------------------


def test_usage_counts_up_to_the_limit_and_stops(pg):
    usage = PgUsageStore(pg)
    day = date(2026, 10, 9)
    assert [usage.try_add("alice", day, "pages", 2) for _ in range(3)] == [True, True, False]
    assert usage.get("alice", day) == {"model_calls": 0, "pages": 2}
    assert usage.get("alice", date(2026, 10, 10)) == {"model_calls": 0, "pages": 0}
    assert usage.get("bob", day) == {"model_calls": 0, "pages": 0}


def test_usage_is_never_over_the_limit_when_threads_race(pg):
    usage = PgUsageStore(pg)
    day = date(2026, 10, 9)
    results = []

    def spend():
        results.append(usage.try_add("alice", day, "model_calls", 5))

    threads = [threading.Thread(target=spend) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 5 and usage.get("alice", day)["model_calls"] == 5


def test_usage_rejects_unknown_fields_and_non_positive_limits(pg):
    usage = PgUsageStore(pg)
    with pytest.raises(ValueError):
        usage.try_add("alice", date(2026, 10, 9), "pages; DROP TABLE app.usage", 5)
    assert usage.try_add("alice", date(2026, 10, 9), "pages", 0) is False


def test_the_meter_works_on_top_of_the_postgres_counters(pg):
    meter = UsageMeter("alice", PgUsageStore(pg), Config(daily_pages=1), today=lambda: date(2026, 10, 9))
    meter.charge_page()
    with pytest.raises(UsageLimitReached):
        meter.charge_page()
    assert meter.snapshot()["pages"] == 1
```

- [ ] **Step 4: Run the tests to see them fail**

```bash
uv run pytest tests/test_pgstore.py tests/test_db.py -q
```

Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 5: Create `src/scraper_api/db.py`**

`migrate` creates the `app` schema only when it is missing. This matters: `CREATE SCHEMA IF NOT EXISTS` still needs the `CREATE` privilege on the whole database, which the API's least-privilege role (it owns only its schema) does not have. That was found by running the migration as such a role. The LangGraph checkpoint tables go in the same schema through `search_path`.

```python
"""Postgres plumbing for the hosted API: schema, connection pools and the LangGraph checkpointer."""

from contextlib import AsyncExitStack

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, ConnectionPool

TABLES = """
CREATE TABLE IF NOT EXISTS app.sessions (
    user_id text PRIMARY KEY, session_id text NOT NULL, run_counter integer NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS app.pages (
    user_id text NOT NULL, key text NOT NULL, position integer NOT NULL, url text NOT NULL, title text NOT NULL,
    markdown text NOT NULL, links jsonb NOT NULL, html text NOT NULL, looks_empty boolean NOT NULL,
    PRIMARY KEY (user_id, key));
CREATE INDEX IF NOT EXISTS pages_user_position ON app.pages (user_id, position);
CREATE TABLE IF NOT EXISTS app.rows (id bigserial PRIMARY KEY, user_id text NOT NULL, data jsonb NOT NULL);
CREATE INDEX IF NOT EXISTS rows_user ON app.rows (user_id, id);
CREATE TABLE IF NOT EXISTS app.history (
    id bigserial PRIMARY KEY, user_id text NOT NULL, role text NOT NULL, content text NOT NULL, reasoning text NOT NULL,
    sources jsonb NOT NULL, interrupted boolean NOT NULL, note text NOT NULL, trace jsonb NOT NULL);
CREATE INDEX IF NOT EXISTS history_user ON app.history (user_id, id);
CREATE TABLE IF NOT EXISTS app.runs (user_id text PRIMARY KEY, data jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS app.prefs (
    user_id text NOT NULL, key text NOT NULL, value text NOT NULL, PRIMARY KEY (user_id, key));
CREATE TABLE IF NOT EXISTS app.usage (
    user_id text NOT NULL, day date NOT NULL, model_calls integer NOT NULL DEFAULT 0, pages integer NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day));
"""
USER_TABLES = ("sessions", "pages", "rows", "history", "runs", "prefs", "usage")


def make_pool(url: str, *, max_size: int = 10) -> ConnectionPool:
    """A pool of ordinary connections, opened. Callers close it."""
    pool = ConnectionPool(url, min_size=1, max_size=max_size, open=False)
    pool.open(wait=True, timeout=15)
    return pool


def migrate(pool: ConnectionPool) -> None:
    """Create the `app` schema if it is missing, then the tables. A database role that only owns the schema (the
    deployment setup) cannot run CREATE SCHEMA, even IF NOT EXISTS, so the schema is only created when absent."""
    with pool.connection() as conn:
        if conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = 'app'").fetchone() is None:
            conn.execute("CREATE SCHEMA app")
        conn.execute(TABLES)


async def open_checkpointer(url: str, stack: AsyncExitStack) -> AsyncPostgresSaver:
    """LangGraph's Postgres checkpointer, with its tables in the `app` schema. Run `migrate` first so the schema exists."""
    pool = AsyncConnectionPool(
        url,
        min_size=1,
        max_size=5,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row, "options": "-c search_path=app"},
    )
    await pool.open(wait=True, timeout=15)
    stack.push_async_callback(pool.close)
    saver = AsyncPostgresSaver(pool)
    await saver.setup()
    return saver
```

- [ ] **Step 6: Create `src/scraper_api/pgstore.py`**

`session_id()` begins with the user id, so every checkpoint thread id does too (`alice:3fa9c1d2b4e6-1`); that is what lets one user's checkpoints be deleted by prefix and never collide with another's. Caps raise `UsageLimitReached` with a message a person can act on.

```python
"""Postgres persistence for one user's session. Same methods as scraper.store.SessionStore, every query scoped to user_id."""

import uuid
from dataclasses import asdict
from datetime import date

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from scraper.config import Config
from scraper.crawl import normalize
from scraper.fetch import Page
from scraper.models import Message, TraceEntry
from scraper.store import LoadedSession, RunRecord
from scraper.usage import FIELDS, UsageLimitReached


class PgSessionStore:
    recovered = False  # SQLite can find a corrupt file; a database server does not

    def __init__(self, pool: ConnectionPool, user_id: str, cfg: Config) -> None:
        if not user_id:
            raise ValueError("user_id is required")
        self.pool = pool
        self.user_id = user_id
        self.cfg = cfg

    # ---- session ids and run numbers ------------------------------------------

    def session_id(self) -> str:
        """Unique per user and session, and it starts with the user id, so checkpoint thread ids can never collide."""
        fresh = f"{self.user_id}:{uuid.uuid4().hex[:12]}"
        with self.pool.connection() as conn:
            conn.execute("INSERT INTO app.sessions (user_id, session_id) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING", (self.user_id, fresh))
            return conn.execute("SELECT session_id FROM app.sessions WHERE user_id = %s", (self.user_id,)).fetchone()[0]

    def run_counter(self) -> int:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT run_counter FROM app.sessions WHERE user_id = %s", (self.user_id,)).fetchone()
            return row[0] if row else 0

    def next_run_number(self) -> int:
        self.session_id()  # make sure the row exists
        with self.pool.connection() as conn:
            return conn.execute(
                "UPDATE app.sessions SET run_counter = run_counter + 1 WHERE user_id = %s RETURNING run_counter", (self.user_id,)
            ).fetchone()[0]

    # ---- preferences (kept across new sessions) ---------------------------------

    def get_pref(self, key: str) -> str | None:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT value FROM app.prefs WHERE user_id = %s AND key = %s", (self.user_id, key)).fetchone()
            return row[0] if row else None

    def set_pref(self, key: str, value: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO app.prefs (user_id, key, value) VALUES (%s, %s, %s) ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value",
                (self.user_id, key, value),
            )

    # ---- content ----------------------------------------------------------------

    def save_page(self, page: Page) -> None:
        key = normalize(page.url)
        limit = self.cfg.max_page_chars
        with self.pool.connection() as conn:
            existing = conn.execute("SELECT position FROM app.pages WHERE user_id = %s AND key = %s", (self.user_id, key)).fetchone()
            if existing:
                position = existing[0]
            else:
                stored, top = conn.execute(
                    "SELECT count(*), COALESCE(max(position), 0) FROM app.pages WHERE user_id = %s", (self.user_id,)
                ).fetchone()
                if stored >= self.cfg.max_stored_pages:
                    raise UsageLimitReached(
                        f"You have {stored} pages stored, the most allowed. Start a new session to clear them, then carry on."
                    )
                position = top + 1
            conn.execute(
                """INSERT INTO app.pages (user_id, key, position, url, title, markdown, links, html, looks_empty)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (user_id, key) DO UPDATE SET url = EXCLUDED.url, title = EXCLUDED.title, markdown = EXCLUDED.markdown,
                       links = EXCLUDED.links, html = EXCLUDED.html, looks_empty = EXCLUDED.looks_empty""",
                (self.user_id, key, position, page.url, page.title, page.markdown[:limit], Jsonb(list(page.links)), page.html[:limit], page.looks_empty),
            )

    def append_rows(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self.pool.connection() as conn:
            stored = conn.execute("SELECT count(*) FROM app.rows WHERE user_id = %s", (self.user_id,)).fetchone()[0]
            if stored + len(rows) > self.cfg.max_result_rows:
                raise UsageLimitReached(
                    f"The results table holds {stored} rows, and {self.cfg.max_result_rows} is the most allowed. "
                    "Download them, then start a new session."
                )
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO app.rows (user_id, data) VALUES (%s, %s)", [(self.user_id, Jsonb(row)) for row in rows])

    def append_history(self, messages: list[Message]) -> None:
        records = [
            (
                self.user_id, m.role, m.content, m.reasoning, Jsonb(list(m.sources)), m.interrupted, m.note,
                Jsonb([asdict(t) for t in m.trace]),
            )
            for m in messages
        ]
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO app.history (user_id, role, content, reasoning, sources, interrupted, note, trace) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                records,
            )

    def save_run(self, record: RunRecord) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO app.runs (user_id, data) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET data = EXCLUDED.data",
                (self.user_id, Jsonb(asdict(record))),
            )

    def load_run(self) -> RunRecord | None:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT data FROM app.runs WHERE user_id = %s", (self.user_id,)).fetchone()
            return RunRecord(**row[0]) if row else None

    def load(self) -> LoadedSession:
        with self.pool.connection() as conn:
            pages = [
                Page(url=r[0], title=r[1], markdown=r[2], links=tuple(r[3]), looks_empty=r[5], html=r[4])
                for r in conn.execute(
                    "SELECT url, title, markdown, links, html, looks_empty FROM app.pages WHERE user_id = %s ORDER BY position", (self.user_id,)
                )
            ]
            rows = [r[0] for r in conn.execute("SELECT data FROM app.rows WHERE user_id = %s ORDER BY id", (self.user_id,))]
            history = [
                Message(
                    role=r[0], content=r[1], reasoning=r[2], sources=tuple(r[3]), interrupted=r[4], note=r[5],
                    trace=tuple(TraceEntry(**t) for t in r[6]),
                )
                for r in conn.execute(
                    "SELECT role, content, reasoning, sources, interrupted, note, trace FROM app.history WHERE user_id = %s ORDER BY id",
                    (self.user_id,),
                )
            ]
        return LoadedSession(pages, rows, history, self.load_run())

    def wipe(self) -> None:
        """New session: forget the session, pages, results, history and run. Preferences and usage stay."""
        with self.pool.connection() as conn:
            for table in ("pages", "rows", "history", "runs", "sessions"):
                conn.execute(f"DELETE FROM app.{table} WHERE user_id = %s", (self.user_id,))  # noqa: S608 (fixed table names)


class PgUsageStore:
    """Daily counters for scraper.usage.UsageMeter, shared by every user."""

    def __init__(self, pool: ConnectionPool) -> None:
        self.pool = pool

    def try_add(self, user_id: str, day: date, field: str, limit: int) -> bool:
        if field not in FIELDS:
            raise ValueError(f"unknown usage field {field!r}")
        if limit <= 0:
            return False
        with self.pool.connection() as conn:
            row = conn.execute(
                f"""INSERT INTO app.usage (user_id, day, {field}) VALUES (%s, %s, 1)
                    ON CONFLICT (user_id, day) DO UPDATE SET {field} = app.usage.{field} + 1 WHERE app.usage.{field} < %s
                    RETURNING 1""",  # noqa: S608 (field is checked against FIELDS)
                (user_id, day, limit),
            ).fetchone()
            return row is not None

    def get(self, user_id: str, day: date) -> dict[str, int]:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT model_calls, pages FROM app.usage WHERE user_id = %s AND day = %s", (user_id, day)).fetchone()
        return {"model_calls": row[0], "pages": row[1]} if row else {field: 0 for field in FIELDS}
```

- [ ] **Step 7: Run the tests to see them pass**

Expected: all pass and the summary line has no `skipped`. If it says `skipped`, the database variable is not set in this shell.

```bash
uv run pytest tests/test_pgstore.py tests/test_db.py -q -rs
```

Expected: PASS (output contains `passed`)

- [ ] **Step 8: Commit**

```bash
git add src/scraper_api/db.py src/scraper_api/pgstore.py tests/conftest.py tests/test_pgstore.py tests/test_db.py
git commit -m "Store each user's session in Postgres, with daily usage counters"
```

### Task 8: Verify the web app's tokens

This is the one place the Python side learns who is calling. It trusts nothing about the token until the signature, issuer, audience and expiry all check out, and it never calls the web app per request.

**Files:**
- Create: `src/scraper_api/auth.py`, `tests/jwks_support.py`, `tests/test_auth.py`

**Interfaces:**
- Consumes: PyJWT (`jwt`, `PyJWKClient`).
- Produces: `auth.JWKSVerifier(jwks_url, issuer, audience, *, algorithms=("EdDSA",), refresh_interval=30.0, clock=time.monotonic)` with `verify(token) -> dict` and `user_id(token) -> str`; `AuthError` (bad token), `AuthUnavailable` (key server unreachable). Test helpers `Signer(kid).token(sub, **claims)` and `JWKSServer(*signers)` in `tests/jwks_support.py`.

- [ ] **Step 1: Create `tests/jwks_support.py`**

A throwaway identity provider: it signs EdDSA tokens and serves their public keys, so the verifier is tested against real signatures and a real HTTP key server.

```python
"""A throwaway identity provider for tests: signs EdDSA tokens and serves their public keys as a JWKS."""

import http.server
import json
import threading
import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ISSUER = "https://web.test"
AUDIENCE = "https://api.test"


class Signer:
    def __init__(self, kid: str) -> None:
        self.kid = kid
        self._private = Ed25519PrivateKey.generate()
        self.jwk = json.loads(jwt.algorithms.OKPAlgorithm.to_jwk(self._private.public_key()))
        self.jwk["kid"] = kid

    def token(self, sub: str | None = "user-1", *, kid: str | None = None, **overrides) -> str:
        claims = {"sub": sub, "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 900, **overrides}
        claims = {k: v for k, v in claims.items() if v is not None}
        pem = self._private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        return jwt.encode(claims, pem, algorithm="EdDSA", headers={"kid": kid or self.kid})


class JWKSServer:
    """Serves whichever keys `keys` currently holds and counts how often it was asked."""

    def __init__(self, *signers: Signer) -> None:
        self.keys = [s.jwk for s in signers]
        self.requests = 0
        self.down = False
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests += 1
                if outer.down:
                    self.send_response(503)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"keys": outer.keys}).encode())

            def log_message(self, *args):
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_port}/api/auth/jwks"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
```

- [ ] **Step 2: Create `tests/test_auth.py`**

```python
import time

import jwt
import pytest

from jwks_support import AUDIENCE, ISSUER, JWKSServer, Signer
from scraper_api.auth import AuthError, AuthUnavailable, JWKSVerifier


@pytest.fixture
def idp():
    signer = Signer("k1")
    server = JWKSServer(signer)
    yield signer, server
    server.close()


def verifier(server, clock=time.monotonic, **kw):
    return JWKSVerifier(server.url, ISSUER, AUDIENCE, clock=clock, **kw)


def test_a_valid_token_gives_the_user_id(idp):
    signer, server = idp
    assert verifier(server).user_id(signer.token("user-42")) == "user-42"


def test_the_key_set_is_fetched_once_and_cached(idp):
    signer, server = idp
    v = verifier(server)
    for _ in range(5):
        v.user_id(signer.token())
    assert server.requests == 1


@pytest.mark.parametrize(
    "overrides",
    [{"exp": 1}, {"aud": "https://elsewhere.test"}, {"iss": "https://evil.test"}, {"sub": None}, {"sub": ""}, {"exp": None}, {"aud": None}, {"iss": None}],
)
def test_tokens_that_fail_any_check_are_rejected(idp, overrides):
    signer, server = idp
    with pytest.raises(AuthError):
        verifier(server).user_id(signer.token(**overrides))


def test_a_small_clock_skew_is_tolerated_but_not_a_large_one(idp):
    signer, server = idp
    v = verifier(server)
    assert v.user_id(signer.token(exp=int(time.time()) - 5)) == "user-1"
    with pytest.raises(AuthError):
        v.user_id(signer.token(exp=int(time.time()) - 120))


def test_a_token_signed_by_another_key_with_the_same_kid_is_rejected(idp):
    _, server = idp
    forger = Signer("k1")
    with pytest.raises(AuthError):
        verifier(server).user_id(forger.token())


def test_other_algorithms_are_rejected_including_none_and_a_shared_secret(idp):
    signer, server = idp
    v = verifier(server)
    claims = {"sub": "u", "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 100}
    hs = jwt.encode(claims, "secret" * 8, algorithm="HS256", headers={"kid": "k1"})
    unsigned = jwt.encode(claims, None, algorithm="none", headers={"kid": "k1"})
    for bad in (hs, unsigned):
        with pytest.raises(AuthError):
            v.user_id(bad)


@pytest.mark.parametrize("junk", ["", "abc", "a.b.c", "Bearer x", "x" * 10_000, "\x00"])
def test_garbage_is_rejected_without_crashing(idp, junk):
    _, server = idp
    with pytest.raises(AuthError):
        verifier(server).user_id(junk)


def test_a_rotated_in_key_is_picked_up_without_waiting_for_the_cache_to_expire(idp):
    signer, server = idp
    v = verifier(server)
    v.user_id(signer.token())  # caches k1
    newer = Signer("k2")
    server.keys = [signer.jwk, newer.jwk]
    assert v.user_id(newer.token("rotated")) == "rotated"
    assert server.requests == 2


def test_unknown_key_ids_cannot_make_the_server_hammer_the_identity_provider(idp):
    signer, server = idp
    now = [1000.0]
    v = verifier(server, clock=lambda: now[0])
    v.user_id(signer.token())
    stranger = Signer("nobody")
    for _ in range(20):
        with pytest.raises(AuthError):
            v.user_id(stranger.token())
    assert server.requests == 2  # the first fetch plus a single forced refresh
    now[0] += 31
    with pytest.raises(AuthError):
        v.user_id(stranger.token())
    assert server.requests == 3  # allowed again after the interval


def test_an_unreachable_identity_provider_is_a_service_problem_not_a_bad_token(idp):
    signer, server = idp
    server.down = True
    with pytest.raises(AuthUnavailable):
        verifier(server).user_id(signer.token())


def test_a_key_already_cached_keeps_working_if_the_provider_goes_down(idp):
    signer, server = idp
    v = verifier(server)
    v.user_id(signer.token())
    server.down = True
    assert v.user_id(signer.token("still-here")) == "still-here"
```

- [ ] **Step 3: Run the tests to see them fail**

```bash
uv run pytest tests/test_auth.py -q
```

Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 4: Create `src/scraper_api/auth.py`**

PyJWT's `PyJWKClient` caches keys for five minutes and does **not** refetch when it meets an unknown key id (checked by running it), so a key rotation would lock everyone out. The verifier forces one refresh, at most once per 30 seconds, so forged key ids cannot make the API hammer the web app. Only the allowed algorithm list is accepted, which rules out `alg: none` and the HS256-with-the-public-key trick.

```python
"""Verify the bearer tokens the web app's Better Auth issues, using the public keys it publishes."""

import threading
import time
from collections.abc import Callable

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

MAX_TOKEN_CHARS = 4096
LEEWAY_SECONDS = 10


class AuthError(Exception):
    """The token is missing, malformed, expired or not meant for this API."""


class AuthUnavailable(Exception):
    """The token could not be checked because the key server cannot be reached."""


class JWKSVerifier:
    def __init__(
        self,
        jwks_url: str,
        issuer: str,
        audience: str,
        *,
        algorithms: tuple[str, ...] = ("EdDSA",),
        refresh_interval: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self.algorithms = list(algorithms)
        self.refresh_interval = refresh_interval
        self._clock = clock
        self._client = PyJWKClient(jwks_url, cache_keys=True, lifespan=300, timeout=5)
        self._lock = threading.Lock()
        self._last_refresh: float | None = None

    def _signing_key(self, token: str):
        try:
            return self._client.get_signing_key_from_jwt(token)
        except PyJWKClientConnectionError as exc:
            raise AuthUnavailable("Cannot reach the sign-in service's key server") from exc
        except PyJWKClientError:
            pass  # most likely a key id we have not seen: the provider may have rotated its keys
        with self._lock:  # one forced refresh per interval, so forged key ids cannot flood the provider
            now = self._clock()
            if self._last_refresh is not None and now - self._last_refresh < self.refresh_interval:
                raise AuthError("Unknown signing key")
            self._last_refresh = now
            try:
                self._client.get_jwk_set(refresh=True)
                return self._client.get_signing_key_from_jwt(token)
            except PyJWKClientConnectionError as exc:
                raise AuthUnavailable("Cannot reach the sign-in service's key server") from exc
            except PyJWKClientError as exc:
                raise AuthError("Unknown signing key") from exc

    def verify(self, token: str) -> dict:
        if not token or len(token) > MAX_TOKEN_CHARS:
            raise AuthError("Missing or oversized token")
        try:
            key = self._signing_key(token)
            return jwt.decode(
                token,
                key.key,
                algorithms=self.algorithms,
                audience=self.audience,
                issuer=self.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except (AuthError, AuthUnavailable):
            raise
        except jwt.PyJWTError as exc:
            raise AuthError(str(exc)) from exc

    def user_id(self, token: str) -> str:
        sub = self.verify(token).get("sub")
        if not isinstance(sub, str) or not sub.strip():
            raise AuthError("Token has no subject")
        return sub
```

- [ ] **Step 5: Run the tests to see them pass**

```bash
uv run pytest tests/test_auth.py -q
```

Expected: PASS (output contains `passed`)

- [ ] **Step 6: Commit**

```bash
git add src/scraper_api/auth.py tests/jwks_support.py tests/test_auth.py
git commit -m "Verify the web app's bearer tokens against its published keys"
```

### Task 9: Session registry, run slots and rate limiter

Each person's `Session` lives in memory while they are active and in Postgres always. The registry builds it with that person's store, meter and hosted fetch, so nothing one user does can touch another's.

**Files:**
- Create: `src/scraper_api/registry.py`, `tests/test_registry.py`

**Interfaces:**
- Consumes: `PgSessionStore`, `UsageMeter`, `make_hosted_fetch`, `Session`.
- Produces: `registry.SessionRegistry(cfg, *, pool, checkpointer, llm, usage_store, idle_seconds=1800.0, clock, fetch_base, url_check, model_factory)` with `async get(user_id) -> Entry`, `peek(user_id)`, `forget(user_id)`, `evict_idle() -> int`, `__len__`; `Entry(user_id, session, meter, last_used, lock)`; `RunSlots(limit).hold()` (context manager raising `TooBusy`); `RateLimiter(limit).check(user_id)` raising `RateLimited(retry_after)`.

- [ ] **Step 1: Create `tests/test_registry.py`**

```python
import asyncio

import pytest

from fakes import FakeLLM, make_fetch, make_page
from scraper.config import Config
from scraper.usage import MemoryUsageStore
from scraper_api.registry import RateLimited, RateLimiter, RunSlots, SessionRegistry, TooBusy

A = "https://site.test/a"
CFG = Config(hosted=True, request_delay=0.0, max_stored_pages=10)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def make_registry(pg):
    def build(clock=None, **kw):
        web = {A: make_page(A, markdown="# A\n\nText of A.")}
        return SessionRegistry(
            CFG,
            pool=pg,
            checkpointer=None,
            llm=FakeLLM(),
            usage_store=MemoryUsageStore(),
            clock=clock or Clock(),
            fetch_base=make_fetch(web),
            url_check=lambda url: None,
            **kw,
        )

    return build


async def test_a_user_gets_the_same_session_every_time(make_registry):
    registry = make_registry()
    first = await registry.get("alice")
    assert await registry.get("alice") is first
    assert await registry.get("bob") is not first


async def test_simultaneous_first_requests_create_one_session(make_registry):
    registry = make_registry()
    entries = await asyncio.gather(*(registry.get("alice") for _ in range(8)))
    assert len({id(e) for e in entries}) == 1 and len(registry) == 1


async def test_users_sessions_do_not_share_pages(make_registry):
    registry = make_registry()
    alice, bob = await registry.get("alice"), await registry.get("bob")
    await alice.session.scrape(A)
    assert len(alice.session.pages) == 1 and bob.session.pages == []


async def test_an_idle_session_is_evicted_and_comes_back_from_postgres(make_registry):
    clock = Clock()
    registry = make_registry(clock, idle_seconds=60)
    entry = await registry.get("alice")
    await entry.session.scrape(A)
    clock.now += 61
    assert registry.evict_idle() == 1 and len(registry) == 0
    restored = await registry.get("alice")
    assert restored is not entry
    assert [p.url for p in restored.session.pages] == [A]


async def test_a_session_in_use_is_never_evicted(make_registry):
    clock = Clock()
    registry = make_registry(clock, idle_seconds=60)
    entry = await registry.get("alice")
    entry.session.busy = True
    clock.now += 600
    assert registry.evict_idle() == 0
    entry.session.busy = False
    assert registry.evict_idle() == 1


async def test_a_recently_used_session_is_kept(make_registry):
    clock = Clock()
    registry = make_registry(clock, idle_seconds=60)
    await registry.get("alice")
    clock.now += 50
    await registry.get("alice")
    clock.now += 50
    assert registry.evict_idle() == 0


async def test_the_sessions_fetch_blocks_private_addresses_and_counts_pages(pg):
    registry = SessionRegistry(
        Config(hosted=True, request_delay=0.0, daily_pages=1), pool=pg, checkpointer=None, llm=FakeLLM(),
        usage_store=MemoryUsageStore(), fetch_base=make_fetch({A: make_page(A)}),
    )  # the real address check is left in place
    entry = await registry.get("alice")
    with pytest.raises(Exception, match="not a public address|Could not find"):
        await entry.session.scrape("http://127.0.0.1/")
    assert entry.meter.snapshot()["pages"] == 0


async def test_forgetting_a_user_drops_the_live_session(make_registry):
    registry = make_registry()
    await registry.get("alice")
    registry.forget("alice")
    assert len(registry) == 0
    registry.forget("nobody")  # harmless


def test_run_slots_refuse_work_beyond_the_cap_and_free_up_afterwards():
    slots = RunSlots(2)
    with slots.hold(), slots.hold():
        with pytest.raises(TooBusy):
            with slots.hold():
                pass
        assert slots.active == 2
    assert slots.active == 0
    with slots.hold():
        assert slots.active == 1


def test_a_run_slot_is_released_when_the_work_fails():
    slots = RunSlots(1)
    with pytest.raises(RuntimeError):
        with slots.hold():
            raise RuntimeError("boom")
    assert slots.active == 0


def test_the_rate_limiter_allows_a_burst_up_to_the_limit_then_says_when_to_retry():
    clock = Clock()
    limiter = RateLimiter(3, clock=clock)
    for _ in range(3):
        limiter.check("alice")
    with pytest.raises(RateLimited) as caught:
        limiter.check("alice")
    assert 1 <= caught.value.retry_after <= 61
    limiter.check("bob")  # someone else is unaffected
    clock.now += 61
    limiter.check("alice")  # the window has slid past the old calls
```

- [ ] **Step 2: Run the tests to see them fail**

```bash
uv run pytest tests/test_registry.py -q
```

Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Create `src/scraper_api/registry.py`**

`get` takes a per-user creation lock so eight simultaneous first requests build one `Session`, not eight. `evict_idle` never drops a session that is busy; an evicted session comes back from Postgres on the next request.

```python
"""One live Session per signed-in user, a cap on simultaneous work, and a per-user request limit."""

import asyncio
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field

from psycopg_pool import ConnectionPool

from scraper import fetch as fetch_mod
from scraper.config import Config
from scraper.hosted import make_hosted_fetch
from scraper.netguard import check_public_url
from scraper.session import Session
from scraper.usage import UsageMeter, UsageStore

from .pgstore import PgSessionStore


class TooBusy(Exception):
    """The server is already doing as much at once as it is set up for."""


class RateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__(f"Too many requests. Try again in {retry_after} seconds.")
        self.retry_after = retry_after


class RunSlots:
    """A cap on streams (runs, questions, scrapes) in flight across all users. The API is one process, so this protects it."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.active = 0

    @contextmanager
    def hold(self):
        if self.active >= self.limit:
            raise TooBusy("The server is busy with other runs. Try again in a minute.")
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1


class RateLimiter:
    """At most `limit` calls per user in any 60 seconds."""

    def __init__(self, limit: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self._clock = clock
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()  # check() runs in FastAPI's thread pool

    def check(self, user_id: str) -> None:
        with self._lock:
            now = self._clock()
            calls = self._calls[user_id]
            while calls and now - calls[0] >= 60:
                calls.popleft()
            if len(calls) >= self.limit:
                raise RateLimited(max(1, int(60 - (now - calls[0])) + 1))
            calls.append(now)
            if len(self._calls) > 10_000:  # forget users who have gone quiet
                for user in [u for u, c in self._calls.items() if not c or now - c[-1] >= 60]:
                    del self._calls[user]


@dataclass
class Entry:
    user_id: str
    session: Session
    meter: UsageMeter
    last_used: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)  # taken by whoever is changing the session's settings


class SessionRegistry:
    def __init__(
        self,
        cfg: Config,
        *,
        pool: ConnectionPool,
        checkpointer,
        llm,
        usage_store: UsageStore,
        idle_seconds: float = 1800.0,
        clock: Callable[[], float] = time.monotonic,
        fetch_base: Callable[..., fetch_mod.Page] = fetch_mod.fetch,
        url_check: Callable[[str], None] = check_public_url,
        model_factory=None,
    ) -> None:
        self.cfg = cfg
        self.pool = pool
        self.checkpointer = checkpointer
        self.llm = llm
        self.usage_store = usage_store
        self.idle_seconds = idle_seconds
        self._clock = clock
        self._fetch_base = fetch_base
        self._url_check = url_check
        self._model_factory = model_factory
        self._entries: dict[str, Entry] = {}
        self._creating: dict[str, asyncio.Lock] = {}

    async def get(self, user_id: str) -> Entry:
        entry = self._entries.get(user_id)
        if entry is None:
            async with self._creating.setdefault(user_id, asyncio.Lock()):
                entry = self._entries.get(user_id)
                if entry is None:
                    entry = await self._create(user_id)
                    self._entries[user_id] = entry
            self._creating.pop(user_id, None)
        entry.last_used = self._clock()
        return entry

    async def _create(self, user_id: str) -> Entry:
        meter = UsageMeter(user_id, self.usage_store, self.cfg)
        session = Session(
            self.cfg,
            self.llm,
            make_hosted_fetch(self.cfg, meter, base=self._fetch_base, check=self._url_check),
            store=PgSessionStore(self.pool, user_id, self.cfg),
            checkpointer=self.checkpointer,
            providers={self.llm.key: self.llm},
            usage=meter,
            model_factory=self._model_factory,
        )
        await session.restore()
        return Entry(user_id, session, meter, self._clock())

    def evict_idle(self) -> int:
        """Drop sessions nobody has touched for a while. Their state is in Postgres, so the next request restores it."""
        now = self._clock()
        stale = [u for u, e in self._entries.items() if not e.session.busy and now - e.last_used > self.idle_seconds]
        for user_id in stale:
            del self._entries[user_id]
        return len(stale)

    def peek(self, user_id: str) -> Entry | None:
        """The live entry if there is one. Never creates a session."""
        return self._entries.get(user_id)

    def forget(self, user_id: str) -> None:
        self._entries.pop(user_id, None)

    def __len__(self) -> int:
        return len(self._entries)
```

- [ ] **Step 4: Run the tests to see them pass**

Confirm the summary says 0 skipped.

```bash
uv run pytest tests/test_registry.py -q -rs
```

Expected: PASS (output contains `passed`)

- [ ] **Step 5: Commit**

```bash
git add src/scraper_api/registry.py tests/test_registry.py
git commit -m "Keep one live session per user, with run and request limits"
```

### Task 10: Account deletion and the Postgres checkpointer

Checkpoint tables are created by LangGraph, so they may not exist yet on a fresh database; deletion checks first.

**Files:**
- Create: `src/scraper_api/accounts.py`, `tests/test_pg_checkpoint.py`

**Interfaces:**
- Consumes: `db.open_checkpointer`, `db.USER_TABLES`, `SessionRegistry`.
- Produces: `accounts.delete_user_data(pool, user_id) -> None`, removing the user's rows in every table and their checkpoints.

- [ ] **Step 1: Create `tests/test_pg_checkpoint.py`**

These tests use the real Postgres checkpointer, not an in-memory one: a paused run survives a simulated restart, a run whose checkpoint was lost is ended and not resumed, checkpoints are namespaced by user and deleted by prefix, and the tables are in the `app` schema only.

```python
from contextlib import AsyncExitStack

from fakes import FakeLLM, ScriptedChat, ai, call, make_fetch, make_page
from scraper.config import Config
from scraper.usage import MemoryUsageStore
from scraper_api.accounts import delete_user_data
from scraper_api.db import open_checkpointer
from scraper_api.registry import SessionRegistry

A = "https://site.test/a"
CFG = Config(hosted=True, request_delay=0.0)


def registry_with(pg, saver, script):
    model = ScriptedChat(script=list(script))
    registry = SessionRegistry(
        CFG, pool=pg, checkpointer=saver, llm=FakeLLM(), usage_store=MemoryUsageStore(),
        fetch_base=make_fetch({A: make_page(A)}), url_check=lambda url: None, model_factory=lambda model_id: model,
    )
    return registry, model


def thread_ids(pg):
    with pg.connection() as conn:
        return sorted(r[0] for r in conn.execute("SELECT DISTINCT thread_id FROM app.checkpoints"))


async def test_a_paused_run_survives_a_restart_and_its_checkpoints_belong_to_the_user(pg, pg_url):
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(pg_url, stack)
        delete_user_data(pg, "alice")  # a clean slate even if an earlier run was interrupted
        first, _ = registry_with(pg, saver, [ai("", call("ask_user", "c1", question="Which plan?"))])
        entry = await first.get("alice")
        await _drain(entry.session.run_agent("pick a plan", "qwen-test"))
        assert entry.session.run.status == "paused"
        assert thread_ids(pg) and all(t.startswith("alice:") for t in thread_ids(pg))

        # the server restarts: nothing is in memory, everything is in Postgres
        second, _ = registry_with(pg, saver, [ai("Starter it is.")])
        restored = (await second.get("alice")).session
        assert restored.run.status == "paused" and restored.pending["question"] == "Which plan?"
        events = await _drain(restored.resume_run("Starter"))
        assert events[-1].kind == "done" and restored.run.status == "done"
        delete_user_data(pg, "alice")


async def test_a_run_that_lost_its_checkpoint_is_ended_not_resumed(pg, pg_url):
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(pg_url, stack)
        delete_user_data(pg, "alice")
        first, _ = registry_with(pg, saver, [ai("", call("ask_user", "c1", question="Which?"))])
        entry = await first.get("alice")
        await _drain(entry.session.run_agent("x", "qwen-test"))
        with pg.connection() as conn:
            for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                conn.execute(f"DELETE FROM app.{table} WHERE starts_with(thread_id, 'alice:')")
        second, _ = registry_with(pg, saver, [])
        assert (await second.get("alice")).session.run.status == "ended"
        delete_user_data(pg, "alice")


async def test_deleting_an_account_removes_that_users_checkpoints_and_no_one_elses(pg, pg_url):
    async with AsyncExitStack() as stack:
        saver = await open_checkpointer(pg_url, stack)
        for user in ("alice", "bob"):
            delete_user_data(pg, user)
        registry, model = registry_with(pg, saver, [])
        for user in ("alice", "bob"):
            model.script = [ai("", call("ask_user", "c1", question="Which?"))]
            await _drain((await registry.get(user)).session.run_agent("x", "qwen-test"))
        assert {t.split(":")[0] for t in thread_ids(pg)} == {"alice", "bob"}
        delete_user_data(pg, "alice")
        assert {t.split(":")[0] for t in thread_ids(pg)} == {"bob"}
        delete_user_data(pg, "bob")
        assert thread_ids(pg) == []


async def test_the_checkpoint_tables_live_in_the_app_schema_only(pg, pg_url):
    async with AsyncExitStack() as stack:
        await open_checkpointer(pg_url, stack)
        with pg.connection() as conn:
            schemas = {r[0] for r in conn.execute("SELECT table_schema FROM information_schema.tables WHERE table_name = 'checkpoints'")}
    assert schemas == {"app"}


def test_deleting_data_before_the_checkpointer_ever_ran_does_not_fail(pg):
    delete_user_data(pg, "nobody")  # the checkpoint tables may not exist yet on a fresh database


async def _drain(stream):
    return [event async for event in stream]
```

- [ ] **Step 2: Run the tests to see them fail**

```bash
uv run pytest tests/test_pg_checkpoint.py -q
```

Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Create `src/scraper_api/accounts.py`**

```python
"""Deleting everything the service holds about one user."""

from psycopg_pool import ConnectionPool

from .db import USER_TABLES

CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")


def delete_user_data(pool: ConnectionPool, user_id: str) -> None:
    """Remove the user's session, pages, results, history, runs, preferences, usage and agent checkpoints.

    Checkpoint thread ids start with `<user id>:`, which PgSessionStore.session_id guarantees."""
    with pool.connection() as conn:
        for table in USER_TABLES:
            conn.execute(f"DELETE FROM app.{table} WHERE user_id = %s", (user_id,))  # noqa: S608 (fixed table names)
        for table in CHECKPOINT_TABLES:
            if conn.execute("SELECT to_regclass(%s)", (f"app.{table}",)).fetchone()[0] is not None:  # absent until the saver's setup ran
                conn.execute(f"DELETE FROM app.{table} WHERE starts_with(thread_id, %s)", (f"{user_id}:",))  # noqa: S608
```

- [ ] **Step 4: Run the tests to see them pass**

Confirm the summary says 0 skipped.

```bash
uv run pytest tests/test_pg_checkpoint.py -q -rs
```

Expected: PASS (output contains `passed`)

- [ ] **Step 5: Commit**

```bash
git add src/scraper_api/accounts.py tests/test_pg_checkpoint.py
git commit -m "Delete a user's data and agent checkpoints together"
```

### Task 11: The API: sign-in, state, settings and downloads

The read side of the API, and the whole authentication story. The streaming routes follow in Task 12; until then `/ask` and friends return 404.

**Files:**
- Create: `src/scraper_api/errors.py`, `catalog.py`, `deps.py`, `schemas.py`, `events.py`, `routes.py`, `app.py` (all under `src/scraper_api/`)
- Create: `tests/api_support.py`, `tests/test_api_state.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: Everything from Tasks 3 to 10.
- Produces: `app.create_app(cfg, deps=None) -> FastAPI`; `deps.Deps`, `deps.current_user`, `deps.get_entry`; `errors.ApiError`; `catalog.ModelCatalog`; `schemas.*` request bodies and `build_state`; `events.frame`, `events.error_frame`, `events.event_dict`. Routes: `GET /health`, `GET /state`, `GET /models`, `GET /pages/{index}`, `GET /context`, `PUT /settings/{name}`, `PUT /model`, `GET /rows.csv`, `GET /rows.json`, `DELETE /account-data`. Test fixtures `api` and `make_api`; helpers `Harness`, `seed_page`, `frames`, `names`.

- [ ] **Step 1: Create `tests/api_support.py`**

`Harness` assembles the whole API on the real Postgres with a scripted model, a fake web and a throwaway identity provider. `seed_page` puts a page into a user's session without going through a route, so a test needs only the code it is testing.

```python
import json

import httpx
from langgraph.checkpoint.memory import InMemorySaver

from fakes import FakeLLM, ScriptedChat, make_fetch, make_page
from jwks_support import AUDIENCE, ISSUER, JWKSServer, Signer
from scraper.config import Config
from scraper.llm import Event, ModelInfo
from scraper.usage import MemoryUsageStore
from scraper_api.app import create_app
from scraper_api.auth import JWKSVerifier
from scraper_api.catalog import ModelCatalog
from scraper_api.deps import Deps
from scraper_api.registry import RateLimiter, RunSlots, SessionRegistry

A = "https://site.test/a"
B = "https://site.test/b"
WEB = {A: make_page(A, title="Page A", markdown="# A\n\nStarter is $10."), B: make_page(B, title="Page B")}
MODEL = "qwen-test"


class Harness:
    """The whole API on a real Postgres, with a scripted model, a fake web and a throwaway identity provider."""

    def __init__(self, pg, url_check=None, **cfg_overrides):
        self.signer = Signer("k1")
        self.idp = JWKSServer(self.signer)
        self.cfg = Config(
            hosted=True, request_delay=0.0, cors_origins="https://web.test", service_secret="s3cret", max_stored_pages=20,
            **cfg_overrides,
        )
        self.llm = FakeLLM(events=[Event("reasoning", "hmm"), Event("answer", "Ten dollars.")],
                           models=[ModelInfo(MODEL, "llm", True, 100_000), ModelInfo("other-model", "llm", True, 100_000)])
        self.model = ScriptedChat()
        self.pool = pg
        self.url_check = url_check or (lambda url: None)
        self.web = dict(WEB)

    def build(self):
        registry = SessionRegistry(
            self.cfg, pool=self.pool, checkpointer=InMemorySaver(), llm=self.llm, usage_store=MemoryUsageStore(),
            fetch_base=lambda url, mode="fast", *, timeout=20.0: make_fetch(self.web)(url, mode, timeout=timeout),
            url_check=self.url_check, model_factory=lambda model_id: self.model,
        )
        self.deps = Deps(
            cfg=self.cfg, verifier=JWKSVerifier(self.idp.url, ISSUER, AUDIENCE), registry=registry,
            slots=RunSlots(self.cfg.max_concurrent_runs), limiter=RateLimiter(self.cfg.requests_per_minute),
            catalog=ModelCatalog(self.llm, self.cfg.allowed_model_list), pool=self.pool,
        )
        self.app = create_app(self.cfg, self.deps)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://api.test")
        return self

    def auth(self, user="alice", **claims):
        return {"Authorization": f"Bearer {self.signer.token(user, **claims)}"}

    async def close(self):
        await self.client.aclose()
        self.idp.close()


def frames(response):
    """Parse a text/event-stream body into [(event name, data)]."""
    out = []
    for block in response.text.split("\n\n"):
        if not block.strip():
            continue
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        out.append((name, data))
    return out


def names(response):
    return [n for n, _ in frames(response)]


async def seed_page(api, user="alice", url=A):
    """Give the user a scraped page by going straight to their session, so a test needs no route to do it."""
    entry = await api.deps.registry.get(user)
    await entry.session.scrape(url)
    return entry
```

- [ ] **Step 2: Add the API fixtures to `tests/conftest.py`**

```python
@pytest.fixture
async def api(pg):
    from api_support import Harness

    harness = Harness(pg).build()
    yield harness
    await harness.close()


@pytest.fixture
async def make_api(pg):
    from api_support import Harness

    made = []

    def build(**overrides):
        harness = Harness(pg, **overrides).build()
        made.append(harness)
        return harness

    yield build
    for harness in made:
        await harness.close()
```

- [ ] **Step 3: Create `tests/test_api_state.py`**

```python
import json

import pytest

from api_support import A, MODEL, WEB, seed_page


# ---- sign-in ------------------------------------------------------------------------


async def test_health_is_open_and_everything_else_needs_a_token(api):
    assert (await api.client.get("/health")).json() == {"ok": True}
    for method, path in [("GET", "/state"), ("GET", "/rows.csv"), ("GET", "/rows.json"), ("GET", "/models"), ("GET", "/pages/0"), ("GET", "/context"), ("PUT", "/model"), ("PUT", "/settings/url")]:
        response = await api.client.request(method, path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "unauthorized"
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["", "Bearer", "Bearer ", "Basic abc", "Bearer not.a.token", "token abc"])
async def test_malformed_credentials_are_rejected(api, header):
    response = await api.client.get("/state", headers={"Authorization": header} if header else {})
    assert response.status_code == 401


async def test_an_expired_or_foreign_token_is_rejected_without_saying_why(api):
    for headers in (api.auth(exp=1), api.auth(aud="https://elsewhere.test"), api.auth(iss="https://evil.test")):
        response = await api.client.get("/state", headers=headers)
        assert response.status_code == 401 and "expired" in response.json()["error"]["message"].lower()


async def test_a_valid_token_reaches_the_route(api):
    assert (await api.client.get("/state", headers=api.auth())).status_code == 200


async def test_an_unreachable_key_server_is_a_503_not_a_401(api):
    api.idp.down = True
    response = await api.client.get("/state", headers=api.auth())
    assert response.status_code == 503 and response.json()["error"]["code"] == "auth_unavailable"


async def test_the_per_user_request_rate_is_limited_with_a_retry_after(make_api):
    api = make_api(requests_per_minute=3)
    for _ in range(3):
        assert (await api.client.get("/state", headers=api.auth("alice"))).status_code == 200
    blocked = await api.client.get("/state", headers=api.auth("alice"))
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1
    assert (await api.client.get("/state", headers=api.auth("bob"))).status_code == 200


# ---- state ------------------------------------------------------------------------------


async def test_a_new_users_state_is_empty_and_lists_models_usage_and_modes(api):
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert state["user"] == {"id": "alice"}
    assert (state["pages"], state["rows"], state["history"], state["run"]) == ([], [], [], None)
    assert [m["id"] for m in state["models"]] == [MODEL, "other-model"] and state["models_error"] is None
    assert state["fetch_modes"] == ["fast"]
    assert state["usage"]["model_calls"] == 0 and state["usage"]["pages_limit"] == 300
    assert state["busy"] is False


async def test_the_model_list_can_be_narrowed_by_configuration(make_api):
    api = make_api(allowed_models=MODEL)
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert [m["id"] for m in state["models"]] == [MODEL]


async def test_state_describes_pages_without_sending_their_text(api):
    await seed_page(api)
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert state["pages"] == [{"url": A, "title": "Page A", "chars": len(WEB[A].markdown), "links": 0, "looks_empty": False, "used": False}]
    assert "markdown" not in json.dumps(state)


# ---- reading pages, context and results -----------------------------------------------------------------------------


async def test_a_pages_text_is_available_for_preview_by_position(api):
    await seed_page(api)
    preview = (await api.client.get("/pages/0", headers=api.auth())).json()
    assert preview == {"url": A, "title": "Page A", "markdown": WEB[A].markdown}
    for bad in ("/pages/1", "/pages/-1"):
        assert (await api.client.get(bad, headers=api.auth())).status_code == 404
    assert (await api.client.get("/pages/x", headers=api.auth())).status_code == 422


async def test_the_context_is_empty_until_a_question_has_been_asked(api):
    assert (await api.client.get("/context", headers=api.auth())).json() == {"text": "", "sources": [], "mode": None}


async def test_results_download_as_csv_and_json_and_spreadsheet_formulas_are_defused(api):
    entry = await api.deps.registry.get("alice")
    entry.session.rows.extend([{"plan": "Starter", "price": "$10"}, {"plan": "=HYPERLINK(\"http://evil.test\")", "price": "$40"}])
    csv = await api.client.get("/rows.csv", headers=api.auth())
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv.headers["content-disposition"] and csv.headers["x-content-type-options"] == "nosniff"
    assert csv.text.splitlines()[1] == "Starter,$10" and csv.text.splitlines()[2].startswith("\"'=HYPERLINK")
    assert json.loads((await api.client.get("/rows.json", headers=api.auth())).text)[0] == {"plan": "Starter", "price": "$10"}
    assert (await api.client.get("/rows.csv", headers=api.auth("bob"))).text == ""


# ---- settings and the model choice ------------------------------------------------------------------------------


async def test_form_settings_and_the_chosen_model_are_remembered_per_user(api):
    assert (await api.client.put("/settings/url", json={"value": "https://site.test/a"}, headers=api.auth())).status_code == 204
    assert (await api.client.put("/model", json={"id": MODEL}, headers=api.auth())).status_code == 204
    alice = (await api.client.get("/state", headers=api.auth())).json()
    assert alice["settings"]["url"] == "https://site.test/a" and alice["model"] == MODEL
    bob = (await api.client.get("/state", headers=api.auth("bob"))).json()
    assert bob["settings"]["url"] == "" and bob["model"] is None


async def test_only_known_settings_and_listed_models_can_be_saved(api):
    assert (await api.client.put("/settings/provider", json={"value": "x"}, headers=api.auth())).status_code == 404
    assert (await api.client.put("/settings/../x", json={"value": "x"}, headers=api.auth())).status_code in (404, 405)
    assert (await api.client.put("/model", json={"id": "gpt-9000"}, headers=api.auth())).status_code == 400
    assert (await api.client.put("/settings/url", json={"value": "x" * 3000}, headers=api.auth())).status_code == 422


# ---- bad input and headers -------------------------------------------------------------------------------------


async def test_responses_are_never_cached_and_not_sniffed(api):
    response = await api.client.get("/state", headers=api.auth())
    assert response.headers["cache-control"] == "no-store" and response.headers["x-content-type-options"] == "nosniff"


async def test_the_api_documentation_pages_are_not_served(api):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await api.client.get(path)).status_code == 404


async def test_only_the_configured_web_origin_may_call_the_api_from_a_browser(api):
    allowed = await api.client.options(
        "/state", headers={"Origin": "https://web.test", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"}
    )
    assert allowed.headers["access-control-allow-origin"] == "https://web.test"
    assert "access-control-allow-credentials" not in allowed.headers
    denied = await api.client.options("/state", headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in denied.headers


# ---- deleting an account ----------------------------------------------------------------------------------------------


async def test_account_data_is_deleted_for_that_user_only_and_needs_the_service_secret(api):
    for user in ("alice", "bob"):
        await seed_page(api, user)
    for headers in ({}, {"X-Service-Secret": "wrong"}, {"X-Service-Secret": ""}):
        refused = await api.client.request("DELETE", "/account-data", json={"user_id": "alice"}, headers=headers)
        assert refused.status_code == 401
    assert (await api.client.request("DELETE", "/account-data", json={"user_id": "alice"}, headers={"X-Service-Secret": "s3cret"})).status_code == 204
    assert (await api.client.get("/state", headers=api.auth("alice"))).json()["pages"] == []
    assert len((await api.client.get("/state", headers=api.auth("bob"))).json()["pages"]) == 1
    with api.pool.connection() as conn:
        assert conn.execute("SELECT count(*) FROM app.pages WHERE user_id = 'alice'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM app.pages WHERE user_id = 'bob'").fetchone()[0] == 1


async def test_a_user_token_cannot_delete_account_data(api):
    response = await api.client.request("DELETE", "/account-data", json={"user_id": "bob"}, headers=api.auth("alice"))
    assert response.status_code == 401
```

- [ ] **Step 4: Run the tests to see them fail**

```bash
uv run pytest tests/test_api_state.py -q
```

Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 5: Create `src/scraper_api/errors.py`**

One error shape everywhere. Validation errors name the field and the rule but never echo the value, which could be a secret or a pasted page.

```python
"""One error shape for every failure: {"error": {"code": ..., "message": ..., ...}}."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, headers: dict[str, str] | None = None, **extra) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}
        self.extra = extra


def error_body(code: str, message: str, **extra) -> dict:
    return {"error": {"code": code, "message": message, **extra}}


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(error_body(exc.code, exc.message, **exc.extra), status_code=exc.status, headers=exc.headers)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Name the field and the rule, never the value: it may be a secret or a whole pasted page.
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
    return JSONResponse(error_body("invalid", f"{where or 'request'}: {first.get('msg', 'is not valid')}"), status_code=422)
```

- [ ] **Step 6: Create `src/scraper_api/catalog.py`**

Users may only pick models the provider lists, optionally narrowed by `SCRAPER_ALLOWED_MODELS`. Without this check anyone could choose the most expensive model on your key.

```python
"""The models users may pick: what the provider lists, narrowed by SCRAPER_ALLOWED_MODELS, cached for a few minutes."""

import time
from collections.abc import Callable

from scraper.llm import LLMUnavailable, ModelInfo

from .errors import ApiError


class ModelCatalog:
    def __init__(self, llm, allowed: tuple[str, ...] = (), *, ttl: float = 300.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.llm = llm
        self.allowed = allowed
        self.ttl = ttl
        self._clock = clock
        self._cached: list[ModelInfo] | None = None
        self._fetched_at = 0.0

    async def models(self) -> list[ModelInfo]:
        """Raises LLMUnavailable only when the provider cannot be reached and nothing is cached."""
        fresh = self._cached is not None and self._clock() - self._fetched_at < self.ttl
        if not fresh:
            try:
                listed = await self.llm.list_models()
            except LLMUnavailable:
                if self._cached is None:
                    raise
            else:
                self._cached = [m for m in listed if not self.allowed or m.id in self.allowed]
                self._fetched_at = self._clock()
        return list(self._cached)

    async def safe_models(self) -> tuple[list[ModelInfo], str | None]:
        try:
            return await self.models(), None
        except LLMUnavailable as exc:
            return [], str(exc)

    async def require(self, model_id: str) -> ModelInfo:
        try:
            models = await self.models()
        except LLMUnavailable as exc:
            raise ApiError(503, "model_unavailable", str(exc)) from exc
        for model in models:
            if model.id == model_id:
                return model
        raise ApiError(400, "unknown_model", "That model is not available. Pick one from the list.")
```

- [ ] **Step 7: Create `src/scraper_api/deps.py`**

`current_user` is a plain `def` so FastAPI runs it in a thread: fetching keys can block. Missing or bad credentials are `401`; an unreachable key server is `503`; the per-user rate limit is applied here, once for every authenticated route.

```python
"""What the routes share: the service objects, and the dependency that turns a bearer token into a user."""

from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Request
from psycopg_pool import ConnectionPool

from scraper.config import Config

from .auth import AuthError, AuthUnavailable, JWKSVerifier
from .catalog import ModelCatalog
from .errors import ApiError
from .registry import Entry, RateLimited, RateLimiter, RunSlots, SessionRegistry


@dataclass
class Deps:
    cfg: Config
    verifier: JWKSVerifier
    registry: SessionRegistry
    slots: RunSlots
    limiter: RateLimiter
    catalog: ModelCatalog
    pool: ConnectionPool
    checkpointer: Any = None


def get_deps(request: Request) -> Deps:
    return request.app.state.deps


def current_user(request: Request, deps: Deps = Depends(get_deps)) -> str:
    """The signed-in user's id. A plain `def`, so FastAPI runs it in a thread: fetching keys can block."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, "unauthorized", "Sign in to continue.", headers={"WWW-Authenticate": "Bearer"})
    try:
        user_id = deps.verifier.user_id(token.strip())
    except AuthError:
        raise ApiError(401, "unauthorized", "Your sign-in has expired. Sign in again.", headers={"WWW-Authenticate": "Bearer"}) from None
    except AuthUnavailable:
        raise ApiError(503, "auth_unavailable", "Sign-in checks are unavailable right now. Try again shortly.") from None
    try:
        deps.limiter.check(user_id)
    except RateLimited as exc:
        raise ApiError(429, "rate_limited", str(exc), headers={"Retry-After": str(exc.retry_after)}) from None
    return user_id


async def get_entry(user_id: str = Depends(current_user), deps: Deps = Depends(get_deps)) -> Entry:
    return await deps.registry.get(user_id)
```

- [ ] **Step 8: Create `src/scraper_api/schemas.py`**

Every body forbids unknown fields and has length limits. `ResumeBody` accepts exactly the three answer shapes the agent understands: text, `{action, note}` and `{allow}`.

```python
"""Request bodies (with their limits) and the JSON the API sends back."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from scraper.session import Session

SETTING_NAMES = ("url", "mode", "follow", "depth", "agent")  # form values kept between visits
RESUME_ACTIONS = ("continue", "wrap_up", "stop")


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")  # an unknown field is a mistake worth reporting


class ScrapeBody(Body):
    url: str = Field(min_length=1, max_length=2048)
    mode: Literal["fast", "dynamic", "stealthy"] = "fast"
    follow: bool = False
    depth: int = Field(default=1, ge=1, le=3)


class AskBody(Body):
    question: str = Field(min_length=1, max_length=8000)
    model: str = Field(min_length=1, max_length=200)


class RetryBody(Body):
    model: str = Field(min_length=1, max_length=200)


class RunBody(Body):
    instruction: str = Field(min_length=1, max_length=8000)
    model: str = Field(min_length=1, max_length=200)
    seed_url: str | None = Field(default=None, max_length=2048)


class ResumeBody(Body):
    """What a paused run is waiting for: an answer, a limit decision, or a yes/no for a new site."""

    value: str | dict

    @field_validator("value")
    @classmethod
    def _shape(cls, value):
        if isinstance(value, str):
            if not value.strip() or len(value) > 2000:
                raise ValueError("an answer must be 1 to 2000 characters")
            return value
        unknown = set(value) - {"action", "note", "allow"}
        if unknown:
            raise ValueError(f"unexpected key {sorted(unknown)[0]!r}")
        if "action" in value and value["action"] not in RESUME_ACTIONS:
            raise ValueError(f"action must be one of {', '.join(RESUME_ACTIONS)}")
        if "allow" in value and not isinstance(value["allow"], bool):
            raise ValueError("allow must be true or false")
        if "note" in value and (not isinstance(value["note"], str) or len(value["note"]) > 2000):
            raise ValueError("note must be text of at most 2000 characters")
        if "action" not in value and "allow" not in value:
            raise ValueError("give an action or allow")
        return value


class SteerBody(Body):
    text: str = Field(min_length=1, max_length=2000)


class SettingBody(Body):
    value: str = Field(max_length=2048)


class ModelBody(Body):
    id: str = Field(min_length=1, max_length=200)


class DeleteAccountBody(Body):
    user_id: str = Field(min_length=1, max_length=200)


# ---- responses ----------------------------------------------------------------------


def page_summary(page, used: set[str]) -> dict:
    return {
        "url": page.url,
        "title": page.title,
        "chars": len(page.markdown),
        "links": len(page.links),
        "looks_empty": page.looks_empty,
        "used": page.url in used,
    }


def run_dict(session: Session) -> dict | None:
    run = session.run
    if run is None:
        return None
    return {
        "number": run.number,
        "instruction": run.instruction,
        "model": run.model,
        "status": run.status,
        "resumable": session.resumable,
        "pending": run.pending,
        "fetched": run.fetched,
        "max_pages": run.max_pages,
        "delay": run.delay,
        "rows_added": run.rows_added,
        "trace": run.trace,
        "followups": [{"id": f["id"], "text": f["text"], "delivered": f["delivered"]} for f in run.followups],
    }


def history_dict(message) -> dict:
    return {
        "role": message.role,
        "content": message.content,
        "reasoning": message.reasoning,
        "sources": list(message.sources),
        "interrupted": message.interrupted,
        "note": message.note,
        "trace": [{"name": t.name, "args": t.args, "text": t.text, "ok": t.ok} for t in message.trace],
    }


def model_dict(model) -> dict:
    return {"id": model.id, "context": model.context, "loaded": model.loaded}


def build_state(entry, models, models_error: str | None) -> dict:
    session = entry.session
    used = set(session.last_context.sources) if session.last_context else set()
    settings = {name: session.setting(name, "") for name in SETTING_NAMES}
    return {
        "user": {"id": entry.user_id},
        "provider": {"key": session.llm.key, "name": session.llm.name, "hosted": session.llm.hosted},
        "busy": session.busy,
        "pages": [page_summary(p, used) for p in session.pages],
        "failed": [{"url": url, "reason": why} for url, why in session.failed],
        "rows": session.rows,
        "history": [history_dict(m) for m in session.history],
        "run": run_dict(session),
        "progress": list(session.progress),
        "settings": settings,
        "model": session.preferred_model,
        "models": [model_dict(m) for m in models],
        "models_error": models_error,
        "fetch_modes": list(session.cfg.allowed_modes),
        "usage": entry.meter.snapshot(),
    }
```

- [ ] **Step 9: Create `src/scraper_api/events.py`**

```python
"""Server-sent events: one frame per event, plus a final `end` frame so a finished stream is not mistaken for a dropped one."""

import json
from dataclasses import asdict, is_dataclass


def event_dict(event) -> dict:
    return asdict(event) if is_dataclass(event) else dict(event)


def frame(name: str, data: dict) -> bytes:
    # json.dumps never emits a raw newline, so one `data:` line always holds the whole object
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def error_frame(code: str, message: str) -> bytes:
    return frame("error", {"kind": "error", "code": code, "text": message})
```

- [ ] **Step 10: Create `src/scraper_api/routes.py`**

```python
"""Routes that read or change a user's saved state. All but /health and /account-data act on the signed-in user's own session."""

import hmac

from fastapi import APIRouter, Depends, Header, Response

from scraper.exports import rows_to_csv, rows_to_json

from .accounts import delete_user_data
from .deps import Deps, get_deps, get_entry
from .errors import ApiError
from .registry import Entry
from .schemas import SETTING_NAMES, DeleteAccountBody, ModelBody, SettingBody, build_state, model_dict

router = APIRouter()


# ---- open ------------------------------------------------------------------------------


@router.get("/health")
def health() -> dict:
    return {"ok": True}


@router.delete("/account-data", status_code=204)
async def account_data(
    body: DeleteAccountBody, deps: Deps = Depends(get_deps), x_service_secret: str = Header(default="")
) -> Response:
    """Called by the web app when it deletes an account. Authenticated by a shared secret, not by a user token."""
    if not x_service_secret or not hmac.compare_digest(x_service_secret.encode(), deps.cfg.service_secret.encode()):
        raise ApiError(401, "unauthorized", "Not allowed.")
    entry = deps.registry.peek(body.user_id)
    if entry is not None:
        entry.session.cancel()
    deps.registry.forget(body.user_id)
    delete_user_data(deps.pool, body.user_id)
    return Response(status_code=204)


# ---- state ------------------------------------------------------------------------------


@router.get("/state")
async def state(entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> dict:
    models, error = await deps.catalog.safe_models()
    return build_state(entry, models, error)


@router.get("/models")
async def models(entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> dict:
    listed, error = await deps.catalog.safe_models()
    return {"models": [model_dict(m) for m in listed], "error": error}


@router.get("/pages/{index}")
async def page(index: int, entry: Entry = Depends(get_entry)) -> dict:
    pages = entry.session.pages
    if not 0 <= index < len(pages):
        raise ApiError(404, "not_found", "There is no such page.")
    p = pages[index]
    return {"url": p.url, "title": p.title, "markdown": p.markdown}


@router.get("/context")
async def context(entry: Entry = Depends(get_entry)) -> dict:
    last = entry.session.last_context
    if last is None:
        return {"text": "", "sources": [], "mode": None}
    return {"text": last.text, "sources": list(last.sources), "mode": last.mode}


@router.put("/settings/{name}", status_code=204)
async def setting(name: str, body: SettingBody, entry: Entry = Depends(get_entry)) -> Response:
    if name not in SETTING_NAMES:
        raise ApiError(404, "not_found", "There is no such setting.")
    entry.session.set_setting(name, body.value)
    return Response(status_code=204)


@router.put("/model", status_code=204)
async def model(body: ModelBody, entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> Response:
    await deps.catalog.require(body.id)
    entry.session.remember_model(body.id)
    return Response(status_code=204)


# ---- results -----------------------------------------------------------------------------------


@router.get("/rows.csv")
async def rows_csv(entry: Entry = Depends(get_entry)) -> Response:
    return Response(
        rows_to_csv(entry.session.rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="results.csv"', "X-Content-Type-Options": "nosniff"},
    )


@router.get("/rows.json")
async def rows_json(entry: Entry = Depends(get_entry)) -> Response:
    return Response(
        rows_to_json(entry.session.rows),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="results.json"'},
    )
```

- [ ] **Step 11: Create `src/scraper_api/app.py`**

Docs and the OpenAPI schema are off, every response is `no-store` and `nosniff`, and CORS allows only configured origins with no credentials. The stream routes are added in the next task.

```python
"""The FastAPI application: wiring, middleware and start-up."""

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from scraper.config import Config
from scraper.llm import MistralAPI
from scraper.usage import UsageStore

from .auth import JWKSVerifier
from .catalog import ModelCatalog
from .db import make_pool, migrate, open_checkpointer
from .deps import Deps
from .errors import ApiError, api_error_handler, validation_error_handler
from .pgstore import PgUsageStore
from .registry import RateLimiter, RunSlots, SessionRegistry
from .routes import router as state_router

log = logging.getLogger("scraper_api")
EVICT_EVERY_SECONDS = 60


async def build_deps(cfg: Config, stack: AsyncExitStack) -> Deps:
    pool = make_pool(cfg.database_url)
    stack.callback(pool.close)
    migrate(pool)
    checkpointer = await open_checkpointer(cfg.database_url, stack)
    llm = MistralAPI(cfg.mistral_base_url, cfg.api_key, fallback_context=cfg.fallback_context)
    usage: UsageStore = PgUsageStore(pool)
    return Deps(
        cfg=cfg,
        verifier=JWKSVerifier(cfg.jwks_url, cfg.jwt_issuer, cfg.jwt_audience),
        registry=SessionRegistry(cfg, pool=pool, checkpointer=checkpointer, llm=llm, usage_store=usage),
        slots=RunSlots(cfg.max_concurrent_runs),
        limiter=RateLimiter(cfg.requests_per_minute),
        catalog=ModelCatalog(llm, cfg.allowed_model_list),
        pool=pool,
        checkpointer=checkpointer,
    )


def create_app(cfg: Config, deps: Deps | None = None) -> FastAPI:
    """`deps` is given by tests; in production it is built at start-up from the configuration."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stack = AsyncExitStack()
        if deps is None:
            app.state.deps = await build_deps(cfg, stack)

        async def sweep() -> None:
            while True:
                await asyncio.sleep(EVICT_EVERY_SECONDS)
                app.state.deps.registry.evict_idle()

        task = asyncio.create_task(sweep())
        try:
            yield
        finally:
            task.cancel()
            await stack.aclose()

    app = FastAPI(title="Page Chat API", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    if deps is not None:
        app.state.deps = deps
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_origin_list),
        allow_origin_regex=cfg.cors_origin_regex or None,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,  # tokens travel in a header, never in cookies
        max_age=600,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    app.include_router(state_router)
    return app
```

- [ ] **Step 12: Run the tests to see them pass**

Confirm the summary says 0 skipped.

```bash
uv run pytest tests/test_api_state.py -q -rs
```

Expected: PASS (output contains `passed`)

- [ ] **Step 13: Commit**

```bash
git add src/scraper_api tests/api_support.py tests/conftest.py tests/test_api_state.py
git commit -m "Serve a user's saved state, settings and downloads over an authenticated API"
```

### Task 12: The API: scraping, questions and the agent

The write side. Streams use server-sent events, read by the browser with `fetch` so the bearer header can be sent.

**Files:**
- Create: `src/scraper_api/streams.py`, `tests/test_api_runs.py`
- Modify: `src/scraper_api/app.py`

**Interfaces:**
- Consumes: Everything from Task 11, plus `Session.release_after_disconnect`.
- Produces: Routes: `POST /scrape`, `POST /scrape/cancel`, `POST /session/new`, `POST /ask`, `POST /ask/retry`, `POST /agent/run`, `POST /agent/resume`, `POST /agent/proceed`, `POST /agent/stop`, `POST /agent/discard`, `POST /agent/steer`. `streams.stream_response(deps, entry, make_events) -> StreamingResponse`. The server-sent event stream has one frame per event (`event: <kind>` with a JSON `data:` line) and ends with an `end` frame carrying `drafts`, `leftovers`, `interrupted` and `usage`.

- [ ] **Step 1: Create `tests/test_api_runs.py`**

```python
import json

import pytest

from api_support import A, B, MODEL, WEB, frames, names
from fakes import ai, call
from scraper.llm import Event


# ---- the work routes ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/scrape", "/scrape/cancel", "/session/new", "/ask", "/ask/retry", "/agent/run", "/agent/resume", "/agent/proceed", "/agent/stop", "/agent/discard", "/agent/steer"],
)
async def test_every_work_route_needs_a_token(api, path):
    response = await api.client.post(path, json={})
    assert response.status_code == 401 and response.json()["error"]["code"] == "unauthorized"


async def test_oversized_and_unknown_fields_are_rejected_without_echoing_the_input(api):
    secret = "SECRET-" + "x" * 9000
    response = await api.client.post("/ask", json={"question": secret, "model": MODEL}, headers=api.auth())
    assert response.status_code == 422 and "SECRET" not in response.text
    response = await api.client.post("/scrape", json={"url": A, "surprise": 1}, headers=api.auth())
    assert response.status_code == 422
    response = await api.client.post("/scrape", content=b"not json", headers={**api.auth(), "Content-Type": "application/json"})
    assert response.status_code == 422


# ---- scraping ------------------------------------------------------------------------------


async def test_scrape_returns_the_pages_it_fetched(api):
    response = await api.client.post("/scrape", json={"url": A}, headers=api.auth())
    assert response.status_code == 200 and [p["url"] for p in response.json()["pages"]] == [A]
    assert (await api.client.get("/pages/0", headers=api.auth())).json()["markdown"] == WEB[A].markdown


async def test_a_failed_fetch_is_a_400_with_the_hint(api):
    response = await api.client.post("/scrape", json={"url": "https://site.test/missing"}, headers=api.auth())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "fetch_failed" and "404" in response.json()["error"]["message"]


async def test_a_mode_this_server_does_not_offer_is_a_400(api):
    response = await api.client.post("/scrape", json={"url": A, "mode": "stealthy"}, headers=api.auth())
    assert response.status_code == 400 and "not available" in response.json()["error"]["message"]


async def test_private_addresses_cannot_be_scraped(make_api):
    from scraper.netguard import check_public_url

    api = make_api(url_check=check_public_url)  # the real guard, not the test stand-in
    for url in ["http://127.0.0.1/", "http://localhost:8080/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/"]:
        response = await api.client.post("/scrape", json={"url": url}, headers=api.auth())
        assert response.status_code == 400, url
        assert "public address" in response.json()["error"]["message"] or "Could not find" in response.json()["error"]["message"]


async def test_a_busy_session_refuses_a_second_scrape_with_a_409(api):
    entry = await api.deps.registry.get("alice")
    entry.session.busy = True
    response = await api.client.post("/scrape", json={"url": A}, headers=api.auth())
    assert response.status_code == 409 and response.json()["error"]["code"] == "busy"


async def test_the_daily_page_limit_is_a_429_with_the_reset_time(make_api):
    api = make_api(daily_pages=1)
    assert (await api.client.post("/scrape", json={"url": A}, headers=api.auth())).status_code == 200
    response = await api.client.post("/scrape", json={"url": B}, headers=api.auth())
    assert response.status_code == 429 and "00:00 UTC" in response.json()["error"]["message"]


# ---- asking -----------------------------------------------------------------------------------


async def test_ask_streams_reasoning_then_the_answer_then_an_end_frame(api):
    await api.client.post("/scrape", json={"url": A}, headers=api.auth())
    response = await api.client.post("/ask", json={"question": "price?", "model": MODEL}, headers=api.auth())
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert names(response) == ["reasoning", "answer", "end"]
    end = frames(response)[-1][1]
    assert end["interrupted"] is False and end["usage"]["model_calls"] == 1
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert [m["role"] for m in state["history"]] == ["user", "assistant"] and state["history"][1]["content"] == "Ten dollars."
    assert state["pages"][0]["used"] is True


async def test_a_model_that_is_not_offered_is_refused_before_anything_streams(make_api):
    api = make_api(allowed_models=MODEL)
    await api.client.post("/scrape", json={"url": A}, headers=api.auth())
    response = await api.client.post("/ask", json={"question": "price?", "model": "other-model"}, headers=api.auth())
    assert response.status_code == 400 and response.json()["error"]["code"] == "unknown_model"
    assert api.llm.calls == []


async def test_asking_with_no_pages_streams_an_error_event(api):
    response = await api.client.post("/ask", json={"question": "price?", "model": MODEL}, headers=api.auth())
    assert names(response) == ["error", "end"] and "Scrape a page first" in frames(response)[0][1]["text"]


async def test_the_daily_model_call_limit_refuses_up_front_with_a_429(make_api):
    api = make_api(daily_model_calls=1)
    await api.client.post("/scrape", json={"url": A}, headers=api.auth())
    assert (await api.client.post("/ask", json={"question": "one?", "model": MODEL}, headers=api.auth())).status_code == 200
    response = await api.client.post("/ask", json={"question": "two?", "model": MODEL}, headers=api.auth())
    assert response.status_code == 429 and response.json()["error"]["code"] == "limit"
    assert len(api.llm.calls) == 1


async def test_a_full_server_refuses_new_streams_with_a_429_and_frees_up_afterwards(make_api):
    api = make_api(max_concurrent_runs=1)
    await api.client.post("/scrape", json={"url": A}, headers=api.auth())
    with api.deps.slots.hold():
        response = await api.client.post("/ask", json={"question": "price?", "model": MODEL}, headers=api.auth())
        assert response.status_code == 429 and response.json()["error"]["code"] == "server_busy"
    assert (await api.client.post("/ask", json={"question": "price?", "model": MODEL}, headers=api.auth())).status_code == 200
    assert api.deps.slots.active == 0


async def test_retrying_with_nothing_interrupted_is_a_409(api):
    response = await api.client.post("/ask/retry", json={"model": MODEL}, headers=api.auth())
    assert response.status_code == 409 and response.json()["error"]["code"] == "nothing_to_retry"


async def test_an_interrupted_answer_can_be_retried(api):
    await api.client.post("/scrape", json={"url": A}, headers=api.auth())
    api.llm.events = [Event("answer", "Ten dol"), Event("error", "Cannot reach Mistral")]
    first = await api.client.post("/ask", json={"question": "price?", "model": MODEL}, headers=api.auth())
    assert frames(first)[-1][1]["interrupted"] is True
    api.llm.events = [Event("answer", "Ten dollars.")]
    again = await api.client.post("/ask/retry", json={"model": MODEL}, headers=api.auth())
    assert names(again) == ["answer", "end"]
    history = (await api.client.get("/state", headers=api.auth())).json()["history"]
    assert [m["content"] for m in history] == ["price?", "Ten dollars."]


# ---- the agent -----------------------------------------------------------------------------------


async def test_an_agent_run_streams_its_steps_and_fills_the_results_table(api):
    api.model.script = [
        ai("", call("fetch_page", "c1", url=A)),
        ai("", call("add_rows", "c2", rows=[{"plan": "Starter", "price": "$10"}])),
        ai("Found one plan."),
    ]
    response = await api.client.post("/agent/run", json={"instruction": f"collect plans from {A}", "model": MODEL}, headers=api.auth())
    kinds = names(response)
    assert kinds[-2:] == ["done", "end"] and "tool_start" in kinds and "tool_result" in kinds and "rows_added" in kinds
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert state["rows"] == [{"plan": "Starter", "price": "$10"}] and state["run"]["status"] == "done"
    assert state["history"][-1]["note"] == "1 rows added to the results table"
    csv = await api.client.get("/rows.csv", headers=api.auth())
    assert csv.text == "plan,price\nStarter,$10\n" and "attachment" in csv.headers["content-disposition"]
    assert json.loads((await api.client.get("/rows.json", headers=api.auth())).text) == [{"plan": "Starter", "price": "$10"}]


async def test_a_paused_run_waits_for_an_answer_and_resumes_with_it(api):
    api.model.script = [ai("", call("ask_user", "c1", question="Which plan?", options=["Starter", "Team"])), ai("Starter it is.")]
    first = await api.client.post("/agent/run", json={"instruction": "pick a plan", "model": MODEL}, headers=api.auth())
    pause = next(d for n, d in frames(first) if n == "needs_input")
    assert pause["payload"]["question"] == "Which plan?" and names(first)[-1] == "end"
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert state["run"]["status"] == "paused" and state["run"]["pending"]["options"] == ["Starter", "Team"]
    resumed = await api.client.post("/agent/resume", json={"value": "Starter"}, headers=api.auth())
    assert names(resumed)[-2:] == ["done", "end"]


@pytest.mark.parametrize(
    "value",
    [{"action": "teleport"}, {"allow": "yes"}, {"note": "x"}, {"action": "continue", "extra": 1}, "", "x" * 2001, {"action": "continue", "note": "n" * 2001}, 5, None],
)
async def test_resume_answers_are_validated(api, value):
    response = await api.client.post("/agent/resume", json={"value": value}, headers=api.auth())
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid"


async def test_resume_without_a_paused_run_is_a_409(api):
    response = await api.client.post("/agent/resume", json={"value": "yes"}, headers=api.auth())
    assert response.status_code == 409 and response.json()["error"]["code"] == "no_paused_run"
    assert (await api.client.post("/agent/proceed", headers=api.auth())).status_code == 409


async def test_a_run_stopped_by_the_user_can_be_proceeded(api):
    api.model.script = [ai("", call("ask_user", "c1", question="Which?")), ai("Done.")]
    await api.client.post("/agent/run", json={"instruction": "x", "model": MODEL}, headers=api.auth())
    discard = await api.client.post("/agent/discard", headers=api.auth())
    assert discard.json() == {"drafts": []}
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert state["run"]["status"] == "ended" and state["run"]["resumable"] is False


async def test_steering_queues_a_message_only_while_a_run_is_live(api):
    assert (await api.client.post("/agent/steer", json={"text": "also the SKU"}, headers=api.auth())).json()["status"] == "no_run"
    api.model.script = [ai("", call("ask_user", "c1", question="Which?"))]
    await api.client.post("/agent/run", json={"instruction": "x", "model": MODEL}, headers=api.auth())
    queued = (await api.client.post("/agent/steer", json={"text": "also the SKU"}, headers=api.auth())).json()
    assert queued["status"] == "queued" and queued["id"]
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert state["run"]["followups"] == [{"id": queued["id"], "text": "also the SKU", "delivered": False}]


async def test_the_daily_model_limit_ends_an_agent_run_with_an_error_event_and_keeps_it_resumable(make_api):
    api = make_api(daily_model_calls=1)
    api.model.script = [ai("", call("fetch_page", "c1", url=A)), ai("Done.")]
    response = await api.client.post("/agent/run", json={"instruction": f"read {A}", "model": MODEL}, headers=api.auth())
    errors = [d for n, d in frames(response) if n == "error"]
    assert errors and "model-call limit" in errors[0]["text"]
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert state["run"]["status"] == "error" and state["run"]["resumable"] is True


# ---- users never see each other -----------------------------------------------------------------------------


async def test_one_users_pages_rows_history_and_runs_are_invisible_to_another(api):
    api.model.script = [ai("", call("ask_user", "c1", question="Which?"))]
    await api.client.post("/scrape", json={"url": A}, headers=api.auth("alice"))
    await api.client.post("/agent/run", json={"instruction": "x", "model": MODEL}, headers=api.auth("alice"))
    bob = (await api.client.get("/state", headers=api.auth("bob"))).json()
    assert (bob["pages"], bob["rows"], bob["history"], bob["run"]) == ([], [], [], None)
    assert (await api.client.get("/pages/0", headers=api.auth("bob"))).status_code == 404
    assert (await api.client.get("/rows.csv", headers=api.auth("bob"))).text == ""
    assert (await api.client.post("/agent/steer", json={"text": "hi"}, headers=api.auth("bob"))).json()["status"] == "no_run"
    assert (await api.client.post("/agent/resume", json={"value": "x"}, headers=api.auth("bob"))).status_code == 409
    assert (await api.client.post("/agent/proceed", headers=api.auth("bob"))).status_code == 409
    assert (await api.client.get("/context", headers=api.auth("bob"))).json()["sources"] == []


async def test_one_users_new_session_leaves_the_other_untouched(api):
    for user in ("alice", "bob"):
        await api.client.post("/scrape", json={"url": A}, headers=api.auth(user))
    assert (await api.client.post("/session/new", headers=api.auth("alice"))).status_code == 204
    assert (await api.client.get("/state", headers=api.auth("alice"))).json()["pages"] == []
    assert len((await api.client.get("/state", headers=api.auth("bob"))).json()["pages"]) == 1


async def test_a_users_daily_limits_are_their_own(make_api):
    api = make_api(daily_pages=1)
    assert (await api.client.post("/scrape", json={"url": A}, headers=api.auth("alice"))).status_code == 200
    assert (await api.client.post("/scrape", json={"url": B}, headers=api.auth("alice"))).status_code == 429
    assert (await api.client.post("/scrape", json={"url": A}, headers=api.auth("bob"))).status_code == 200


# ---- the browser goes away, or the server fails mid-stream -------------------------------------------------------------


async def test_a_dropped_stream_stops_the_run_frees_the_slot_and_leaves_it_resumable(api):
    from scraper_api.streams import stream_response

    api.model.script = [ai("", call("fetch_page", "c1", url=A)), ai("All done.")]
    entry = await api.deps.registry.get("alice")
    stream = stream_response(api.deps, entry, lambda: entry.session.run_agent(f"read {A}", MODEL)).body_iterator
    assert (await anext(stream)).startswith(b"event: ")  # the browser reads a frame...
    assert api.deps.slots.active == 1
    await stream.aclose()  # ...and goes away
    assert api.deps.slots.active == 0 and entry.session.busy is False
    assert entry.session.run.status == "stopped" and entry.session.resumable
    resumed = await api.client.post("/agent/proceed", headers=api.auth())
    assert names(resumed)[-2:] == ["done", "end"]


async def test_an_unexpected_failure_mid_stream_becomes_a_plain_error_frame_that_leaks_nothing(api):
    from scraper_api.streams import stream_response

    async def explode():
        raise RuntimeError("secret page text that must not reach anyone")
        yield  # pragma: no cover - makes this an async generator

    entry = await api.deps.registry.get("alice")
    chunks = [chunk async for chunk in stream_response(api.deps, entry, explode).body_iterator]
    text = b"".join(chunks).decode()
    assert "server_error" in text and "secret page text" not in text
    assert api.deps.slots.active == 0
```

- [ ] **Step 2: Run the tests to see them fail**

```bash
uv run pytest tests/test_api_runs.py -q
```

Expected: FAIL (`failed`)

- [ ] **Step 3: Create `src/scraper_api/streams.py`**

Everything that can go wrong before a stream starts is a proper HTTP status (409 busy, 429 limit or server full, 400 unknown model). After it starts, problems are `error` frames. The generator's `finally` calls `release_after_disconnect`, so a browser that leaves mid-run leaves a stopped, resumable run, and the run slot is always released. An unexpected exception becomes a plain message and a log line with only the exception's type, never its text, which could contain page content.

```python
"""Routes that do work for the user: scrape a page, ask a question, run the agent. The long ones stream server-sent events."""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse

from scraper.fetch import FetchError
from scraper.session import SessionBusy
from scraper.usage import UsageLimitReached

from .deps import Deps, get_deps, get_entry
from .errors import ApiError
from .events import error_frame, event_dict, frame
from .registry import Entry, TooBusy
from .schemas import AskBody, ResumeBody, RetryBody, RunBody, ScrapeBody, SteerBody, page_summary

log = logging.getLogger("scraper_api")
router = APIRouter()
STREAM_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def _require_idle(entry: Entry) -> None:
    if entry.session.busy:
        raise ApiError(409, "busy", "Another run or scrape is already in progress. Wait for it to finish, or press Stop.")


def _require_room(deps: Deps, entry: Entry) -> None:
    """Refuse a new stream up front, with a proper status, when it could not run."""
    _require_idle(entry)
    if deps.slots.active >= deps.slots.limit:
        raise ApiError(429, "server_busy", "The server is busy with other runs. Try again in a minute.", headers={"Retry-After": "30"})
    spent = entry.meter.exhausted()
    if spent:
        raise ApiError(429, "limit", spent)


def stream_response(deps: Deps, entry: Entry, make_events: Callable[[], AsyncIterator]) -> StreamingResponse:
    """Run `make_events()` as a server-sent event stream. If the browser goes away the run is stopped and stays resumable."""

    async def body():
        session = entry.session
        try:
            with deps.slots.hold():
                async with aclosing(make_events()) as events:
                    async for event in events:
                        yield frame(event.kind, event_dict(event))
            yield frame(
                "end",
                {
                    "drafts": session.take_stopped_drafts(),
                    "leftovers": session.take_leftovers(),
                    "interrupted": session.last_ask_interrupted,
                    "usage": entry.meter.snapshot(),
                },
            )
        except TooBusy as exc:
            yield error_frame("server_busy", str(exc))
        except SessionBusy as exc:
            yield error_frame("busy", str(exc))
        except UsageLimitReached as exc:
            yield error_frame("limit", str(exc))
        except Exception as exc:  # never show the person a traceback, and never log text that may be page content
            log.error("stream failed: %s", type(exc).__name__)
            yield error_frame("server_error", "Something went wrong on the server. Your session is saved.")
        finally:
            session.release_after_disconnect()  # a no-op unless the browser left while the run was live

    return StreamingResponse(body(), media_type="text/event-stream", headers=STREAM_HEADERS)


# ---- the session -----------------------------------------------------------------------------


@router.post("/scrape")
async def scrape(body: ScrapeBody, entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> dict:
    _require_idle(entry)
    try:
        with deps.slots.hold():
            result = await entry.session.scrape(body.url, body.mode, follow=body.follow, depth=body.depth)
    except TooBusy as exc:
        raise ApiError(429, "server_busy", str(exc), headers={"Retry-After": "30"}) from None
    except SessionBusy as exc:
        raise ApiError(409, "busy", str(exc)) from None
    except FetchError as exc:
        raise ApiError(400, "fetch_failed", exc.message, hint=exc.hint, retry_mode=exc.retry_mode) from None
    except UsageLimitReached as exc:
        raise ApiError(429, "limit", str(exc)) from None
    used: set[str] = set()
    return {
        "pages": [page_summary(p, used) for p in result.pages],
        "failed": [{"url": url, "reason": why} for url, why in result.failed],
    }


@router.post("/scrape/cancel", status_code=204)
async def scrape_cancel(entry: Entry = Depends(get_entry)) -> Response:
    entry.session.cancel()
    return Response(status_code=204)


@router.post("/session/new", status_code=204)
async def new_session(entry: Entry = Depends(get_entry)) -> Response:
    try:
        await entry.session.new_session()
    except SessionBusy as exc:
        raise ApiError(409, "busy", str(exc)) from None
    return Response(status_code=204)


# ---- streams -----------------------------------------------------------------------------------


@router.post("/ask")
async def ask(body: AskBody, entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> StreamingResponse:
    _require_room(deps, entry)
    await deps.catalog.require(body.model)
    return stream_response(deps, entry, lambda: entry.session.ask(body.question, body.model))


@router.post("/ask/retry")
async def ask_retry(body: RetryBody, entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> StreamingResponse:
    """Ask the last question again after an answer was cut off."""
    _require_room(deps, entry)
    await deps.catalog.require(body.model)
    question = entry.session.pop_interrupted()
    if question is None:
        raise ApiError(409, "nothing_to_retry", "There is no interrupted answer to retry.")
    return stream_response(deps, entry, lambda: entry.session.ask(question, body.model))


@router.post("/agent/run")
async def agent_run(body: RunBody, entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> StreamingResponse:
    _require_room(deps, entry)
    await deps.catalog.require(body.model)
    return stream_response(deps, entry, lambda: entry.session.run_agent(body.instruction, body.model, seed_url=body.seed_url))


@router.post("/agent/resume")
async def agent_resume(body: ResumeBody, entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> StreamingResponse:
    _require_room(deps, entry)
    if entry.session.run is None or entry.session.run.status != "paused":
        raise ApiError(409, "no_paused_run", "There is no question waiting for an answer.")
    return stream_response(deps, entry, lambda: entry.session.resume_run(body.value))


@router.post("/agent/proceed")
async def agent_proceed(entry: Entry = Depends(get_entry), deps: Deps = Depends(get_deps)) -> StreamingResponse:
    _require_room(deps, entry)
    if not entry.session.resumable:
        raise ApiError(409, "no_run", "There is no run to resume.")
    return stream_response(deps, entry, entry.session.proceed_run)


@router.post("/agent/stop", status_code=204)
async def agent_stop(entry: Entry = Depends(get_entry)) -> Response:
    entry.session.cancel()
    return Response(status_code=204)


@router.post("/agent/discard")
async def agent_discard(entry: Entry = Depends(get_entry)) -> dict:
    try:
        await entry.session.discard_run()
    except SessionBusy as exc:
        raise ApiError(409, "busy", str(exc)) from None
    return {"drafts": entry.session.take_stopped_drafts()}


@router.post("/agent/steer")
async def agent_steer(body: SteerBody, entry: Entry = Depends(get_entry)) -> dict:
    result = entry.session.steer(body.text)
    return {"status": result.status, "id": result.id}
```

- [ ] **Step 4: Wire the stream routes into the app**

In `src/scraper_api/app.py`, replace:

```python
from .routes import router as state_router
```

with:

```python
from .routes import router as state_router
from .streams import router as stream_router
```

- [ ] **Step 5: Include the router**

In `src/scraper_api/app.py`, replace:

```python
    app.include_router(state_router)
```

with:

```python
    app.include_router(state_router)
    app.include_router(stream_router)
```

- [ ] **Step 6: Run the API tests to see them pass**

Confirm the summary says 0 skipped.

```bash
uv run pytest tests/test_api_state.py tests/test_api_runs.py -q -rs
```

Expected: PASS (output contains `passed`)

- [ ] **Step 7: Commit**

```bash
git add src/scraper_api/streams.py src/scraper_api/app.py tests/test_api_runs.py
git commit -m "Stream scraping, questions and agent runs to the browser"
```

### Task 13: Entry point, container, deployment notes and final checks

The last task makes the service startable and documents what an operator must do before exposing it. It also runs the final gates.

**Files:**
- Create: `src/scraper_api/__main__.py`, `scripts/check_egress.py`, `tests/test_entrypoint.py`, `tests/test_check_egress.py`, `Containerfile`, `.containerignore`, `compose.yaml`, `docs/hosted-deploy.md`
- Modify: `.env.example`, `README.md`

**Interfaces:**
- Consumes: `app.create_app`, `config.load`.
- Produces: `python -m scraper_api` starts the service (hosted mode only). `scripts/check_egress.py host:port ...` exits 1 if any target is reachable.

- [ ] **Step 1: Create `tests/test_entrypoint.py`**

```python
import os
import subprocess
import sys


def run_python(code: str, **env) -> subprocess.CompletedProcess:
    """A fresh interpreter, so what the module does at import time is what we observe."""
    full_env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path), **env}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=full_env, timeout=60)


def test_importing_the_entry_point_switches_cloud_tracing_off_even_if_the_environment_turned_it_on():
    code = "import os, scraper_api.__main__; print(os.environ['LANGSMITH_TRACING'], os.environ['LANGCHAIN_TRACING_V2'])"
    result = run_python(code, LANGSMITH_TRACING="true", LANGCHAIN_TRACING_V2="true")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["false", "false"]


def test_it_refuses_to_start_outside_hosted_mode():
    result = run_python("from scraper_api.__main__ import main; main()", SCRAPER_HOSTED="false")
    assert result.returncode != 0 and "SCRAPER_HOSTED=true" in (result.stdout + result.stderr)


def test_it_refuses_to_start_hosted_without_its_secrets():
    env = {k: "" for k in os.environ if k.startswith(("SCRAPER_", "MISTRAL_"))}
    result = run_python("from scraper_api.__main__ import main; main()", SCRAPER_HOSTED="true", **env)
    output = result.stdout + result.stderr
    assert result.returncode != 0 and "SCRAPER_HOSTED needs" in output and "Traceback" not in output
```

- [ ] **Step 2: Create `tests/test_check_egress.py`**

```python
import socket
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_egress.py"


def run(*targets):
    return subprocess.run([sys.executable, str(SCRIPT), *targets], capture_output=True, text=True, timeout=30)


def test_a_reachable_target_fails_the_check():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        result = run(f"127.0.0.1:{listener.getsockname()[1]}")
    assert result.returncode == 1 and "OPEN" in result.stdout and "NOT blocked" in result.stdout


def test_an_unreachable_target_passes_the_check():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]  # nothing listens here once the socket closes
    result = run(f"127.0.0.1:{port}")
    assert result.returncode == 0 and "closed" in result.stdout


def test_one_open_target_among_closed_ones_fails():
    with socket.socket() as listener, socket.socket() as probe:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
        probe.close()
        result = run(f"127.0.0.1:{closed}", f"127.0.0.1:{listener.getsockname()[1]}")
    assert result.returncode == 1 and "1 of 2" in result.stdout


def test_a_malformed_target_is_a_clear_error():
    result = run("not-a-target")
    assert result.returncode != 0 and "host:port" in result.stderr
```

- [ ] **Step 3: Run the tests to see them fail**

```bash
uv run pytest tests/test_entrypoint.py tests/test_check_egress.py -q
```

Expected: FAIL (`failed`)

- [ ] **Step 4: Create `src/scraper_api/__main__.py`**

Tracing is forced off before anything from LangChain is imported, exactly as `scraper/__main__.py` does, so page text can never reach LangSmith. One worker, on purpose. Access logging is off so addresses and prompts never reach a log file. A configuration mistake prints one clear line instead of a traceback.

```python
"""Entry point: python -m scraper_api"""

import os

# Scraped pages must never reach a tracing service: switch LangChain and LangSmith tracing off before anything from
# LangChain is imported or configured.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import logging  # noqa: E402

import uvicorn  # noqa: E402

from scraper.config import load  # noqa: E402

from .app import create_app  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        cfg = load()
    except ValueError as exc:
        raise SystemExit(f"Configuration error: {exc}") from None
    if not cfg.hosted:
        raise SystemExit("scraper_api only runs in hosted mode. Set SCRAPER_HOSTED=true (see .env.example).")
    print(f"Serving on {cfg.host}:{cfg.port}. Page text is sent to Mistral for every run.", flush=True)
    # One process on purpose: each user's live Session lives in memory (see the design spec, "Process model").
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, workers=1, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Create `scripts/check_egress.py`**

```python
"""Run this on the machine that hosts the API to prove it cannot reach private addresses.

    python scripts/check_egress.py 10.0.0.5:5432 169.254.169.254:80

Each argument is host:port of something that WOULD answer if the network let the packet through (your database's
private address, the cloud metadata address). The check passes only when every connection fails. A connection that
succeeds means egress is open and the application-level address check is the only thing standing in the way."""

import socket
import sys

TIMEOUT_SECONDS = 3.0
DEFAULT_PROBES = ["169.254.169.254:80"]


def parse(target: str) -> tuple[str, int]:
    host, _, port = target.rpartition(":")
    if not host or not port.isdigit():
        raise SystemExit(f"Expected host:port, got {target!r}")
    return host.strip("[]"), int(port)


def is_open(host: str, port: int, timeout: float = TIMEOUT_SECONDS) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main(argv: list[str]) -> int:
    targets = argv or DEFAULT_PROBES
    leaks = 0
    for target in targets:
        host, port = parse(target)
        if is_open(host, port):
            print(f"OPEN    {target}: the connection succeeded, so egress to it is NOT blocked")
            leaks += 1
        else:
            print(f"closed  {target}")
    if leaks:
        print(f"\nFAIL: {leaks} of {len(targets)} private target(s) are reachable from this host.")
        return 1
    print(f"\nOK: none of {len(targets)} target(s) could be reached. Make sure each one would answer from somewhere else.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 6: Run the tests to see them pass**

```bash
uv run pytest tests/test_entrypoint.py tests/test_check_egress.py -q
```

Expected: PASS (output contains `passed`)

- [ ] **Step 7: Create `Containerfile`**

```dockerfile
FROM docker.io/library/python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app

# Dependencies first, so editing source does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --system --create-home --uid 10001 scraper
USER scraper

ENV SCRAPER_HOSTED=true SCRAPER_HOST=0.0.0.0 SCRAPER_PORT=8080
EXPOSE 8080
CMD ["/app/.venv/bin/python", "-m", "scraper_api"]
```

- [ ] **Step 8: Create `.containerignore`**

```
.git
.venv
.env
.idea
**/__pycache__
tests
docs
scripts
web
node_modules
```

- [ ] **Step 9: Create `compose.yaml`**

```yaml
# Local stack for the hosted API: Postgres and the API. The web app (Next.js) runs separately; see web/README.md.
# Start with: podman-compose up --build
services:
  db:
    image: docker.io/library/postgres:17
    environment:
      POSTGRES_USER: scraper
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
      POSTGRES_DB: scraper
    volumes:
      - dbdata:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U scraper -d scraper"]
      interval: 5s
      timeout: 3s
      retries: 10

  api:
    build:
      context: .
      dockerfile: Containerfile
    env_file: .env
    environment:
      SCRAPER_DATABASE_URL: postgresql://scraper:${POSTGRES_PASSWORD}@db:5432/scraper
    depends_on:
      db:
        condition: service_healthy
    ports:
      - "127.0.0.1:8080:8080"

volumes:
  dbdata:
```

- [ ] **Step 10: Create `docs/hosted-deploy.md`**

````markdown
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

- **No access log.** The API does not log requests, so page addresses and prompts never reach a log file. It logs failures by exception type only.
- **One process.** Restarting it ends any run that is streaming. The run is saved and the person presses Resume.
- **Rotating the Mistral key.** Change `SCRAPER_API_KEY` and restart.
- **Deleting a person.** The web app calls `DELETE /account-data` with `X-Service-Secret`. That removes their session, pages, results, history, runs, preferences, usage counts and agent checkpoints.
- **Costs.** The per-person daily limits count model calls and pages, not tokens. A call's size is bounded by the model's context window, but set `SCRAPER_ALLOWED_MODELS` to cheap models to keep the worst case small.
````

- [ ] **Step 11: Add the hosted settings to `.env.example`**

```
# ---- Hosted API (python -m scraper_api). See docs/hosted-deploy.md ----------------------------------------------
# SCRAPER_HOSTED=true
# SCRAPER_DATABASE_URL=postgresql://scraper_api:change-me@localhost:5432/scraper
# SCRAPER_JWKS_URL=https://your-web-app.example.com/api/auth/jwks
# SCRAPER_JWT_ISSUER=https://your-web-app.example.com
# SCRAPER_JWT_AUDIENCE=https://your-web-app.example.com
# SCRAPER_SERVICE_SECRET=
# SCRAPER_CORS_ORIGINS=https://your-web-app.example.com
# SCRAPER_ALLOWED_MODELS=
# POSTGRES_PASSWORD=          # only for the local compose stack
```

- [ ] **Step 12: Edit `README.md`**

In `README.md`, replace:

```markdown

## Tests
```

with:

```markdown

## Hosted API

`python -m scraper_api` serves the same agent to many signed-in people from one Postgres-backed process, for the Next.js web app. Setup, settings, the required network rules and a database role layout are in [docs/hosted-deploy.md](docs/hosted-deploy.md). The local app above is unchanged.

## Tests
```

- [ ] **Step 13: Edit `README.md`**

In `README.md`, replace:

````markdown
uv run pytest
```
````

with:

````markdown
uv run pytest
```

The Postgres tests are skipped unless `SCRAPER_TEST_DATABASE_URL` points at a database they may fill and empty (use a scratch one). A run with it unset still reports green, so check the summary says `0 skipped` before you trust it:

```bash
podman run -d --name scraper-test-db -e POSTGRES_PASSWORD=test -e POSTGRES_DB=scraper_test -p 127.0.0.1:55432:5432 docker.io/library/postgres:17
export SCRAPER_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/scraper_test
uv run pytest -rs
```
````

- [ ] **Step 14: Build the image**

```bash
podman build -t page-chat-api -f Containerfile .
```

Expected: the build succeeds. **This plan's author could not run this step** (the Podman machine was not responding), so it is unverified. What was verified instead: the locked production-only dependency set installs without dev tools, and `python -m scraper_api` then starts and prints a clear configuration error. If the build fails, fix the `Containerfile` and re-run; do not change the application code to suit it.


- [ ] **Step 15: Run the whole suite**

Same note as before about the one intermittent test.

```bash
uv run pytest -q
```

Expected: PASS (output contains `passed`)

- [ ] **Step 16: Run the database tests and confirm none were skipped**

Expected: all pass and the summary says nothing about `skipped`. If anything was skipped, `SCRAPER_TEST_DATABASE_URL` is not set in this shell and the run proved nothing.

```bash
uv run pytest -q -rs tests/test_pgstore.py tests/test_db.py tests/test_registry.py tests/test_pg_checkpoint.py tests/test_api_state.py tests/test_api_runs.py
```

Expected: PASS (output contains `passed`)

- [ ] **Step 17: Measure the intermittent follow-up test**

This is Review Focus 1. Count the `FAILED RUN` lines and put the count in your hand-over message. Expect a few (0 to 15 of 30 depending on how busy the machine is). Do **not** try to fix it here: it is an existing bug in the follow-up feature, and the fix needs its own design.

```bash
for i in $(seq 1 30); do uv run pytest -q -p no:cacheprovider -k delivered_flag_is_lost || echo "FAILED RUN $i"; done
```

- [ ] **Step 18: Commit**

```bash
git add src/scraper_api/__main__.py scripts tests/test_entrypoint.py tests/test_check_egress.py Containerfile .containerignore compose.yaml docs/hosted-deploy.md .env.example README.md
git commit -m "Add the hosted entry point, container files and deployment notes"
```


## Self-review notes

- **Spec coverage:** architecture and token flow (Tasks 8, 11); data model and per-user scoping (7); process model (9); API (11, 12); SSRF, modes, limits, secrets and privacy (2, 3, 4, 5, 7, 13); account deletion (10, 11); testing (every task). Not in this plan, by design: Better Auth, invitations, the admin page, email and every screen (plans 2 and 3), and removing NiceGUI (plan 3).
- **Verified while writing:** every code block in this plan came from a scratch copy where its tests passed (583 tests in all, 102 of which need the database and are skipped without one). The plan was then replayed step by step on a fresh checkout of this repository: every step ran, and every test failed or passed exactly as the plan says. The protections (token checks, user scoping, address guard, usage limits, CORS, run slots, disconnect handling) were each deliberately broken once to confirm a test fails.
- **Not verified:** the container image build (Task 13), the example firewall rules in `docs/hosted-deploy.md`, anything against the real Mistral API (no key was used), and behaviour on Vercel.
