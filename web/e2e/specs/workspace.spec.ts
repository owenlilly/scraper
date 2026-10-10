import { expect, test } from "@playwright/test";
import pg from "pg";
import { env } from "../support/env";
import { createMember, ownerCookie, signInBrowser, signInHttp } from "../support/journeys";
import { PLAN_HISTORY, PLAN_PAGES, PLAN_ROWS, seedSession, userIdOf } from "../support/seed";

test.describe("the workspace", () => {
  test("a person with nothing yet is invited to start, and the results say what will appear", async ({ page, context }) => {
    const member = await createMember("empty");
    await signInBrowser(context, member.cookie);
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "What should it collect?" })).toBeVisible();
    await expect(page.getByText("Rows the agent collects appear here")).toBeVisible();
    await expect(page.getByRole("button", { name: "Download CSV" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "New session" })).toBeVisible();
  });

  test("shows the saved conversation, the results table and the pages read", async ({ page, context }) => {
    const member = await createMember("saved");
    await seedSession(await userIdOf(member.cookie), { pages: PLAN_PAGES, rows: PLAN_ROWS, history: PLAN_HISTORY });
    await signInBrowser(context, member.cookie);
    await page.goto("/");

    await expect(page.getByText("Go to shop.example/plans and collect every plan with its price.")).toBeVisible();
    await expect(page.getByText("I found three plans on three pages")).toBeVisible();
    await expect(page.getByText("3 rows added to the results table")).toBeVisible();
    await expect(page.getByText("Worked for 2 steps, read 1 page")).toBeVisible();

    const table = page.getByRole("table", { name: "Collected results" });
    await expect(table.getByRole("columnheader", { name: "plan" })).toBeVisible();
    await expect(table.getByRole("row")).toHaveCount(4);
    await expect(table.getByRole("cell", { name: "Studio", exact: true })).toBeVisible();
    await expect(table.getByRole("cell", { name: "$49", exact: true })).toHaveClass(/num/);
    await expect(page.getByText("3 rows", { exact: true })).toBeVisible();

    await expect(page.getByRole("heading", { name: "Pages read" })).toBeVisible();
    const pages = page.getByRole("region", { name: "Pages read" });
    await expect(pages.getByText("Plans and pricing")).toBeVisible();
    await expect(pages.getByText("shop.example/plans/team")).toBeVisible();
  });

  test("shows an answer that quotes a web page as formatted text only", async ({ page, context }) => {
    const member = await createMember("hostile");
    const hostile = "Heading text ![tracker](http://evil.test/pixel.png) <img src=x onerror=alert(1)> [bad](javascript:alert(1)) [good](https://example.org/page).\n\n<script>window.hacked = true</script>";
    await seedSession(await userIdOf(member.cookie), { history: [{ role: "user", content: "Say hello." }, { role: "assistant", content: hostile }] });
    await signInBrowser(context, member.cookie);
    const requested: string[] = [];
    page.on("request", (request) => requested.push(request.url()));
    await page.goto("/");
    await expect(page.locator(".prose a", { hasText: "good" })).toHaveAttribute("href", "https://example.org/page");
    await expect(page.locator(".prose a", { hasText: "good" })).toHaveAttribute("rel", /noopener/);
    await expect(page.locator(".prose img")).toHaveCount(0);
    await expect(page.locator(".prose a[href^='javascript']")).toHaveCount(0);
    await expect(page.locator(".prose script")).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as { hacked?: boolean }).hacked)).toBeUndefined();
    expect(requested.some((url) => url.includes("evil.test"))).toBe(false);
  });

  test("downloads the results as CSV and as JSON", async ({ page, context }) => {
    const member = await createMember("download");
    await seedSession(await userIdOf(member.cookie), { rows: PLAN_ROWS });
    await signInBrowser(context, member.cookie);
    await page.goto("/");

    const csv = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download CSV" }).click();
    const csvFile = await csv;
    expect(csvFile.suggestedFilename()).toBe("results.csv");
    const csvText = await (await import("node:fs/promises")).readFile((await csvFile.path())!, "utf8");
    expect(csvText.split("\n")[0]).toBe("plan,price,page");
    expect(csvText).toContain("Studio,$99,shop.example/plans/studio");

    const json = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download JSON" }).click();
    const jsonFile = await json;
    expect(jsonFile.suggestedFilename()).toBe("results.json");
    const parsed = JSON.parse(await (await import("node:fs/promises")).readFile((await jsonFile.path())!, "utf8"));
    expect(parsed).toHaveLength(3);
  });

  test("the account menu shows who is signed in and today's allowance, and offers invitations only to an administrator", async ({ page, context }) => {
    const member = await createMember("menu");
    await signInBrowser(context, member.cookie);
    await page.goto("/");
    await page.getByRole("button", { name: "Account menu" }).click();
    const allowance = page.getByRole("group", { name: "Today's allowance" });
    await expect(allowance.getByText("Pages read today")).toBeVisible();
    await expect(allowance.getByText("0 of 300").first()).toBeVisible();
    await expect(page.getByText(member.email)).toBeVisible();
    await expect(page.getByRole("link", { name: "Invitations and people" })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(allowance).toHaveCount(0);

    await context.clearCookies();
    await signInBrowser(context, await ownerCookie());
    await page.goto("/");
    await page.getByRole("button", { name: "Account menu" }).click();
    await expect(page.getByRole("link", { name: "Invitations and people" })).toBeVisible();
  });

  test("starts a new session only after asking, and then shows the empty workspace", async ({ page, context }) => {
    const member = await createMember("fresh");
    await seedSession(await userIdOf(member.cookie), { pages: PLAN_PAGES, rows: PLAN_ROWS, history: PLAN_HISTORY });
    await signInBrowser(context, member.cookie);
    await page.goto("/");
    await expect(page.getByRole("table", { name: "Collected results" })).toBeVisible();

    await page.getByRole("button", { name: "New session" }).click();
    const dialog = page.getByRole("dialog", { name: "Start a new session?" });
    await dialog.getByRole("button", { name: "Keep working" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByRole("table", { name: "Collected results" })).toBeVisible();

    await page.getByRole("button", { name: "New session" }).click();
    await dialog.getByRole("button", { name: "Clear everything" }).click();
    await expect(page.getByRole("heading", { name: "What should it collect?" })).toBeVisible();
    await expect(page.getByRole("table", { name: "Collected results" })).toHaveCount(0);
    await expect(page.getByText("Rows the agent collects appear here")).toBeVisible();
  });

  test("on a phone, chat and results are two tabs", async ({ page, context }) => {
    const member = await createMember("phone");
    await seedSession(await userIdOf(member.cookie), { rows: PLAN_ROWS, history: PLAN_HISTORY });
    await signInBrowser(context, member.cookie);
    await page.setViewportSize({ width: 390, height: 800 });
    await page.goto("/");
    await expect(page.getByText("I found three plans on three pages")).toBeVisible();
    await expect(page.getByRole("table", { name: "Collected results" })).toBeHidden();
    await page.getByRole("tab", { name: "Results (3)" }).click();
    await expect(page.getByRole("table", { name: "Collected results" })).toBeVisible();
    await expect(page.getByText("I found three plans on three pages")).toBeHidden();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });

  test("a signed-out visitor is sent to sign in", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/sign-in/);
  });
});

