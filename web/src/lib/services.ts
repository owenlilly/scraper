import type { Pool } from "pg";
import { accountDataClient, type AccountDataClient } from "./account-data";
import { createAuth, type Auth } from "./auth";
import { makePool } from "./db";
import { logSender, mailpitSender, resendSender, type EmailSender } from "./email";
import { loadEnv, type WebEnv } from "./env";
import { InviteStore } from "./invites";

export type Services = {
  env: WebEnv;
  pool: Pool;
  invites: InviteStore;
  sendEmail: EmailSender;
  accountData: AccountDataClient;
  auth: Auth;
};

/** Everything the routes use, wired together. Tests pass their own pool, email sender and API client. */
export function buildServices(env: WebEnv, overrides: Partial<Pick<Services, "pool" | "sendEmail" | "accountData">> = {}): Services {
  const pool = overrides.pool ?? makePool(env.databaseUrl);
  const invites = new InviteStore(pool, env.inviteTtlDays);
  const sendEmail =
    overrides.sendEmail ??
    (env.emailTransport === "resend"
      ? resendSender({ apiKey: env.resendApiKey, from: env.emailFrom })
      : env.emailTransport === "mailpit"
        ? mailpitSender({ url: env.mailpitUrl, from: env.emailFrom })
        : logSender());
  const accountData = overrides.accountData ?? accountDataClient({ apiUrl: env.apiUrl, serviceSecret: env.serviceSecret });
  return { env, pool, invites, sendEmail, accountData, auth: createAuth({ env, pool, sendEmail, invites, accountData }) };
}

const KEY = Symbol.for("page-chat.services");

/** One set of services per server process (and across hot reloads in development). Built on first use, so `next build` needs no secrets. */
export function getServices(): Services {
  const holder = globalThis as unknown as Record<symbol, Services | undefined>;
  return (holder[KEY] ??= buildServices(loadEnv()));
}
