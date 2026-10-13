import { describe, expect, it } from "vitest";
import { ago, canRetry, canStop, counters, duration, elapsed, endNote, formatBytes, isActive, isStopping, statusLabel, statusTone, workerNotice } from "../../src/lib/spiders/derive";
import type { Run } from "../../src/lib/spiders/types";

const NOW = Date.parse("2026-10-12T12:00:00Z");
const at = (secondsAgo: number) => new Date(NOW - secondsAgo * 1000).toISOString();

const run = (fields: Partial<Run> = {}): Run => ({
  id: 7, source: "books_toscrape", kind: "live", status: "running", queued_at: at(120), started_at: at(100), finished_at: null, heartbeat_at: at(1),
  retry_of: null, cancel_requested: false, options: {}, stats: {}, error: null, ...fields,
});

describe("status", () => {
  it("knows which runs are in flight, in the words a person uses", () => {
    expect(["queued", "running", "completed", "paused", "failed", "cancelled"].map((s) => isActive(s as Run["status"]))).toEqual([true, true, false, false, false, false]);
    expect(statusLabel("paused")).toBe("Stopped");
    expect(statusLabel("cancelled")).toBe("Cancelled");
  });
  it("allows Stop once, while a run is in flight, and shows Stopping until it ends", () => {
    expect(canStop(run())).toBe(true);
    expect(canStop(run({ cancel_requested: true }))).toBe(false);
    expect(isStopping(run({ cancel_requested: true }))).toBe(true);
    expect(canStop(run({ status: "completed" }))).toBe(false);
    expect(isStopping(run({ status: "queued", cancel_requested: false }))).toBe(false);
  });
  it("allows Retry for a failed or stopped run only", () => {
    expect((["failed", "paused", "completed", "running", "queued", "cancelled"] as const).map((status) => canRetry(run({ status })))).toEqual([true, true, false, false, false, false]);
  });
});

describe("time", () => {
  it.each([[2, "just now"], [40, "40s ago"], [300, "5m ago"], [7300, "2h ago"], [200000, "2d ago"]])("%s seconds ago reads %s", (seconds, text) => {
    expect(ago(at(seconds), NOW)).toBe(text);
  });
  it("never reads a clock that is slightly ahead as negative", () => expect(ago(at(-30), NOW)).toBe("just now"));
  it.each([[42, "42s"], [65, "1m 05s"], [3725, "1h 02m"]])("%s seconds is %s", (seconds, text) => expect(duration(seconds)).toBe(text));
  it("measures a running run to now and a finished one to its end", () => {
    expect(elapsed(run(), NOW)).toBe("1m 40s");
    expect(elapsed(run({ status: "completed", finished_at: at(40) }), NOW)).toBe("1m 00s");
    expect(elapsed(run({ status: "queued", started_at: null }), NOW)).toBeNull();
  });
});

describe("worker notices", () => {
  it("says how to start a worker when a run has waited more than 30 seconds", () => {
    expect(workerNotice(run({ status: "queued", started_at: null, heartbeat_at: null, queued_at: at(31) }), NOW)?.text).toBe("No worker has picked this up. Start one with make pipeline-worker.");
    expect(workerNotice(run({ status: "queued", started_at: null, heartbeat_at: null, queued_at: at(29) }), NOW)).toBeNull();
  });
  it("warns when a running run's worker has been quiet for more than 15 seconds", () => {
    expect(workerNotice(run({ heartbeat_at: at(40) }), NOW)?.text).toBe("Worker last seen 40s ago.");
    expect(workerNotice(run({ heartbeat_at: at(5) }), NOW)).toBeNull();
  });
  it("is silent for a finished run", () => expect(workerNotice(run({ status: "failed", heartbeat_at: at(900) }), NOW)).toBeNull());
});

describe("counters", () => {
  it("shows six totals for a live run, zero when the stats are empty", () => {
    expect(counters(run())).toEqual([
      { label: "Requests", value: 0 }, { label: "New", value: 0 }, { label: "Changed", value: 0 },
      { label: "Unchanged", value: 0 }, { label: "Rejected", value: 0 }, { label: "Blocked", value: 0 },
    ]);
    const stats = { requests_count: 38, records_created: 740, records_changed: 12, records_unchanged: 3, items_rejected: 2, blocked_requests_count: 1 };
    expect(counters(run({ stats })).map((c) => c.value)).toEqual([38, 740, 12, 3, 2, 1]);
  });
  it("shows what a trial parsed instead of what it stored", () => {
    const stats = { requests_count: 5, items_scraped: 98, items_rejected: 2, blocked_requests_count: 0 };
    expect(counters(run({ kind: "trial", stats }))).toEqual([
      { label: "Requests", value: 5 }, { label: "Parsed", value: 98 }, { label: "Rejected", value: 2 }, { label: "Blocked", value: 0 },
    ]);
  });
  it("explains how a trial ended", () => {
    expect(endNote(run({ kind: "trial", status: "completed", options: { max_requests: 5 }, stats: { stopped_at_cap: true } }))).toBe("Stopped at the page cap (5 requests).");
    expect(endNote(run({ status: "completed" }))).toBeNull();
  });
});

describe("the ledger", () => {
  it.each([[200, "ok"], [204, "ok"], [301, "muted"], [404, "bad"], [429, "bad"], [503, "bad"], [0, "bad"]])("status %s is %s", (code, tone) => expect(statusTone(code)).toBe(tone));
  it.each([[0, "0 B"], [900, "900 B"], [2048, "2.0 KB"], [5 * 1024 * 1024, "5.0 MB"]])("%s bytes is %s", (n, text) => expect(formatBytes(n)).toBe(text));
});
