import { describe, expect, it } from "vitest";
import { MAX_PASSWORD, MIN_PASSWORD, passwordProblem } from "../src/lib/password";

describe("passwordProblem", () => {
  it("accepts a passphrase of the right length", () => {
    expect(passwordProblem("correct horse battery")).toBeNull();
    expect(passwordProblem("a".repeat(MIN_PASSWORD))).toBeNull();
    expect(passwordProblem("a".repeat(MAX_PASSWORD))).toBeNull();
  });

  it("needs 8 characters, no more", () => {
    expect(MIN_PASSWORD).toBe(8);
    expect(passwordProblem("abcdefgh")).toBeNull();
    expect(passwordProblem("abcdefg")).not.toBeNull();
  });

  it("says how many characters are needed when it is too short", () => {
    expect(passwordProblem("short")).toBe(`Use at least ${MIN_PASSWORD} characters. A few ordinary words work well.`);
    expect(passwordProblem("a".repeat(MIN_PASSWORD - 1))).not.toBeNull();
  });

  it("refuses an absurdly long one", () => {
    expect(passwordProblem("a".repeat(MAX_PASSWORD + 1))).toMatch(/at most/);
  });

  it("counts characters people see, not UTF-16 units", () => {
    expect(passwordProblem("🔑".repeat(MIN_PASSWORD))).toBeNull(); // each emoji is two units but one character
  });
});
