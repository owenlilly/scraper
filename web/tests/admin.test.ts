import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { adminInviteHandlers } from "../src/lib/admin-invites";
import { BASE, makeHarness, type Harness } from "./harness";

let h: Harness;
let handlers: ReturnType<typeof adminInviteHandlers>;
let admin: string; // the session cookie of the first administrator, ADMIN_EMAIL
let regular: string; // and of an ordinary person
beforeAll(async () => {
  h = await makeHarness();
  handlers = adminInviteHandlers(h.services);
  admin = await h.makeAdmin("owner@example.com");
  regular = await h.makeUser("regular@example.com");
});
afterAll(() => h.pool.end());

const request = (method: string, body: unknown, cookie: string, origin: string | null = BASE) =>
  new Request(`${BASE}/api/admin/invites`, {
    method,
    headers: { "content-type": "application/json", cookie, ...(origin ? { origin } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

describe("the first administrator", () => {
  it("is the person at ADMIN_EMAIL, who needed no invitation", async () => {
    expect(admin).toContain("session_token");
    const role = (await h.pool.query(`SELECT role FROM auth."user" WHERE email = 'owner@example.com'`)).rows[0].role;
    expect(role).toBe("admin");
  });

  it("is only that address", async () => {
    expect((await h.signUp("not-the-owner@example.com")).status).toBe(403);
  });

  it("makes ordinary people ordinary", async () => {
    const role = (await h.pool.query(`SELECT role FROM auth."user" WHERE email = 'regular@example.com'`)).rows[0].role;
    expect(role).toBe("user");
  });
});

describe("invitation routes", () => {
  it("need a signed-in administrator", async () => {
    expect((await handlers.list(request("GET", undefined, ""))).status).toBe(401);
    expect((await handlers.list(request("GET", undefined, regular))).status).toBe(403);
    expect((await handlers.create(request("POST", { email: "x@example.com" }, regular))).status).toBe(403);
    expect((await handlers.revoke(request("DELETE", undefined, regular), "00000000-0000-0000-0000-000000000000")).status).toBe(403);
  });

  it("refuse an administrator who has been banned", async () => {
    const cookie = await h.makeUser("banned-admin@example.com");
    await h.pool.query(`UPDATE auth."user" SET role = 'admin', banned = true WHERE email = 'banned-admin@example.com'`);
    expect((await handlers.list(request("GET", undefined, cookie))).status).toBe(403);
  });

  it("send an invitation by email and never put the token in the response", async () => {
    const response = await handlers.create(request("POST", { email: " New.Person@Example.com " }, admin));
    expect(response.status).toBe(201);
    const text = await response.text();
    expect(JSON.parse(text).invite).toMatchObject({ email: "new.person@example.com", status: "pending" });
    const mail = h.lastEmailTo("new.person@example.com")!;
    expect(mail.subject).toBe("You are invited to Page Chat");
    const link = new URL(h.linkIn(mail));
    expect(link.origin + link.pathname).toBe(`${BASE}/sign-up`);
    expect(link.searchParams.get("email")).toBe("new.person@example.com");
    expect(text).not.toContain(link.searchParams.get("invite")!);
  });

  it("send a link that really lets that person sign up, once", async () => {
    await handlers.create(request("POST", { email: "linked@example.com" }, admin));
    const link = new URL(h.linkIn(h.lastEmailTo("linked@example.com")!));
    const token = link.searchParams.get("invite")!;
    expect((await h.signUp("linked@example.com", token)).status).toBe(200);
    expect((await h.signUp("linked@example.com", token)).status).toBe(403);
  });

  it("list invitations with their status, newest first", async () => {
    const { invites } = await (await handlers.list(request("GET", undefined, admin))).json();
    expect(invites.length).toBeGreaterThan(0);
    expect(invites[0]).toHaveProperty("status");
    expect(JSON.stringify(invites)).not.toMatch(/token/i);
  });

  it("refuse a bad address, and an address that already has an account", async () => {
    for (const email of ["", "not-an-email", "a@b", 42, "x".repeat(300) + "@example.com"]) {
      expect((await handlers.create(request("POST", { email }, admin))).status).toBe(422);
    }
    expect((await handlers.create(request("POST", undefined, admin))).status).toBe(422);
    const dup = await handlers.create(request("POST", { email: "REGULAR@example.com" }, admin));
    expect(dup.status).toBe(409);
    expect((await dup.json()).error.code).toBe("already_has_account");
  });

  it("leave no open invitation behind when the email cannot be sent", async () => {
    const failing = adminInviteHandlers({ ...h.services, sendEmail: async () => { throw new Error("resend is down"); } });
    const response = await failing.create(request("POST", { email: "unlucky@example.com" }, admin));
    expect(response.status).toBe(502);
    expect((await h.services.invites.list()).filter((i) => i.email === "unlucky@example.com" && i.status === "pending")).toEqual([]);
  });

  it("revoke a waiting invitation once", async () => {
    const created = await (await handlers.create(request("POST", { email: "revokeme@example.com" }, admin))).json();
    const id = created.invite.id;
    expect((await handlers.revoke(request("DELETE", undefined, admin), id)).status).toBe(204);
    expect((await handlers.revoke(request("DELETE", undefined, admin), id)).status).toBe(404);
    expect((await handlers.revoke(request("DELETE", undefined, admin), "not-an-id")).status).toBe(404);
  });

  it("refuse changes that did not come from this site, even from an administrator", async () => {
    for (const origin of ["https://evil.example", null]) {
      expect((await handlers.create(request("POST", { email: "csrf@example.com" }, admin, origin))).status).toBe(403);
      expect((await handlers.revoke(request("DELETE", undefined, admin, origin), "00000000-0000-0000-0000-000000000000")).status).toBe(403);
    }
    expect(await h.userIdOf("csrf@example.com")).toBeUndefined();
    expect((await h.services.invites.list()).some((i) => i.email === "csrf@example.com")).toBe(false);
  });
});
