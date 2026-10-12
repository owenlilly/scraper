import { expect, test } from "@playwright/test";
import pg from "pg";
import { env } from "../support/env";
import { createMember, emailLink, logLength, ownerCookie, signInBrowser, signInHttp, uniqueEmail } from "../support/journeys";
import { PLAN_ROWS, seedSession, userIdOf } from "../support/seed";

test.describe("the administrator's page", () => {
  test("is for administrators only", async ({ page, context }) => {
    const member = await createMember("plain");
    await signInBrowser(context, member.cookie);
    await page.goto("/admin");
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", { name: "Invitations and people" })).toHaveCount(0);

    await context.clearCookies();
    await page.goto("/admin");
    await expect(page).toHaveURL(/\/sign-in/);
  });

  test("invites someone, who gets the email, and revokes the invitation, then invites again", async ({ page, context }) => {
    await signInBrowser(context, await ownerCookie());
    await page.goto("/admin");
    const email = uniqueEmail("guest");
    const since = logLength();

    await page.getByLabel("Email address").fill(email);
    await page.getByRole("button", { name: "Send invitation" }).click();
    const row = page.getByRole("row", { name: new RegExp(email) });
    await expect(row.getByText("Waiting")).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: `Invitation sent to ${email}` })).toBeVisible();
    expect(await emailLink(email, "You are invited to Page Chat", since)).toContain("/sign-up#invite=");

    await row.getByRole("button", { name: "Revoke" }).click();
    await expect(row.getByText("Revoked")).toBeVisible();

    await row.getByRole("button", { name: "Invite again" }).click();
    await expect(page.getByRole("row", { name: new RegExp(email) }).getByText("Waiting")).toHaveCount(1);
  });

  test("says so when the address already has an account, or is not an address", async ({ page, context }) => {
    const member = await createMember("taken");
    await signInBrowser(context, await ownerCookie());
    await page.goto("/admin");
    await page.getByLabel("Email address").fill(member.email);
    await page.getByRole("button", { name: "Send invitation" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "That address already has an account." })).toBeVisible();
  });

  test("bans someone, who can then no longer use the service, and lifts the ban", async ({ page, context }) => {
    const member = await createMember("banned");
    await signInBrowser(context, await ownerCookie());
    await page.goto("/admin");
    const row = page.getByRole("region", { name: "People" }).getByRole("row", { name: new RegExp(member.email) });
    await row.getByRole("button", { name: "Ban" }).click();
    await expect(row.locator(".pill", { hasText: "Banned" })).toBeVisible();
    expect((await fetch(`${env.web}/api/auth/token`, { headers: { cookie: member.cookie } })).status).toBeGreaterThanOrEqual(400);

    await row.getByRole("button", { name: "Lift ban" }).click();
    await expect(row.locator(".pill", { hasText: "Active" })).toBeVisible();
    await expect(signInHttp(member.email)).resolves.toContain("=");
  });

  test("removes someone after asking, and erases what the agent saved for them", async ({ page, context }) => {
    const member = await createMember("removed");
    const id = await userIdOf(member.cookie);
    await seedSession(id, { rows: PLAN_ROWS });
    await signInBrowser(context, await ownerCookie());
    await page.goto("/admin");
    const row = page.getByRole("region", { name: "People" }).getByRole("row", { name: new RegExp(member.email) });

    await row.getByRole("button", { name: "Remove" }).click();
    const dialog = page.getByRole("dialog", { name: /Remove/ });
    await dialog.getByRole("button", { name: "Keep this account" }).click();
    await expect(row).toBeVisible();

    await row.getByRole("button", { name: "Remove" }).click();
    await dialog.getByRole("button", { name: "Remove account" }).click();
    await expect(row).toHaveCount(0);
    await expect(page.getByRole("status").filter({ hasText: "Account removed" })).toBeVisible();

    const client = new pg.Client({ connectionString: env.database });
    await client.connect();
    const left = await client.query("SELECT (SELECT count(*) FROM app.rows WHERE user_id = $1) AS rows", [id]);
    await client.end();
    expect(Number(left.rows[0].rows)).toBe(0);
  });

  test("offers no ban or removal for the administrator's own row", async ({ page, context }) => {
    await signInBrowser(context, await ownerCookie());
    await page.goto("/admin");
    const own = page.getByRole("region", { name: "People" }).getByRole("row", { name: /owner@example.com/ }).filter({ hasText: "Administrator" });
    await expect(own.getByText("You")).toBeVisible();
    await expect(own.getByRole("button")).toHaveCount(0);
  });
});
