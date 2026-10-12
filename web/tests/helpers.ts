import { Pool } from "pg";
import { INVITE_TABLE_SQL } from "../src/lib/invites";
import { makePool } from "../src/lib/db";

/** The scratch database the tests may wipe. They drop and recreate its `auth` schema, so the name must say "test". */
export function testDatabaseUrl(): string {
  const url = process.env.WEB_TEST_DATABASE_URL;
  if (!url) throw new Error("Set WEB_TEST_DATABASE_URL to a scratch Postgres database. These tests drop and recreate its `auth` schema.");
  const name = /^postgres(?:ql)?:\/\/[^/]*\/([^?#]*)/.exec(url)?.[1] ?? ""; // not `new URL`: it rejects socket-style addresses with no host
  if (!/test/i.test(name)) throw new Error(`Refusing to use database "${name}": the name must contain "test", because the tests wipe it.`);
  return url;
}

export async function freshSchema(): Promise<Pool> {
  const admin = new Pool({ connectionString: testDatabaseUrl() });
  await admin.query("DROP SCHEMA IF EXISTS auth CASCADE");
  await admin.query("CREATE SCHEMA auth");
  await admin.end();
  return makePool(testDatabaseUrl());
}

export async function inviteOnlyPool(): Promise<Pool> {
  const pool = await freshSchema();
  await pool.query(INVITE_TABLE_SQL);
  return pool;
}
