import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { migrate } from "../src/lib/migrate";
import { makeHarness, type Harness } from "./harness";

let h: Harness;
beforeAll(async () => {
  h = await makeHarness();
});
afterAll(() => h.pool.end());

const tablesIn = async (schema: string) =>
  (await h.pool.query("SELECT table_name FROM information_schema.tables WHERE table_schema = $1 ORDER BY 1", [schema])).rows.map((r) => r.table_name as string);

describe("the database", () => {
  it("keeps everything in the auth schema, the rate limit table included", async () => {
    expect(await tablesIn("auth")).toEqual(["account", "invite", "jwks", "rateLimit", "session", "user", "verification"]);
  });

  it("puts nothing in the public schema", async () => {
    expect(await tablesIn("public")).toEqual([]);
  });

  it("can be migrated again without harm or change", async () => {
    await migrate(h.services.auth, h.pool);
    await migrate(h.services.auth, h.pool);
    expect(await tablesIn("auth")).toHaveLength(7);
  });

  it("has the columns the administrator and ban features need", async () => {
    const { rows } = await h.pool.query(`SELECT column_name FROM information_schema.columns WHERE table_schema = 'auth' AND table_name = 'user'`);
    const columns = rows.map((r) => r.column_name);
    expect(columns).toEqual(expect.arrayContaining(["role", "banned", "banReason", "banExpires", "emailVerified"]));
  });

  it("stores no plain password anywhere readable as one", async () => {
    await h.makeUser("hashed@example.com");
    const { rows } = await h.pool.query(`SELECT password FROM auth.account WHERE password IS NOT NULL`);
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) expect(row.password).not.toContain("correct horse");
  });
});
