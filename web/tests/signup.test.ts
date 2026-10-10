import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { INVITE_REQUIRED_MESSAGE } from "../src/lib/auth";
import { BASE, makeHarness, PASSWORD, type Harness } from "./harness";

let h: Harness;
beforeAll(async () => {
  h = await makeHarness();
});
afterAll(() => h.pool.end());

const countUsers = async () => Number((await h.pool.query(`SELECT count(*) FROM auth."user"`)).rows[0].count);

describe("invitation-only sign-up", () => {
  it("refuses a sign-up with no invitation and creates nothing", async () => {
    const before = await countUsers();
    const response = await h.signUp("stranger@x.test");
    expect(response.status).toBe(403);
    expect(await response.json()).toMatchObject({ code: "INVITE_REQUIRED", message: INVITE_REQUIRED_MESSAGE });
    expect(await countUsers()).toBe(before);
  });

  it("refuses a made-up token and says nothing about why", async () => {
    const response = await h.signUp("stranger@x.test", "made-up-token");
    expect(response.status).toBe(403);
    expect(await response.json()).toMatchObject({ code: "INVITE_REQUIRED", message: INVITE_REQUIRED_MESSAGE });
  });

  it("refuses a real token used for a different address", async () => {
    const { token } = await h.services.invites.create("alice@x.test", null);
    expect((await h.signUp("mallory@x.test", token)).status).toBe(403);
    expect(await h.userIdOf("mallory@x.test")).toBeUndefined();
  });

  it("accepts the right token for the right address, in any letter case, and sends a verification email", async () => {
    const { token } = await h.services.invites.create("Bob@X.test", null);
    const response = await h.signUp("bob@x.test", token);
    expect(response.status).toBe(200);
    expect(await h.userIdOf("bob@x.test")).toBeTruthy();
    expect(h.lastEmailTo("bob@x.test")?.subject).toBe("Confirm your email address");
    expect((await h.services.invites.list()).find((i) => i.email === "bob@x.test")?.status).toBe("used");
  });

  it("will not let the same invitation be used a second time", async () => {
    const { token } = await h.services.invites.create("carol@x.test", null);
    expect((await h.signUp("carol@x.test", token)).status).toBe(200);
    expect((await h.signUp("carol@x.test", token)).status).toBe(403);
    expect((await h.signUp("someone-else@x.test", token)).status).toBe(403);
  });

  it("refuses an expired invitation and a revoked one", async () => {
    const expired = await h.services.invites.create("dave@x.test", null);
    await h.pool.query("UPDATE auth.invite SET expires_at = now() - interval '1 minute' WHERE id = $1", [expired.invite.id]);
    expect((await h.signUp("dave@x.test", expired.token)).status).toBe(403);
    const revoked = await h.services.invites.create("erin@x.test", null);
    await h.services.invites.revoke(revoked.invite.id);
    expect((await h.signUp("erin@x.test", revoked.token)).status).toBe(403);
  });

  it("does not let a newer invitation leave the older one working", async () => {
    const first = await h.services.invites.create("frank@x.test", null);
    const second = await h.services.invites.create("frank@x.test", null);
    expect((await h.signUp("frank@x.test", first.token)).status).toBe(403);
    expect((await h.signUp("frank@x.test", second.token)).status).toBe(200);
  });

  it("does not need the token for anything but sign-up", async () => {
    const response = await h.call("/sign-in/email", { body: { email: "nobody@x.test", password: PASSWORD } });
    expect(response.status).toBe(401); // wrong credentials, not a missing invitation
    expect(await response.text()).not.toContain("INVITE_REQUIRED");
  });

  it("rejects a short password even with a good invitation", async () => {
    const { token } = await h.services.invites.create("gina@x.test", null);
    const response = await h.signUp("gina@x.test", token, "short");
    expect(response.status).toBe(400);
    expect(await h.userIdOf("gina@x.test")).toBeUndefined();
  });

  it("takes a password of exactly 8 characters and refuses 7", async () => {
    const first = await h.services.invites.create("hugo@x.test", null);
    expect((await h.signUp("hugo@x.test", first.token, "abcdefg")).status).toBe(400);
    const second = await h.services.invites.create("ida@x.test", null);
    expect((await h.signUp("ida@x.test", second.token, "abcdefgh")).status).toBe(200);
  });
});

