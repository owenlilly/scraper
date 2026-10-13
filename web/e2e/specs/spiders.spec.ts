import { expect, test } from "@playwright/test";
import { createMember, ownerCookie, signInBrowser } from "../support/journeys";

// The stack has no worker, so a run stays queued: enough to check the page, the API and the access rules end to end.
test.describe("the spiders page", () => {
  test("is for administrators only", async ({ page, context }) => {
    const member = await createMember("spiderless");
    await signInBrowser(context, member.cookie);
    for (const path of ["/spiders", "/spiders/books_toscrape"]) {
      await page.goto(path);
      await expect(page).toHaveURL(/\/$/);
    }
    await context.clearCookies();
    await page.goto("/spiders");
    await expect(page).toHaveURL(/\/sign-in/);
  });

  test("lists the spiders, queues a trial run from the list and stops it", async ({ page, context }) => {
    await signInBrowser(context, await ownerCookie());
    await page.goto("/spiders");
    await expect(page.getByRole("heading", { name: "Spiders" })).toBeVisible();
    const row = page.getByRole("row", { name: /books_toscrape/ });
    await row.getByRole("button", { name: "Trial run" }).click();
    await expect(row.getByText("Queued")).toBeVisible();
    await expect(row.getByText("Trial")).toBeVisible();
    await row.getByRole("button", { name: "Stop" }).click();
    await expect(row.getByText("Cancelled")).toBeVisible();
    await expect(row.getByRole("button", { name: "Run now" })).toBeVisible();
  });

  test("opens a spider, queues a run, stops it there and starts a trial", async ({ page, context }) => {
    await signInBrowser(context, await ownerCookie());
    await page.goto("/spiders");
    await page.getByRole("link", { name: "books_toscrape" }).click();
    await expect(page).toHaveURL(/\/spiders\/books_toscrape$/);

    await page.getByRole("button", { name: "Run now" }).click();
    await expect(page.getByRole("heading", { name: /Run #\d+/ })).toBeVisible();
    await expect(page.getByText("Queued").first()).toBeVisible();
    await expect(page.getByText("A run is already in flight")).toBeVisible();
    await expect(page.getByRole("button", { name: "Run now" })).toBeDisabled();

    await page.getByRole("button", { name: "Stop" }).click();
    await expect(page.getByText("Cancelled").first()).toBeVisible();

    await page.getByRole("button", { name: "Trial run" }).click();
    await expect(page.getByText("Trial", { exact: true }).first()).toBeVisible();
    await page.getByRole("button", { name: "Stop" }).click(); // leave the source idle for whatever runs next
    await expect(page.getByRole("button", { name: "Run now" })).toBeEnabled();
  });
});
