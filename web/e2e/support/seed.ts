/**
 * Giving a person something to look at without running the agent. The server will not fetch addresses on this machine (that is the
 * point of its guard), so a browser test cannot scrape a local page. It writes the saved session straight into the `app` schema instead,
 * in the same shape the Python API saves.
 */
import pg from "pg";
import { env } from "./env";

export type SeedPage = { url: string; title: string; markdown?: string };
export type SeedMessage = { role: "user" | "assistant"; content: string; note?: string; trace?: { name: string; args: Record<string, unknown>; text: string; ok: boolean }[] };

/** The id Better Auth gave this person, read from their session cookie. */
export async function userIdOf(cookie: string): Promise<string> {
  const response = await fetch(`${env.web}/api/auth/get-session`, { headers: { cookie } });
  const body = (await response.json()) as { user?: { id?: string } } | null;
  if (!body?.user?.id) throw new Error("that cookie is not a signed-in session");
  return body.user.id;
}

export async function seedSession(userId: string, seed: { pages?: SeedPage[]; rows?: Record<string, unknown>[]; history?: SeedMessage[] }): Promise<void> {
  const client = new pg.Client({ connectionString: env.database });
  await client.connect();
  try {
    await client.query("BEGIN");
    for (const table of ["pages", "rows", "history"]) await client.query(`DELETE FROM app.${table} WHERE user_id = $1`, [userId]);
    await client.query("INSERT INTO app.sessions (user_id, session_id, run_counter) VALUES ($1, $2, $3) ON CONFLICT (user_id) DO NOTHING", [userId, `seed-${Date.now()}`, 0]);
    for (const [position, page] of (seed.pages ?? []).entries()) {
      const markdown = page.markdown ?? `# ${page.title}\n\nSeeded page text for ${page.url}.`;
      await client.query(
        "INSERT INTO app.pages (user_id, key, position, url, title, markdown, links, html, looks_empty) VALUES ($1, $2, $3, $4, $5, $6, '[]'::jsonb, '', false)",
        [userId, page.url, position, page.url, page.title, markdown],
      );
    }
    for (const row of seed.rows ?? []) await client.query("INSERT INTO app.rows (user_id, data) VALUES ($1, $2::json)", [userId, JSON.stringify(row)]);
    for (const message of seed.history ?? []) {
      await client.query(
        "INSERT INTO app.history (user_id, role, content, reasoning, sources, interrupted, note, trace) VALUES ($1, $2, $3, '', '[]'::jsonb, false, $4, $5::jsonb)",
        [userId, message.role, message.content, message.note ?? "", JSON.stringify(message.trace ?? [])],
      );
    }
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    await client.end();
  }
}

export const PLAN_ROWS = [
  { plan: "Solo", price: "$9", page: "shop.example/plans" },
  { plan: "Team", price: "$49", page: "shop.example/plans/team" },
  { plan: "Studio", price: "$99", page: "shop.example/plans/studio" },
];

export const PLAN_HISTORY: SeedMessage[] = [
  { role: "user", content: "Go to shop.example/plans and collect every plan with its price." },
  {
    role: "assistant",
    content: "I found three plans on three pages and added them to the table.\n\nTeam and Studio each show two prices.",
    note: "3 rows added to the results table",
    trace: [
      { name: "fetch_page", args: { url: "https://shop.example/plans" }, text: "Fetched", ok: true },
      { name: "add_rows", args: { rows: [{}, {}, {}] }, text: "Added 3 rows", ok: true },
    ],
  },
];

export const PLAN_PAGES: SeedPage[] = [
  { url: "https://shop.example/plans", title: "Plans and pricing" },
  { url: "https://shop.example/plans/team", title: "Team plan" },
];

/** Today's count of model calls, so a person can be put at their daily limit. */
export async function seedUsage(userId: string, modelCalls: number): Promise<void> {
  const client = new pg.Client({ connectionString: env.database });
  await client.connect();
  try {
    await client.query(
      "INSERT INTO app.usage (user_id, day, model_calls, pages) VALUES ($1, (now() AT TIME ZONE 'utc')::date, $2, 0) ON CONFLICT (user_id, day) DO UPDATE SET model_calls = EXCLUDED.model_calls",
      [userId, modelCalls],
    );
  } finally {
    await client.end();
  }
}

