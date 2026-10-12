import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { DELETE as revokeInvite } from "../src/app/api/admin/invites/[id]/route";
import { GET as listInvites, POST as createInvite } from "../src/app/api/admin/invites/route";
import { GET as authGet, POST as authPost } from "../src/app/api/auth/[...all]/route";
import { BASE, makeHarness, type Harness } from "./harness";

// Importing the route modules above, with none of the web app's settings present, must not fail: services are built on first request.
const SERVICES = Symbol.for("page-chat.services");
let h: Harness;
let admin: string;

beforeAll(async () => {
  h = await makeHarness();
  admin = await h.makeAdmin();
  (globalThis as unknown as Record<symbol, unknown>)[SERVICES] = h.services;
});
afterAll(async () => {
  delete (globalThis as unknown as Record<symbol, unknown>)[SERVICES];
  await h.pool.end();
});

const post = (path: string, body: unknown, cookie = admin) =>
  new Request(`${BASE}${path}`, { method: "POST", headers: { "content-type": "application/json", origin: BASE, cookie }, body: JSON.stringify(body) });

describe("the Next.js route files", () => {
  it("hand every /api/auth request to Better Auth", async () => {
    const response = await authGet(new Request(`${BASE}/api/auth/ok`));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ok: true });
    const signIn = await authPost(post("/api/auth/sign-in/email", { email: "nobody@example.com", password: "whatever long enough" }, ""));
    expect(signIn.status).toBe(401);
  });

  it("serve the published keys", async () => {
    const response = await authGet(new Request(`${BASE}/api/auth/jwks`));
    expect(response.status).toBe(200);
    expect((await response.json()).keys).toBeInstanceOf(Array);
  });

  it("run the invitation routes for an administrator, and for nobody else", async () => {
    expect((await listInvites(new Request(`${BASE}/api/admin/invites`))).status).toBe(401);
    const created = await createInvite(post("/api/admin/invites", { email: "route@example.com" }));
    expect(created.status).toBe(201);
    const { invite } = await created.json();
    const listed = await listInvites(new Request(`${BASE}/api/admin/invites`, { headers: { cookie: admin } }));
    expect((await listed.json()).invites.map((i: { id: string }) => i.id)).toContain(invite.id);
    const revoke = (id: string) => revokeInvite(new Request(`${BASE}/api/admin/invites/${id}`, { method: "DELETE", headers: { origin: BASE, cookie: admin } }), { params: Promise.resolve({ id }) });
    expect((await revoke(invite.id)).status).toBe(204);
    expect((await revoke(invite.id)).status).toBe(404);
  });
});
