import { parseSse, type SseFrame } from "./sse";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly extra: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type Tokens = { get(): Promise<string>; invalidate(): void };
type Send = { method: string; body?: unknown; accept?: string; signal?: AbortSignal };

export type ApiClient = {
  get<T>(path: string): Promise<T>;
  post<T = void>(path: string, body?: unknown, signal?: AbortSignal): Promise<T>;
  put<T = void>(path: string, body?: unknown): Promise<T>;
  stream(path: string, body: unknown, signal?: AbortSignal): AsyncGenerator<SseFrame>;
  download(path: string): Promise<Blob>;
};

async function toError(response: Response): Promise<ApiError> {
  try {
    const { error } = (await response.json()) as { error?: { code?: string; message?: string; [key: string]: unknown } };
    if (error?.message) {
      const { code, message, ...extra } = error;
      return new ApiError(response.status, code ?? "error", message, extra);
    }
  } catch {
    // not JSON: fall through, and never show whatever it was
  }
  return new ApiError(response.status, "server_error", "The server had a problem. Try again in a moment.");
}

export function createApiClient(options: { baseUrl: string; tokens: Tokens; fetchImpl?: typeof fetch }): ApiClient {
  const { baseUrl, tokens, fetchImpl = fetch } = options;

  async function send(path: string, { method, body, accept, signal }: Send): Promise<Response> {
    for (let attempt = 0; ; attempt++) {
      const token = await tokens.get();
      let response: Response;
      try {
        response = await fetchImpl(`${baseUrl}${path}`, {
          method,
          headers: { authorization: `Bearer ${token}`, ...(body === undefined ? {} : { "content-type": "application/json" }), ...(accept ? { accept } : {}) },
          body: body === undefined ? undefined : JSON.stringify(body),
          signal,
        });
      } catch (error) {
        if (signal?.aborted) throw error; // the caller stopped it on purpose
        throw new ApiError(0, "network", "Could not reach the server. Check your connection and try again.");
      }
      if (response.status === 401 && attempt === 0) {
        tokens.invalidate(); // the token may simply have expired: get a fresh one and try once more
        continue;
      }
      if (!response.ok) throw await toError(response);
      return response;
    }
  }

  const json = async <T>(response: Response): Promise<T> => (response.status === 204 ? (undefined as T) : ((await response.json()) as T));

  return {
    get: async (path) => json(await send(path, { method: "GET" })),
    post: async (path, body, signal) => json(await send(path, { method: "POST", body, signal })),
    put: async (path, body) => json(await send(path, { method: "PUT", body })),
    async *stream(path, body, signal) {
      const response = await send(path, { method: "POST", body, accept: "text/event-stream", signal });
      if (!response.body) throw new ApiError(response.status, "server_error", "The server had a problem. Try again in a moment.");
      yield* parseSse(response.body);
    },
    download: async (path) => (await send(path, { method: "GET" })).blob(),
  };
}
