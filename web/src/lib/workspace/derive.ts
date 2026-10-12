/** What the conversation and the run panel show, worked out from the saved state and the turn streaming here. */
import { describeCall, plural, readsPage } from "../api/format";
import type { Pending, TraceEntry, WorkspaceState } from "../api/types";
import type { LiveTurn } from "./live";

export type TraceLine = TraceEntry & { done: boolean };

export type ThreadItem =
  | { kind: "user"; key: string; text: string; queued: boolean }
  | {
      kind: "agent";
      key: string;
      answer: string;
      reasoning: string;
      note: string;
      sources: string[];
      trace: TraceLine[];
      interrupted: boolean;
      live: boolean;
      notice: string | null;
    };

export type RunPanel =
  | { kind: "none" }
  | { kind: "working"; step: number; doing: string }
  | { kind: "question"; pending: Pending }
  | { kind: "interrupted"; resumable: boolean; message: string };

const rowsNote = (count: number) => (count > 0 ? `${plural(count, "row")} added to the results table` : "");
const done = (entry: TraceEntry): TraceLine => ({ ...entry, done: true });

export function deriveThread(state: WorkspaceState, live: LiveTurn | null): ThreadItem[] {
  const items: ThreadItem[] = state.history.map((message, index): ThreadItem =>
    message.role === "user"
      ? { kind: "user", key: `h${index}`, text: message.content, queued: false }
      : {
          kind: "agent",
          key: `h${index}`,
          answer: message.content,
          reasoning: message.reasoning,
          note: message.note,
          sources: message.sources,
          trace: message.trace.map(done),
          interrupted: message.interrupted,
          live: false,
          notice: null,
        },
  );

  if (live) {
    items.push({ kind: "user", key: "live-user", text: live.text, queued: false });
    live.followups.forEach((text, i) => items.push({ kind: "user", key: `live-followup-${i}`, text, queued: false }));
    items.push({
      kind: "agent",
      key: "live-agent",
      answer: live.answer,
      reasoning: live.reasoning,
      note: rowsNote(live.rowsAdded),
      sources: [],
      trace: live.log.map((entry) => ({ name: entry.name, args: entry.args, text: entry.text, ok: entry.ok, done: entry.done })),
      interrupted: live.interrupted,
      live: live.status === "streaming",
      notice: live.status === "error" || live.status === "stopped" ? live.notice : null,
    });
    live.queued.forEach((q) => items.push({ kind: "user", key: `live-queued-${q.id}`, text: q.text, queued: true }));
    return items;
  }

  const run = state.run;
  if (run && run.status !== "done" && run.status !== "ended") {
    items.push({ kind: "user", key: "run-user", text: run.instruction, queued: false });
    run.followups.filter((f) => f.delivered).forEach((f) => items.push({ kind: "user", key: `run-followup-${f.id}`, text: f.text, queued: false }));
    items.push({
      kind: "agent",
      key: "run-agent",
      answer: "",
      reasoning: "",
      note: rowsNote(run.rows_added),
      sources: [],
      trace: run.trace.map(done),
      interrupted: false,
      live: false,
      notice: null,
    });
    run.followups.filter((f) => !f.delivered).forEach((f) => items.push({ kind: "user", key: `run-queued-${f.id}`, text: f.text, queued: true }));
  }
  return items;
}

const STOPPED = "This run was stopped. Your work so far is saved.";

export function deriveRunPanel(state: WorkspaceState, live: LiveTurn | null): RunPanel {
  if (live) {
    if (live.kind === "ask") return { kind: "none" };
    switch (live.status) {
      case "streaming": {
        const calling = [...live.log].reverse().find((entry) => !entry.done);
        return { kind: "working", step: live.step, doing: calling ? describeCall(calling.name, calling.args) : "Thinking" };
      }
      case "paused":
        return live.pending ? { kind: "question", pending: live.pending } : { kind: "interrupted", resumable: true, message: STOPPED };
      case "stopped":
        return { kind: "interrupted", resumable: true, message: STOPPED };
      case "error":
        return live.resumable ? { kind: "interrupted", resumable: true, message: live.notice ?? STOPPED } : { kind: "none" };
      default:
        return { kind: "none" };
    }
  }

  const run = state.run;
  if (!run) return state.busy ? { kind: "working", step: 0, doing: "Working" } : { kind: "none" };
  switch (run.status) {
    case "running":
      return state.busy
        ? { kind: "working", step: 0, doing: "Finishing up" }
        : { kind: "interrupted", resumable: run.resumable, message: "This run was interrupted. Your work so far is saved." };
    case "paused":
      return run.pending ? { kind: "question", pending: run.pending } : { kind: "interrupted", resumable: run.resumable, message: STOPPED };
    case "stopped":
      return { kind: "interrupted", resumable: run.resumable, message: STOPPED };
    case "error":
      return { kind: "interrupted", resumable: run.resumable, message: "This run stopped because of an error. Your work so far is saved." };
    default:
      return { kind: "none" };
  }
}

export function isWorking(state: WorkspaceState, live: LiveTurn | null): boolean {
  return live ? live.status === "streaming" : state.busy;
}


/** "Worked for 7 steps, read 3 pages": the line above a collapsed work log. */
export function summarizeWork(trace: TraceLine[]): string {
  if (trace.length === 0) return "";
  const read = trace.filter((line) => readsPage(line.name) && line.ok).length;
  return `Worked for ${plural(trace.length, "step")}${read > 0 ? `, read ${plural(read, "page")}` : ""}`;
}

/** Whether the turn that just finished streaming stays on screen. Most are replaced by the saved state; a failure is kept, because the saved state does not say why it failed. */
export function shouldKeepLive(turn: LiveTurn): boolean {
  return turn.kind === "agent" && turn.status === "error";
}
