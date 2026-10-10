import { expect, test } from "@playwright/test";
import { alertSaying, createMember, emailLink, inviteLink, logLength, PASSWORD, signInBrowser, uniqueEmail } from "../support/journeys";

test.describe("signing in", () => {
  test("a visitor who is not signed in is sent to the sign-in page", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/sign-in/);
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  });

  test("a wrong password gets a plain message and keeps the person on the page", async ({ page }) => {
    const member = await createMember("wrongpass");
    await page.goto("/sign-in");
    await page.getByLabel("Email address").fill(member.email);
    await page.getByLabel("Password").fill("not the right password");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(alertSaying(page, "The email address or password is not right. Check both and try again.")).toBeVisible();
    await expect(page).toHaveURL(/\/sign-in/);
  });

  test("the right password opens the workspace, and signing out comes back to sign in", async ({ page }) => {
    const member = await createMember("signin");
    await page.goto("/sign-in");
    await page.getByLabel("Email address").fill(member.email);
    await page.getByLabel("Password").fill(member.password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", { name: "What should it collect?" })).toBeVisible();
    await page.getByRole("button", { name: "Account menu" }).click();
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/sign-in/);
    await page.goto("/");
    await expect(page).toHaveURL(/\/sign-in/); // the session really ended
  });

  test("someone already signed in is taken past the sign-in page", async ({ page, context }) => {
    const member = await createMember("already");
    await signInBrowser(context, member.cookie);
    await page.goto("/sign-in");
    await expect(page).toHaveURL(/\/$/);
  });
});

test.describe("joining by invitation", () => {
  test("an invited person creates an account from the emailed link and ends up signed in", async ({ page }) => {
    const email = uniqueEmail("joiner");
    const link = await inviteLink(email);
    await page.goto(link);
    await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();
    await expect(page.getByLabel("Invited address")).toHaveValue(email);
    await expect(page.getByLabel("Invited address")).toHaveAttribute("readonly", "");
    expect(page.url()).not.toContain("invite="); // the token is out of the address bar
    await page.getByLabel("Your name").fill("Jo Joiner");
    await page.getByLabel("Password").fill("short");
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(alertSaying(page, "Use at least 12 characters")).toBeVisible();
    const since = logLength();
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page.getByRole("heading", { name: "Check your inbox" })).toBeVisible();
    await page.goto(await emailLink(email, "Confirm your email address", since));
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", { name: "What should it collect?" })).toBeVisible();
  });

  test("a sign-up page opened without an invitation says one is needed", async ({ page }) => {
    await page.goto("/sign-up");
    await expect(page.getByRole("heading", { name: "You need an invitation" })).toBeVisible();
    await page.goto("/sign-up#invite=not-a-real-token&email=someone%40example.com");
    await expect(page.getByRole("heading", { name: "You need an invitation" })).toBeVisible();
  });

  test("a link that has already been used is refused, with a way forward", async ({ page, browser }) => {
    const email = uniqueEmail("twice");
    const link = await inviteLink(email);
    for (const attempt of [0, 1]) {
      const context = attempt === 0 ? page.context() : await browser.newContext();
      const tab = attempt === 0 ? page : await context.newPage();
      await tab.goto(link);
      await tab.getByLabel("Your name").fill("Twice");
      await tab.getByLabel("Password").fill(PASSWORD);
      await tab.getByRole("button", { name: "Create account" }).click();
      if (attempt === 0) await expect(tab.getByRole("heading", { name: "Check your inbox" })).toBeVisible();
      else await expect(alertSaying(tab, "Ask for a new one")).toBeVisible();
    }
  });

  test("someone who has not confirmed their address is told to, and can ask for the link again", async ({ page }) => {
    const email = uniqueEmail("unconfirmed");
    await page.goto(await inviteLink(email));
    await page.getByLabel("Your name").fill("Unconfirmed");
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Create account" }).click();
    await expect(page.getByRole("heading", { name: "Check your inbox" })).toBeVisible();
    await page.goto("/sign-in");
    await page.getByLabel("Email address").fill(email);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(alertSaying(page, "Confirm your email address first")).toBeVisible();
    const since = logLength();
    await page.getByRole("button", { name: "Send the link again" }).click();
    await expect(page.getByText(`We sent a new confirmation link to ${email}.`)).toBeVisible();
    await emailLink(email, "Confirm your email address", since);
  });
});

test.describe("forgotten passwords", () => {
  test("the page answers the same for any address, and a reset link sets a new password", async ({ page }) => {
    const member = await createMember("forgetful");
    await page.goto("/forgot-password");
    await page.getByLabel("Email address").fill("nobody-at-all@example.com");
    await page.getByRole("button", { name: "Send the link" }).click();
    await expect(page.getByRole("heading", { name: "Check your inbox" })).toBeVisible();

    await page.goto("/forgot-password");
    const since = logLength();
    await page.getByLabel("Email address").fill(member.email);
    await page.getByRole("button", { name: "Send the link" }).click();
    await expect(page.getByRole("heading", { name: "Check your inbox" })).toBeVisible();

    await page.goto(await emailLink(member.email, "Reset your password", since));
    await expect(page.getByRole("heading", { name: "Choose a new password" })).toBeVisible();
    await page.getByLabel("New password").fill("a brand new passphrase");
    await page.getByRole("button", { name: "Save new password" }).click();
    await expect(page).toHaveURL(/\/sign-in\?reset=1/);
    await expect(page.getByText("Your password was changed. Sign in with the new one.")).toBeVisible();

    await page.getByLabel("Email address").fill(member.email);
    await page.getByLabel("Password").fill("a brand new passphrase");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("heading", { name: "What should it collect?" })).toBeVisible();
  });

  test("a reset page without a valid link offers to send a new one", async ({ page }) => {
    await page.goto("/reset-password");
    await expect(page.getByRole("heading", { name: "This link has expired" })).toBeVisible();
    await page.getByRole("link", { name: "Send a new link" }).click();
    await expect(page).toHaveURL(/\/forgot-password/);
  });

  test("a confirmation link that has expired offers a new one", async ({ page }) => {
    await page.goto("/verify-email?error=invalid_token");
    await expect(page.getByRole("heading", { name: "This link has expired" })).toBeVisible();
    await page.getByLabel("Email address").fill("someone@example.com");
    await page.getByRole("button", { name: "Send a new link" }).click();
    await expect(page.getByText("If someone@example.com has an account waiting to be confirmed")).toBeVisible();
  });
});
