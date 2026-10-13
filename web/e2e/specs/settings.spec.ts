import { expect, test } from "@playwright/test";
import { createMember, ownerCookie, signInBrowser } from "../support/journeys";

// The stack has no worker, so a run stays queued: enough to check the screens, the API and the access rules end to end.
test.describe("spider settings", () => {
  test("are for administrators only", async ({ page, context }) => {
    const member = await createMember("nosettings");
    await signInBrowser(context, member.cookie);
    await page.goto("/spiders/realtor_ca/settings");
    await expect(page).toHaveURL(/\/$/);
    await context.clearCookies();
    await page.goto("/spiders/realtor_ca/settings");
    await expect(page).toHaveURL(/\/sign-in/);
  });

  test("a bad value shows its message under the table and saves nothing", async ({ page, context }) => {
    await signInBrowser(context, await ownerCookie());
    await page.goto("/spiders/realtor_ca/settings");
    await page.getByLabel("GeoName of location 1").fill("");
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "locations.0.geo_name" })).toBeVisible();
    await page.reload();
    await expect(page.getByLabel("GeoName of location 1")).toHaveValue("Toronto, ON");
  });

  test("adds a location, keeps it after a reload, and starts a run for it", async ({ page, context }) => {
    await signInBrowser(context, await ownerCookie());
    await page.goto("/spiders/realtor_ca/settings");
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Save settings" })).toBeDisabled(); // nothing changed yet
    await page.getByRole("button", { name: "Add location" }).click();
    await page.getByLabel("GeoId of location 2").fill("g2_e2e");
    await page.getByLabel("GeoName of location 2").fill("Ottawa, ON");
    await page.getByLabel("Pages per run").fill("2");
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Settings saved" })).toBeVisible();
    await page.reload();
    await expect(page.getByLabel("GeoName of location 2")).toHaveValue("Ottawa, ON");
    await expect(page.getByLabel("Pages per run")).toHaveValue("2");

    await page.goto("/spiders/realtor_ca");
    await page.getByLabel("Location", { exact: true }).selectOption({ label: "Ottawa, ON" });
    await page.getByRole("button", { name: "Trial run" }).click();
    await expect(page.getByText("Location: Ottawa, ON").first()).toBeVisible();
    await page.getByRole("button", { name: "Stop" }).click();
    await expect(page.getByText("Cancelled").first()).toBeVisible();
  });

  test("chooses which fields the results show, and in what order", async ({ page, context }) => {
    await signInBrowser(context, await ownerCookie());
    await page.goto("/spiders/realtor_ca/settings");
    await page.getByRole("checkbox", { name: "mls_no" }).uncheck();
    await page.getByRole("button", { name: "Move price up" }).click();
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Settings saved" })).toBeVisible();
    await page.reload();
    const rows = page.locator(".sp-rows li");
    await expect(rows.nth(1)).toContainText("price");
    await expect(rows.last()).toContainText("mls_no");
    await expect(rows.last().getByRole("checkbox")).not.toBeChecked();
  });
});
