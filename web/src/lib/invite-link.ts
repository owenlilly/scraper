/**
 * The address in an invitation email. The token and the person's email go after the `#`, so they are never sent to a server:
 * they stay out of request logs and out of `Referer` headers, and only the sign-up page's own script reads them.
 */

const TOKEN_SHAPE = /^[A-Za-z0-9_-]{16,128}$/; // what 32 random bytes encoded as base64url look like

export function buildInviteLink(baseUrl: string, token: string, email: string): string {
  return `${baseUrl}/sign-up#invite=${encodeURIComponent(token)}&email=${encodeURIComponent(email)}`;
}

export function parseInviteHash(hash: string): { invite: string; email: string } | null {
  const params = new URLSearchParams(hash.startsWith("#") ? hash.slice(1) : hash);
  const invite = params.get("invite") ?? "";
  const email = params.get("email") ?? "";
  if (!TOKEN_SHAPE.test(invite) || email.length > 254 || !email.includes("@")) return null;
  return { invite, email };
}
