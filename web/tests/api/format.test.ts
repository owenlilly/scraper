import { describe, expect, it } from "vitest";
import { describeCall, hostAndPath, shorten } from "../../src/lib/api/format";

describe("describeCall", () => {
  it.each([
    ["fetch_page", { url: "https://shop.example/plans" }, "Fetching shop.example/plans"],
    ["fetch_page", { url: "https://shop.example/plans", mode: "stealthy" }, "Fetching shop.example/plans in stealthy mode"],
    ["list_links", { url: "https://shop.example/" }, "Listing the links on shop.example"],
    ["read_page", { url: "https://shop.example/plans/team" }, "Reading shop.example/plans/team"],
    ["extract", { url: "https://shop.example/plans", selector: ".price" }, "Pulling .price from shop.example/plans"],
    ["add_rows", { rows: [{}, {}, {}] }, "Adding 3 rows to the results"],
    ["add_rows", { rows: [{}] }, "Adding 1 row to the results"],
    ["set_fetch_delay", { seconds: 5, reason: "the site is slow" }, "Waiting 5 seconds between pages: the site is slow"],
    ["ask_user", { question: "Which?" }, "Asking you a question"],
    ["something_new", {}, "something_new"],
  ])("%s", (name, args, expected) => {
    expect(describeCall(name, args)).toBe(expected);
  });

  it("copes with arguments that are missing or odd", () => {
    expect(describeCall("fetch_page", {})).toBe("Fetching a page");
    expect(describeCall("add_rows", { rows: "nope" })).toBe("Adding 0 rows to the results");
    expect(describeCall("set_fetch_delay", { seconds: 1 })).toBe("Waiting 1 second between pages");
  });
});

describe("hostAndPath", () => {
  it("drops the scheme, www and a trailing slash", () => {
    expect(hostAndPath("https://www.shop.example/plans/")).toBe("shop.example/plans");
    expect(hostAndPath("http://shop.example/")).toBe("shop.example");
    expect(hostAndPath("https://shop.example/a?b=1#c")).toBe("shop.example/a?b=1");
  });
  it("leaves something that is not an address alone", () => {
    expect(hostAndPath("not a url")).toBe("not a url");
  });
});

describe("shorten", () => {
  it("leaves short text alone and cuts long text with a note", () => {
    expect(shorten("short", 10)).toBe("short");
    expect(shorten("x".repeat(30), 10)).toBe("xxxxxxxxxx\n[shortened]");
  });
});
