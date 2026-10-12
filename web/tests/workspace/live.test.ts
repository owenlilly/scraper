import { describe, expect, it } from "vitest";
import { liveReducer, type LiveAction, type LiveTurn } from "../../src/lib/workspace/live";
import { endFrame, event, makeRun } from "./fixtures";

const run = (actions: LiveAction[], from: LiveTurn | null = null) => actions.reduce<LiveTurn | null>((state, action) => liveReducer(state, action), from);
const frames = (...list: { event: string; data: unknown }[]): LiveAction[] => list.map((f) => ({ type: "frame", event: f.event, data: f.data }));
const start = (text = "Collect the plans"): LiveAction => ({ type: "start", kind: "agent", text });

describe("starting", () => {
  it("begins a streaming turn holding what the person sent", () => {
    expect(run([start()])).toMatchObject({ kind: "agent", text: "Collect the plans", status: "streaming", log: [], answer: "", reasoning: "", pending: null, ended: false });
  });

  it("can pick up a run that is already under way, with its log, its rows and the follow-ups already read", () => {
    const existing = makeRun({
      instruction: "Collect the plans",
      rows_added: 4,
      trace: [{ name: "fetch_page", args: { url: "https://a.test" }, text: "Fetched", ok: true }],
      followups: [{ id: "f1", text: "also the SKU", delivered: true }, { id: "f2", text: "not read yet", delivered: false }],
    });
    const turn = run([{ type: "start", kind: "agent", text: existing.instruction, from: existing }])!;
    expect(turn.log).toEqual([{ id: 1, name: "fetch_page", args: { url: "https://a.test" }, text: "Fetched", ok: true, done: true }]);
    expect(turn.rowsAdded).toBe(4);
    expect(turn.followups).toEqual(["also the SKU"]);
    expect(turn.queued).toEqual([{ id: "f2", text: "not read yet" }]);
  });
});

describe("the agent's steps", () => {
  it("collects reasoning and answer separately, and starts them again at each new step", () => {
    const turn = run([start(), ...frames(event("step", { step: 1 }), event("reasoning", { text: "Hmm. " }), event("reasoning", { text: "Yes." }), event("answer", { text: "I will " }), event("answer", { text: "look." }))])!;
    expect(turn).toMatchObject({ step: 1, reasoning: "Hmm. Yes.", answer: "I will look." });
    const next = run(frames(event("step", { step: 2 })), turn)!;
    expect(next).toMatchObject({ step: 2, reasoning: "", answer: "" });
  });

  it("logs each call when it starts and fills in its result when it finishes, in order", () => {
    const turn = run([
      start(),
      ...frames(
        event("tool_start", { name: "fetch_page", args: { url: "https://a.test" } }),
        event("tool_start", { name: "read_page", args: { url: "https://a.test" } }),
        event("tool_result", { text: "Fetched a.test", ok: true }),
        event("tool_result", { text: "Could not read", ok: false }),
      ),
    ])!;
    expect(turn.log.map((e) => [e.name, e.done, e.ok, e.text])).toEqual([
      ["fetch_page", true, true, "Fetched a.test"],
      ["read_page", true, false, "Could not read"],
    ]);
  });

  it("ignores a result with no call waiting for it", () => {
    expect(run([start(), ...frames(event("tool_result", { text: "stray" }))])!.log).toEqual([]);
  });

  it("adds up the rows the agent records", () => {
    expect(run([start(), ...frames(event("rows_added", { count: 3 }), event("rows_added", { count: 2 }))])!.rowsAdded).toBe(5);
  });
});

