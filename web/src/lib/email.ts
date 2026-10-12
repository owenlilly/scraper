/** Sending email. One function type, so Resend or Mailpit can be swapped for anything else by writing another sender. */

export type EmailMessage = { to: string; subject: string; text: string; html: string };
export type EmailSender = (message: EmailMessage) => Promise<void>;

export class EmailError extends Error {
  constructor(public readonly status: number) {
    super(`The email service answered ${status}`); // never the response body: it can repeat the message
  }
}

const RESEND_URL = "https://api.resend.com/emails";

/** Split `Name <a@b.test>` into its parts; a bare address has no name. */
function parseAddress(value: string): { Email: string; Name?: string } {
  const match = /^\s*(.*?)\s*<([^<>]+)>\s*$/.exec(value);
  return match ? { Email: match[2].trim(), ...(match[1] ? { Name: match[1].replace(/^"|"$/g, "") } : {}) } : { Email: value.trim() };
}

/** POST a JSON body with a time limit. Any failure to get an answer, or any answer that is not a success, becomes an EmailError. */
async function postJson(fetchImpl: typeof fetch, url: string, headers: Record<string, string>, body: unknown, timeoutMs: number): Promise<void> {
  let response: Response;
  try {
    response = await fetchImpl(url, {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch {
    throw new EmailError(0); // could not reach the service, or it took too long
  }
  if (!response.ok) throw new EmailError(response.status);
}

export function resendSender(options: { apiKey: string; from: string; fetchImpl?: typeof fetch; timeoutMs?: number }): EmailSender {
  const { apiKey, from, fetchImpl = fetch, timeoutMs = 10_000 } = options;
  return (message) =>
    postJson(fetchImpl, RESEND_URL, { authorization: `Bearer ${apiKey}` }, { from, to: [message.to], subject: message.subject, text: message.text, html: message.html }, timeoutMs);
}

/**
 * Development only: hand the message to Mailpit (https://mailpit.axllent.org), which keeps it in a web inbox and sends nothing
 * on. Uses Mailpit's HTTP API, so no mail library is needed. `url` is Mailpit's web address, port 8025 by default.
 */
export function mailpitSender(options: { url: string; from: string; fetchImpl?: typeof fetch; timeoutMs?: number }): EmailSender {
  const { url, from, fetchImpl = fetch, timeoutMs = 5_000 } = options;
  return (message) =>
    postJson(fetchImpl, `${url}/api/v1/send`, {}, { From: parseAddress(from), To: [{ Email: message.to }], Subject: message.subject, Text: message.text, HTML: message.html }, timeoutMs);
}

/** Development only: print the message instead of sending it, so a sign-up can be tried without a Resend account. */
export function logSender(log: (line: string) => void = console.log): EmailSender {
  return async (message) => {
    log(`[email to ${message.to}] ${message.subject}\n${message.text}`);
  };
}

const escapeHtml = (value: string) =>
  value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

function layout(heading: string, intro: string, linkText: string, url: string, outro: string): { text: string; html: string } {
  const text = `${heading}\n\n${intro}\n\n${url}\n\n${outro}\n`;
  const html =
    `<div style="font-family:system-ui,sans-serif;max-width:32rem;margin:0 auto;line-height:1.5">` +
    `<h1 style="font-size:1.25rem">${escapeHtml(heading)}</h1><p>${escapeHtml(intro)}</p>` +
    `<p><a href="${escapeHtml(url)}" style="display:inline-block;padding:.6rem 1rem;background:#1b2321;color:#fff;text-decoration:none;border-radius:6px">${escapeHtml(linkText)}</a></p>` +
    `<p style="color:#555;font-size:.875rem">If the button does not work, paste this address into your browser:<br>${escapeHtml(url)}</p>` +
    `<p style="color:#555;font-size:.875rem">${escapeHtml(outro)}</p></div>`;
  return { text, html };
}

export function inviteMessage(options: { to: string; url: string; ttlDays: number }): EmailMessage {
  const body = layout(
    "You are invited to Page Chat",
    "Someone has invited you to use Page Chat. Choose a password to create your account.",
    "Create your account",
    options.url,
    `This invitation works once, for this email address, and expires in ${options.ttlDays} days. If you did not expect it, ignore this message.`,
  );
  return { to: options.to, subject: "You are invited to Page Chat", ...body };
}

export function verifyMessage(options: { to: string; url: string }): EmailMessage {
  const body = layout(
    "Confirm your email address",
    "Confirm this address to finish creating your Page Chat account.",
    "Confirm email",
    options.url,
    "If you did not create an account, ignore this message.",
  );
  return { to: options.to, subject: "Confirm your email address", ...body };
}

export function resetMessage(options: { to: string; url: string }): EmailMessage {
  const body = layout(
    "Reset your password",
    "Use the link below to choose a new password for your Page Chat account.",
    "Choose a new password",
    options.url,
    "The link works once and expires soon. If you did not ask for this, ignore this message and your password stays as it is.",
  );
  return { to: options.to, subject: "Reset your password", ...body };
}
