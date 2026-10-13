"use client";

import { useState } from "react";
import type { Run } from "../../lib/spiders/types";

const MAX_COLUMNS = 6;
const text = (value: unknown): string => (typeof value === "string" ? value : value === null || value === undefined ? "" : JSON.stringify(value));

/** What a trial parsed and what it rejected. A live run shows only its rejected samples. Give it `key={run.id}` so each run starts on its own first tab. */
export function TrialResults({ run }: { run: Run }) {
  const trial = run.kind === "trial";
  const items = run.stats.trial_items ?? [];
  const rejected = run.stats.rejected_samples ?? [];
  const [tab, setTab] = useState<"parsed" | "rejected">(trial ? "parsed" : "rejected");
  if (!trial && rejected.length === 0) return null;
  const columns = [...new Set(items.flatMap((item) => Object.keys(item)))].slice(0, MAX_COLUMNS);
  const byField = Object.entries(run.stats.rejected_by_field ?? {}).map(([field, count]) => `${field} ${count}`).join(", ");

  return (
    <section aria-label="What the spider parsed">
      <div className="sp-tabs" role="tablist">
        {trial ? (
          <button type="button" role="tab" id="tab-parsed" aria-selected={tab === "parsed"} aria-controls="panel-results" onClick={() => setTab("parsed")}>
            Parsed items ({items.length})
          </button>
        ) : null}
        <button type="button" role="tab" id="tab-rejected" aria-selected={tab === "rejected"} aria-controls="panel-results" onClick={() => setTab("rejected")}>
          Rejected items ({run.stats.items_rejected ?? 0})
        </button>
      </div>
      <div role="tabpanel" id="panel-results" aria-labelledby={tab === "parsed" ? "tab-parsed" : "tab-rejected"}>
        {tab === "parsed" ? (
          items.length === 0 ? (
            <p className="sp-empty">Nothing was parsed. Check the pages fetched above for a block page, then the spider's selectors.</p>
          ) : (
            <div className="sp-table-wrap">
              <table className="sp-table">
                <thead>
                  <tr>
                    {columns.map((column) => (
                      <th key={column} scope="col">
                        {column}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {items.map((item, row) => (
                    <tr key={row}>
                      {columns.map((column) => (
                        <td key={column} title={text(item[column])}>
                          {text(item[column]).slice(0, 80)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : rejected.length === 0 ? (
          <p className="sp-empty">{(run.stats.items_rejected ?? 0) === 0 ? "No items were rejected." : "The rejected items were counted but no samples were kept."}</p>
        ) : (
          <>
            {byField ? <p className="muted small">Fields that failed: {byField}.</p> : null}
            <ul className="sp-rejects">
              {rejected.map((sample, index) => (
                <li key={index}>
                  <dl className="fields">
                    {Object.entries(sample.item).map(([key, value]) => (
                      <div key={key} style={{ display: "contents" }}>
                        <dt>{key}</dt>
                        <dd>{value}</dd>
                      </div>
                    ))}
                  </dl>
                  <p className="why">{sample.errors.join("; ")}</p>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </section>
  );
}
