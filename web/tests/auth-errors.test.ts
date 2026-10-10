import { describe, expect, it } from "vitest";
import { describeAuthError } from "../src/lib/auth-errors";

describe("describeAuthError", () => {
  it("explains a wrong email or password without saying which one is wrong", () => {
    expect(describeAuthError({ code: "INVALID_EMAIL_OR_PASSWORD", status: 401 }, "sign-in")).toMatch(/email address or password is not right/i);
  });

  it("tells someone whose address is unconfirmed what to do", () => {
    const text = describeAuthError({ code: "EMAIL_NOT_VERIFIED", status: 403 }, "sign-in");
    expect(text).toMatch(/confirm your email address/i);
  });

  it("says a suspended account is suspended", () => {
    expect(describeAuthError({ code: "BANNED_USER", status: 403 }, "sign-in")).toMatch(/suspended/i);
  });

  it("explains an invitation that does not work, and what to do next", () => {
    const text = describeAuthError({ code: "INVITE_REQUIRED", status: 403 }, "sign-up");
    expect(text).toMatch(/invitation/i);
    expect(text).toMatch(/new one/i);
  });

  it("points an existing account to sign in", () => {
    expect(describeAuthError({ code: "USER_ALREADY_EXISTS", status: 422 }, "sign-up")).toMatch(/sign in/i);
    expect(describeAuthError({ code: "USER_ALREADY_EXISTS_USE_ANOTHER_EMAIL", status: 422 }, "sign-up")).toMatch(/sign in/i);
  });

  it("says when to try again after too many attempts", () => {
    expect(describeAuthError({ status: 429 }, "sign-in")).toMatch(/too many attempts/i);
  });

  it("explains a reset link that no longer works", () => {
    expect(describeAuthError({ code: "INVALID_TOKEN", status: 400 }, "reset")).toMatch(/link/i);
  });

  it("explains a refused deletion", () => {
    expect(describeAuthError({ code: "ACCOUNT_DATA_NOT_DELETED", status: 409, message: "A run is still stopping. Try again in a few seconds." }, "delete")).toMatch(/try again/i);
    expect(describeAuthError({ code: "PASSWORD_REQUIRED", status: 400 }, "delete")).toMatch(/password/i);
  });

  it("never shows a raw code or an unknown server message", () => {
    const text = describeAuthError({ code: "SOMETHING_NEW", status: 500, message: "ECONNREFUSED db.internal:5432" }, "sign-in");
    expect(text).not.toMatch(/SOMETHING_NEW|ECONNREFUSED|5432/);
    expect(text).toMatch(/something went wrong/i);
  });

  it("copes with no error details at all", () => {
    expect(describeAuthError(null, "sign-in")).toMatch(/something went wrong/i);
    expect(describeAuthError(undefined, "sign-up")).toMatch(/something went wrong/i);
  });
});
