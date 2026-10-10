import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { AccountDataError, type AccountDataClient } from "../src/lib/account-data";
import { makeHarness, PASSWORD, type Harness } from "./harness";

let h: Harness;
let admin: string;
let behaviour: (userId: string) => Promise<void> = async () => {};
const calls: { userId: string; userStillExisted: boolean }[] = [];

beforeAll(async () => {
  const accountData: AccountDataClient = {
    delete: async (userId) => {
      calls.push({ userId, userStillExisted: (await h.pool.query(`SELECT 1 FROM auth."user" WHERE id = $1`, [userId])).rowCount === 1 });
      await behaviour(userId);
    },
  };
  h = await makeHarness({ accountData });
  admin = await h.makeAdmin();
});
afterAll(() => h.pool.end());

const exists = async (email: string) => (await h.userIdOf(email)) !== undefined;

/** What a person needs to carry on after a failed deletion: their account, their password record and their sessions. */
const footprint = async (email: string) => {
  const id = await h.userIdOf(email);
  const count = async (table: string) => Number((await h.pool.query(`SELECT count(*) FROM auth.${table} WHERE "userId" = $1`, [id])).rows[0].count);
  return { user: id !== undefined, accounts: await count("account"), sessions: await count("session") };
};

describe("deleting an account", () => {
  it("asks the Python API to erase the person's data first, and only then removes the account", async () => {
    behaviour = async () => {};
    const cookie = await h.makeUser("leaving@example.com");
    const id = (await h.userIdOf("leaving@example.com"))!;
    const response = await h.call("/delete-user", { cookie, body: { password: PASSWORD } });
    expect(response.status).toBe(200);
    expect(calls.at(-1)).toEqual({ userId: id, userStillExisted: true }); // the API was asked while the account still existed
    expect(await exists("leaving@example.com")).toBe(false);
  });

  it("works the same when an administrator removes someone", async () => {
    behaviour = async () => {};
    await h.makeUser("removed@example.com");
    const id = (await h.userIdOf("removed@example.com"))!;
    const response = await h.call("/admin/remove-user", { cookie: admin, body: { userId: id } });
    expect(response.status).toBe(200);
    expect(calls.at(-1)?.userId).toBe(id);
    expect(await exists("removed@example.com")).toBe(false);
  });

  it("keeps the account, and says to try again, while a run is still stopping", async () => {
    behaviour = async () => {
      throw new AccountDataError(409);
    };
    const cookie = await h.makeUser("busy@example.com");
    const before = await footprint("busy@example.com");
    const response = await h.call("/delete-user", { cookie, body: { password: PASSWORD } });
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ code: "ACCOUNT_DATA_NOT_DELETED" });
    expect(await footprint("busy@example.com")).toEqual(before); // nothing was taken away, not even their sessions or password
    expect((await h.call("/get-session", { cookie })).status).toBe(200);
    expect(await (await h.call("/get-session", { cookie })).json()).not.toBeNull();
    expect((await h.signIn("busy@example.com")).status).toBe(200); // so they can sign in and try again
  });

  it.each([0, 401, 500])("keeps the account when the Python API answers %i", async (status) => {
    behaviour = async () => {
      throw new AccountDataError(status);
    };
    const email = `api-${status}@example.com`;
    const cookie = await h.makeUser(email);
    const id = (await h.userIdOf(email))!;
    const before = await footprint(email);
    const response = await h.call("/admin/remove-user", { cookie: admin, body: { userId: id } });
    expect(response.status).toBe(502);
    expect(await footprint(email)).toEqual(before);
    expect(await (await h.call("/get-session", { cookie })).json()).not.toBeNull();
    expect((await h.signIn(email)).status).toBe(200);
  });

  it("refuses an administrator removing themselves, without erasing anything of theirs first", async () => {
    behaviour = async () => {};
    const asked = calls.length;
    const adminId = (await h.userIdOf("owner@example.com"))!;
    const response = await h.call("/admin/remove-user", { cookie: admin, body: { userId: adminId } });
    expect(response.status).toBeGreaterThanOrEqual(400);
    expect(calls.length).toBe(asked); // the Python API was not asked to erase the administrator's data
    expect(await exists("owner@example.com")).toBe(true);
  });

  it("does not let an ordinary person remove someone else", async () => {
    behaviour = async () => {};
    const mallory = await h.makeUser("mallory@example.com");
    await h.makeUser("victim@example.com");
    const id = (await h.userIdOf("victim@example.com"))!;
    const asked = calls.length;
    expect([401, 403]).toContain((await h.call("/admin/remove-user", { cookie: mallory, body: { userId: id } })).status);
    expect([401, 403]).toContain((await h.call("/admin/remove-user", { body: { userId: id } })).status); // not signed in at all
    expect(await exists("victim@example.com")).toBe(true);
    expect(calls.length).toBe(asked); // and the Python API was never asked to erase the victim's data on their behalf
  });

  it.each([
    ["no password", {}],
    ["an empty password", { password: "" }],
    ["a password that is not text", { password: 12345 }],
    ["a null password", { password: null }],
  ])("needs the password even while the session is fresh, so a stolen session cookie cannot erase an account: %s", async (name, body) => {
    const email = `fresh-${name.replace(/\W+/g, "-")}@example.com`;
    const cookie = await h.makeUser(email);
    const asked = calls.length;
    const response = await h.call("/delete-user", { cookie, body });
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ code: "PASSWORD_REQUIRED" });
    expect(await exists(email)).toBe(true);
    expect(calls.length).toBe(asked); // nothing was erased in the Python API either
  });

  it("needs the password to delete your own account", async () => {
    behaviour = async () => {};
    const cookie = await h.makeUser("careful@example.com");
    const response = await h.call("/delete-user", { cookie, body: { password: "wrong password entirely" } });
    expect(response.status).toBeGreaterThanOrEqual(400);
    expect(await exists("careful@example.com")).toBe(true);
  });
});
