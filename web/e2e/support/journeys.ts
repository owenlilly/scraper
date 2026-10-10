/**
 * Setting people up for a browser test without clicking through screens that another test already covers. Everything here goes over
 * HTTP to the real web app, and reads emailed links from its log (the stack runs with EMAIL_TRANSPORT=log).
 */
import { readFileSync } from "node:fs";
import type { BrowserContext, Locator, Page } from "@playwright/test";
import { env } from "./env";

export const PASSWORD = "correct horse battery staple";
export const OWNER = "owner@example.com";
export type Member = { email: string; password: string; cookie: string };

let counter = 0;
export const uniqueEmail = (prefix: string) => `${prefix}-${Date.now().toString(36)}${counter++}@example.com`;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
export const logLength = () => readFileSync(env.webLog, "utf8").length;

/** The first link in the newest email with this subject sent to `to`, written after `since` (a value from `logLength()`). */
export async function emailLink(to: string, subject: string, since = 0): Promise<string> {
  for (let i = 0; i < 100; i++) {
    const log = readFileSync(env.webLog, "utf8").slice(since);
    const at = log.lastIndexOf(`[email to ${to}] ${subject}`);
    const link = at >= 0 ? /https?:\/\/\S+/.exec(log.slice(at))?.[0] : undefined;
    if (link) return link;
    await sleep(100);
  }
  throw new Error(`no "${subject}" email for ${to} appeared in the web server's log`);
}

const web = (path: string, init: RequestInit & { cookie?: string } = {}) =>
  fetch(`${env.web}${path}`, {
    ...init,
    redirect: "manual",
    headers: { "content-type": "application/json", origin: env.web, ...(init.cookie ? { cookie: init.cookie } : {}), ...(init.headers as Record<string, string>) },
  });

const cookieOf = (response: Response) => response.headers.getSetCookie().map((c) => c.split(";")[0]).join("; ");

export async function signInHttp(email: string, password = PASSWORD): Promise<string> {
  const response = await web("/api/auth/sign-in/email", { method: "POST", body: JSON.stringify({ email, password }) });
  if (response.status !== 200) throw new Error(`sign-in for ${email} answered ${response.status}`);
  return cookieOf(response);
}

async function confirm(email: string, since: number) {
  const link = await emailLink(email, "Confirm your email address", since);
  const response = await fetch(link, { redirect: "manual", headers: { origin: env.web } });
  if (![200, 302].includes(response.status)) throw new Error(`the confirmation link answered ${response.status}`);
}

/** The first administrator, ADMIN_EMAIL. Signs up once, and signs in on every later call. */
export async function ownerCookie(): Promise<string> {
  try {
    return await signInHttp(OWNER);
  } catch {
    const since = logLength();
    const signUp = await web("/api/auth/sign-up/email", { method: "POST", body: JSON.stringify({ email: OWNER, password: PASSWORD, name: "Owner" }) });
    if (signUp.status !== 200) throw new Error(`the owner's sign-up answered ${signUp.status}: ${await signUp.text()}`);
    await confirm(OWNER, since);
    return signInHttp(OWNER);
  }
}

/** Ask the administrator routes to invite `email`, and return the link from the email. */
export async function inviteLink(email: string): Promise<string> {
  const since = logLength();
  const response = await web("/api/admin/invites", { method: "POST", cookie: await ownerCookie(), body: JSON.stringify({ email }) });
  if (response.status !== 201) throw new Error(`inviting ${email} answered ${response.status}: ${await response.text()}`);
  return emailLink(email, "You are invited to Page Chat", since);
}

/** Someone with a confirmed account, ready to use. */
export async function createMember(prefix = "member"): Promise<Member> {
  const email = uniqueEmail(prefix);
  const invite = new URLSearchParams(new URL(await inviteLink(email)).hash.slice(1)).get("invite")!;
  const since = logLength();
  const signUp = await web("/api/auth/sign-up/email", {
    method: "POST",
    body: JSON.stringify({ email, password: PASSWORD, name: prefix }),
    headers: { "x-invite-token": invite },
  });
  if (signUp.status !== 200) throw new Error(`sign-up for ${email} answered ${signUp.status}: ${await signUp.text()}`);
  await confirm(email, since);
  return { email, password: PASSWORD, cookie: await signInHttp(email) };
}

/** Put a session cookie into the browser, so a test can start already signed in. */
export async function signInBrowser(context: BrowserContext, cookie: string): Promise<void> {
  await context.addCookies(
    cookie.split("; ").map((pair) => {
      const [name, ...value] = pair.split("=");
      return { name, value: value.join("="), url: env.web };
    }),
  );
}

/** An alert on the page that says `text`. (Next.js adds an alert of its own, empty, that announces page changes; ask for ours by what it says.) */
export const alertSaying = (page: Page, text: string): Locator => page.getByRole("alert").filter({ hasText: text });