describe("how a turn ends", () => {
  it("is done when the agent says so, and the end frame carries the usage", () => {
    const turn = run([start(), ...frames(event("done"), endFrame())])!;
    expect(turn).toMatchObject({ status: "done", ended: true });
    expect(turn.usage?.model_calls).toBe(3);
  });

  it("is paused, with the question, when the agent needs an answer, and stays paused after the stream closes", () => {
    const payload = { kind: "ask", question: "Which plan?", options: ["Solo", "Team"], interrupt_id: "i1" };
    const turn = run([start(), ...frames(event("needs_input", { payload }), endFrame())])!;
    expect(turn).toMatchObject({ status: "paused", ended: true, pending: payload });
  });

  it("is stopped when the person stopped it, and hands back the messages that were never read", () => {
    const turn = run([start(), ...frames(event("limit", { text: "stopped" }), endFrame({ drafts: ["never mind the footer"] }))])!;
    expect(turn).toMatchObject({ status: "stopped", drafts: ["never mind the footer"] });
  });

  it("says why the conversation ended when it no longer fits the model, and that it cannot be resumed", () => {
    const turn = run([start(), ...frames(event("limit", { text: "context" }), endFrame())])!;
    expect(turn).toMatchObject({ status: "error", resumable: false });
    expect(turn.notice).toMatch(/no longer fits the model's context window/);
  });

  it("treats an error the server marks final as one that cannot be resumed", () => {
    const turn = run([start(), ...frames(event("error", { text: "The model returned an empty answer.", payload: { final: true } }), endFrame())])!;
    expect(turn).toMatchObject({ status: "error", resumable: false });
  });

  it("shows an error as an error, with the words the API gave", () => {
    const turn = run([start(), ...frames(event("error", { text: "Daily page limit reached (300 a day)." }), endFrame())])!;
    expect(turn).toMatchObject({ status: "error", notice: "Daily page limit reached (300 a day).", resumable: true });
  });

  it("says the connection was lost when the stream ends without any outcome", () => {
    const turn = run([start(), ...frames(event("answer", { text: "partial" }), endFrame())])!;
    expect(turn.status).toBe("error");
    expect(turn.notice).toMatch(/connection was lost/i);
    expect(turn.answer).toBe("partial");
  });

  it("reports a request that failed before any stream began", () => {
    expect(run([start(), { type: "failed", message: "Another run is in progress." }])).toMatchObject({ status: "error", notice: "Another run is in progress.", ended: true });
  });

  it("cannot be resumed after a request the server refused, but can after a stream that broke part-way", () => {
    expect(run([start(), { type: "failed", message: "The server is busy." }])).toMatchObject({ resumable: false });
    expect(run([start(), { type: "failed", message: "The connection was lost.", resumable: true }])).toMatchObject({ resumable: true });
  });

  it("goes back to exactly what was on screen when a continuation is refused", () => {
    const before = run([start(), ...frames(event("error", { text: "Daily limit." }), endFrame())])!;
    const resumed = run([{ type: "resume" }], before)!;
    expect(resumed.status).toBe("streaming");
    expect(run([{ type: "restore", turn: before }], resumed)).toEqual(before);
    expect(run([{ type: "restore", turn: null }], resumed)).toBeNull();
  });

  it("can be continued after a pause or a stop, keeping what it had", () => {
    const paused = run([start(), ...frames(event("tool_start", { name: "fetch_page" }), event("tool_result", { text: "ok" }), event("needs_input", { payload: { kind: "ask", question: "?", options: [] } }), endFrame())])!;
    const again = run([{ type: "resume" }], paused)!;
    expect(again).toMatchObject({ status: "streaming", pending: null, ended: false, notice: null });
    expect(again.log).toHaveLength(1);
  });
});

describe("messages sent while it works", () => {
  it("waits as queued until the agent reads it, then becomes part of the conversation", () => {
    const queued = run([start(), { type: "queued", id: "f1", text: "only the plans" }])!;
    expect(queued.queued).toEqual([{ id: "f1", text: "only the plans" }]);
    const read = run(frames(event("followup", { text: "only the plans", id: "f1" })), queued)!;
    expect(read.queued).toEqual([]);
    expect(read.followups).toEqual(["only the plans"]);
  });

  it("shows a follow-up the agent read even if this screen never saw it queued", () => {
    expect(run([start(), ...frames(event("followup", { text: "from another tab", id: "zz" }))])!.followups).toEqual(["from another tab"]);
  });
});

describe("a question about the pages", () => {
  const ask: LiveAction = { type: "start", kind: "ask", text: "What does Team cost?" };

  it("streams reasoning and answer, and finishes done", () => {
    const turn = run([ask, ...frames({ event: "reasoning", data: { kind: "reasoning", text: "Check." } }, { event: "answer", data: { kind: "answer", text: "Forty-nine dollars." } }, endFrame())])!;
    expect(turn).toMatchObject({ kind: "ask", reasoning: "Check.", answer: "Forty-nine dollars.", status: "done", ended: true });
  });

  it("keeps a partial answer when the model fails part-way, and remembers it was cut off", () => {
    const turn = run([ask, ...frames({ event: "answer", data: { kind: "answer", text: "Forty-" } }, { event: "error", data: { kind: "error", text: "Cannot reach the model server" } }, endFrame({ interrupted: true }))])!;
    expect(turn).toMatchObject({ status: "error", answer: "Forty-", notice: "Cannot reach the model server", interrupted: true });
  });
});

describe("robustness", () => {
  it("ignores frames once the turn has been cleared", () => {
    expect(run([...frames(event("answer", { text: "late" }))])).toBeNull();
    expect(run([start(), { type: "clear" }, ...frames(event("answer", { text: "late" }))])).toBeNull();
  });

  it("ignores events it does not know and data of the wrong shape", () => {
    const turn = run([start(), ...frames({ event: "mystery", data: { a: 1 } }, { event: "answer", data: "not an object" }, { event: "answer", data: null })])!;
    expect(turn).toMatchObject({ answer: "", status: "streaming" });
  });

  it("does not change the turn it was given", () => {
    const before = run([start()])!;
    const snapshot = JSON.stringify(before);
    liveReducer(before, { type: "frame", event: "answer", data: event("answer", { text: "x" }).data });
    expect(JSON.stringify(before)).toBe(snapshot);
  });
});
