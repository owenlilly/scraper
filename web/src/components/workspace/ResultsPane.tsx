"use client";

import { plural } from "../../lib/api/format";
import type { WorkspaceState } from "../../lib/api/types";
import { Ledger, LedgerEmpty } from "./Ledger";
import { PagesList } from "./PagesList";

export function ResultsPane({ state, onDownload }: { state: WorkspaceState; onDownload: (kind: "csv" | "json") => void }) {
  const count = state.rows.length;
  return (
    <div className="pane results">
      <div className="ledger-head">
        <h2>Results</h2>
        <span className="count">{plural(count, "row")}</span>
        <div className="btn-group">
          <button className="btn btn-small" type="button" disabled={count === 0} onClick={() => onDownload("csv")}>
            Download CSV
          </button>
          <button className="btn btn-small" type="button" disabled={count === 0} onClick={() => onDownload("json")}>
            Download JSON
          </button>
        </div>
      </div>
      {count > 0 ? <Ledger rows={state.rows} /> : <LedgerEmpty />}
      <PagesList pages={state.pages} failed={state.failed} />
    </div>
  );
}
