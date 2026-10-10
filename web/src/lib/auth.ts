import { betterAuth } from "better-auth";
import { APIError, createAuthMiddleware, getSessionFromCtx } from "better-auth/api";
import { nextCookies } from "better-auth/next-js";
import { admin, jwt } from "better-auth/plugins";
import type { Pool } from "pg";
import { AccountDataError, type AccountDataClient } from "./account-data";
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

  /**
   * Ask the Python API to erase a person's data, and refuse to go on if it cannot. This must happen BEFORE anything of the
   * account is touched: Better Auth removes a person's sessions and password record first and the user row last, so a refusal
   * at the last step would leave them unable to sign in and try again. The API's erase can safely be repeated.
   */
  async function eraseApiData(userId: string): Promise<void> {
    try {
      await accountData.delete(userId);
    } catch (error) {
      const status = error instanceof AccountDataError ? error.status : 0;
      throw new APIError(status === 409 ? "CONFLICT" : "BAD_GATEWAY", {
        message: status === 409 ? "A run is still stopping. Try again in a few seconds." : "Could not delete your data. Try again shortly.",
        code: "ACCOUNT_DATA_NOT_DELETED",
      });
    }
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
    user: { deleteUser: { enabled: true, beforeDelete: async (user) => eraseApiData(user.id) } },
    advanced: { useSecureCookies: env.production },
    rateLimit: { enabled: env.production, storage: "database", window: 60, max: 60 },
    hooks: {
      // Invite-only: an account can be created only with the single-use token that was emailed to that exact address.
      before: createAuthMiddleware(async (ctx) => {
        if (ctx.path === "/delete-user") {
          // Better Auth asks for the password only when the session is more than a day old. Erasing an account, and everything the
          // Python API holds for it, is not something a stolen session cookie alone should be able to do.
          const password = (ctx.body as { password?: unknown } | undefined)?.password;
          if (typeof password !== "string" || !password) throw new APIError("BAD_REQUEST", { message: "Enter your password to delete your account.", code: "PASSWORD_REQUIRED" });
          return;
        }
        if (ctx.path === "/admin/remove-user") {
          // An administrator removing someone: Better Auth's own admin route never calls `beforeDelete`, so do the erase here, but only
          // for a real administrator removing someone else (the route refuses self-removal, and must not erase a person's data first).
          const session = await getSessionFromCtx(ctx);
          const caller = session?.user as { id: string; role?: string | null; banned?: boolean | null } | undefined;
          const target = (ctx.body as { userId?: unknown } | undefined)?.userId;
          if (caller?.role === "admin" && !caller.banned && typeof target === "string" && target && target !== caller.id) await eraseApiData(target);
          return;
        }
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
        delete: {
          // A last line of defence for any other way a user can be removed. The paths above have already erased the data.
          before: async (user) => eraseApiData(user.id),
        },
      },
    },
    plugins: [
      admin(),
      jwt({
        jwks: { keyPairConfig: { alg: "EdDSA", crv: "Ed25519" }, rotationInterval: 30 * DAY, gracePeriod: 30 * DAY },
        jwt: {
          issuer: env.baseUrl,
          audience: env.apiAudience,
          expirationTime: "15m",
          definePayload: () => ({}), // the default puts the whole user, email and name included, in the token; the API needs only `sub`
          getSubject: ({ user }) => user.id,
        },
      }),
      nextCookies(), // keep last
    ],
  });
}

export type Auth = ReturnType<typeof createAuth>;
