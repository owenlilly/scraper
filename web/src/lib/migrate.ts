import { getMigrations } from "better-auth/db/migration";
import type { Pool } from "pg";
import type { Auth } from "./auth";
import { INVITE_TABLE_SQL } from "./invites";

/** Create the `auth` schema if it is missing, then Better Auth's tables, then ours. Safe to run again. */
export async function migrate(auth: Auth, pool: Pool): Promise<void> {
  // A role that owns only the schema cannot run CREATE SCHEMA, even IF NOT EXISTS, so only create it when it is absent.
  const { rowCount } = await pool.query("SELECT 1 FROM pg_namespace WHERE nspname = 'auth'");
  if (!rowCount) await pool.query("CREATE SCHEMA auth");
  const { runMigrations } = await getMigrations(auth.options);
  await runMigrations();
  await pool.query(INVITE_TABLE_SQL);
}
