/** Everything the spiders page decides from a run, kept apart from the screens so it can be tested without a browser. */
import type { Run, RunStatus, SourceSummary } from "./types";

export const POLL_RUN_MS = 2000;
export const POLL_LIST_MS = 6000;
export const NO_WORKER_AFTER_SECONDS = 30;
export const STALE_BEAT_AFTER_SECONDS = 15;

const LABELS: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  paused: "Stopped", // the run was stopped; what it saved is kept
  failed: "Failed",
  cancelled: "Cancelled",
};

export const isActive = (status: RunStatus): boolean => status === "queued" || status === "running";
export const statusLabel = (status: RunStatus): string => LABELS[status];
export const canStop = (run: Run): boolean => isActive(run.status) && !run.cancel_requested;
export const isStopping = (run: Run): boolean => run.status === "running" && run.cancel_requested;
export const canRetry = (run: Run): boolean => run.status === "failed" || run.status === "paused";

const secondsBetween = (iso: string, now: number): number => Math.max(0, Math.round((now - Date.parse(iso)) / 1000));

export function ago(iso: string, now: number): string {
  const seconds = secondsBetween(iso, now);
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

const two = (n: number): string => String(n).padStart(2, "0");

export function duration(total: number): string {
  if (total < 60) return `${total}s`;
  if (total < 3600) return `${Math.floor(total / 60)}m ${two(total % 60)}s`;
  return `${Math.floor(total / 3600)}h ${two(Math.floor((total % 3600) / 60))}m`;
}

export function elapsed(run: Run, now: number): string | null {
  if (!run.started_at) return null;
  const end = run.finished_at ? Date.parse(run.finished_at) : now;
  return duration(Math.max(0, Math.round((end - Date.parse(run.started_at)) / 1000)));
}

export function workerNotice(run: Run, now: number): { text: string } | null {
  if (run.status === "queued" && secondsBetween(run.queued_at, now) > NO_WORKER_AFTER_SECONDS) {
    return { text: "No worker has picked this up. Start one with make pipeline-worker." };
  }
  if (run.status === "running" && run.heartbeat_at && secondsBetween(run.heartbeat_at, now) > STALE_BEAT_AFTER_SECONDS) {
    return { text: `Worker last seen ${ago(run.heartbeat_at, now)}.` };
  }
  return null;
}

export type Counter = { label: string; value: number };

export function counters(run: Run): Counter[] {
  const s = run.stats;
  const n = (value: number | undefined) => value ?? 0;
  if (run.kind === "trial") {
    return [
      { label: "Requests", value: n(s.requests_count) },
      { label: "Parsed", value: n(s.items_scraped) },
      { label: "Rejected", value: n(s.items_rejected) },
      { label: "Blocked", value: n(s.blocked_requests_count) },
    ];
  }
  return [
    { label: "Requests", value: n(s.requests_count) },
    { label: "New", value: n(s.records_created) },
    { label: "Changed", value: n(s.records_changed) },
    { label: "Unchanged", value: n(s.records_unchanged) },
    { label: "Rejected", value: n(s.items_rejected) },
    { label: "Blocked", value: n(s.blocked_requests_count) },
  ];
}

export function endNote(run: Run): string | null {
  if (!run.stats.stopped_at_cap) return null;
  const saved = run.options.settings?.max_requests;
  const cap = run.kind === "trial" ? run.options.max_requests : typeof saved === "number" ? saved : undefined;
  return cap ? `Stopped at the request cap (${cap} request${cap === 1 ? "" : "s"}).` : "Stopped at the request cap.";
}

export function statusTone(status: number): "ok" | "muted" | "bad" {
  if (status >= 200 && status < 300) return "ok";
  if (status >= 300 && status < 400) return "muted";
  return "bad";
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** How often to ask again, or false for never. The polling rules live here, apart from the hooks, so they can be tested. */
export const pollRunEvery = (run: Run | undefined): number | false => (run && isActive(run.status) ? POLL_RUN_MS : false);
export const pollRunsEvery = (runs: Run[] | undefined): number | false => (runs?.some((run) => isActive(run.status)) ? POLL_LIST_MS : false);
export const pollSourcesEvery = (sources: SourceSummary[] | undefined): number | false =>
  sources?.some((source) => source.latest_run && isActive(source.latest_run.status)) ? POLL_LIST_MS : false;
export const pollFeedEvery = (runIsActive: boolean): number | false => (runIsActive ? POLL_RUN_MS : false);