test.describe("deleting an account", () => {
  test("needs the password, then erases the account and what was saved for it, and says so at sign-in", async ({ page, context }) => {
    const member = await createMember("leaving");
    const id = await userIdOf(member.cookie);
    await seedSession(id, { rows: PLAN_ROWS, history: PLAN_HISTORY });
    await signInBrowser(context, member.cookie);
    await page.goto("/");
    await page.getByRole("button", { name: "Account menu" }).click();
    await page.getByRole("button", { name: "Delete my account" }).click();

    const dialog = page.getByRole("dialog", { name: "Delete your account?" });
    await expect(dialog.getByRole("button", { name: "Delete my account" })).toBeDisabled();
    await dialog.getByLabel("Password").fill("not the right password at all");
    await dialog.getByRole("button", { name: "Delete my account" }).click();
    await expect(dialog.getByRole("alert")).toHaveText("That password is not right.");
    await expect(page).toHaveURL(/\/$/);

    await dialog.getByLabel("Password").fill(member.password);
    await dialog.getByRole("button", { name: "Delete my account" }).click();
    await expect(page).toHaveURL(/\/sign-in\?deleted=1/);
    await expect(page.getByText("Your account was deleted.")).toBeVisible();
    await expect(signInHttp(member.email)).rejects.toThrow();

    const client = new pg.Client({ connectionString: env.database });
    await client.connect();
    const left = await client.query("SELECT (SELECT count(*) FROM app.rows WHERE user_id = $1) AS rows, (SELECT count(*) FROM app.history WHERE user_id = $1) AS history", [id]);
    await client.end();
    expect(Number(left.rows[0].rows) + Number(left.rows[0].history)).toBe(0);
  });

  test("keeping the account closes the dialog and forgets the typed password", async ({ page, context }) => {
    const member = await createMember("staying");
    await signInBrowser(context, member.cookie);
    await page.goto("/");
    await page.getByRole("button", { name: "Account menu" }).click();
    await page.getByRole("button", { name: "Delete my account" }).click();
    const dialog = page.getByRole("dialog", { name: "Delete your account?" });
    await dialog.getByLabel("Password").fill("something typed");
    await dialog.getByRole("button", { name: "Keep my account" }).click();
    await expect(dialog).toBeHidden();
    await page.getByRole("button", { name: "Account menu" }).click();
    await page.getByRole("button", { name: "Delete my account" }).click();
    await expect(dialog.getByLabel("Password")).toHaveValue("");
  });
});
