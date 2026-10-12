import { describe, expect, it } from "vitest";
import { formatDay, inviteStatusLabel, personStatus, roleLabel } from "../src/lib/admin-view";

describe("inviteStatusLabel", () => {
  it("uses the words people use for each state", () => {
    expect(inviteStatusLabel("pending")).toBe("Waiting");
    expect(inviteStatusLabel("used")).toBe("Joined");
    expect(inviteStatusLabel("revoked")).toBe("Revoked");
    expect(inviteStatusLabel("expired")).toBe("Expired");
  });
});

describe("roleLabel", () => {
  it("calls an administrator an administrator and everyone else a member", () => {
    expect(roleLabel("admin")).toBe("Administrator");
    expect(roleLabel("user")).toBe("Member");
    expect(roleLabel(null)).toBe("Member");
    expect(roleLabel(undefined)).toBe("Member");
  });
});

describe("personStatus", () => {
  it("is Banned only for a banned person", () => {
    expect(personStatus({ banned: true })).toBe("Banned");
    expect(personStatus({ banned: false })).toBe("Active");
    expect(personStatus({ banned: null })).toBe("Active");
  });
});

describe("formatDay", () => {
  it("writes the same short date on the server and in the browser, whatever the time zone", () => {
    expect(formatDay("2026-10-10T23:30:00.000Z")).toBe("Oct 10, 2026");
    expect(formatDay("2026-01-02T00:00:00.000Z")).toBe("Jan 2, 2026");
  });
  it("is empty for something that is not a date", () => {
    expect(formatDay("nonsense")).toBe("");
  });
});
