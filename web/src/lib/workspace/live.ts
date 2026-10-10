/** The turn that is streaming into this screen, built from the events of one request. */
import type { EndFrame, Pending, RunView, Usage } from "../api/types";

export type LogEntry = { id: number; name: string; args: Record<string, unknown>; text: string; ok: boolean; done: boolean };
export type QueuedMessage = { id: string; text: string };
export type LiveStatus = "streaming" | "paused" | "done" | "stopped" | "error";

export type LiveTurn = {
  kind: "agent" | "ask";
  text: string;
  status: LiveStatus;
  step: number;
  reasoning: string;
  answer: string;
  log: LogEntry[];
  rowsAdded: number;
  pending: Pending | null;
  notice: string | null;
  /** False once the server says the run ended for good: there is nothing to resume. */
  resumable: boolean;
  followups: string[];
  queued: QueuedMessage[];
  drafts: string[];
  leftovers: string[];
  interrupted: boolean;
  usage: Usage | null;
  ended: boolean;
};

export type LiveAction =
  | { type: "start"; kind: "agent" | "ask"; text: string; from?: RunView }
  | { type: "frame"; event: string; data: unknown }
  | { type: "queued"; id: string; text: string }
  | { type: "failed"; message: string; resumable?: boolean }
  | { type: "resume" }
  | { type: "clear" };

export const LIMIT_MESSAGES: Record<string, string> = {
  context: "The conversation no longer fits the model's context window, so the run ended. Try a narrower instruction.",
};

export const CONNECTION_LOST = "The connection was lost before the run finished. Your work so far is saved.";

function begin(kind: "agent" | "ask", text: string, from?: RunView): LiveTurn {
  const turn: LiveTurn = {
    kind,
    text,
    status: "streaming",
    step: 0,
    reasoning: "",
    answer: "",
    log: [],
    rowsAdded: 0,
    pending: null,
    notice: null,
    resumable: true,
    followups: [],
    queued: [],
    drafts: [],
    leftovers: [],
    interrupted: false,
    usage: null,
    ended: false,
  };
  if (!from) return turn;
  return {
    ...turn,
    rowsAdded: from.rows_added,
    log: from.trace.map((entry, index) => ({ id: index + 1, name: entry.name, args: entry.args, text: entry.text, ok: entry.ok, done: true })),
    followups: from.followups.filter((f) => f.delivered).map((f) => f.text),
    queued: from.followups.filter((f) => !f.delivered).map((f) => ({ id: f.id, text: f.text })),
  };
}

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const str = (value: unknown) => (typeof value === "string" ? value : "");

function applyFrame(turn: LiveTurn, event: string, data: unknown): LiveTurn {
  if (!isRecord(data)) return turn;
  switch (event) {
    case "step":
      return { ...turn, step: Number(data.step) || 0, reasoning: "", answer: "" };
    case "reasoning":
      return { ...turn, reasoning: turn.reasoning + str(data.text) };
    case "answer":
      return { ...turn, answer: turn.answer + str(data.text) };
    case "tool_start": {
      const id = turn.log.reduce((highest, entry) => Math.max(highest, entry.id), 0) + 1;
      const args = isRecord(data.args) ? data.args : {};
      return { ...turn, log: [...turn.log, { id, name: str(data.name), args, text: "", ok: true, done: false }] };
    }
    case "tool_result": {
      const index = turn.log.findIndex((entry) => !entry.done);
      if (index === -1) return turn;
      const log = turn.log.map((entry, i) => (i === index ? { ...entry, text: str(data.text), ok: data.ok !== false, done: true } : entry));
      return { ...turn, log };
    }
    case "rows_added":
      return { ...turn, rowsAdded: turn.rowsAdded + (Number(data.count) || 0) };
    case "needs_input":
      return { ...turn, status: "paused", pending: isRecord(data.payload) ? (data.payload as Pending) : null };
    case "limit": {
      const reason = str(data.text);
      if (reason === "stopped") return { ...turn, status: "stopped", notice: null };
      return { ...turn, status: "error", notice: LIMIT_MESSAGES[reason] ?? reason, resumable: false };
    }
    case "error": {
      const final = isRecord(data.payload) && data.payload.final === true;
      return { ...turn, status: "error", notice: str(data.text), resumable: !final };
    }
    case "done":
      return { ...turn, status: "done" };
    case "followup": {
      const id = str(data.id);
      const text = str(data.text);
      const queued = turn.queued.filter((q) => (id ? q.id !== id : q.text !== text));
      return { ...turn, queued, followups: [...turn.followups, text] };
    }
    case "end": {
      const end = data as Partial<EndFrame>;
      const ended = {
        ...turn,
        ended: true,
        drafts: Array.isArray(end.drafts) ? end.drafts : [],
        leftovers: Array.isArray(end.leftovers) ? end.leftovers : [],
        interrupted: end.interrupted === true,
        usage: end.usage ?? turn.usage,
      };
      if (ended.status !== "streaming") return ended;
      // A question to the pages has no "done" event: finishing the stream is finishing the answer.
      if (turn.kind === "ask") return { ...ended, status: "done" };
      return { ...ended, status: "error", notice: CONNECTION_LOST };
    }
    default:
      return turn;
  }
}

export function liveReducer(state: LiveTurn | null, action: LiveAction): LiveTurn | null {
  switch (action.type) {
    case "start":
      return begin(action.kind, action.text, action.from);
    case "clear":
      return null;
    case "frame":
      return state ? applyFrame(state, action.event, action.data) : null;
    case "queued":
      return state ? { ...state, queued: [...state.queued, { id: action.id, text: action.text }] } : null;
    case "failed":
      return state ? { ...state, status: "error", notice: action.message, resumable: action.resumable ?? false, ended: true } : null;
    case "resume":
      return state ? { ...state, status: "streaming", pending: null, ended: false, notice: null, resumable: true, drafts: [], leftovers: [], interrupted: false } : null;
  }
}
