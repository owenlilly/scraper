"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef } from "react";
import { useApi } from "../../components/workspace/ApiProvider";
import { ApiError } from "../api/client";
import type { SseFrame } from "../api/sse";
import type { RunView, WorkspaceState } from "../api/types";
import { createBatcher } from "./batch";
import { shouldKeepLive } from "./derive";
import { CONNECTION_LOST, liveReducer, type LiveAction, type LiveTurn } from "./live";
import { STATE_KEY, useRefreshState } from "./state";

export type SteerOutcome = "queued" | "refused";

const STEER_REFUSALS: Record<string, string> = {
  full: "The agent has not read your earlier messages yet. Wait a moment, then send this again.",
  too_long: "Keep follow-up messages under 2000 characters.",
  empty: "Write something to send.",
  no_run: "There is no run to send this to.",
};

const messageOf = (error: unknown) => (error instanceof ApiError ? error.message : "Something went wrong. Try again.");

/** Starts and follows the agent's runs and questions, feeding what streams back into one live turn. */
export function useRun(options: { onNotice: (text: string) => void; onDrafts: (drafts: string[]) => void }) {
  const api = useApi();
  const queries = useQueryClient();
  const refresh = useRefreshState();
  const [live, dispatch] = useReducer(liveReducer, null);
  const liveRef = useRef<LiveTurn | null>(null);
  liveRef.current = live;
  const abort = useRef<AbortController | null>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  useEffect(() => () => abort.current?.abort(), []); // leaving the page stops the run on the server, which keeps it resumable

  const stream = useCallback(
    /** Resolves to the server's reason when it refused to start the request at all, so the caller can put the person's text back. */
    async (path: string, body: unknown, begin: LiveAction): Promise<string | null> => {
      const controller = new AbortController();
      abort.current = controller;
      // `mirror` follows the same actions the screen gets, so what the turn ended as is known here without waiting for a render.
      let mirror = liveReducer(liveRef.current, begin);
      dispatch(begin);
      const batcher = createBatcher<LiveAction>((actions) => actions.forEach(dispatch));
      // While the run works, the results table and the page list follow along, a few times a second at most.
      let refreshTimer: ReturnType<typeof setTimeout> | null = null;
      const refreshSoon = () => {
        if (!refreshTimer) refreshTimer = setTimeout(() => ((refreshTimer = null), void refresh()), 400);
      };
      const apply = (action: LiveAction) => {
        mirror = liveReducer(mirror, action);
        batcher.push(action);
        if (action.type === "frame" && (action.event === "rows_added" || action.event === "tool_result")) refreshSoon();
      };
      let refused: string | null = null;
      let sawFrame = false;
      try {
        for await (const frame of api.stream(path, body, controller.signal) as AsyncIterable<SseFrame>) {
          sawFrame = true;
          apply({ type: "frame", event: frame.event, data: frame.data });
        }
      } catch (error) {
        if (controller.signal.aborted) return null;
        const broken = !(error instanceof ApiError); // the stream began and then broke, or could not be read
        if (!broken && !sawFrame) refused = messageOf(error);
        apply({ type: "failed", message: broken ? CONNECTION_LOST : messageOf(error), resumable: broken });
      }
      batcher.flush();
      if (refreshTimer) clearTimeout(refreshTimer);
      if (abort.current === controller) abort.current = null;
      if (controller.signal.aborted || !mirror) return null;
      if (refused && begin.type === "start" && !begin.from) {
        dispatch({ type: "clear" }); // nothing began, so nothing is left to show: the caller keeps the person's text and says why
        return refused;
      }

      const turn = mirror;
      await refresh();
      if (!shouldKeepLive(turn)) dispatch({ type: "clear" });
      if (turn.kind === "ask" && turn.status === "error" && turn.notice) optionsRef.current.onNotice(turn.notice);
      if (turn.drafts.length) optionsRef.current.onDrafts(turn.drafts);
      if (turn.leftovers.length && turn.status !== "paused") {
        // The run ended before the agent read these messages: they become a normal turn.
        const state = queries.getQueryData<WorkspaceState>(STATE_KEY);
        if (state?.model) void stream("/agent/run", { instruction: turn.leftovers.join("\n"), model: state.model }, { type: "start", kind: "agent", text: turn.leftovers.join("\n") });
      }
      return refused;
    },
    [api, queries, refresh],
  );

  const continuing = (run: RunView | null): LiveAction => {
    const current = liveRef.current;
    if (current && current.kind === "agent" && current.status !== "streaming") return { type: "resume" };
    return { type: "start", kind: "agent", text: run?.instruction ?? "", from: run ?? undefined };
  };

  return {
    live,
    start: (text: string, model: string, seedUrl?: string) =>
      stream("/agent/run", { instruction: text, model, seed_url: seedUrl || undefined }, { type: "start", kind: "agent", text }),
    clear: () => dispatch({ type: "clear" }),
    ask: (text: string, model: string) => stream("/ask", { question: text, model }, { type: "start", kind: "ask", text }),
    retry: (text: string, model: string) => stream("/ask/retry", { model }, { type: "start", kind: "ask", text }),
    answer: (value: string | { action?: string; note?: string; allow?: boolean }, run: RunView | null) => stream("/agent/resume", { value }, continuing(run)),
    proceed: (run: RunView | null) => stream("/agent/proceed", undefined, continuing(run)),
    async stop() {
      try {
        await api.post("/agent/stop");
      } catch (error) {
        optionsRef.current.onNotice(messageOf(error));
      }
    },
    async discard() {
      try {
        const { drafts } = await api.post<{ drafts: string[] }>("/agent/discard");
        dispatch({ type: "clear" });
        await refresh();
        if (drafts.length) optionsRef.current.onDrafts(drafts);
      } catch (error) {
        optionsRef.current.onNotice(messageOf(error));
      }
    },
    async steer(text: string): Promise<SteerOutcome> {
      try {
        const result = await api.post<{ status: string; id: string }>("/agent/steer", { text });
        if (result.status !== "queued") {
          optionsRef.current.onNotice(STEER_REFUSALS[result.status] ?? "That message could not be sent.");
          return "refused";
        }
        if (liveRef.current) dispatch({ type: "queued", id: result.id, text });
        else await refresh(); // a run this screen is not streaming: the saved state lists the message as waiting
        return "queued";
      } catch (error) {
        optionsRef.current.onNotice(messageOf(error));
        return "refused";
      }
    },
  };
}

export type RunController = ReturnType<typeof useRun>;
