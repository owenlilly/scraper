import { describe, expect, it, vi } from "vitest";
import { createTokenCache } from "../../src/lib/api/token";

const jwt = (exp: number | undefined) => `h.${Buffer.from(JSON.stringify(exp === undefined ? { sub: "u" } : { sub: "u", exp })).toString("base64url")}.s`;
const NOW = 1_800_000_000_000; // ms

describe("createTokenCache", () => {
  it("fetches a token once and reuses it until a minute before it expires", async () => {
    let now = NOW;
    const fetchToken = vi.fn(async () => jwt(NOW / 1000 + 900)); // good for 15 minutes
    const cache = createTokenCache({ fetchToken, now: () => now });
    const first = await cache.get();
    now += 13 * 60_000; // 13 minutes later: still more than a minute left
    expect(await cache.get()).toBe(first);
    expect(fetchToken).toHaveBeenCalledTimes(1);
  });

  it("fetches a new one once less than a minute is left", async () => {
    let now = NOW;
    const fetchToken = vi.fn(async () => jwt(now / 1000 + 900));
    const cache = createTokenCache({ fetchToken, now: () => now });
    await cache.get();
    now += 14.5 * 60_000; // 30 seconds left
    await cache.get();
    expect(fetchToken).toHaveBeenCalledTimes(2);
  });

  it("makes one request when several callers ask at the same moment", async () => {
    const fetchToken = vi.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
      return jwt(NOW / 1000 + 900);
    });
    const cache = createTokenCache({ fetchToken, now: () => NOW });
    const tokens = await Promise.all([cache.get(), cache.get(), cache.get()]);
    expect(new Set(tokens).size).toBe(1);
    expect(fetchToken).toHaveBeenCalledTimes(1);
  });

  it("forgets the token when told it was refused", async () => {
    const fetchToken = vi.fn(async () => jwt(NOW / 1000 + 900));
    const cache = createTokenCache({ fetchToken, now: () => NOW });
    await cache.get();
    cache.invalidate();
    await cache.get();
    expect(fetchToken).toHaveBeenCalledTimes(2);
  });

  it("passes a failure on and tries again next time, remembering nothing", async () => {
    const fetchToken = vi.fn().mockRejectedValueOnce(new Error("signed out")).mockResolvedValueOnce(jwt(NOW / 1000 + 900));
    const cache = createTokenCache({ fetchToken, now: () => NOW });
    await expect(cache.get()).rejects.toThrow("signed out");
    expect(await cache.get()).toBeTruthy();
  });

  it.each([["has no expiry", jwt(undefined)], ["is not a token at all", "garbage"], ["has a payload that is not JSON", "a.!!!.c"]])("does not keep a token that %s", async (_name, token) => {
    const fetchToken = vi.fn(async () => token);
    const cache = createTokenCache({ fetchToken, now: () => NOW });
    await cache.get();
    await cache.get();
    expect(fetchToken).toHaveBeenCalledTimes(2);
  });
});
