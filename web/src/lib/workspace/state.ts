"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { useApi } from "../../components/workspace/ApiProvider";
import type { WorkspaceState } from "../api/types";
import { normalizeState } from "./normalize";

export const STATE_KEY = ["state"] as const;

export function useWorkspaceState() {
  const api = useApi();
  return useQuery({ queryKey: STATE_KEY, queryFn: () => api.get<WorkspaceState>("/state"), select: normalizeState });
}

/** Ask the server for the state again, and resolve when it has arrived. */
export function useRefreshState() {
  const queries = useQueryClient();
  return useCallback(() => queries.invalidateQueries({ queryKey: STATE_KEY }), [queries]);
}
