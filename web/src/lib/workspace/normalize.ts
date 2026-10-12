import type { WorkspaceState } from "../api/types";

/** The API and the web app are deployed separately and can be briefly out of step. Fill in what a slightly older API leaves out. */
export function normalizeState(state: WorkspaceState): WorkspaceState {
  return { ...state, providers: state.providers ?? [state.provider], browser_available: state.browser_available ?? false };
}
