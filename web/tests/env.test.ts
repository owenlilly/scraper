import { describe, expect, it } from "vitest";
import { loadEnv } from "../src/lib/env";

const GOOD = {
  DATABASE_URL: "postgresql://web@db/scraper",
  BETTER_AUTH_SECRET: "s".repeat(32),
  BETTER_AUTH_URL: "https://app.example.com/",
  API_URL: "https://api.example.com",
  SERVICE_SECRET: "x".repeat(24),
  EMAIL_TRANSPORT: "log",
};

describe("loadEnv", () => {
  it("reads a good environment and tidies addresses", () => {
    const env = loadEnv({ ...GOOD, ADMIN_EMAIL: " Owner@Example.com ", TRUSTED_ORIGINS: "https://a.test, https://b.test," });
    expect(env.baseUrl).toBe("https://app.example.com");
    expect(env.apiAudience).toBe("https://api.example.com"); // defaults to the API's address
    expect(env.adminEmail).toBe("owner@example.com");
    expect(env.trustedOrigins).toEqual(["https://a.test", "https://b.test"]);
    expect(env.inviteTtlDays).toBe(7);
  });

  it("lets the audience be set separately", () => {
    expect(loadEnv({ ...GOOD, API_AUDIENCE: "page-chat-api" }).apiAudience).toBe("page-chat-api");
  });

  it.each(["DATABASE_URL", "BETTER_AUTH_SECRET", "BETTER_AUTH_URL", "API_URL", "SERVICE_SECRET"])("names %s when it is missing", (name) => {
    const bad: Record<string, string | undefined> = { ...GOOD, [name]: undefined };
    expect(() => loadEnv(bad)).toThrow(new RegExp(name));
  });

  it("reports every problem at once and never echoes a value", () => {
    const attempt = () => loadEnv({ ...GOOD, BETTER_AUTH_SECRET: "tooshort-SECRET-value", SERVICE_SECRET: "short-SECRET", INVITE_TTL_DAYS: "99" });
    expect(attempt).toThrow(/BETTER_AUTH_SECRET[\s\S]*SERVICE_SECRET[\s\S]*INVITE_TTL_DAYS/);
    try {
      attempt();
    } catch (error) {
      expect(String(error)).not.toContain("tooshort-SECRET-value");
      expect(String(error)).not.toContain("short-SECRET");
    }
  });

  it("insists on https and a real email transport in production", () => {
    expect(() => loadEnv({ ...GOOD, NODE_ENV: "production", BETTER_AUTH_URL: "http://app.example.com" })).toThrow(/https/);
    expect(() => loadEnv({ ...GOOD, NODE_ENV: "production" })).toThrow(/EMAIL_TRANSPORT=log/);
  });

  it("needs a key and a sender address for Resend, and picks Resend when a key is given", () => {
    expect(() => loadEnv({ ...GOOD, EMAIL_TRANSPORT: "resend" })).toThrow(/RESEND_API_KEY/);
    expect(() => loadEnv({ ...GOOD, EMAIL_TRANSPORT: "resend", RESEND_API_KEY: "re_x" })).toThrow(/EMAIL_FROM/);
    const withKey = { ...GOOD, EMAIL_TRANSPORT: undefined, RESEND_API_KEY: "re_x", EMAIL_FROM: "Page Chat <no-reply@example.com>" };
    expect(loadEnv(withKey).emailTransport).toBe("resend");
  });

  it("reads the proxies in front of the app, and defaults to none", () => {
    expect(loadEnv(GOOD).trustedProxies).toEqual([]);
    expect(loadEnv({ ...GOOD, TRUSTED_PROXIES: "10.0.0.0/8, 192.0.2.7 ,2001:db8::/32," }).trustedProxies).toEqual(["10.0.0.0/8", "192.0.2.7", "2001:db8::/32"]);
  });

  it.each(["not-an-address", "10.0.0.0/99", "10.0.0.256", "10.0.0.0/", "10.0.0.0/8/8", "https://proxy.example"])("rejects the proxy entry %j", (entry) => {
    expect(() => loadEnv({ ...GOOD, TRUSTED_PROXIES: entry })).toThrow(/TRUSTED_PROXIES/);
  });

  it("rejects a malformed admin email and a bad transport name", () => {
    expect(() => loadEnv({ ...GOOD, ADMIN_EMAIL: "not an email" })).toThrow(/ADMIN_EMAIL/);
    expect(() => loadEnv({ ...GOOD, EMAIL_TRANSPORT: "smoke-signals" })).toThrow(/EMAIL_TRANSPORT/);
  });
});
