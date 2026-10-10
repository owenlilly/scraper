import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { BASE, makeHarness, PASSWORD, type Harness } from "./harness";

let h: Harness;
beforeAll(async () => {
  h = await makeHarness();
});
afterAll(() => h.pool.end());

const requestReset = (email: string) => h.call("/request-password-reset", { body: { email, redirectTo: `${BASE}/reset-password` } });
const tokenIn = (link: string) => new URL(link).pathname.split("/").pop()!;

describe("password reset", () => {
  it("emails a one-time link to a person who has an account", async () => {
    await h.makeUser("forgetful@example.com");
    expect((await requestReset("forgetful@example.com")).status).toBe(200);
    const mail = h.lastEmailTo("forgetful@example.com")!;
    expect(mail.subject).toBe("Reset your password");
    expect(h.linkIn(mail).startsWith(`${BASE}/api/auth/reset-password/`)).toBe(true);
  });

  it("answers an address with no account exactly the same way, and sends nothing", async () => {
    const known = await requestReset("forgetful@example.com");
    const before = h.sent.length;
    const unknown = await requestReset("nobody-at-all@example.com");
    expect(unknown.status).toBe(known.status);
    expect(await unknown.json()).toEqual(await known.json());
    expect(h.sent.length).toBe(before);
  });

  it("sets the new password, ends the old sessions, and works only once", async () => {
    const oldSession = await h.makeUser("resetme@example.com");
    await requestReset("resetme@example.com");
    const token = tokenIn(h.linkIn(h.lastEmailTo("resetme@example.com")!));
    const newPassword = "an entirely different passphrase";
    expect((await h.call("/reset-password", { body: { newPassword, token } })).status).toBe(200);
    expect((await h.signIn("resetme@example.com", PASSWORD)).status).toBe(401);
    expect((await h.signIn("resetme@example.com", newPassword)).status).toBe(200);
    expect(await (await h.call("/get-session", { cookie: oldSession })).json()).toBeNull();
    expect((await h.call("/reset-password", { body: { newPassword: "yet another passphrase here", token } })).status).toBe(400);
  });

  it("refuses a short new password and a made-up token", async () => {
    await h.makeUser("short@example.com");
    await requestReset("short@example.com");
    const token = tokenIn(h.linkIn(h.lastEmailTo("short@example.com")!));
    expect((await h.call("/reset-password", { body: { newPassword: "short", token } })).status).toBe(400);
    expect((await h.call("/reset-password", { body: { newPassword: "a long enough new passphrase", token: "made-up" } })).status).toBe(400);
  });
});
