/** Reads a server-sent event stream (`event:` and `data:` lines, a blank line between frames) from a fetch response body. */

export type SseFrame = { event: string; data: unknown };

export class SseFormatError extends Error {
  constructor() {
    super("The server sent something that could not be read.");
  }
}

function parseFrame(raw: string): SseFrame | null {
  let event = "message";
  const data: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith(":")) continue; // a comment, such as a keep-alive
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  if (data.length === 0) return null;
  try {
    return { event, data: JSON.parse(data.join("\n")) };
  } catch {
    throw new SseFormatError();
  }
}

export async function* parseSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseFrame> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
      for (let end = buffer.indexOf("\n\n"); end >= 0; end = buffer.indexOf("\n\n")) {
        const frame = parseFrame(buffer.slice(0, end));
        buffer = buffer.slice(end + 2);
        if (frame) yield frame;
      }
    }
    // Whatever is left is a frame the stream was cut off in the middle of: drop it rather than act on half a message.
  } finally {
    await reader.cancel().catch(() => undefined); // also runs when the consumer stops early, which closes the connection
  }
}
