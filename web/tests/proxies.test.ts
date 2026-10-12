import { afterAll, describe, expect, it } from "vitest";
import { makeHarness, type Harness } from "./harness";

// Rate limits are kept per client address. Better Auth reads that address from `x-forwarded-for`, and trusts the header only when
// it holds exactly one address, unless the proxies in front of the app are named. A proxy that adds its own address to the header
// would otherwise put every visitor in one shared bucket, and three sign-ins in ten seconds would then lock out everyone.
let h: Harness;
afterAll(() => h?.pool.end());

const wrong = (ip: string, forwarded = ip) => h.call("/sign-in/email", { body: { email: "nobody@example.com", password: "a wrong password here" }, headers: { "x-forwarded-for": forwarded } });
const hammer = async (forwarded: string) => {
  const statuses: number[] = [];
  for (let i = 0; i < 6; i++) statuses.push((await wrong(forwarded)).status);
  return statuses;
};

describe("rate limits are kept per client", () => {
  it("one person's failed sign-ins do not slow down another person when the header holds one address", async () => {
    h = await makeHarness({ production: true });
    expect(await hammer("198.51.100.1")).toContain(429);
    expect((await wrong("198.51.100.2")).status).toBe(401); // a different address starts with a fresh allowance
  });

  it("with the proxies named, each client behind them has their own allowance", async () => {
    await h.pool.end();
    h = await makeHarness({ production: true, trustedProxies: "10.0.0.0/8" });
    expect(await hammer("203.0.113.5, 10.0.0.2")).toContain(429);
    expect((await wrong("203.0.113.6", "203.0.113.6, 10.0.0.2")).status).toBe(401);
  });

  it("without them, a header with two addresses puts everyone in one shared bucket (why TRUSTED_PROXIES exists)", async () => {
    await h.pool.end();
    h = await makeHarness({ production: true });
    expect(await hammer("203.0.113.5, 10.0.0.2")).toContain(429);
    expect((await wrong("203.0.113.6", "203.0.113.6, 10.0.0.2")).status).toBe(429);
  });
});
