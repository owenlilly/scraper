/**
 * A stand-in for a local model server (LM Studio's API), driven by a script. The browser tests run the real Python API against it,
 * so a whole run, with its questions, tool calls and answers, is repeatable and costs nothing. It never contacts anything.
 *
 * Each chat request takes the next step of the script. A step can stream reasoning, text and tool calls, and can hold until a test
 * releases it, which is how a test looks at the screen while a run is still working.
 */
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";

export type Step = {
  reasoning?: string;
  text?: string;
  toolCalls?: { name: string; args: unknown }[];
  /** Wait until a test calls `release(label)` before answering. */
  hold?: string;
  delayMs?: number;
};
export type RecordedRequest = { model: string; messages: { role: string; content: unknown }[]; tools: string[] };
export type FakeModel = {
  url: string;
  script(steps: Step[]): Promise<void>;
  requests(): Promise<RecordedRequest[]>;
  release(label: string): Promise<void>;
  reset(): Promise<void>;
  close(): Promise<void>;
};

const MODEL_ID = "fake-model";
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function readJson(request: IncomingMessage): Promise<any> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(chunk as Buffer);
  const text = Buffer.concat(chunks).toString("utf8");
  return text ? JSON.parse(text) : {};
}

const send = (response: ServerResponse, status: number, body: unknown) => {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(body));
};

export async function startFakeModel(port: number, host = "127.0.0.1"): Promise<FakeModel> {
  let queue: Step[] = [];
  let recorded: RecordedRequest[] = [];
  const released = new Set<string>();
  const waiters = new Map<string, (() => void)[]>();
  let callNumber = 0;

  const hold = async (label: string) => {
    if (released.has(label)) return;
    await new Promise<void>((resolve) => waiters.set(label, [...(waiters.get(label) ?? []), resolve]));
  };
  const open = (label: string) => {
    released.add(label);
    for (const resolve of waiters.get(label) ?? []) resolve();
    waiters.delete(label);
  };

  async function streamCompletion(request: IncomingMessage, response: ServerResponse) {
    const body = await readJson(request);
    recorded.push({
      model: String(body.model ?? ""),
      messages: (body.messages ?? []).map((m: { role: string; content: unknown }) => ({ role: m.role, content: m.content })),
      tools: (body.tools ?? []).map((t: { function: { name: string } }) => t.function.name),
    });
    const step: Step = queue.shift() ?? { text: "(script exhausted: the test gave the model nothing more to say)" };
    if (step.hold) await hold(step.hold);
    if (step.delayMs) await sleep(step.delayMs);

    const id = `chatcmpl-${++callNumber}`;
    const frame = (delta: object, finish: string | null = null) =>
      response.write(`data: ${JSON.stringify({ id, object: "chat.completion.chunk", created: 0, model: MODEL_ID, choices: [{ index: 0, delta, finish_reason: finish }] })}\n\n`);
    response.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-store" });
    frame({ role: "assistant", content: "" });
    for (const piece of step.reasoning?.match(/\S+\s*|\s+/g) ?? []) frame({ reasoning_content: piece });
    for (const piece of step.text?.match(/\S+\s*|\s+/g) ?? []) frame({ content: piece });
    (step.toolCalls ?? []).forEach((call, index) =>
      frame({ tool_calls: [{ index, id: `call_${id}_${index}`, type: "function", function: { name: call.name, arguments: JSON.stringify(call.args) } }] }),
    );
    frame({}, step.toolCalls?.length ? "tool_calls" : "stop");
    response.write("data: [DONE]\n\n");
    response.end();
  }

  const server = createServer((request, response) => {
    const url = new URL(request.url ?? "/", "http://fake");
    const route = `${request.method} ${url.pathname}`;
    const run = async () => {
      if (route === "GET /api/v0/models") return send(response, 200, { data: [{ id: MODEL_ID, type: "llm", state: "loaded", loaded_context_length: 32768 }] });
      if (route === "GET /v1/models") return send(response, 200, { data: [{ id: MODEL_ID }] });
      if (route === "POST /v1/chat/completions") return streamCompletion(request, response);
      if (route === "POST /__script") {
        queue = ((await readJson(request)).steps ?? []) as Step[];
        return send(response, 200, { queued: queue.length });
      }
      if (route === "GET /__requests") return send(response, 200, { requests: recorded });
      if (route === "POST /__reset") {
        queue = [];
        recorded = [];
        for (const label of [...waiters.keys()]) open(label);
        released.clear();
        return send(response, 200, {});
      }
      const gate = /^POST \/__release\/(.+)$/.exec(route);
      if (gate) {
        open(decodeURIComponent(gate[1]));
        return send(response, 200, {});
      }
      send(response, 404, { error: `no route ${route}` });
    };
    run().catch((error) => {
      if (!response.headersSent) send(response, 500, { error: String(error) });
      else response.end();
    });
  });

  await new Promise<void>((resolve) => server.listen(port, host, resolve));
  const url = `http://${host}:${(server.address() as AddressInfo).port}`;
  const call = (path: string, init?: RequestInit) => fetch(`${url}${path}`, init);
  return {
    url,
    script: async (steps) => void (await call("/__script", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ steps }) })),
    requests: async () => ((await (await call("/__requests")).json()) as { requests: RecordedRequest[] }).requests,
    release: async (label) => void (await call(`/__release/${encodeURIComponent(label)}`, { method: "POST" })),
    reset: async () => void (await call("/__reset", { method: "POST" })),
    close: () => new Promise<void>((resolve) => (server.closeAllConnections(), server.close(() => resolve()))),
  };
}

/** The same controls for a fake model started elsewhere (by scripts/e2e-ui.sh), reached over HTTP. */
export function fakeModelClient(url: string): Omit<FakeModel, "close"> {
  const post = (path: string, body?: unknown) => fetch(`${url}${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  return {
    url,
    script: async (steps) => void (await post("/__script", { steps })),
    requests: async () => ((await (await fetch(`${url}/__requests`)).json()) as { requests: RecordedRequest[] }).requests,
    release: async (label) => void (await post(`/__release/${encodeURIComponent(label)}`)),
    reset: async () => void (await post("/__reset")),
  };
}
