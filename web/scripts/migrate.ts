import { getServices } from "../src/lib/services";
import { migrate } from "../src/lib/migrate";

async function main() {
  const { auth, pool } = getServices();
  await migrate(auth, pool);
  await pool.end();
  console.log("Database is up to date.");
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
});
