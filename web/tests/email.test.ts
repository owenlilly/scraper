import { describe, expect, it, vi } from "vitest";
import { EmailError, inviteMessage, logSender, resendSender, resetMessage, verifyMessage } from "../src/lib/email";

const MESSAGE = { to: "a@x.test", subject: "Hi", text: "plain", html: "<p>rich</p>" };

describe("resendSender", () => {
  it("posts the message to Resend with the key and the sender address", async () => {
    const fetchImpl = vi.fn(async () => new Response("{}", { status: 200 }));
    await resendSender({ apiKey: "re_key", from: "Page Chat <no-reply@x.test>", fetchImpl })(MESSAGE);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.resend.com/emails");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer re_key");
    expect(JSON.parse(String(init.body))).toEqual({ from: "Page Chat <no-reply@x.test>", to: ["a@x.test"], subject: "Hi", text: "plain", html: "<p>rich</p>" });
  });

  it("reports the status, not the body, when Resend refuses", async () => {
    const fetchImpl = vi.fn(async () => new Response("secret detail a@x.test", { status: 422 }));
    const send = resendSender({ apiKey: "k", from: "f", fetchImpl });
    const error = await send(MESSAGE).catch((e) => e);
    expect(error).toBeInstanceOf(EmailError);
    expect(error.status).toBe(422);
    expect(String(error)).not.toContain("secret detail");
  });

  it("turns a network failure into an EmailError with status 0", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    await expect(resendSender({ apiKey: "k", from: "f", fetchImpl })(MESSAGE)).rejects.toMatchObject({ status: 0 });
  });

  it("gives up after the timeout", async () => {
    const fetchImpl = vi.fn((_url: unknown, init?: RequestInit) => new Promise<Response>((_resolve, reject) => init?.signal?.addEventListener("abort", () => reject(new Error("aborted")))));
    await expect(resendSender({ apiKey: "k", from: "f", fetchImpl: fetchImpl as unknown as typeof fetch, timeoutMs: 20 })(MESSAGE)).rejects.toMatchObject({ status: 0 });
  });
});

describe("logSender", () => {
  it("prints the recipient, subject and link text", async () => {
    const lines: string[] = [];
    await logSender((line) => lines.push(line))(MESSAGE);
    expect(lines[0]).toContain("a@x.test");
    expect(lines[0]).toContain("plain");
  });
});

describe("message templates", () => {
  const url = "https://app.test/sign-up#invite=tok&email=a%40x.test";

  it("put the link in both the text and the html", () => {
    for (const message of [inviteMessage({ to: "a@x.test", url, ttlDays: 7 }), verifyMessage({ to: "a@x.test", url }), resetMessage({ to: "a@x.test", url })]) {
      expect(message.to).toBe("a@x.test");
      expect(message.text).toContain(url);
      expect(message.html).toContain("https://app.test/sign-up#invite=tok&amp;email=a%40x.test"); // escaped for html
    }
  });

  it("states how long an invitation lasts", () => {
    expect(inviteMessage({ to: "a@x.test", url, ttlDays: 7 }).text).toContain("7 days");
  });

  it("escape anything that could break out of the html", () => {
    const hostile = 'https://x.test/"><script>alert(1)</script>';
    const message = verifyMessage({ to: "a@x.test", url: hostile });
    expect(message.html).not.toContain("<script>");
    expect(message.html).toContain("&lt;script&gt;");
  });
});
