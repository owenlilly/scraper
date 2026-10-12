import { Pool } from "pg";

/** A pool whose connections see only the `auth` schema, so this app never touches the Python API's tables. */
export function makePool(connectionString: string, max = 5): Pool {
  return new Pool({ connectionString, max, options: "-c search_path=auth" });
}
