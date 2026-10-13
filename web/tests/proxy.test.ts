import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import { config, hasSessionCookie, proxy } from "../src/proxy";

const request = (path: string, cookie?: string) => new NextRequest(`http://localhost:3100${path}`, { headers: cookie ? { cookie } : {} });

describe("hasSessionCookie", () => {
  it.each([
    ["better-auth.session_token=abc", true],
    ["__Secure-better-auth.session_token=abc", true], // the name in production
    ["theme=dark; better-auth.session_token=abc", true],
    ["better-auth.session_data=abc", false], // only a cache of the session
    ["better-auth.session_token=", false],
    ["other=1", false],
    ["", false],
  ])("%j -> %s", (cookie, expected) => {
    const names = new Request("http://x", { headers: cookie ? { cookie } : {} });
    expect(hasSessionCookie(names.headers.get("cookie") ?? "")).toBe(expected);
  });
});

describe("proxy", () => {
  it("sends a visitor with no session cookie from the workspace and the admin page to sign in", () => {
    for (const path of ["/", "/admin", "/spiders", "/spiders/books_toscrape"]) {
      const response = proxy(request(path));
      expect(response.status, path).toBe(307);
      expect(new URL(response.headers.get("location")!).pathname).toBe("/sign-in");
    }
  });

  it("lets a visitor with a session cookie through (the page itself checks the session for real)", () => {
    const response = proxy(request("/", "better-auth.session_token=abc"));
    expect(response.headers.get("location")).toBeNull();
    expect(response.status).toBe(200);
  });

  it("only guards the pages that need a session", () => {
    expect(config.matcher).toEqual(["/", "/admin/:path*", "/spiders/:path*"]);
  });
});
