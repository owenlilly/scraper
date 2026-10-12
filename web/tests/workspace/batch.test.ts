import { describe, expect, it } from "vitest";
import { createBatcher } from "../../src/lib/workspace/batch";

function setup() {
  const flushed: number[][] = [];
  const timers: (() => void)[] = [];
  const batcher = createBatcher<number>((items) => flushed.push(items), (run) => timers.push(run));
  return { batcher, flushed, fire: () => timers.splice(0).forEach((run) => run()), timers };
}

describe("createBatcher", () => {
  it("hands over everything that arrived before the timer fired, in one go and in order", () => {
    const { batcher, flushed, fire } = setup();
    [1, 2, 3].forEach((n) => batcher.push(n));
    expect(flushed).toEqual([]);
    fire();
    expect(flushed).toEqual([[1, 2, 3]]);
  });

  it("sets one timer for a burst, and a new one for the next burst", () => {
    const { batcher, flushed, fire, timers } = setup();
    batcher.push(1);
    batcher.push(2);
    expect(timers).toHaveLength(1);
    fire();
    batcher.push(3);
    expect(timers).toHaveLength(1);
    fire();
    expect(flushed).toEqual([[1, 2], [3]]);
  });

  it("can be flushed at once, and the timer that was already set then finds nothing to do", () => {
    const { batcher, flushed, fire } = setup();
    batcher.push(1);
    batcher.flush();
    expect(flushed).toEqual([[1]]);
    fire();
    expect(flushed).toEqual([[1]]);
  });

  it("does nothing when flushed with nothing waiting", () => {
    const { batcher, flushed } = setup();
    batcher.flush();
    expect(flushed).toEqual([]);
  });
});
