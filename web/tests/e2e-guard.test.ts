import { spawnSync } from "node:child_process";
import { describe, expect, it } from "vitest";

// The end-to-end script drops the `auth` and `app` schemas of the database it is given, so its guard must look at the database
// NAME, not at the whole address. Every address here points at a closed port, so nothing could be touched even if the guard failed.
const SCRIPTS = ["scripts/e2e.sh", "scripts/e2e-ui.sh"];
const run = (script: string, url: string) => spawnSync("bash", [script], { env: { ...process.env, E2E_DATABASE_URL: url }, encoding: "utf8", timeout: 60_000 });

describe.each(SCRIPTS)("%s database guard", (script) => {
  it.each([
    ["a user named like a test", "postgresql://tester@127.0.0.1:1/production"],
    ["a host named like a test", "postgresql://owner@test-db.internal:1/production"],
    ["a password containing test", "postgresql://owner:test1234@127.0.0.1:1/production"],
    ["a query string containing test", "postgresql://owner@127.0.0.1:1/production?application_name=test"],
    ["no database name at all", "postgresql://owner@test-host:1"],
    ["a socket-style address with a real name", "postgresql://owner@/production?host=/tmp/test"],
  ])("refuses %s", (_name, url) => {
    const result = run(script, url);
    expect(result.status).toBe(2);
    expect(result.stderr).toMatch(/database name/);
  });

  it("lets a database whose name contains test get past the guard", () => {
    const result = run(script, "postgresql://owner@127.0.0.1:1/scraper_e2e_test"); // fails later, on the closed port, which is the point
    expect(result.stderr).not.toMatch(/database name/);
  });

  it("asks for the address when none is given", () => {
    const result = spawnSync("bash", [script], { env: { ...process.env, E2E_DATABASE_URL: "" }, encoding: "utf8", timeout: 60_000 });
    expect(result.status).not.toBe(0);
    expect(result.stderr).toMatch(/E2E_DATABASE_URL/);
  });
});
