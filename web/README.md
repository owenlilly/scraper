# Page Chat web app

Next.js app that owns accounts for the hosted service: sign-up by invitation only, email confirmation, sign-in, password reset, an administrator's invitation routes, and the short-lived tokens the browser sends to the Python API. The screens are in a later plan; this folder holds the server side and its tests.

## Run it

```bash
cd web
npm install
cp .env.example .env.local        # then fill it in
npm run migrate                   # creates the `auth` tables; safe to run again
npm run dev
```

With `EMAIL_TRANSPORT=log`, invitation and confirmation links are printed in the terminal running `npm run dev`.

## Test it

The tests use a real Postgres and **drop and recreate its `auth` schema**, so give them a scratch database whose name contains "test":

```bash
export WEB_TEST_DATABASE_URL=postgresql://user@localhost/scraper_web_test
npm test            # 90-odd integration tests
npm run typecheck
```

A missing or non-test database is an error, not a skip.

Check the whole chain (web app, Python API, one database) with real servers:

```bash
E2E_DATABASE_URL=postgresql://user@localhost/scraper_e2e_test npm run e2e
```

It drops that database's `auth` and `app` schemas first, starts both servers, and walks through sign-up, invitation, token use, data separation and account deletion.

## How it fits together

- `src/lib/auth.ts` is the whole Better Auth configuration, in one place.
- An account can only be created with the single-use token emailed to that exact address (`x-invite-token` header on `POST /api/auth/sign-up/email`). The first administrator is the one address in `ADMIN_EMAIL`.
- `GET /api/auth/token` gives a signed-in person a 15 minute token (EdDSA, claims `sub`, `iss`, `aud`, `iat`, `exp` only). The Python API checks it against `GET /api/auth/jwks`.
- Deleting an account first asks the Python API to erase that person's data; if it cannot, the account stays and the deletion can be retried.
