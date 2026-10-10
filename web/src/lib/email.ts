/** Sending email. One function type, so Resend can be swapped for anything else by writing another sender. */

export type EmailMessage = { to: string; subject: string; text: string; html: string };
export type EmailSender = (message: EmailMessage) => Promise<void>;

export class EmailError extends Error {
  constructor(public readonly status: number) {
    super(`The email service answered ${status}`); // never the response body: it can repeat the message
  }
}

const RESEND_URL = "https://api.resend.com/emails";

export function resendSender(options: { apiKey: string; from: string; fetchImpl?: typeof fetch; timeoutMs?: number }): EmailSender {
  const { apiKey, from, fetchImpl = fetch, timeoutMs = 10_000 } = options;
  return async (message) => {
    let response: Response;
    try {
      response = await fetchImpl(RESEND_URL, {
        method: "POST",
        headers: { authorization: `Bearer ${apiKey}`, "content-type": "application/json" },
        body: JSON.stringify({ from, to: [message.to], subject: message.subject, text: message.text, html: message.html }),
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch {
      throw new EmailError(0); // could not reach the service, or it took too long
    }
    if (!response.ok) throw new EmailError(response.status);
  };
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
