import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { makeHarness, PASSWORD, type Harness } from "./harness";

let h: Harness;
beforeAll(async () => {
  h = await makeHarness({ production: true });
});
afterAll(() => h.pool.end());

const fromTheInternet = { "x-forwarded-for": "203.0.113.7" };

describe("in production", () => {
  it("serves the app over https and marks the session cookie Secure, HttpOnly and SameSite", async () => {
    expect(h.base.startsWith("https://")).toBe(true);
    await h.makeUser("secure@example.com");
    const cookies = (await h.signIn("secure@example.com")).headers.getSetCookie();
    const session = cookies.find((c) => c.includes("session_token"))!;
    expect(session.startsWith("__Secure-")).toBe(true);
    expect(session).toMatch(/;\s*Secure/i);
    expect(session).toMatch(/;\s*HttpOnly/i);
    expect(session).toMatch(/;\s*SameSite=Lax/i);
  });

  it("slows down someone guessing passwords", async () => {
    const statuses: number[] = [];
    for (let i = 0; i < 8; i++) statuses.push((await h.call("/sign-in/email", { body: { email: "guess@example.com", password: `wrong password ${i} here` }, headers: fromTheInternet })).status);
    expect(statuses).toContain(429);
    expect(statuses[0]).toBe(401);
  });

  it("keeps the rate limit counts in the database, shared by every server instance", async () => {
    const { rows } = await h.pool.query(`SELECT count(*)::int AS n FROM auth."rateLimit"`);
    expect(rows[0].n).toBeGreaterThan(0);
  });
});
