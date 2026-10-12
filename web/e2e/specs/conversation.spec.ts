import { expect, test, type Page } from "@playwright/test";
import { env } from "../support/env";
import { fakeModelClient } from "../support/fake-model";
import { createMember, signInBrowser, type Member } from "../support/journeys";
import { PLAN_PAGES, PLAN_ROWS, seedSession, seedUsage, userIdOf } from "../support/seed";

const model = fakeModelClient(env.model);
test.beforeEach(() => model.reset());

async function signedIn(page: Page, prefix: string, seed?: Parameters<typeof seedSession>[1]): Promise<Member & { id: string }> {
  const member = await createMember(prefix);
  const id = await userIdOf(member.cookie);
  if (seed) await seedSession(id, seed);
  await signInBrowser(page.context(), member.cookie);
  await page.goto("/");
  await expect(page.getByLabel("Message")).toBeVisible();
  return { ...member, id };
}

const say = async (page: Page, text: string) => {
  await page.getByLabel("Message").fill(text);
  await page.getByLabel("Message").press("Enter");
};

const ADD_TWO = { name: "add_rows", args: { rows: [{ plan: "Solo", price: "$9" }, { plan: "Team", price: "$49" }] } };

test.describe("a run of the agent", () => {
  test("streams into the conversation, fills the results table and ends with a formatted answer", async ({ page }) => {
    await model.script([{ toolCalls: [ADD_TWO] }, { text: "I added two plans.\n\n**Solo** and **Team** are in the table.\n\n- Solo is $9\n- Team is $49" }]);
    await signedIn(page, "collect");
    await say(page, "Collect the plans.");

    await expect(page.locator(".msg-user", { hasText: "Collect the plans." })).toBeVisible();
    await expect(page.getByText("I added two plans.")).toBeVisible();
    await expect(page.locator(".prose strong", { hasText: "Solo" })).toBeVisible();
    await expect(page.locator(".prose li")).toHaveCount(2);
    await expect(page.getByText("Worked for 1 step")).toBeVisible();
    await expect(page.getByText("2 rows added to the results table")).toBeVisible();

    const table = page.getByRole("table", { name: "Collected results" });
    await expect(table.getByRole("row")).toHaveCount(3);
    await expect(table.getByRole("cell", { name: "Team", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0);
    await expect(page.getByLabel("Message")).toHaveValue("");

    const [first] = await model.requests();
    expect(JSON.stringify(first.messages)).toContain("Collect the plans.");
    expect(first.tools).toContain("add_rows");
  });

  test("shows that it is working, with a stop button, until the model answers", async ({ page }) => {
    await model.script([{ hold: "answer", text: "Finished." }]);
    await signedIn(page, "working");
    await say(page, "Collect something.");
    const status = page.getByRole("status").filter({ hasText: "Step" });
    await expect(status.getByRole("button", { name: "Stop" })).toBeVisible();
    await expect(page.getByRole("button", { name: "New session" })).toHaveCount(0);
    await model.release("answer");
    await expect(page.getByText("Finished.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0);
  });

  test("stops on request, offers to resume, and carries on from where it was", async ({ page }) => {
    await model.script([{ hold: "first", toolCalls: [ADD_TWO] }, { text: "All done after the pause." }]);
    await signedIn(page, "stopper");
    await say(page, "Collect the plans.");
    await page.getByRole("button", { name: "Stop" }).click();
    await model.release("first");

    const stopped = page.getByRole("status").filter({ hasText: "This run was stopped." });
    await expect(stopped).toBeVisible();
    await expect(stopped.getByRole("button", { name: "Discard this run" })).toBeVisible();
    await stopped.getByRole("button", { name: "Resume" }).click();
    await expect(page.getByText("All done after the pause.")).toBeVisible();
    await expect(page.getByRole("table", { name: "Collected results" }).getByRole("row")).toHaveCount(3);
  });

  test("a note sent while it works waits in the conversation until the agent reads it", async ({ page }) => {
    await model.script([{ hold: "first", toolCalls: [ADD_TWO] }, { text: "Done, with your note in mind." }]);
    await signedIn(page, "steer");
    await say(page, "Collect the plans.");
    await expect(page.getByRole("button", { name: "Send note" })).toBeDisabled();
    await page.getByLabel("Message").fill("Only the plans, please.");
    await page.getByRole("button", { name: "Send note" }).click();

    const bubble = page.locator(".msg-user.queued", { hasText: "Only the plans, please." });
    await expect(bubble).toBeVisible();
    await expect(bubble.getByText("Waiting for the agent to read this")).toBeVisible();
    await expect(page.getByLabel("Message")).toHaveValue("");

    await model.release("first");
    await expect(page.getByText("Done, with your note in mind.")).toBeVisible();
    await expect(page.locator(".msg-user.queued")).toHaveCount(0);
    expect(JSON.stringify((await model.requests()).at(-1)!.messages)).toContain("Only the plans, please.");
  });

  test("pauses with a question, and the answer goes back to the agent", async ({ page }) => {
    await model.script([
      { toolCalls: [{ name: "ask_user", args: { question: "Which price should I collect?", options: ["Monthly", "Yearly"] } }] },
      { text: "Collected the monthly price." },
    ]);
    await signedIn(page, "asker");
    await say(page, "Collect the plans.");

    const question = page.getByRole("group", { name: "Which price should I collect?" });
    await expect(question).toBeVisible();
    await expect(page.getByRole("button", { name: "Stop" })).toHaveCount(0);
    await question.getByRole("button", { name: "Monthly" }).click();
    await expect(page.getByText("Collected the monthly price.")).toBeVisible();
    await expect(question).toHaveCount(0);
    expect(JSON.stringify((await model.requests()).at(-1)!.messages)).toContain("Monthly");
  });

  test("an answer typed in the person's own words works too", async ({ page }) => {
    await model.script([{ toolCalls: [{ name: "ask_user", args: { question: "Which price?", options: [] } }] }, { text: "Used your wording." }]);
    await signedIn(page, "words");
    await say(page, "Collect the plans.");
    const question = page.getByRole("group", { name: "Which price?" });
    await question.getByLabel("Your own answer").fill("Both, please");
    await question.getByRole("button", { name: "Send answer" }).click();
    await expect(page.getByText("Used your wording.")).toBeVisible();
    expect(JSON.stringify((await model.requests()).at(-1)!.messages)).toContain("Both, please");
  });

  test("a person at the daily limit is told so, and keeps what they typed", async ({ page }) => {
    const member = await signedIn(page, "spent");
    await seedUsage(member.id, 300);
    await say(page, "Collect the plans.");
    await expect(page.getByRole("alert").filter({ hasText: "Daily model-call limit reached" })).toBeVisible();
    await expect(page.getByLabel("Message")).toHaveValue("Collect the plans.");
    await expect(page.locator(".msg-user")).toHaveCount(0);
    expect(await model.requests()).toEqual([]);
  });
});

test.describe("the browser switch", () => {
  test("is on to begin with, is kept, and decides whether the agent is given the browser tools", async ({ page }) => {
    await model.script([{ text: "First." }, { text: "Second." }]);
    await signedIn(page, "formsswitch");
    const browser = page.getByLabel("Use the browser");
    await expect(browser).toBeChecked();
    await say(page, "Collect the plans.");
    await expect(page.getByText("First.")).toBeVisible();
    expect((await model.requests())[0].tools).toContain("browser_submit_form");

    await browser.uncheck();
    await page.reload();
    await expect(page.getByLabel("Use the browser")).not.toBeChecked();
    await say(page, "Collect them again.");
    await expect(page.getByText("Second.")).toBeVisible();
    expect((await model.requests())[1].tools).not.toContain("browser_submit_form");
  });

  test("cannot be changed while a run is working, and is not shown when asking about the pages", async ({ page }) => {
    await model.script([{ hold: "first", text: "Done." }]);
    await signedIn(page, "formsbusy");
    await say(page, "Collect the plans.");
    await expect(page.getByRole("button", { name: "Stop" })).toBeVisible();
    await expect(page.getByLabel("Use the browser")).toBeDisabled();
    await model.release("first");
    await expect(page.getByText("Done.")).toBeVisible();
    await page.getByRole("button", { name: "Ask about the pages" }).click();
    await expect(page.getByLabel("Use the browser")).toHaveCount(0);
  });
});

test.describe("choosing the model server", () => {
  test("the picker switches provider, says where page text goes, and the choice is kept", async ({ page }) => {
    await model.script([{ text: "Back on the local model." }]);
    await signedIn(page, "picker");
    const picker = page.getByLabel("Model server");
    await expect(picker).toHaveValue("lmstudio");
    await expect(page.getByText("Page text is sent to")).toHaveCount(0);

    await picker.selectOption("mistral");
    await expect(page.getByText("Page text is sent to Mistral.")).toBeVisible();
    await expect(page.getByRole("alert").filter({ hasText: /reach|unavailable|connect/i })).toBeVisible(); // its address goes nowhere in this stack
    await expect(page.getByRole("button", { name: "Send", exact: true })).toBeDisabled();

    await page.reload();
    await expect(page.getByLabel("Model server")).toHaveValue("mistral");

    await page.getByLabel("Model server").selectOption("lmstudio");
    await expect(page.getByText("Page text is sent to")).toHaveCount(0);
    await say(page, "Collect the plans.");
    await expect(page.getByText("Back on the local model.")).toBeVisible();
  });

  test("cannot be changed while a run is working", async ({ page }) => {
    await model.script([{ hold: "first", text: "Done." }]);
    await signedIn(page, "pickerbusy");
    await say(page, "Collect the plans.");
    await expect(page.getByRole("button", { name: "Stop" })).toBeVisible();
    await expect(page.getByLabel("Model server")).toBeDisabled();
    await model.release("first");
    await expect(page.getByText("Done.")).toBeVisible();
    await expect(page.getByLabel("Model server")).toBeEnabled();
  });
});

test.describe("asking about the pages", () => {
  test("can be sent before any page has been read, and is answered", async ({ page }) => {
    await model.script([{ text: "I can fetch pages, search the web and fill a table." }]);
    await signedIn(page, "asknopages");
    await page.getByRole("button", { name: "Ask about the pages" }).click();
    await page.getByLabel("Message").fill("What tools do you have?");
    await expect(page.getByRole("button", { name: "Send", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.getByText("I can fetch pages, search the web and fill a table.")).toBeVisible();
    const system = JSON.stringify((await model.requests())[0].messages[0]);
    expect(system).toContain("No pages have been read yet");
  });

  test("is held back while a run is working, says why, and can be sent once the run is stopped", async ({ page }) => {
    await model.script([{ hold: "first", text: "Done." }, { text: "Answer about the page." }]);
    await signedIn(page, "askbusy");
    await say(page, "Collect something.");
    await expect(page.getByRole("button", { name: "Stop" })).toBeVisible();
    await page.getByRole("button", { name: "Ask about the pages" }).click();
    await page.getByLabel("Message").fill("A question");
    await expect(page.getByRole("button", { name: "Send", exact: true })).toBeDisabled();
    await expect(page.getByLabel("Message")).toHaveAttribute("placeholder", /run is working/i);
    await expect(page.getByText("A run is working, so questions have to wait")).toBeVisible(); // said in plain sight, not only in the empty box
    await page.getByRole("button", { name: "Stop" }).click();
    await model.release("first");
    await expect(page.getByRole("button", { name: "Send", exact: true })).toBeEnabled();
  });
});

test.describe("a question about the pages", () => {
  test("streams reasoning and a formatted answer", async ({ page }) => {
    await model.script([{ reasoning: "Checking the Team page.", text: "The Team plan costs **$49** a month.\n\n| Plan | Price |\n| --- | --- |\n| Team | $49 |" }]);
    await signedIn(page, "ask", { pages: PLAN_PAGES, rows: PLAN_ROWS });
    await page.getByRole("button", { name: "Ask about the pages" }).click();
    await say(page, "What does Team cost?");
    await expect(page.locator(".msg-user", { hasText: "What does Team cost?" })).toBeVisible();
    await expect(page.locator(".prose strong", { hasText: "$49" })).toBeVisible();
    await expect(page.locator(".prose table")).toBeVisible();
    await page.getByText("Reasoning").click();
    await expect(page.getByText("Checking the Team page.")).toBeVisible();
  });

  test("shows only formatted text: no images, no scripts, no unsafe links", async ({ page }) => {
    await model.script([
      { text: "Hello ![tracker](http://evil.test/pixel.png) <img src=x onerror=alert(1)> [bad](javascript:alert(1)) [good](https://example.org/page).\n\n<script>window.hacked = true</script>" },
    ]);
    await signedIn(page, "unsafe", { pages: PLAN_PAGES });
    await page.getByRole("button", { name: "Ask about the pages" }).click();
    const requested: string[] = [];
    page.on("request", (request) => requested.push(request.url()));
    await say(page, "Say hello.");
    await expect(page.locator(".prose a", { hasText: "good" })).toHaveAttribute("href", "https://example.org/page");
    await expect(page.locator(".prose a", { hasText: "good" })).toHaveAttribute("rel", /noopener/);
    await expect(page.locator(".prose img")).toHaveCount(0);
    await expect(page.locator(".prose a[href^='javascript']")).toHaveCount(0);
    await expect(page.locator(".prose script")).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as { hacked?: boolean }).hacked)).toBeUndefined();
    expect(requested.some((url) => url.includes("evil.test"))).toBe(false);
  });
});

test.describe("a run that needs a decision", () => {
  test("a repeated call asks what to do, and the note and choice go back to the agent", async ({ page }) => {
    const same = { toolCalls: [{ name: "add_rows", args: { rows: [{ plan: "Solo", price: "$9" }] } }] };
    await model.script([same, same, same, { text: "Wrapped up with what I had." }]);
    await signedIn(page, "limited");
    await say(page, "Collect the plans.");

    const card = page.getByRole("group", { name: /same call/ });
    for (const label of ["Continue", "Wrap up and answer", "Stop"]) await expect(card.getByRole("button", { name: label })).toBeVisible();
    await card.getByLabel("A note for the agent (optional)").fill("Keep it short.");
    await card.getByRole("button", { name: "Wrap up and answer" }).click();
    await expect(page.getByText("Wrapped up with what I had.")).toBeVisible();
    await expect(card).toHaveCount(0);
    expect(JSON.stringify((await model.requests()).at(-1)!.messages)).toContain("Keep it short.");
  });

  test("a page on a new site asks for permission first, and a refusal is passed on", async ({ page }) => {
    await model.script([{ toolCalls: [{ name: "fetch_page", args: { url: "https://other.example/page" } }] }, { text: "I was not allowed to read other.example." }]);
    await signedIn(page, "site");
    await say(page, "Collect the plans from shop.example.");

    const card = page.getByRole("group", { name: "May I fetch pages from other.example?" });
    await expect(card.getByRole("button", { name: "Allow other.example" })).toBeVisible();
    await card.getByRole("button", { name: "Do not allow" }).click();
    await expect(page.getByText("I was not allowed to read other.example.")).toBeVisible();
    expect(await model.requests()).toHaveLength(2);
  });

  test("discarding a stopped run clears it, and puts back a note that was never read", async ({ page }) => {
    await model.script([{ hold: "first", toolCalls: [ADD_TWO] }]);
    await signedIn(page, "discard");
    await say(page, "Collect the plans.");
    await page.getByRole("button", { name: "Stop" }).click();
    await page.getByLabel("Message").fill("Also the yearly price.");
    await page.getByRole("button", { name: "Send note" }).click();
    await expect(page.locator(".msg-user.queued", { hasText: "Also the yearly price." })).toBeVisible();
    await model.release("first");

    const stopped = page.getByRole("status").filter({ hasText: "This run was stopped." });
    await stopped.getByRole("button", { name: "Discard this run" }).click();
    await expect(stopped).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "What should it collect?" })).toBeVisible();
    await expect(page.getByLabel("Message")).toHaveValue("Also the yearly price.");
  });

  test("a second tab sees the run working, and can send it a note", async ({ page, context }) => {
    await model.script([{ hold: "first", toolCalls: [ADD_TWO] }, { text: "Done, noted." }]);
    await signedIn(page, "twotabs");
    await say(page, "Collect the plans.");
    await expect(page.getByRole("button", { name: "Stop" })).toBeVisible();

    const other = await context.newPage();
    await other.goto("/");
    await expect(other.getByRole("status").filter({ hasText: "Finishing up" })).toBeVisible();
    await other.getByLabel("Message").fill("Also the yearly price.");
    await other.getByRole("button", { name: "Send note" }).click();
    await expect(other.locator(".msg-user.queued", { hasText: "Also the yearly price." })).toBeVisible();

    await model.release("first");
    await expect(page.getByText("Done, noted.")).toBeVisible();
    await other.reload();
    await expect(other.getByText("Done, noted.")).toBeVisible();
    expect(JSON.stringify((await model.requests()).at(-1)!.messages)).toContain("Also the yearly price.");
  });

  test("closing the page in the middle of a run leaves a run that can be resumed", async ({ page }) => {
    await model.script([{ hold: "first", toolCalls: [ADD_TWO] }, { text: "Finished after coming back." }]);
    await signedIn(page, "closer");
    await say(page, "Collect the plans.");
    await expect(page.getByRole("button", { name: "Stop" })).toBeVisible();
    await page.reload();
    await model.release("first");
    const card = page.getByRole("status").filter({ hasText: "This run was stopped." });
    await expect(card.getByRole("button", { name: "Resume" })).toBeVisible();
    await card.getByRole("button", { name: "Resume" }).click();
    await expect(page.getByText("Finished after coming back.")).toBeVisible();
  });

  test("a refused Resume says why and keeps the Resume button", async ({ page }) => {
    await model.script([{ hold: "first", toolCalls: [ADD_TWO] }]);
    const member = await signedIn(page, "refused");
    await say(page, "Collect the plans.");
    await page.getByRole("button", { name: "Stop" }).click();
    await model.release("first");
    const card = page.getByRole("status").filter({ hasText: "This run was stopped." });
    await expect(card).toBeVisible();
    await seedUsage(member.id, 300);
    await card.getByRole("button", { name: "Resume" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Daily model-call limit reached" })).toBeVisible();
    await expect(card.getByRole("button", { name: "Resume" })).toBeVisible();
    await expect(card.getByRole("button", { name: "Discard this run" })).toBeVisible();
  });

  test("an answer the server refuses leaves the question to answer again", async ({ page }) => {
    await model.script([{ toolCalls: [{ name: "ask_user", args: { question: "Which price?", options: ["Monthly"] } }] }]);
    const member = await signedIn(page, "refusedanswer");
    await say(page, "Collect the plans.");
    const question = page.getByRole("group", { name: "Which price?" });
    await expect(question).toBeVisible();
    await seedUsage(member.id, 300);
    await question.getByRole("button", { name: "Monthly" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Daily model-call limit reached" })).toBeVisible();
    await expect(question.getByRole("button", { name: "Monthly" })).toBeVisible();
  });

  test("Ask again shows why when the server refuses it", async ({ page }) => {
    const member = await createMember("retry");
    const id = await userIdOf(member.cookie);
    await seedSession(id, { pages: PLAN_PAGES, history: [{ role: "user", content: "What does Team cost?" }, { role: "assistant", content: "The Team plan co", interrupted: true }] });
    await seedUsage(id, 300);
    await signInBrowser(page.context(), member.cookie);
    await page.goto("/");
    await page.getByRole("button", { name: "Ask again" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Daily model-call limit reached" })).toBeVisible();
  });

  test("an API that rejects the token shows an error with a way out, not a redirect loop", async ({ page, context }) => {
    const member = await createMember("rejected");
    await signInBrowser(context, member.cookie);
    await page.route(`${env.api}/state`, (route) => route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ error: { code: "unauthorized", message: "The server did not accept your sign-in." } }) }));
    await page.goto("/");
    await expect(page.getByRole("alert").filter({ hasText: "The server did not accept your sign-in." })).toBeVisible();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  });

  test("a run stopped before the page was opened is still there after a reload", async ({ page }) => {
    await model.script([{ hold: "first", toolCalls: [ADD_TWO] }]);
    await signedIn(page, "reload");
    await say(page, "Collect the plans.");
    await page.getByRole("button", { name: "Stop" }).click();
    await model.release("first");
    await expect(page.getByRole("status").filter({ hasText: "This run was stopped." })).toBeVisible();
    await page.reload();
    await expect(page.locator(".msg-user", { hasText: "Collect the plans." })).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "This run was stopped." }).getByRole("button", { name: "Resume" })).toBeVisible();
  });
});
