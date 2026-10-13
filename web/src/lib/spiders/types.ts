/** What the Python API's /pipeline routes send. */

export type RunStatus = "queued" | "running" | "completed" | "paused" | "failed" | "cancelled";
export type RunKind = "live" | "trial";

export type RejectedSample = { item: Record<string, string>; errors: string[] };

export type RunStats = {
  requests_count?: number;
  blocked_requests_count?: number;
  failed_requests_count?: number;
  items_scraped?: number;
  records_created?: number;
  records_changed?: number;
  records_unchanged?: number;
  items_rejected?: number;
  rejected_by_field?: Record<string, number>;
  /** Only on a run's own view (GET /pipeline/runs/{id}), not in lists. */
  rejected_samples?: RejectedSample[];
  trial_items?: Record<string, unknown>[];
  stopped_at_cap?: boolean;
};

export type Run = {
  id: number;
  source: string;
  kind: RunKind;
  status: RunStatus;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  heartbeat_at: string | null;
  retry_of: number | null;
  cancel_requested: boolean;
  options: { max_requests?: number };
  stats: RunStats;
  error: string | null;
};

export type SourceSummary = { name: string; latest_run: Run | null };

export type FetchRow = { id: number; url: string; final_url: string; status: number; fetched_at: string; duration_ms: number | null; size: number };
