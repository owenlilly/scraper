import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { makeHarness, type Harness } from "./harness";

let h: Harness;
let admin: string;
beforeAll(async () => {
  h = await makeHarness();
  admin = await h.makeAdmin();
});
afterAll(() => h.pool.end());

describe("banning", () => {
  it("stops a banned person signing in, and a lifted ban lets them back", async () => {
    await h.makeUser("naughty@example.com");
    const id = (await h.userIdOf("naughty@example.com"))!;
    expect((await h.call("/admin/ban-user", { cookie: admin, body: { userId: id } })).status).toBe(200);
    const blocked = await h.signIn("naughty@example.com");
    expect(blocked.status).toBe(403);
    expect(await blocked.json()).toMatchObject({ code: "BANNED_USER" });
    expect((await h.call("/admin/unban-user", { cookie: admin, body: { userId: id } })).status).toBe(200);
    expect((await h.signIn("naughty@example.com")).status).toBe(200);
  });

  it("ends the sessions the person already had", async () => {
    const cookie = await h.makeUser("loggedin@example.com");
    const id = (await h.userIdOf("loggedin@example.com"))!;
    await h.call("/admin/ban-user", { cookie: admin, body: { userId: id } });
    const session = await h.call("/get-session", { cookie });
    expect(await session.json()).toBeNull();
  });
});
