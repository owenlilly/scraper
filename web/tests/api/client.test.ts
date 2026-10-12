import { describe, expect, it, vi } from "vitest";
import { ApiError, createApiClient } from "../../src/lib/api/client";

const BASE = "https://api.test";
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const stream = (text: string) => new Response(text, { status: 200, headers: { "content-type": "text/event-stream" } });

function setup(responses: (Response | Error)[]) {
  const calls: { url: string; init: RequestInit & { headers: Record<string, string> } }[] = [];
  const fetchImpl = vi.fn(async (url: string, init: RequestInit & { headers: Record<string, string> }) => {
    calls.push({ url, init });
    const next = responses.shift();
    if (next instanceof Error) throw next;
    return next ?? json(500, {});
  });
  let issued = 0;
  const tokens = { get: vi.fn(async () => `token-${++issued}`), invalidate: vi.fn() };
  return { api: createApiClient({ baseUrl: BASE, tokens, fetchImpl: fetchImpl as unknown as typeof fetch }), calls, tokens, fetchImpl };
}

describe("createApiClient", () => {
  it("sends the token in the Authorization header and returns the JSON", async () => {
    const { api, calls } = setup([json(200, { ok: 1 })]);
    expect(await api.get<{ ok: number }>("/state")).toEqual({ ok: 1 });
    expect(calls[0].url).toBe(`${BASE}/state`);
    expect(calls[0].init.headers.authorization).toBe("Bearer token-1");
  });

  it("sends a JSON body with POST and PUT", async () => {
    const { api, calls } = setup([json(200, {}), new Response(null, { status: 204 })]);
    await api.post("/scrape", { url: "https://a.test" });
    await api.put("/settings/url", { value: "x" });
    expect(calls[0].init.method).toBe("POST");
    expect(calls[0].init.body).toBe('{"url":"https://a.test"}');
    expect(calls[0].init.headers["content-type"]).toBe("application/json");
    expect(calls[1].init.method).toBe("PUT");
  });

  it("returns undefined for an answer with no body", async () => {
    const { api } = setup([new Response(null, { status: 204 })]);
    expect(await api.post("/session/new")).toBeUndefined();
  });

  it("asks for a fresh token and tries once more when the first is refused", async () => {
    const { api, calls, tokens } = setup([json(401, { error: { code: "unauthorized", message: "expired" } }), json(200, { ok: 1 })]);
    expect(await api.get("/state")).toEqual({ ok: 1 });
    expect(tokens.invalidate).toHaveBeenCalledTimes(1);
    expect(calls.map((c) => c.init.headers.authorization)).toEqual(["Bearer token-1", "Bearer token-2"]);
  });

  it("gives up after one retry", async () => {
    const { api, calls } = setup([json(401, { error: { code: "unauthorized", message: "no" } }), json(401, { error: { code: "unauthorized", message: "still no" } })]);
    await expect(api.get("/state")).rejects.toMatchObject({ status: 401, code: "unauthorized", message: "still no" });
    expect(calls).toHaveLength(2);
  });

  it("turns the API's error body into an ApiError with its code and message", async () => {
    const { api } = setup([json(429, { error: { code: "limit", message: "Daily page limit reached (300 a day)." } })]);
    const error = await api.get("/state").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 429, code: "limit", message: "Daily page limit reached (300 a day)." });
  });

  it("keeps the extra detail the API sends, such as a fetch hint", async () => {
    const { api } = setup([json(400, { error: { code: "fetch_failed", message: "HTTP 403", hint: "Try stealthy mode.", retry_mode: "stealthy" } })]);
    const error = (await api.post("/scrape", {}).catch((e) => e)) as ApiError;
    expect(error.extra).toMatchObject({ hint: "Try stealthy mode.", retry_mode: "stealthy" });
  });

  it("copes with an error that is not JSON", async () => {
    const { api } = setup([new Response("<html>Bad gateway</html>", { status: 502 })]);
    const error = (await api.get("/state").catch((e) => e)) as ApiError;
    expect(error).toMatchObject({ status: 502, code: "server_error" });
    expect(error.message).not.toContain("<html>");
  });

  it("says plainly when the server cannot be reached", async () => {
    const { api } = setup([new TypeError("fetch failed")]);
    const error = (await api.get("/state").catch((e) => e)) as ApiError;
    expect(error).toMatchObject({ status: 0, code: "network" });
    expect(error.message).toMatch(/could not reach/i);
  });

  it("does not retry a refused request that never reached the server", async () => {
    const { api, fetchImpl } = setup([new TypeError("fetch failed")]);
    await api.get("/state").catch(() => undefined);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("streams events from a POST", async () => {
    const { api, calls } = setup([stream('event: answer\ndata: {"kind":"answer","text":"Hi"}\n\nevent: end\ndata: {"drafts":[]}\n\n')]);
    const frames = [];
    for await (const frame of api.stream("/agent/run", { instruction: "go", model: "m" })) frames.push(frame);
    expect(frames.map((f) => f.event)).toEqual(["answer", "end"]);
    expect(calls[0].init.method).toBe("POST");
    expect(calls[0].init.headers.accept).toBe("text/event-stream");
    expect(calls[0].init.body).toBe('{"instruction":"go","model":"m"}');
  });

  it("throws before streaming anything when the API refuses to start the stream", async () => {
    const { api } = setup([json(409, { error: { code: "busy", message: "Another run is in progress." } })]);
    const frames: unknown[] = [];
    const attempt = (async () => {
      for await (const frame of api.stream("/ask", { question: "q", model: "m" })) frames.push(frame);
    })();
    await expect(attempt).rejects.toMatchObject({ status: 409, code: "busy" });
    expect(frames).toEqual([]);
  });

  it("passes the abort signal to the request, so leaving the page stops the stream", async () => {
    const controller = new AbortController();
    const { api, calls } = setup([stream("")]);
    for await (const _frame of api.stream("/ask", { question: "q" }, controller.signal)) void _frame;
    expect(calls[0].init.signal).toBe(controller.signal);
  });

  it("downloads a file with the token", async () => {
    const { api, calls } = setup([new Response("plan,price\nSolo,$9\n", { status: 200, headers: { "content-type": "text/csv" } })]);
    const blob = await api.download("/rows.csv");
    expect(await blob.text()).toBe("plan,price\nSolo,$9\n");
    expect(calls[0].init.headers.authorization).toBe("Bearer token-1");
  });
});
