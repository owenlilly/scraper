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

/** A place a run can crawl, as the person sees it: the id a run is started with and the name shown for it. */
export type LocationOption = { id: string; label: string };

export type RunOptions = {
  /** A trial's request count. */
  max_requests?: number;
  /** The whole entry the run crawled, with every one of its bound values. */
  location?: Record<string, unknown>;
  /** The settings the run was queued with. */
  settings?: Record<string, unknown>;
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
  options: RunOptions;
  stats: RunStats;
  error: string | null;
  location: LocationOption | null;
};

export type SourceSummary = { name: string; latest_run: Run | null; locations: LocationOption[]; trial_max_requests: number };

export type FetchRow = { id: number; url: string; final_url: string; status: number; fetched_at: string; duration_ms: number | null; size: number };

/** How the API describes one of a spider's own settings, so the form can be drawn without knowing the spider. */
export type FormField = {
  key: string;
  label: string;
  description: string | null;
  type: "integer" | "number" | "string" | "boolean" | "array";
  nullable: boolean;
  default: unknown;
  minimum: number | null;
  maximum: number | null;
  minLength: number | null;
  maxLength: number | null;
  maxItems: number | null;
  /** For a list: the fields of each record. */
  items?: FormField[];
};

export type SettingsView = {
  /** The saved values with defaults filled in, or the stored values as they are when they no longer validate (see `problems`). */
  settings: Record<string, unknown>;
  defaults: Record<string, unknown>;
  problems: string[];
  form: FormField[];
  /** The names of the spider's item fields, in the item's own order. */
  fields: string[];
  locations_setting: string | null;
  location_id_field: string;
  location_label_field: string;
};
