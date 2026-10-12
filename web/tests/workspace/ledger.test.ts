import { describe, expect, it } from "vitest";
import { cellText, ledgerColumns, looksNumeric, meterShare, resetNote } from "../../src/lib/workspace/ledger";
import { usage } from "./fixtures";

describe("ledgerColumns", () => {
  it("is every key the rows use, in the order they first appear", () => {
    expect(ledgerColumns([{ plan: "Solo", price: "$9" }, { plan: "Team", seats: 5, price: "$49" }])).toEqual(["plan", "price", "seats"]);
  });
  it("is empty when there are no rows", () => {
    expect(ledgerColumns([])).toEqual([]);
  });
});

describe("cellText", () => {
  it("shows text and numbers as they are, and nothing for a missing value", () => {
    expect(cellText("Solo")).toBe("Solo");
    expect(cellText(9)).toBe("9");
    expect(cellText(true)).toBe("true");
    expect(cellText(null)).toBe("");
    expect(cellText(undefined)).toBe("");
  });
  it("writes lists and objects out as JSON, so nothing renders as [object Object]", () => {
    expect(cellText({ a: 1 })).toBe('{"a":1}');
    expect(cellText(["x", "y"])).toBe('["x","y"]');
  });
});

describe("meterShare", () => {
  it("is the used part as a percentage, never more than 100 and never less than 0", () => {
    expect(meterShare(118, 300)).toBe(39);
    expect(meterShare(400, 300)).toBe(100);
    expect(meterShare(-3, 300)).toBe(0);
  });
  it("is zero when there is no limit to measure against", () => {
    expect(meterShare(5, 0)).toBe(0);
  });
});

describe("resetNote", () => {
  it("names the time the allowance starts again, in the viewer's own clock", () => {
    const text = resetNote(usage, "en-US", "UTC");
    expect(text).toMatch(/^Starts again /);
    expect(text).toContain("12:00");
  });
  it("is plain when the time cannot be read", () => {
    expect(resetNote({ ...usage, resets_at: "nonsense" }, "en-US", "UTC")).toBe("Starts again at midnight UTC.");
  });
});

describe("looksNumeric", () => {
  it("is true for numbers, prices and percentages, which line up on the right", () => {
    for (const text of ["9", "1,250", "12.5", "$49", "€1,299.00", "-3", "40%"]) expect(looksNumeric(text), text).toBe(true);
  });
  it("is false for words, dates, codes and empty cells", () => {
    for (const text of ["", "Solo", "2026-10-10", "A-100", "$49 a month", "1.2.3"]) expect(looksNumeric(text), text).toBe(false);
  });
});
