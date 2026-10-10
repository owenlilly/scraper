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
    const response = await h.call("/delete-user", { cookie, body: { password: PASSWORD } });
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ code: "ACCOUNT_DATA_NOT_DELETED" });
    expect(await exists("busy@example.com")).toBe(true);
  });

  it.each([0, 401, 500])("keeps the account when the Python API answers %i", async (status) => {
    behaviour = async () => {
      throw new AccountDataError(status);
    };
    const email = `api-${status}@example.com`;
    await h.makeUser(email);
    const id = (await h.userIdOf(email))!;
    const response = await h.call("/admin/remove-user", { cookie: admin, body: { userId: id } });
    expect(response.status).toBe(502);
    expect(await exists(email)).toBe(true);
  });

  it("does not let an ordinary person remove someone else", async () => {
    behaviour = async () => {};
    const mallory = await h.makeUser("mallory@example.com");
    await h.makeUser("victim@example.com");
    const id = (await h.userIdOf("victim@example.com"))!;
    expect([401, 403]).toContain((await h.call("/admin/remove-user", { cookie: mallory, body: { userId: id } })).status);
    expect(await exists("victim@example.com")).toBe(true);
  });

  it("needs the password to delete your own account", async () => {
    behaviour = async () => {};
    const cookie = await h.makeUser("careful@example.com");
    const response = await h.call("/delete-user", { cookie, body: { password: "wrong password entirely" } });
    expect(response.status).toBeGreaterThanOrEqual(400);
    expect(await exists("careful@example.com")).toBe(true);
  });
});
