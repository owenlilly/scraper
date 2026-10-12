/** The shapes the Python API sends. They mirror `src/scraper/api/schemas.py` and the agent's events. */

export type Usage = { day: string; model_calls: number; model_calls_limit: number; pages: number; pages_limit: number; resets_at: string };
export type PageSummary = { url: string; title: string; chars: number; links: number; looks_empty: boolean; used: boolean };
export type TraceEntry = { name: string; args: Record<string, unknown>; text: string; ok: boolean };
export type HistoryMessage = {
  role: "user" | "assistant";
  content: string;
  reasoning: string;
  sources: string[];
  interrupted: boolean;
  note: string;
  trace: TraceEntry[];
};
export type Followup = { id: string; text: string; delivered: boolean };

/** What a paused run is waiting for. */
export type AskPending = { kind: "ask"; question: string; options: string[]; interrupt_id?: string };
export type SitePending = { kind: "site"; question: string; host: string; interrupt_id?: string };
export type LimitPending = {
  kind: "limit";
  question: string;
  reason: "steps" | "pages" | "repeat";
  actions: { label: string; action: "continue" | "wrap_up" | "stop" }[];
  interrupt_id?: string;
};
export type Pending = AskPending | SitePending | LimitPending;

export type RunStatus = "running" | "paused" | "stopped" | "error" | "done" | "ended";
export type RunView = {
  number: number;
  instruction: string;
  model: string;
  status: RunStatus;
  resumable: boolean;
  pending: Pending | null;
  fetched: number;
  max_pages: number;
  delay: number;
  rows_added: number;
  trace: TraceEntry[];
  followups: Followup[];
};
export type ProviderView = { key: string; name: string; hosted: boolean };
export type ModelView = { id: string; context: number | null; loaded: boolean };
export type SettingName = "url" | "mode" | "follow" | "depth" | "agent" | "browser";

export type WorkspaceState = {
  user: { id: string };
  provider: ProviderView;
  providers: ProviderView[];
  busy: boolean;
  pages: PageSummary[];
  failed: { url: string; reason: string }[];
  rows: Record<string, unknown>[];
  history: HistoryMessage[];
  run: RunView | null;
  progress: [number, number];
  settings: Record<SettingName, string>;
  model: string | null;
  models: ModelView[];
  models_error: string | null;
  fetch_modes: string[];
  browser_available: boolean;
  usage: Usage;
};

/** The events of a stream. `kind` is also the SSE event name. */
export type AgentEvent = {
  kind: "step" | "reasoning" | "answer" | "tool_start" | "tool_result" | "rows_added" | "needs_input" | "limit" | "error" | "done" | "followup";
  text: string;
  name: string;
  args: Record<string, unknown>;
  step: number;
  ok: boolean;
  count: number;
  urls: string[];
  payload: Record<string, unknown>;
  id: string;
};
export type EndFrame = { drafts: string[]; leftovers: string[]; interrupted: boolean; usage: Usage };
