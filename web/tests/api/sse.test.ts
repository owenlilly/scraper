import { describe, expect, it } from "vitest";
import { parseSse, SseFormatError, type SseFrame } from "../../src/lib/api/sse";

const streamOf = (...chunks: (string | Uint8Array)[]) =>
  new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(typeof chunk === "string" ? new TextEncoder().encode(chunk) : chunk);
      controller.close();
    },
  });

async function collect(stream: ReadableStream<Uint8Array>): Promise<SseFrame[]> {
  const frames: SseFrame[] = [];
  for await (const frame of parseSse(stream)) frames.push(frame);
  return frames;
}

describe("parseSse", () => {
  it("reads a frame's event name and JSON data", async () => {
    const frames = await collect(streamOf('event: answer\ndata: {"kind":"answer","text":"Hi"}\n\n'));
    expect(frames).toEqual([{ event: "answer", data: { kind: "answer", text: "Hi" } }]);
  });

  it("reads several frames, in order, from one chunk", async () => {
    const frames = await collect(streamOf('event: a\ndata: {"n":1}\n\nevent: b\ndata: {"n":2}\n\nevent: end\ndata: {}\n\n'));
    expect(frames.map((f) => f.event)).toEqual(["a", "b", "end"]);
  });

  it("puts a frame back together when a chunk ends in the middle of it", async () => {
    const frames = await collect(streamOf("event: answer\nda", 'ta: {"text":"split', ' here"}\n', "\nevent: end\ndata: {}\n\n"));
    expect(frames).toEqual([
      { event: "answer", data: { text: "split here" } },
      { event: "end", data: {} },
    ]);
  });

  it("puts a character back together when a chunk ends in the middle of it", async () => {
    const bytes = new TextEncoder().encode('event: answer\ndata: {"text":"café 🔑"}\n\n');
    const cut = bytes.indexOf(0xc3) + 1; // between the two bytes of é
    const frames = await collect(streamOf(bytes.slice(0, cut), bytes.slice(cut)));
    expect(frames[0].data).toEqual({ text: "café 🔑" });
  });

  it("accepts Windows line endings", async () => {
    const frames = await collect(streamOf('event: answer\r\ndata: {"text":"x"}\r\n\r\n'));
    expect(frames).toEqual([{ event: "answer", data: { text: "x" } }]);
  });

  it("ignores comment lines and unknown fields, and treats a missing event name as 'message'", async () => {
    const frames = await collect(streamOf(': keep-alive\n\nid: 7\nretry: 100\ndata: {"a":1}\n\n'));
    expect(frames).toEqual([{ event: "message", data: { a: 1 } }]);
  });

  it("joins data split over several lines", async () => {
    const frames = await collect(streamOf('event: x\ndata: {"a":\ndata: 1}\n\n'));
    expect(frames[0].data).toEqual({ a: 1 });
  });

  it("drops a half frame left when the stream is cut off", async () => {
    const frames = await collect(streamOf('event: end\ndata: {}\n\nevent: answer\ndata: {"te'));
    expect(frames.map((f) => f.event)).toEqual(["end"]);
  });

  it("refuses data that is not JSON, rather than guessing", async () => {
    await expect(collect(streamOf("event: answer\ndata: not json\n\n"))).rejects.toBeInstanceOf(SseFormatError);
  });

  it("gives nothing for an empty stream", async () => {
    expect(await collect(streamOf())).toEqual([]);
  });

  it("stops reading the stream when the consumer stops early", async () => {
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.enqueue(new TextEncoder().encode('event: a\ndata: {"n":1}\n\n'));
      },
      cancel() {
        cancelled = true;
      },
    });
    for await (const frame of parseSse(stream)) {
      expect(frame.event).toBe("a");
      break;
    }
    expect(cancelled).toBe(true);
  });
});
