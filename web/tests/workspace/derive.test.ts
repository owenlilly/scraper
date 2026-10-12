import { describe, expect, it } from "vitest";
import { deriveRunPanel, deriveThread, isWorking, shouldKeepLive, summarizeWork } from "../../src/lib/workspace/derive";
import { liveReducer, type LiveAction, type LiveTurn } from "../../src/lib/workspace/live";
import { endFrame, event, makeRun, makeState } from "./fixtures";

const live = (...actions: LiveAction[]) => actions.reduce<LiveTurn | null>((s, a) => liveReducer(s, a), null);
const frame = (f: { event: string; data: unknown }): LiveAction => ({ type: "frame", event: f.event, data: f.data });
const start: LiveAction = { type: "start", kind: "agent", text: "Collect the plans" };

describe("deriveThread", () => {
  it("is empty for a session with nothing in it", () => {
    expect(deriveThread(makeState(), null)).toEqual([]);
  });

  it("lays out saved history as questions and answers, with the work behind each answer", () => {
    const state = makeState({
      history: [
        { role: "user", content: "Collect the plans", reasoning: "", sources: [], interrupted: false, note: "", trace: [] },
        { role: "assistant", content: "Found six plans.", reasoning: "Thinking.", sources: ["https://a.test/"], interrupted: false, note: "6 rows added to the results table", trace: [{ name: "fetch_page", args: {}, text: "ok", ok: true }] },
      ],
    });
    const [user, agent] = deriveThread(state, null);
    expect(user).toMatchObject({ kind: "user", text: "Collect the plans", queued: false });
    expect(agent).toMatchObject({ kind: "agent", answer: "Found six plans.", reasoning: "Thinking.", note: "6 rows added to the results table", sources: ["https://a.test/"], live: false });
    expect(agent.kind === "agent" && agent.trace).toHaveLength(1);
  });

  it("shows the turn in progress after the history, with what has streamed so far", () => {
    const turn = live(start, frame(event("tool_start", { name: "fetch_page", args: { url: "https://a.test" } })), frame(event("answer", { text: "Looking" })));
    const items = deriveThread(makeState(), turn);
    expect(items.map((i) => i.kind)).toEqual(["user", "agent"]);
    expect(items[1]).toMatchObject({ kind: "agent", answer: "Looking", live: true });
  });

  it("puts messages the agent has read in the conversation, and the ones it has not read apart as queued", () => {
    const turn = live(start, { type: "queued", id: "f2", text: "skip the blog" }, frame(event("followup", { text: "also the SKU", id: "f1" })));
    const items = deriveThread(makeState(), turn);
    expect(items.filter((i) => i.kind === "user").map((i) => (i.kind === "user" ? [i.text, i.queued] : null))).toEqual([
      ["Collect the plans", false],
      ["also the SKU", false],
      ["skip the blog", true],
    ]);
  });

  it("shows a saved run that is waiting or stopped, with its work log, when nothing is streaming here", () => {
    const run = makeRun({
      status: "paused",
      instruction: "Collect the plans",
      trace: [{ name: "fetch_page", args: { url: "https://a.test" }, text: "ok", ok: true }],
      followups: [{ id: "f1", text: "also the SKU", delivered: true }, { id: "f2", text: "not read", delivered: false }],
    });
    const items = deriveThread(makeState({ run }), null);
    expect(items.map((i) => (i.kind === "user" ? `user:${i.text}:${i.queued}` : "agent"))).toEqual(["user:Collect the plans:false", "user:also the SKU:false", "agent", "user:not read:true"]);
  });

  it("does not show a finished run twice: it is in the history", () => {
    const state = makeState({
      run: makeRun({ status: "done" }),
      history: [{ role: "user", content: "Collect the plans", reasoning: "", sources: [], interrupted: false, note: "", trace: [] }],
    });
    expect(deriveThread(state, null)).toHaveLength(1);
  });

  it("shows the turn in progress and not also the saved run record for the same run", () => {
    const state = makeState({ run: makeRun({ status: "running" }) });
    expect(deriveThread(state, live(start)).filter((i) => i.kind === "user")).toHaveLength(1);
  });

  it("marks a partly streamed answer that was cut off", () => {
    const turn = live({ type: "start", kind: "ask", text: "What does Team cost?" }, frame({ event: "answer", data: { kind: "answer", text: "Forty-" } }), frame({ event: "error", data: { kind: "error", text: "Lost it" } }), frame(endFrame({ interrupted: true })));
    const agent = deriveThread(makeState(), turn).find((i) => i.kind === "agent");
    expect(agent).toMatchObject({ interrupted: true, answer: "Forty-" });
  });
});

