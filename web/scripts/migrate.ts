import { loadEnvConfig } from "@next/env";
import { getServices } from "../src/lib/services";
import { migrate } from "../src/lib/migrate";

async function main() {
  // Read .env and .env.local from this folder, the way `next dev` does (real environment variables still win), so the same
  // settings serve the web app and its migration. In production the host's environment is the source and no file is needed.
  loadEnvConfig(process.cwd(), process.env.NODE_ENV !== "production");
  const { auth, pool } = getServices();
  await migrate(auth, pool);
  await pool.end();
  console.log("Database is up to date.");
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
