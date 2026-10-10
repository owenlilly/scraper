import { betterAuth } from "better-auth";
import { APIError, createAuthMiddleware } from "better-auth/api";
import { nextCookies } from "better-auth/next-js";
import { admin } from "better-auth/plugins";
import type { Pool } from "pg";
import type { AccountDataClient } from "./account-data";
import { resetMessage, verifyMessage, type EmailSender } from "./email";
import type { WebEnv } from "./env";
import { InviteStore, normalizeEmail } from "./invites";

export const INVITE_HEADER = "x-invite-token";
export const INVITE_REQUIRED_MESSAGE = "Sign-up needs a valid invitation for this email address.";
const DAY = 60 * 60 * 24;

export type AuthDeps = {
  env: WebEnv;
  pool: Pool;
  sendEmail: EmailSender;
  invites: InviteStore;
  accountData: AccountDataClient;
};

export function createAuth({ env, pool, sendEmail, invites, accountData }: AuthDeps) {
  /** The very first account may be made without an invitation: the address in ADMIN_EMAIL, while nobody is an admin yet. */
  async function mayBecomeFirstAdmin(email: string): Promise<boolean> {
    if (!env.adminEmail || normalizeEmail(email) !== env.adminEmail) return false;
    const { rowCount } = await pool.query(`SELECT 1 FROM auth."user" WHERE role = 'admin' LIMIT 1`);
    return rowCount === 0;
  }

  return betterAuth({
    appName: "Page Chat",
    baseURL: env.baseUrl,
    secret: env.authSecret,
    database: pool,
    trustedOrigins: [env.baseUrl, ...env.trustedOrigins],
    emailAndPassword: {
      enabled: true,
      requireEmailVerification: true,
      minPasswordLength: 12,
      resetPasswordTokenExpiresIn: 60 * 60,
      revokeSessionsOnPasswordReset: true,
      sendResetPassword: async ({ user, url }) => sendEmail(resetMessage({ to: user.email, url })),
    },
    emailVerification: {
      sendOnSignUp: true,
      autoSignInAfterVerification: true,
      sendVerificationEmail: async ({ user, url }) => sendEmail(verifyMessage({ to: user.email, url })),
    },
    session: { expiresIn: 7 * DAY, updateAge: DAY },
    hooks: {
      // Invite-only: an account can be created only with the single-use token that was emailed to that exact address.
      before: createAuthMiddleware(async (ctx) => {
        if (ctx.path !== "/sign-up/email") return;
        const email = String((ctx.body as { email?: unknown } | undefined)?.email ?? "");
        if (!email || (await mayBecomeFirstAdmin(email))) return; // an empty address fails the sign-up's own validation
        const result = await invites.check(ctx.headers?.get(INVITE_HEADER) ?? "", email);
        if (!result.ok) throw new APIError("FORBIDDEN", { message: INVITE_REQUIRED_MESSAGE, code: "INVITE_REQUIRED" });
      }),
    },
    databaseHooks: {
      user: {
        create: {
          before: async (user) => ((await mayBecomeFirstAdmin(user.email)) ? { data: { ...user, role: "admin" } } : { data: user }),
          after: async (user) => invites.consume(user.email),
        },
      },
    },
    plugins: [
      admin(),
      nextCookies(), // keep last
    ],
  });
}

export type Auth = ReturnType<typeof createAuth>;
