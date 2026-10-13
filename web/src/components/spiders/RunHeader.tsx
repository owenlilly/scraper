"use client";

import { ago, canRetry, canStop, counters, elapsed, endNote, isStopping, statusLabel, workerNotice } from "../../lib/spiders/derive";
import type { Run } from "../../lib/spiders/types";
import { StatusPill } from "./StatusPill";

export function RunHeader({ run, now, busy, onStop, onRetry }: { run: Run; now: number; busy: boolean; onStop: () => void; onRetry: () => void }) {
  const notice = workerNotice(run, now);
  const note = endNote(run);
  const took = elapsed(run, now);
  const meta = [
    run.started_at ? `Started ${ago(run.started_at, now)}` : `Queued ${ago(run.queued_at, now)}`,
    took ? `${took} elapsed` : null,
    run.retry_of ? `Retry of #${run.retry_of}` : null,
  ].filter(Boolean);
  return (
    <section className="run-head" aria-label={`Run ${run.id}`}>
      <div className="run-title">
        <h2>Run #{run.id}</h2>
        <StatusPill run={run} />
        <div className="run-actions">
          {canStop(run) || isStopping(run) ? (
            <button className="btn btn-small" type="button" disabled={busy || !canStop(run)} onClick={onStop}>
              {isStopping(run) ? "Stopping…" : "Stop"}
            </button>
          ) : null}
          {canRetry(run) ? (
            <button className="btn btn-small" type="button" disabled={busy} onClick={onRetry}>
              Retry
            </button>
          ) : null}
        </div>
      </div>
      <p className="muted small">{meta.join(" · ")}</p>
      <p className="sr" role="status" aria-live="polite">{`Run ${run.id} ${statusLabel(run.status).toLowerCase()}`}</p>
      {notice ? <p className="sp-notice">{notice.text}</p> : null}
      {run.error ? (
        <div className="error-box" role="alert">
          {run.error}
        </div>
      ) : null}
      {note ? <p className="muted small">{note}</p> : null}
      <dl className="totals">
        {counters(run).map((counter) => (
          <div key={counter.label}>
            <dt>{counter.label}</dt>
            <dd>{counter.value.toLocaleString("en-US")}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
