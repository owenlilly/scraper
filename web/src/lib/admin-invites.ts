import { inviteMessage } from "./email";
import { normalizeEmail, type Invite } from "./invites";
import type { Services } from "./services";

const json = (body: unknown, status = 200) => Response.json(body, { status, headers: { "cache-control": "no-store" } });
const fail = (status: number, code: string, message: string) => json({ error: { code, message } }, status);

const view = (invite: Invite) => ({
  id: invite.id,
  email: invite.email,
  status: invite.status,
  createdAt: invite.createdAt.toISOString(),
  expiresAt: invite.expiresAt.toISOString(),
  usedAt: invite.usedAt?.toISOString() ?? null,
  revokedAt: invite.revokedAt?.toISOString() ?? null,
});

/** The invitation routes, as plain functions of a Request so they can be tested without a server. */
export function adminInviteHandlers(services: Services) {
  const { auth, invites, env, pool, sendEmail } = services;

  async function requireAdmin(request: Request): Promise<{ userId: string } | Response> {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session) return fail(401, "unauthorized", "Sign in to continue.");
    const user = session.user as { id: string; role?: string | null; banned?: boolean | null };
    if (user.role !== "admin" || user.banned) return fail(403, "forbidden", "Only administrators can do that.");
    return { userId: user.id };
  }

  /** Cookies are sent with cross-site requests too, so a change must come from one of our own pages. */
  function fromOurOwnPage(request: Request): boolean {
    if (request.method === "GET" || request.method === "HEAD") return true;
    const origin = request.headers.get("origin");
    return origin !== null && [env.baseUrl, ...env.trustedOrigins].includes(origin);
  }

  async function guarded(request: Request): Promise<{ userId: string } | Response> {
    if (!fromOurOwnPage(request)) return fail(403, "bad_origin", "This request did not come from this site.");
    return requireAdmin(request);
  }

  return {
    async list(request: Request): Promise<Response> {
      const admin = await guarded(request);
      if (admin instanceof Response) return admin;
      return json({ invites: (await invites.list()).map(view) });
    },

    async create(request: Request): Promise<Response> {
      const admin = await guarded(request);
      if (admin instanceof Response) return admin;
      const body = (await request.json().catch(() => null)) as { email?: unknown } | null;
      const email = typeof body?.email === "string" ? normalizeEmail(body.email) : "";
      if (!email || email.length > 254 || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) return fail(422, "invalid", "Enter a valid email address.");
      const existing = await pool.query(`SELECT 1 FROM auth."user" WHERE lower(email) = $1`, [email]);
      if (existing.rowCount) return fail(409, "already_has_account", "That address already has an account.");
      const { invite, token } = await invites.create(email, admin.userId);
      const url = `${env.baseUrl}/sign-up?invite=${encodeURIComponent(token)}&email=${encodeURIComponent(email)}`;
      try {
        await sendEmail(inviteMessage({ to: email, url, ttlDays: env.inviteTtlDays }));
      } catch {
        await invites.revoke(invite.id); // an invitation nobody received must not stay open
        return fail(502, "email_failed", "The invitation could not be emailed. Nothing was created; try again.");
      }
      return json({ invite: view(invite) }, 201);
    },

    async revoke(request: Request, id: string): Promise<Response> {
      const admin = await guarded(request);
      if (admin instanceof Response) return admin;
      return (await invites.revoke(id)) ? new Response(null, { status: 204 }) : fail(404, "not_found", "There is no waiting invitation with that id.");
    },
  };
}