describe("deriveRunPanel", () => {
  it("is nothing when there is no run and the server is idle", () => {
    expect(deriveRunPanel(makeState(), null)).toEqual({ kind: "none" });
  });

  it("offers a way to stop even when the server is busy with something that is not a saved run", () => {
    expect(deriveRunPanel(makeState({ busy: true, run: null }), null)).toEqual({ kind: "working", step: 0, doing: "Working" });
  });

  it("describes the call being made while a run streams", () => {
    const turn = live(start, frame(event("step", { step: 3 })), frame(event("tool_start", { name: "fetch_page", args: { url: "https://shop.example/plans" } })));
    expect(deriveRunPanel(makeState(), turn)).toEqual({ kind: "working", step: 3, doing: "Fetching shop.example/plans" });
  });

  it("says it is thinking between calls", () => {
    expect(deriveRunPanel(makeState(), live(start, frame(event("step", { step: 1 }))))).toEqual({ kind: "working", step: 1, doing: "Thinking" });
  });

  it("shows the question when the run is waiting for an answer, live or saved", () => {
    const pending = { kind: "ask" as const, question: "Which?", options: ["A", "B"] };
    expect(deriveRunPanel(makeState(), live(start, frame(event("needs_input", { payload: pending })), frame(endFrame())))).toEqual({ kind: "question", pending });
    expect(deriveRunPanel(makeState({ run: makeRun({ status: "paused", pending }) }), null)).toEqual({ kind: "question", pending });
  });

  it("offers to resume or discard a stopped or failed run, with the reason", () => {
    expect(deriveRunPanel(makeState({ run: makeRun({ status: "stopped" }) }), null)).toMatchObject({ kind: "interrupted", resumable: true, message: "This run was stopped. Your work so far is saved." });
    expect(deriveRunPanel(makeState({ run: makeRun({ status: "error" }) }), null)).toMatchObject({ kind: "interrupted", resumable: true });
    const failed = live(start, frame(event("error", { text: "Daily page limit reached (300 a day)." })), frame(endFrame()));
    expect(deriveRunPanel(makeState(), failed)).toEqual({ kind: "interrupted", resumable: true, message: "Daily page limit reached (300 a day)." });
  });

  it("shows nothing in the panel for a run that ended for good: the thread already says why", () => {
    const ended = live(start, frame(event("limit", { text: "context" })), frame(endFrame()));
    expect(deriveRunPanel(makeState(), ended)).toEqual({ kind: "none" });
    expect(deriveRunPanel(makeState({ run: makeRun({ status: "ended", resumable: false }) }), null)).toEqual({ kind: "none" });
  });

  it("does not offer to resume a run that cannot be resumed", () => {
    expect(deriveRunPanel(makeState({ run: makeRun({ status: "stopped", resumable: false }) }), null)).toMatchObject({ resumable: false });
  });

  it("says it is finishing up when the server still has a run going that this screen is not streaming", () => {
    expect(deriveRunPanel(makeState({ run: makeRun({ status: "running" }), busy: true }), null)).toEqual({ kind: "working", step: 0, doing: "Finishing up" });
  });

  it("is nothing for a finished run", () => {
    expect(deriveRunPanel(makeState({ run: makeRun({ status: "done" }) }), live(start, frame(event("done")), frame(endFrame())))).toEqual({ kind: "none" });
  });
});

describe("isWorking", () => {
  it("is true while streaming here, or while the server says it is busy", () => {
    expect(isWorking(makeState(), live(start))).toBe(true);
    expect(isWorking(makeState({ busy: true }), null)).toBe(true);
  });
  it("is false for a paused or finished turn and an idle server", () => {
    expect(isWorking(makeState(), live(start, frame(event("done")), frame(endFrame())))).toBe(false);
    expect(isWorking(makeState(), null)).toBe(false);
    const paused = live(start, frame(event("needs_input", { payload: { kind: "ask", question: "?", options: [] } })), frame(endFrame()));
    expect(isWorking(makeState(), paused)).toBe(false);
  });
});

describe("summarizeWork", () => {
  const line = (name: string, ok = true) => ({ name, args: {}, text: "", ok, done: true });
  it("counts the steps, and the pages that were read", () => {
    expect(summarizeWork([line("fetch_page"), line("read_page"), line("fetch_page"), line("add_rows")])).toBe("Worked for 4 steps, read 2 pages");
  });
  it("does not count a page that could not be fetched as read", () => {
    expect(summarizeWork([line("fetch_page"), line("fetch_page", false)])).toBe("Worked for 2 steps, read 1 page");
  });
  it("counts the page a browser tool came back with as a page read, but not a list of network requests", () => {
    expect(summarizeWork([line("fetch_page"), line("browser_submit_form"), line("browser_click"), line("browser_scroll"), line("browser_network")])).toBe(
      "Worked for 5 steps, read 4 pages",
    );
  });
  it("leaves out the pages when none were read, and says step for one", () => {
    expect(summarizeWork([line("add_rows")])).toBe("Worked for 1 step");
  });
  it("is empty when nothing was done", () => {
    expect(summarizeWork([])).toBe("");
  });
});

describe("shouldKeepLive", () => {
  const finished = (...frames: { event: string; data: unknown }[]) => live(start, ...frames.map(frame), frame(endFrame()))!;
  it("keeps a run that failed, because the saved state does not remember why", () => {
    expect(shouldKeepLive(finished(event("error", { text: "Daily page limit reached (300 a day)." })))).toBe(true);
    expect(shouldKeepLive(finished(event("limit", { text: "context" })))).toBe(true);
  });
  it("lets go of a run that finished, paused or was stopped, because the saved state shows it", () => {
    expect(shouldKeepLive(finished(event("done")))).toBe(false);
    expect(shouldKeepLive(finished(event("needs_input", { payload: { kind: "ask", question: "?", options: [] } })))).toBe(false);
    expect(shouldKeepLive(finished(event("limit", { text: "stopped" })))).toBe(false);
  });
  it("lets go of a question to the pages even when it failed: the answer so far is saved in the history", () => {
    const ask = live({ type: "start", kind: "ask", text: "Q" }, frame({ event: "error", data: { kind: "error", text: "Lost it" } }), frame(endFrame()))!;
    expect(shouldKeepLive(ask)).toBe(false);
  });
});
