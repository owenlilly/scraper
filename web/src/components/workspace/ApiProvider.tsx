"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createContext, useContext, useState, type ReactNode } from "react";
import { authClient } from "../../lib/auth-client";
import { ApiError, createApiClient, type ApiClient } from "../../lib/api/client";
import { createTokenCache } from "../../lib/api/token";

const ApiContext = createContext<ApiClient | null>(null);

/** The code of the error thrown when Better Auth will not issue a token: only this one means "go back to sign in". */
export const SESSION_ENDED = "session_ended";

async function fetchToken(): Promise<string> {
  const { data, error } = await authClient.token();
  if (error || !data?.token) throw new ApiError(401, SESSION_ENDED, "Your session has ended. Sign in again.");
  return data.token;
}

/** Gives every part of the workspace the API client and the query cache. `apiUrl` comes from the server, so no build-time setting is needed. */
export function WorkspaceProviders({ apiUrl, children }: { apiUrl: string; children: ReactNode }) {
  const [api] = useState(() => createApiClient({ baseUrl: apiUrl, tokens: createTokenCache({ fetchToken }) }));
  const [queries] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: true, staleTime: 0 } } }));
  return (
    <ApiContext.Provider value={api}>
      <QueryClientProvider client={queries}>{children}</QueryClientProvider>
    </ApiContext.Provider>
  );
}

export function useApi(): ApiClient {
  const api = useContext(ApiContext);
  if (!api) throw new Error("useApi needs WorkspaceProviders around it");
  return api;
}
