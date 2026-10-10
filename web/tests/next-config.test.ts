import { describe, expect, it } from "vitest";
import config from "../next.config";

describe("next.config headers", () => {
  it("tell the browser never to send the sign-up page's address as a Referer, and not to cache it", async () => {
    const rules = await config.headers!();
    const signUp = rules.find((rule) => rule.source === "/sign-up");
    expect(signUp, "a rule for /sign-up").toBeDefined();
    const headers = Object.fromEntries(signUp!.headers.map((h) => [h.key.toLowerCase(), h.value]));
    expect(headers["referrer-policy"]).toBe("no-referrer");
    expect(headers["cache-control"]).toBe("no-store");
  });
});
