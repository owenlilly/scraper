"use client";

import { ago, statusLabel } from "../../lib/spiders/derive";
import type { Run } from "../../lib/spiders/types";
import { StatusPill } from "./StatusPill";

const when = (run: Run, now: number) => ago(run.finished_at ?? run.started_at ?? run.queued_at, now);

/** The runs, newest first. A list beside the run on wide screens; a select above it on narrow ones. */
export function RunList({ runs, selectedId, onSelect, now }: { runs: Run[]; selectedId: number | null; onSelect: (id: number) => void; now: number }) {
  return (
    <nav aria-label="Runs">
      <ul className="runlist">
        {runs.map((run) => (
          <li key={run.id}>
            <button type="button" aria-current={run.id === selectedId} onClick={() => onSelect(run.id)}>
              <b>#{run.id}</b>
              <StatusPill run={run} />
              {run.location ? <span className="sp-where">{run.location.label}</span> : null}
              <span className="when">{when(run, now)}</span>
            </button>
          </li>
        ))}
      </ul>
      <select className="runpick" aria-label="Run" value={selectedId ?? ""} onChange={(event) => onSelect(Number(event.target.value))}>
        {runs.map((run) => (
          <option key={run.id} value={run.id}>
            #{run.id} {run.kind === "trial" ? "trial " : ""}
            {run.location ? `${run.location.label}, ` : ""}
            {statusLabel(run.status).toLowerCase()}, {when(run, now)}
          </option>
        ))}
      </select>
    </nav>
  );
}
