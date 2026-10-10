import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { startFakeModel, type FakeModel } from "../e2e/support/fake-model";

let model: FakeModel;
beforeAll(async () => {
  model = await startFakeModel(0);
});
afterAll(() => model.close());
beforeEach(() => model.reset());

type Chunk = { choices: { delta: { content?: string; reasoning_content?: string; tool_calls?: { index: number; id: string; function: { name: string; arguments: string } }[] }; finish_reason: string | null }[] };

async function chat(messages: unknown[] = [{ role: "user", content: "hi" }]): Promise<{ chunks: Chunk[]; raw: string }> {
  const response = await fetch(`${model.url}/v1/chat/completions`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ model: "fake-model", messages, stream: true }),
  });
  const raw = await response.text();
  const chunks = raw
    .split("\n\n")
    .filter((frame) => frame.startsWith("data: ") && !frame.includes("[DONE]"))
    .map((frame) => JSON.parse(frame.slice(6)) as Chunk);
  return { chunks, raw };
}
const text = (chunks: Chunk[]) => chunks.map((c) => c.choices[0]?.delta.content ?? "").join("");

describe("the fake model server", () => {
  it("lists one model the way LM Studio does, in both of the places the API looks", async () => {
    const detailed = await (await fetch(`${model.url}/api/v0/models`)).json();
    expect(detailed.data[0]).toMatchObject({ id: "fake-model", type: "llm", state: "loaded", loaded_context_length: 32768 });
    const plain = await (await fetch(`${model.url}/v1/models`)).json();
    expect(plain.data[0].id).toBe("fake-model");
  });

  it("streams scripted text as small chunks and ends the stream", async () => {
    await model.script([{ text: "Ten dollars a month." }]);
    const { chunks, raw } = await chat();
    expect(text(chunks)).toBe("Ten dollars a month.");
    expect(chunks.length).toBeGreaterThan(3);
    expect(chunks.at(-1)!.choices[0].finish_reason).toBe("stop");
    expect(raw.trimEnd().endsWith("data: [DONE]")).toBe(true);
  });

  it("streams reasoning separately from the answer", async () => {
    await model.script([{ reasoning: "Let me think.", text: "Done." }]);
    const { chunks } = await chat();
    expect(chunks.map((c) => c.choices[0].delta.reasoning_content ?? "").join("")).toBe("Let me think.");
    expect(text(chunks)).toBe("Done.");
  });

  it("streams a tool call with its name and JSON arguments, and finishes with tool_calls", async () => {
    await model.script([{ toolCalls: [{ name: "add_rows", args: { rows: [{ plan: "Solo", price: "$9" }] } }] }]);
    const { chunks } = await chat();
    const call = chunks.flatMap((c) => c.choices[0].delta.tool_calls ?? [])[0];
    expect(call.function.name).toBe("add_rows");
    expect(JSON.parse(call.function.arguments)).toEqual({ rows: [{ plan: "Solo", price: "$9" }] });
    expect(call.id).toMatch(/^call_/);
    expect(chunks.at(-1)!.choices[0].finish_reason).toBe("tool_calls");
  });

  it("answers the script's steps in order, one per request, then says it has run out", async () => {
    await model.script([{ text: "first" }, { text: "second" }]);
    expect(text((await chat()).chunks)).toBe("first");
    expect(text((await chat()).chunks)).toBe("second");
    expect(text((await chat()).chunks)).toContain("script exhausted");
  });

  it("records what it was asked, so a test can check what the agent sent", async () => {
    await model.script([{ text: "ok" }]);
    await chat([{ role: "system", content: "be brief" }, { role: "user", content: "Collect the plans" }]);
    const requests = await model.requests();
    expect(requests).toHaveLength(1);
    expect(requests[0].messages.map((m) => m.role)).toEqual(["system", "user"]);
    expect(JSON.stringify(requests[0].messages)).toContain("Collect the plans");
  });

  it("holds a reply until it is released, so a test can look at the screen while a run is working", async () => {
    await model.script([{ hold: "gate", text: "released" }]);
    let finished = false;
    const pending = chat().then((result) => {
      finished = true;
      return result;
    });
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(finished).toBe(false);
    await model.release("gate");
    expect(text((await pending).chunks)).toBe("released");
  });

  it("can be told to start again", async () => {
    await model.script([{ text: "a" }]);
    await chat();
    await model.reset();
    expect(await model.requests()).toEqual([]);
  });
});
