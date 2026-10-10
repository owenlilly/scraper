import { defineConfig, devices } from "@playwright/test";

// Run through `npm run e2e:ui`, which starts the web app, the Python API and a fake model first and sets these addresses.
export default defineConfig({
  testDir: "./e2e/specs",
  // Outside the project: the web app runs in development mode, and files written inside it make it reload the page under the test.
  outputDir: process.env.E2E_RESULTS_DIR ?? "./test-results",
  workers: 1, // the specs share one database and one fake model
  fullyParallel: false,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: { baseURL: process.env.WEB_URL, trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
