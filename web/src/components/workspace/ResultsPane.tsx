"use client";

import type { WorkspaceState } from "../../lib/api/types";
import { Ledger } from "./Ledger";
import { PagesList } from "./PagesList";

export function ResultsPane({ state, onDownload }: { state: WorkspaceState; onDownload: (kind: "csv" | "json") => void }) {
  const count = state.rows.length;
  return (
    <div className="pane results">
      <div className="ledger-head">
        <h2>Results</h2>
        <span className="count">{count === 1 ? "1 row" : `${count} rows`}</span>
        <button className="btn btn-small" type="button" disabled={count === 0} onClick={() => onDownload("csv")}>
          Download CSV
        </button>
        <button className="btn btn-small" type="button" disabled={count === 0} onClick={() => onDownload("json")}>
          Download JSON
        </button>
      </div>
      {count > 0 ? <Ledger rows={state.rows} /> : <p className="ledger-empty">Rows the agent collects appear here as a table you can download.</p>}
      <PagesList pages={state.pages} failed={state.failed} />
    </div>
  );
}
