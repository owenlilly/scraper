/** Collects items that arrive in a burst and hands them over together, so a fast stream of text redraws the screen a few times a second, not hundreds. */

const later = (run: () => void) => void setTimeout(run, 40);

export function createBatcher<T>(flush: (items: T[]) => void, schedule: (run: () => void) => void = later) {
  let waiting: T[] = [];
  let scheduled = false;
  const run = () => {
    scheduled = false;
    const items = waiting;
    waiting = [];
    if (items.length > 0) flush(items);
  };
  return {
    push(item: T) {
      waiting.push(item);
      if (!scheduled) {
        scheduled = true;
        schedule(run);
      }
    },
    flush: run,
  };
}
