import { describe, expect, it } from "vitest";
import { buildInviteLink, parseInviteHash } from "../src/lib/invite-link";

const TOKEN = "Zk3pQ9vXw8LmN2pRt7YbHc5JdFg1SaUeQ0oIx6TnVwA"; // what randomBytes(32).toString("base64url") looks like

describe("invitation links", () => {
  it("put the token and the address after the # so they never reach a server log or a Referer header", () => {
    const link = buildInviteLink("https://app.example.com", TOKEN, "bob+test@example.com");
    expect(link).toBe(`https://app.example.com/sign-up#invite=${TOKEN}&email=bob%2Btest%40example.com`);
    expect(new URL(link).search).toBe(""); // nothing in the part a server sees
  });

  it("round trip through the parser", () => {
    const { hash } = new URL(buildInviteLink("https://app.example.com", TOKEN, "bob+test@example.com"));
    expect(parseInviteHash(hash)).toEqual({ invite: TOKEN, email: "bob+test@example.com" });
  });

  it("parse with or without the leading #", () => {
    expect(parseInviteHash(`invite=${TOKEN}&email=a%40x.test`)).toEqual({ invite: TOKEN, email: "a@x.test" });
  });

  it.each([
    ["nothing at all", ""],
    ["only a #", "#"],
    ["no token", "#email=a%40x.test"],
    ["no address", `#invite=${TOKEN}`],
    ["an empty token", "#invite=&email=a%40x.test"],
    ["a token with odd characters", "#invite=%3Cscript%3E%20alert%281%29&email=a%40x.test"],
    ["a very long token", `#invite=${"a".repeat(500)}&email=a%40x.test`],
    ["an address without an @", `#invite=${TOKEN}&email=not-an-address`],
    ["a very long address", `#invite=${TOKEN}&email=${"a".repeat(300)}%40x.test`],
  ])("refuse %s", (_name, hash) => {
    expect(parseInviteHash(hash)).toBeNull();
  });

  it("ignore other keys and take only what it needs", () => {
    expect(parseInviteHash(`#utm=1&invite=${TOKEN}&email=a%40x.test&next=%2F`)).toEqual({ invite: TOKEN, email: "a@x.test" });
  });
});
