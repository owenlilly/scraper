import type { AgentEvent, RunView, WorkspaceState } from "../../src/lib/api/types";

export const event = (kind: AgentEvent["kind"], fields: Partial<AgentEvent> = {}): { event: string; data: AgentEvent } => ({
  event: kind,
  data: { kind, text: "", name: "", args: {}, step: 0, ok: true, count: 0, urls: [], payload: {}, id: "", ...fields },
});

export const usage = { day: "2026-10-10", model_calls: 3, model_calls_limit: 300, pages: 2, pages_limit: 300, resets_at: "2026-10-11T00:00:00+00:00" };

export const endFrame = (fields: { drafts?: string[]; leftovers?: string[]; interrupted?: boolean } = {}) => ({
  event: "end",
  data: { drafts: [], leftovers: [], interrupted: false, usage, ...fields },
});

export const makeRun = (fields: Partial<RunView> = {}): RunView => ({
  number: 1,
  instruction: "Collect the plans",
  model: "fake-model",
  status: "running",
  resumable: true,
  pending: null,
  fetched: 0,
  max_pages: 30,
  delay: 0.5,
  rows_added: 0,
  trace: [],
  followups: [],
  ...fields,
});

export const makeState = (fields: Partial<WorkspaceState> = {}): WorkspaceState => ({
  user: { id: "u1" },
  provider: { key: "lmstudio", name: "LM Studio", hosted: false },
  busy: false,
  pages: [],
  failed: [],
  rows: [],
  history: [],
  run: null,
  progress: [0, 0],
  settings: { url: "", mode: "", follow: "", depth: "", agent: "" },
  model: null,
  models: [{ id: "fake-model", context: 32768, loaded: true }],
  models_error: null,
  fetch_modes: ["fast"],
  usage,
  ...fields,
});
