import { describe, expect, it } from "vitest";
import { Prose } from "../../src/components/workspace/Prose";

describe("Prose", () => {
  it("is memoized, so an answer already on screen is not parsed again each time a new piece of text streams in", () => {
    expect((Prose as unknown as { $$typeof: symbol }).$$typeof).toBe(Symbol.for("react.memo"));
  });
});