describe("email verification", () => {
  it("blocks sign-in until the address is confirmed, then signs the person in", async () => {
    const { token } = await h.services.invites.create("hana@x.test", null);
    await h.signUp("hana@x.test", token);
    const early = await h.signIn("hana@x.test");
    expect(early.status).toBe(403);
    expect(await early.json()).toMatchObject({ code: "EMAIL_NOT_VERIFIED" });
    const confirmed = await h.verify("hana@x.test");
    expect([200, 302]).toContain(confirmed.status);
    const session = await h.signIn("hana@x.test");
    expect(session.status).toBe(200);
    const me = await h.call("/get-session", { cookie: h.cookieFrom(session) });
    expect((await me.json()).user.email).toBe("hana@x.test");
  });

  it("puts a link on this site, not someone else's, in the verification email", async () => {
    const { token } = await h.services.invites.create("ivan@x.test", null);
    await h.signUp("ivan@x.test", token);
    expect(h.linkIn(h.lastEmailTo("ivan@x.test")!).startsWith(`${BASE}/api/auth/verify-email?token=`)).toBe(true);
  });
});


describe("ways around the invitation check", () => {
  it("finds none in other spellings of the sign-up address", async () => {
    const before = await countUsers();
    for (const path of ["/sign-up/email/", "//sign-up/email", "/SIGN-UP/EMAIL", "/sign-up/Email", "/sign-up/email?x=1", "/sign-up/email#", "/%73ign-up/email", "/sign-up/email%2F", "/sign-up/./email", "/../api/auth/sign-up/email"]) {
      const response = await h.call(path, { body: { email: "sneaky@x.test", password: PASSWORD, name: "s" } });
      expect(response.status, path).not.toBe(200);
    }
    expect(await h.userIdOf("sneaky@x.test")).toBeUndefined();
    expect(await countUsers()).toBe(before);
  });

  it("finds none in the other endpoints that can make an account", async () => {
    const body = { email: "backdoor@x.test", password: PASSWORD, name: "b", role: "admin" };
    for (const path of ["/admin/create-user", "/admin/set-role", "/sign-in/social", "/sign-in/anonymous", "/sign-up", "/sign-in/magic-link", "/sign-in/email-otp", "/sign-in/username"]) {
      const response = await h.call(path, { body });
      expect(response.status, path).toBeGreaterThanOrEqual(400);
    }
    const ordinary = await h.makeUser("ordinary@x.test");
    for (const path of ["/admin/create-user", "/admin/set-role"]) {
      const response = await h.call(path, { cookie: ordinary, body: { ...body, userId: await h.userIdOf("ordinary@x.test") } });
      expect([401, 403], path).toContain(response.status);
    }
    expect(await h.userIdOf("backdoor@x.test")).toBeUndefined();
    const role = (await h.pool.query(`SELECT role FROM auth."user" WHERE email = 'ordinary@x.test'`)).rows[0].role;
    expect(role).toBe("user"); // an ordinary person cannot make themselves an administrator
  });

  it("lets only one of two simultaneous sign-ups with the same invitation succeed", async () => {
    const { token } = await h.services.invites.create("racer@x.test", null);
    const results = await Promise.all([h.signUp("racer@x.test", token), h.signUp("racer@x.test", token), h.signUp("racer@x.test", token)]);
    const statuses = results.map((r) => r.status);
    expect(statuses.filter((status) => status === 200)).toHaveLength(1);
    expect(statuses.every((status) => status < 500)).toBe(true); // the losers are refused cleanly, not with a server error
    expect(Number((await h.pool.query(`SELECT count(*) FROM auth."user" WHERE email = 'racer@x.test'`)).rows[0].count)).toBe(1);
  });

  it("does not let a person set their own role while signing up", async () => {
    const { token } = await h.services.invites.create("climber@x.test", null);
    const response = await h.call("/sign-up/email", { body: { email: "climber@x.test", password: PASSWORD, name: "c", role: "admin" }, headers: { "x-invite-token": token } });
    const role = (await h.pool.query(`SELECT role FROM auth."user" WHERE email = 'climber@x.test'`)).rows[0]?.role;
    expect(role === undefined || role === "user", `role was ${role} after status ${response.status}`).toBe(true);
  });
});
