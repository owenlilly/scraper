"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../../lib/api/client";
import { useRun } from "../../lib/workspace/run";
import { useWorkspaceState, useRefreshState } from "../../lib/workspace/state";
import type { Person } from "./AccountMenu";
import { SESSION_ENDED, useApi } from "./ApiProvider";
import { SignOutButton } from "../SignOutButton";
import { Dialog } from "../Dialog";
import { DeleteAccountDialog } from "./DeleteAccountDialog";
import { ResultsPane } from "./ResultsPane";
import { Conversation } from "./Conversation";
import { TopBar } from "./TopBar";
import "./workspace.css";

export function Workspace({ person }: { person: Person }) {
  const api = useApi();
  const router = useRouter();
  const state = useWorkspaceState();
  const refresh = useRefreshState();
  const [tab, setTab] = useState<"chat" | "results">("chat");
  const [confirmNew, setConfirmNew] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<string[]>([]);
  const run = useRun({ onNotice: setToast, onDrafts: useCallback((texts: string[]) => setDrafts(texts), []) });

  useEffect(() => {
    // Only when Better Auth itself ends the session. An API that rejects a token it was given (a mismatched setting, say) must not send
    // the person to sign in, because sign-in sends them straight back here.
    if (state.error instanceof ApiError && state.error.code === SESSION_ENDED) router.replace("/sign-in");
  }, [state.error, router]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 3200);
    return () => clearTimeout(timer);
  }, [toast]);

  const data = state.data;

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
              <SignOutButton />
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
      run.clear();
      await refresh();
      setTab("chat");
    } catch (error) {
      setToast(error instanceof ApiError ? error.message : "Could not start a new session. Try again.");
    }
  }

  const rowCount = data.rows.length;
  return (
    <div className="page workspace">
      <TopBar person={person} usage={data.usage} canReset={!data.busy && run.live?.status !== "streaming"} onNewSession={() => setConfirmNew(true)} onDelete={() => setDeleting(true)} />
      <div className="work" data-tab={tab}>
        <div className="tabs" role="tablist" aria-label="Workspace">
          <button type="button" role="tab" aria-selected={tab === "chat"} onClick={() => setTab("chat")}>
            Chat
          </button>
          <button type="button" role="tab" aria-selected={tab === "results"} onClick={() => setTab("results")}>
            Results ({rowCount})
          </button>
        </div>
        <Conversation state={data} run={run} pendingDrafts={drafts} onNotice={setToast} />
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
      <DeleteAccountDialog open={deleting} onClose={() => setDeleting(false)} />
      {toast ? (
        <div className="toast" role="status">
          {toast}
        </div>
      ) : null}
    </div>
  );
}
