"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { ApiError } from "../../lib/api/client";
import { pollFeedEvery, pollRunEvery, pollRunsEvery, pollSourcesEvery } from "../../lib/spiders/derive";
import type { FetchRow, Run, SettingsView, SourceSummary } from "../../lib/spiders/types";
import { useApi } from "../workspace/ApiProvider";

const FETCH_PAGE = 200; // the API's page size: a full page means there may be more

const keys = {
  sources: ["pipeline", "sources"] as const,
  runs: (name: string) => ["pipeline", "runs", name] as const,
  run: (id: number | null) => ["pipeline", "run", id] as const,
  fetches: (id: number | null) => ["pipeline", "fetches", id] as const,
  settings: (name: string) => ["pipeline", "settings", name] as const,
};

export const messageOf = (error: unknown): string => (error instanceof ApiError ? error.message : "Something went wrong. Try again.");

export function useSources() {
  const api = useApi();
  return useQuery({
    queryKey: keys.sources,
    queryFn: async () => (await api.get<{ sources: SourceSummary[] }>("/pipeline/sources")).sources,
    refetchInterval: (query) => pollSourcesEvery(query.state.data),
  });
}

export function useRuns(name: string) {
  const api = useApi();
  return useQuery({
    queryKey: keys.runs(name),
    queryFn: async () => (await api.get<{ runs: Run[] }>(`/pipeline/sources/${encodeURIComponent(name)}/runs`)).runs,
    refetchInterval: (query) => pollRunsEvery(query.state.data),
  });
}

/** One run in full (with its trial items and rejected samples), refreshed every two seconds while it is in flight. */
export function useRun(id: number | null) {
  const api = useApi();
  return useQuery({
    queryKey: keys.run(id),
    enabled: id !== null,
    queryFn: () => api.get<Run>(`/pipeline/runs/${id}`),
    refetchInterval: (query) => pollRunEvery(query.state.data),
  });
}

/** The run's fetched pages, oldest first. Each poll asks only for what came after the last one it has. */
export function useFetchFeed(runId: number | null, active: boolean) {
  const api = useApi();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: keys.fetches(runId),
    enabled: runId !== null,
    queryFn: async () => {
      let known = client.getQueryData<FetchRow[]>(keys.fetches(runId)) ?? [];
      for (let page = 0; page < 10; page++) {
        const after = known.length ? known[known.length - 1].id : 0;
        const { fetches } = await api.get<{ fetches: FetchRow[] }>(`/pipeline/runs/${runId}/fetches?after=${after}`);
        if (fetches.length) known = [...known, ...fetches];
        if (fetches.length < FETCH_PAGE) break;
      }
      return known;
    },
    refetchInterval: pollFeedEvery(active),
  });
  const wasActive = useRef(active);
  const { refetch } = query;
  useEffect(() => {
    if (wasActive.current && !active) void refetch(); // the run just ended: fetch the pages that arrived after the last poll
    wasActive.current = active;
  }, [active, refetch]);
  return query;
}

/** One spider's saved settings, with the description of its form. Not polled: it changes only when someone saves. */
export function useSettings(name: string) {
  const api = useApi();
  return useQuery({
    queryKey: keys.settings(name),
    queryFn: () => api.get<SettingsView>(`/pipeline/sources/${encodeURIComponent(name)}/settings`),
  });
}

/** Save the whole settings object. On success the saved view replaces the cached one and the lists refresh (the location picker reads them). */
export function useSaveSettings(name: string, onSaved: () => void, onError: (message: string) => void) {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (settings: Record<string, unknown>) => api.put<SettingsView>(`/pipeline/sources/${encodeURIComponent(name)}/settings`, settings),
    onSuccess: (view) => {
      client.setQueryData(keys.settings(name), view);
      void client.invalidateQueries({ queryKey: ["pipeline", "sources"] });
      onSaved();
    },
    onError: (error) => onError(messageOf(error)),
  });
}

/** Start, stop and retry. Each refreshes the lists, and `onError` gets the API's own sentence (for a toast). */
export function useRunActions(onError: (message: string) => void, onStarted?: (run: Run) => void) {
  const api = useApi();
  const client = useQueryClient();
  const refresh = () => client.invalidateQueries({ queryKey: ["pipeline"] });
  const settle = { onSuccess: refresh, onError: (error: unknown) => onError(messageOf(error)) };
  const start = useMutation({
    mutationFn: ({ name, kind, maxRequests, location }: { name: string; kind: "live" | "trial"; maxRequests?: number; location?: string }) =>
      api.post<Run>(`/pipeline/sources/${encodeURIComponent(name)}/runs`, {
        kind,
        ...(kind === "trial" && maxRequests ? { max_requests: maxRequests } : {}),
        ...(location ? { location } : {}),
      }),
    onSuccess: (run) => {
      void refresh();
      onStarted?.(run);
    },
    onError: settle.onError,
  });
  const stop = useMutation({ mutationFn: (runId: number) => api.post<Run>(`/pipeline/runs/${runId}/stop`), ...settle });
  const retry = useMutation({
    mutationFn: (runId: number) => api.post<Run>(`/pipeline/runs/${runId}/retry`),
    onSuccess: (run) => {
      void refresh();
      onStarted?.(run);
    },
    onError: settle.onError,
  });
  return { start, stop, retry };
}
