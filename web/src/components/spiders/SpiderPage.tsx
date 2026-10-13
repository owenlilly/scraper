"use client";

import Link from "next/link";
import { useState } from "react";
import { isActive } from "../../lib/spiders/derive";
import { Toast, useToast } from "../Toast";
import { messageOf, useFetchFeed, useRun, useRunActions, useRuns } from "./queries";
import { RunHeader } from "./RunHeader";
import { RunLedger } from "./RunLedger";
import { RunList } from "./RunList";
import { TrialResults } from "./TrialResults";
import { useNow } from "./useNow";

const CAPS = [1, 3, 5, 10, 20];

export function SpiderPage({ name }: { name: string }) {
  const runs = useRuns(name);
  const [picked, setPicked] = useState<number | null>(null); // null follows the newest run
  const [cap, setCap] = useState(5);
  const [toast, setToast] = useToast();
  const selectedId = picked ?? runs.data?.[0]?.id ?? null;
  const run = useRun(selectedId);
  const current = run.data; // a local, so TypeScript keeps the narrowing inside the handlers below
  const running = current ? isActive(current.status) : false;
  const feed = useFetchFeed(selectedId, running);
  const { start, stop, retry } = useRunActions(setToast, (created) => setPicked(created.id));
  const inFlight = runs.data?.some((item) => isActive(item.status)) ?? false;
  const now = useNow(inFlight);
  const busy = start.isPending || stop.isPending || retry.isPending;

  return (
    <>
      <div className="sp-head">
        <div>
          <Link className="sp-back small" href="/spiders">
            Spiders
          </Link>
          <h1>{name}</h1>
        </div>
        <div className="sp-controls">
          <label>
            Trial pages
            <select value={cap} onChange={(event) => setCap(Number(event.target.value))}>
              {CAPS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <button className="btn" type="button" disabled={busy || inFlight} onClick={() => start.mutate({ name, kind: "trial", maxRequests: cap })}>
            Trial run
          </button>
          <button className="btn btn-primary" type="button" disabled={busy || inFlight} onClick={() => start.mutate({ name, kind: "live" })}>
            Run now
          </button>
        </div>
      </div>
      {inFlight ? <p className="muted small">A run is already in flight, so a new one cannot start until it ends.</p> : null}

      {runs.isPending ? (
        <p role="status" className="muted">
          Loading runs…
        </p>
      ) : runs.isError ? (
        <div className="error-box" role="alert">
          {messageOf(runs.error)}{" "}
          <button className="link" type="button" onClick={() => runs.refetch()}>
            Try again
          </button>
        </div>
      ) : runs.data.length === 0 ? (
        <p className="sp-empty">No runs yet. Run a trial first to check this spider against the site.</p>
      ) : (
        <div className="sp-grid">
          <RunList runs={runs.data} selectedId={selectedId} onSelect={setPicked} now={now} />
          <div className="sp-main">
            {run.isError ? (
              <div className="error-box" role="alert">
                {messageOf(run.error)}{" "}
                <button className="link" type="button" onClick={() => run.refetch()}>
                  Try again
                </button>
              </div>
            ) : !current ? (
              <p role="status" className="muted">
                Loading run…
              </p>
            ) : (
              <>
                <RunHeader run={current} now={now} busy={busy} onStop={() => stop.mutate(current.id)} onRetry={() => retry.mutate(current.id)} />
                <RunLedger rows={feed.data} running={running} />
                <TrialResults key={current.id} run={current} />
              </>
            )}
          </div>
        </div>
      )}
      <Toast message={toast} />
    </>
  );
}
