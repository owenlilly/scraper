import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterAll, describe, expect, it } from "vitest";

const script = resolve(__dirname, "../scripts/migrate.ts");
const dirs: string[] = [];
afterAll(() => dirs.forEach((d) => rmSync(d, { recursive: true, force: true })));

/** Runs the real migrate script from a folder holding the given env files, with no settings in the environment. */
function runMigrate(files: Record<string, string>, extraEnv: Record<string, string> = {}) {
  const dir = mkdtempSync(join(tmpdir(), "migrate-env-"));
  dirs.push(dir);
  for (const [name, text] of Object.entries(files)) writeFileSync(join(dir, name), text);
  const env = { PATH: process.env.PATH ?? "", HOME: process.env.HOME ?? "", ...extraEnv } as unknown as NodeJS.ProcessEnv;
  const result = spawnSync(resolve(__dirname, "../node_modules/.bin/tsx"), [script], { cwd: dir, env, encoding: "utf8" });
  return result.stderr + result.stdout;
}

const SECRET = `BETTER_AUTH_SECRET=${"s".repeat(40)}\n`;

describe("npm run migrate", () => {
  it("reads .env, as the web app does, so the settings are not asked for again", () => {
    const output = runMigrate({ ".env": SECRET });
    expect(output).toContain("DATABASE_URL is required"); // still missing, and the only thing reported for the database
    expect(output).not.toContain("BETTER_AUTH_SECRET");
  });

  it("lets .env.local win over .env", () => {
    const output = runMigrate({ ".env": "BETTER_AUTH_SECRET=short\n", ".env.local": SECRET });
    expect(output).not.toContain("BETTER_AUTH_SECRET");
  });

  it("lets a real environment variable win over both files", () => {
    const output = runMigrate({ ".env": SECRET }, { BETTER_AUTH_SECRET: "short" });
    expect(output).toContain("BETTER_AUTH_SECRET must be at least 32 characters");
  });
});
