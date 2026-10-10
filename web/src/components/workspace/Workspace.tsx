"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../../lib/api/client";
import { deriveThread } from "../../lib/workspace/derive";
import { useWorkspaceState, useRefreshState } from "../../lib/workspace/state";
import type { Person } from "./AccountMenu";
import { useApi } from "./ApiProvider";
import { Dialog } from "../Dialog";
import { ResultsPane } from "./ResultsPane";
import { EmptyThread, Thread } from "./Thread";
import { TopBar } from "./TopBar";
import "./workspace.css";

export function Workspace({ person }: { person: Person }) {
  const api = useApi();
  const router = useRouter();
  const state = useWorkspaceState();
  const refresh = useRefreshState();
  const [tab, setTab] = useState<"chat" | "results">("chat");
  const [confirmNew, setConfirmNew] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (state.error instanceof ApiError && state.error.status === 401) router.replace("/sign-in");
  }, [state.error, router]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 3200);
    return () => clearTimeout(timer);
  }, [toast]);

  const data = state.data;
  const thread = useMemo(() => (data ? deriveThread(data, null) : []), [data]);

  if (!data) {
    return (
      <div className="page">
        <TopBar person={person} usage={null} canReset={false} onNewSession={() => {}} />
        <div className="work-status" role={state.error ? "alert" : "status"}>
          {state.error ? (
            <>
              <p>{state.error instanceof ApiError ? state.error.message : "Could not load your workspace."}</p>
              <button className="btn" type="button" onClick={() => state.refetch()}>
                Try again
              </button>
            </>
          ) : (
            <p>Loading your workspace</p>
          )}
        </div>
      </div>
    );
  }

  async function download(kind: "csv" | "json") {
    try {
      const blob = await api.download(`/rows.${kind}`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `results.${kind}`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setToast(`Downloaded results.${kind}`);
    } catch (error) {
      setToast(error instanceof ApiError ? error.message : "The download failed. Try again.");
    }
  }

  async function startNewSession() {
    setConfirmNew(false);
    try {
      await api.post("/session/new");
      await refresh();
      setTab("chat");
    } catch (error) {
      setToast(error instanceof ApiError ? error.message : "Could not start a new session. Try again.");
    }
  }

  const rowCount = data.rows.length;
  return (
    <div className="page workspace">
      <TopBar person={person} usage={data.usage} canReset={!data.busy} onNewSession={() => setConfirmNew(true)} />
      <div className="work" data-tab={tab}>
        <div className="tabs" role="tablist" aria-label="Workspace">
          <button type="button" role="tab" aria-selected={tab === "chat"} onClick={() => setTab("chat")}>
            Chat
          </button>
          <button type="button" role="tab" aria-selected={tab === "results"} onClick={() => setTab("results")}>
            Results ({rowCount})
          </button>
        </div>
        <div className="pane chat">
          <div className="thread" aria-live="polite">
            {thread.length === 0 ? <EmptyThread examples={[]} /> : <Thread items={thread} />}
          </div>
        </div>
        <ResultsPane state={data} onDownload={download} />
      </div>
      <Dialog
        open={confirmNew}
        title="Start a new session?"
        onClose={() => setConfirmNew(false)}
        actions={
          <>
            <button className="btn" type="button" onClick={() => setConfirmNew(false)}>
              Keep working
            </button>
            <button className="btn btn-primary" type="button" onClick={startNewSession}>
              Clear everything
            </button>
          </>
        }
      >
        <p>This clears the pages, results and chat, and any run that is waiting. Your settings stay.</p>
      </Dialog>
      {toast ? (
        <div className="toast" role="status">
          {toast}
        </div>
      ) : null}
    </div>
  );
}
