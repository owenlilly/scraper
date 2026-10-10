/**
 * Drives a running web app and a running Python API through a person's whole life cycle:
 * sign up as the first administrator, invite someone, check that the API accepts the web app's tokens (and nothing else),
 * keep two people's data apart, and delete an account. Start both servers with scripts/e2e.sh, which calls this.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { Pool } from "pg";

const WEB = process.env.WEB_URL!;
const API = process.env.API_URL!;
const LOG = process.env.WEB_LOG!;
const DATABASE_URL = process.env.DATABASE_URL!;
const ADMIN = (process.env.ADMIN_EMAIL ?? "owner@example.com").toLowerCase();
const PASSWORD = "correct horse battery staple";
const origin = { origin: WEB };

const step = (text: string) => console.log(`\n== ${text}`);
const ok = (text: string) => console.log(`   ok  ${text}`);

async function emailLink(to: string, subject: string): Promise<string> {
  for (let i = 0; i < 50; i++) {
    const log = readFileSync(LOG, "utf8");
    const at = log.lastIndexOf(`[email to ${to}] ${subject}`);
    const link = at >= 0 ? /https?:\/\/\S+/.exec(log.slice(at))?.[0] : undefined;
    if (link) return link;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`no "${subject}" email for ${to} appeared in the web server's log`);
}

const cookieOf = (response: Response) => response.headers.getSetCookie().map((c) => c.split(";")[0]).join("; ");
const web = (path: string, init: RequestInit & { cookie?: string } = {}) =>
  fetch(`${WEB}${path}`, { ...init, redirect: "manual", headers: { "content-type": "application/json", ...origin, ...(init.cookie ? { cookie: init.cookie } : {}), ...(init.headers as Record<string, string>) } });
const api = (path: string, token?: string, init: RequestInit = {}) =>
  fetch(`${API}${path}`, { ...init, headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}), ...(init.headers as Record<string, string>) } });

async function join(email: string, inviteToken?: string): Promise<{ cookie: string; token: string; id: string }> {
  const signUp = await web("/api/auth/sign-up/email", { method: "POST", body: JSON.stringify({ email, password: PASSWORD, name: email.split("@")[0] }), headers: inviteToken ? { "x-invite-token": inviteToken } : {} });
  const signUpText = await signUp.text();
  assert.equal(signUp.status, 200, `sign-up for ${email}: ${signUpText}`);
  assert.equal((await web("/api/auth/sign-in/email", { method: "POST", body: JSON.stringify({ email, password: PASSWORD }) })).status, 403, "sign-in must wait for the confirmed address");
  const verified = await fetch(await emailLink(email, "Confirm your email address"), { redirect: "manual", headers: origin });
  assert.ok([200, 302].includes(verified.status), `verification link answered ${verified.status}`);
  const signedIn = await web("/api/auth/sign-in/email", { method: "POST", body: JSON.stringify({ email, password: PASSWORD }) });
  assert.equal(signedIn.status, 200);
  const cookie = cookieOf(signedIn);
  const token = ((await (await web("/api/auth/token", { cookie })).json()) as { token: string }).token;
  const session = (await (await web("/api/auth/get-session", { cookie })).json()) as { user: { id: string } };
  return { cookie, token, id: session.user.id };
}

async function main() {
  const pool = new Pool({ connectionString: DATABASE_URL });
  const rows = async (table: string, id: string) => Number((await pool.query(`SELECT count(*) FROM app.${table} WHERE user_id = $1`, [id])).rows[0].count);

  step("the API refuses anyone without a token");
  assert.equal((await api("/health")).status, 200);
  assert.equal((await api("/state")).status, 401);
  assert.equal((await api("/state", "garbage.token.here")).status, 401);
  ok("no token and a garbage token are both refused; /health is open");

  step("the first administrator signs up with no invitation");
  const owner = await join(ADMIN);
  ok(`signed up, confirmed by email and signed in as ${owner.id}`);

  step("the API accepts the web app's token, and only an untampered one");
  const state = await api("/state", owner.token);
  const stateText = await state.text();
  assert.equal(state.status, 200, stateText);
  assert.equal((JSON.parse(stateText) as { user: { id: string } }).user.id, owner.id);
  const [head, body, signature] = owner.token.split(".");
  const flipped = signature.startsWith("A") ? `B${signature.slice(1)}` : `A${signature.slice(1)}`;
  assert.equal((await api("/state", `${head}.${body}.${flipped}`)).status, 401);
  assert.equal((await api("/state", `${head}.${Buffer.from(JSON.stringify({ sub: "someone-else", aud: "x", iss: "y", exp: 9999999999 })).toString("base64url")}.${signature}`)).status, 401);
  ok("the API knows the user from the token alone; a changed signature or a rewritten payload is refused");

  step("invite someone");
  assert.equal((await web("/api/admin/invites", { method: "POST", cookie: owner.cookie, body: JSON.stringify({ email: "bob@example.com" }), headers: { origin: "https://evil.example" } })).status, 403);
  const created = await web("/api/admin/invites", { method: "POST", cookie: owner.cookie, body: JSON.stringify({ email: "bob@example.com" }) });
  assert.equal(created.status, 201);
  const link = new URL(await emailLink("bob@example.com", "You are invited to Page Chat"));
  assert.equal(link.pathname, "/sign-up");
  const inviteToken = link.searchParams.get("invite")!;
  assert.equal((await web("/api/auth/sign-up/email", { method: "POST", body: JSON.stringify({ email: "mallory@example.com", password: PASSWORD, name: "m" }), headers: { "x-invite-token": inviteToken } })).status, 403);
  const bob = await join("bob@example.com", inviteToken);
  ok(`bob joined with the emailed link as ${bob.id}; the same link cannot make someone else's account`);

  step("two people's data stay apart");
  assert.equal((await api("/settings/url", owner.token, { method: "PUT", body: JSON.stringify({ value: "https://owner.example/" }) })).status, 204);
  assert.equal((await api("/settings/url", bob.token, { method: "PUT", body: JSON.stringify({ value: "https://bob.example/" }) })).status, 204);
  const ownerState = (await (await api("/state", owner.token)).json()) as { settings: { url: string }; user: { id: string } };
  const bobState = (await (await api("/state", bob.token)).json()) as { settings: { url: string }; user: { id: string } };
  assert.equal(ownerState.settings.url, "https://owner.example/");
  assert.equal(bobState.settings.url, "https://bob.example/");
  assert.notEqual(ownerState.user.id, bobState.user.id);
  assert.ok((await rows("prefs", bob.id)) > 0);
  ok("each person sees only their own saved settings");

  step("deleting an account removes the person's data in the API first");
  const deletion = await web("/api/auth/delete-user", { method: "POST", cookie: bob.cookie, body: JSON.stringify({ password: PASSWORD }) });
  const deletionText = await deletion.text();
  assert.equal(deletion.status, 200, deletionText);
  assert.equal(await rows("prefs", bob.id), 0);
  assert.equal((await pool.query(`SELECT 1 FROM auth."user" WHERE id = $1`, [bob.id])).rowCount, 0);
  assert.ok((await rows("prefs", owner.id)) > 0, "the owner's data must be untouched");
  ok("bob's account and everything the API held for him are gone; the owner's data is intact");

  await pool.end();
  console.log("\nALL CHECKS PASSED");
}

main().catch((error) => {
  console.error("\nFAILED:", error instanceof Error ? error.message : error);
  process.exit(1);
});
