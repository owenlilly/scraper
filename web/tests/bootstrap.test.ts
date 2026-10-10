import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { makeHarness, type Harness } from "./harness";

let h: Harness;
beforeAll(async () => {
  h = await makeHarness({ adminEmail: "boss@example.com" });
});
afterAll(() => h.pool.end());

describe("ADMIN_EMAIL is only for the very first administrator", () => {
  it("needs an invitation once somebody else is already an administrator", async () => {
    await h.makeUser("someone@example.com");
    await h.pool.query(`UPDATE auth."user" SET role = 'admin' WHERE email = 'someone@example.com'`);
    const response = await h.signUp("boss@example.com");
    expect(response.status).toBe(403);
    expect(await h.userIdOf("boss@example.com")).toBeUndefined();
  });

  it("still works with an invitation", async () => {
    const { token } = await h.services.invites.create("boss@example.com", null);
    expect((await h.signUp("boss@example.com", token)).status).toBe(200);
    const role = (await h.pool.query(`SELECT role FROM auth."user" WHERE email = 'boss@example.com'`)).rows[0].role;
    expect(role).toBe("user"); // being named in ADMIN_EMAIL no longer makes anyone an administrator
  });
});

describe("with no ADMIN_EMAIL at all", () => {
  it("nobody can sign up without an invitation, not even the first person", async () => {
    const bare = await makeHarness({ adminEmail: null });
    try {
      expect((await bare.signUp("owner@example.com")).status).toBe(403);
    } finally {
      await bare.pool.end();
    }
  });
});
