"use client";

import Link from "next/link";
import { useState } from "react";
import { asStrings, columnsFor, fieldsNote } from "../../lib/spiders/settings";
import type { Run, SettingsView } from "../../lib/spiders/types";

const text = (value: unknown): string => (typeof value === "string" ? value : value === null || value === undefined ? "" : JSON.stringify(value));

/** What a trial parsed and what a run rejected. A live run shows only its rejected samples. The columns follow the spider's visible
 *  fields (every field when none are chosen). A rejected item's reason always shows, so a hidden field cannot hide the problem.
 *  Give it `key={run.id}` so each run starts on its own first tab. */
export function TrialResults({ run, view }: { run: Run; view?: SettingsView }) {
  const trial = run.kind === "trial";
  const items = run.stats.trial_items ?? [];
  const rejected = run.stats.rejected_samples ?? [];
  const [tab, setTab] = useState<"parsed" | "rejected">(trial ? "parsed" : "rejected");
  if (!trial && rejected.length === 0) return null;
  const known = view?.fields ?? [...new Set([...items.flatMap((item) => Object.keys(item)), ...rejected.flatMap((sample) => Object.keys(sample.item))])];
  const columns = columnsFor(known, asStrings(view?.settings.visible_fields));
  const note = fieldsNote(columns.length, known.length);
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
      {note ? (
        <p className="sp-note-link">
          {note}.{" "}
          <Link href={`/spiders/${run.source}/settings`}>Change in Settings</Link>
        </p>
      ) : null}
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
                    {columns
                      .filter((column) => column in sample.item)
                      .map((column) => (
                        <div key={column} style={{ display: "contents" }}>
                          <dt>{column}</dt>
                          <dd>{sample.item[column]}</dd>
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
