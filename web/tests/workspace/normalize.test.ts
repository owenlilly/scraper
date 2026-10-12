import { describe, expect, it } from "vitest";
import { normalizeState } from "../../src/lib/workspace/normalize";
import { makeState } from "./fixtures";

describe("normalizeState", () => {
  it("leaves a state from a current API as it is", () => {
    const state = makeState();
    expect(normalizeState(state)).toEqual(state);
  });

  it("offers just the current provider when an older API sends no list, instead of breaking the screen", () => {
    const { providers: _omitted, ...older } = makeState();
    const state = normalizeState(older as never);
    expect(state.providers).toEqual([state.provider]);
  });

  it("treats forms as unavailable when an older API does not say", () => {
    const { browser_available: _omitted, ...older } = makeState();
    expect(normalizeState(older as never).browser_available).toBe(false);
  });
});
