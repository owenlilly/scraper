import { expect, test } from "@playwright/test";
import pg from "pg";
import { env } from "../support/env";
import { createMember, ownerCookie, signInBrowser } from "../support/journeys";

/** Put raw values in the database, as a hand edit or an older version of the settings would have left them. */
async function store(settings: object) {
  const client = new pg.Client({ connectionString: env.database });
  await client.connect();
  await client.query("INSERT INTO pipeline.source (name, settings) VALUES ('realtor_ca', $1) ON CONFLICT (name) DO UPDATE SET settings = EXCLUDED.settings", [JSON.stringify(settings)]);
  await client.end();
}

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
    await expect(page.getByRole("alert").filter({ hasText: "Location 1, GeoName" })).toBeVisible();
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

  // These two go last: they leave the realtor spider's saved settings as a test of their own.
  test("a stored key the model no longer knows can be saved away", async ({ page, context }) => {
    await store({ colour: 1 });
    await signInBrowser(context, await ownerCookie());
    await page.goto("/spiders/realtor_ca/settings");
    await expect(page.getByRole("alert").filter({ hasText: "The saved settings are not valid" })).toBeVisible();
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Settings saved" })).toBeVisible();
    await page.reload();
    await expect(page.getByText("The saved settings are not valid")).toHaveCount(0);
  });

  test("a stored list of the wrong type shows an empty table instead of breaking, and can be replaced", async ({ page, context }) => {
    await store({ locations: "x" });
    await signInBrowser(context, await ownerCookie());
    await page.goto("/spiders/realtor_ca/settings");
    await expect(page.getByRole("alert").filter({ hasText: "The saved settings are not valid" })).toBeVisible();
    await expect(page.getByText("No locations yet")).toBeVisible();
    await page.getByRole("button", { name: "Add location" }).click();
    await page.getByLabel("GeoId of location 1").fill("g9_e2e");
    await page.getByLabel("GeoName of location 1").fill("Nowhere, ON");
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Settings saved" })).toBeVisible();
    await page.reload();
    await expect(page.getByLabel("GeoName of location 1")).toHaveValue("Nowhere, ON");
  });
});
