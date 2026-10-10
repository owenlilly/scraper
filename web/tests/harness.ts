import type { Pool } from "pg";
import { AccountDataError, type AccountDataClient } from "../src/lib/account-data";
import type { EmailMessage } from "../src/lib/email";
import { loadEnv } from "../src/lib/env";
import { migrate } from "../src/lib/migrate";
import { buildServices, type Services } from "../src/lib/services";
import { freshSchema } from "./helpers";

export const BASE = "http://localhost:3100";
export const PASSWORD = "correct horse battery staple";

export type Harness = Awaited<ReturnType<typeof makeHarness>>;

/** The real Better Auth, on a real Postgres, with a mailbox in place of email and a stand-in for the Python API. */
export async function makeHarness(options: { adminEmail?: string | null; accountData?: AccountDataClient; inviteTtlDays?: number; production?: boolean } = {}) {
  const pool: Pool = await freshSchema();
  const base = options.production ? "https://app.example.com" : BASE;
  const sent: EmailMessage[] = [];
  const deletedUsers: string[] = [];
  const accountData: AccountDataClient = options.accountData ?? { delete: async (id) => void deletedUsers.push(id) };
  const env = loadEnv({
    DATABASE_URL: "postgresql://unused@localhost/scraper_web_test",
    BETTER_AUTH_SECRET: "a-test-only-secret-that-is-long-enough-0123456789",
    BETTER_AUTH_URL: base,
    API_URL: "http://api.test",
    API_AUDIENCE: "http://api.test",
    SERVICE_SECRET: "t".repeat(24),
    EMAIL_TRANSPORT: options.production ? "resend" : "log",
    ...(options.production ? { NODE_ENV: "production", RESEND_API_KEY: "re_test", EMAIL_FROM: "Page Chat <no-reply@example.com>" } : {}),
    ADMIN_EMAIL: options.adminEmail === null ? "" : (options.adminEmail ?? "owner@example.com"),
    INVITE_TTL_DAYS: String(options.inviteTtlDays ?? 7),
  });
  const services: Services = buildServices(env, { pool, sendEmail: async (message) => void sent.push(message), accountData });
  await migrate(services.auth, pool);

  const call = (path: string, init: { method?: string; body?: unknown; headers?: Record<string, string>; cookie?: string } = {}) =>
    services.auth.handler(
      new Request(`${base}/api/auth${path}`, {
        method: init.method ?? (init.body === undefined ? "GET" : "POST"),
        headers: { "content-type": "application/json", origin: base, ...(init.cookie ? { cookie: init.cookie } : {}), ...init.headers },
        body: init.body === undefined ? undefined : JSON.stringify(init.body),
      }),
    );

  const cookieFrom = (response: Response) => response.headers.getSetCookie().map((c) => c.split(";")[0]).join("; ");
  const linkIn = (message: EmailMessage) => /https?:\/\/\S+/.exec(message.text)![0];
  const lastEmailTo = (to: string) => [...sent].reverse().find((m) => m.to === to);

  const signUp = (email: string, token?: string, password = PASSWORD) =>
    call("/sign-up/email", { body: { email, password, name: email.split("@")[0] }, headers: token ? { "x-invite-token": token } : {} });
  const signIn = (email: string, password = PASSWORD) => call("/sign-in/email", { body: { email, password } });

  /** Follow the verification link from the latest email to `email`. */
  const verify = async (email: string) => {
    const message = lastEmailTo(email);
    if (!message) throw new Error(`no email was sent to ${email}`);
    return services.auth.handler(new Request(linkIn(message), { headers: { origin: base } }));
  };

  /** Invite, sign up, verify and sign in. Returns the session cookie. */
  const makeUser = async (email: string) => {
    const { token } = await services.invites.create(email, null);
    const response = await signUp(email, token);
    if (response.status !== 200) throw new Error(`sign-up failed: ${response.status} ${await response.text()}`);
    await verify(email);
    const session = await signIn(email);
    if (session.status !== 200) throw new Error(`sign-in failed: ${session.status}`);
    return cookieFrom(session);
  };

  /** The first administrator: ADMIN_EMAIL signs up with no invitation. */
  const makeAdmin = async (email = "owner@example.com") => {
    const response = await signUp(email);
    if (response.status !== 200) throw new Error(`admin sign-up failed: ${response.status} ${await response.text()}`);
    await verify(email);
    return cookieFrom(await signIn(email));
  };

  const userIdOf = async (email: string) => (await pool.query(`SELECT id FROM auth."user" WHERE email = $1`, [email])).rows[0]?.id as string | undefined;

  const unreachableApi = (status: number): AccountDataClient => ({
    delete: async () => {
      throw new AccountDataError(status);
    },
  });

  return { base, pool, services, sent, deletedUsers, call, cookieFrom, linkIn, lastEmailTo, signUp, signIn, verify, makeUser, makeAdmin, userIdOf, unreachableApi };
}
