/** Keeps the short-lived token for the Python API, and asks for a new one a minute before it runs out. */

export type TokenCache = { get(): Promise<string>; invalidate(): void };

/** The `exp` claim of a token in milliseconds, or null if it cannot be read. The signature is not checked: the API does that. */
function expiryOf(token: string): number | null {
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))) as { exp?: unknown };
    return typeof payload.exp === "number" ? payload.exp * 1000 : null;
  } catch {
    return null;
  }
}

export function createTokenCache(options: { fetchToken: () => Promise<string>; now?: () => number; skewMs?: number }): TokenCache {
  const { fetchToken, now = Date.now, skewMs = 60_000 } = options;
  let cached: { token: string; expiresAt: number } | null = null;
  let inflight: Promise<string> | null = null;
  let generation = 0; // bumped by invalidate(), so a token fetched before it is not kept

  return {
    async get() {
      if (cached && cached.expiresAt - skewMs > now()) return cached.token;
      if (!inflight) {
        const mine = generation;
        const request = fetchToken()
          .then((token) => {
            const expiresAt = expiryOf(token);
            if (expiresAt !== null && mine === generation) cached = { token, expiresAt };
            return token;
          })
          .finally(() => {
            if (inflight === request) inflight = null;
          });
        inflight = request;
      }
      return inflight;
    },
    invalidate() {
      cached = null;
      inflight = null;
      generation += 1;
    },
  };
}
