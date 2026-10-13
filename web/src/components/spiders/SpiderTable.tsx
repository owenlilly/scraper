"use client";

import Link from "next/link";
import { useState } from "react";
import { ago, canRetry, canStop, isActive, isStopping } from "../../lib/spiders/derive";
import type { Run } from "../../lib/spiders/types";
import { Toast, useToast } from "../Toast";
import { messageOf, useRunActions, useSources } from "./queries";
import { StatusPill } from "./StatusPill";
import { useNow } from "./useNow";

function recordsNote(run: Run): string {
  const s = run.stats;
  if (run.kind === "trial") return `${s.items_scraped ?? 0} parsed`;
  return `${s.records_created ?? 0} new, ${s.records_changed ?? 0} changed`;
}

export function SpiderTable() {
  const sources = useSources();
  const [toast, setToast] = useToast();
  const { start, stop, retry } = useRunActions(setToast);
  const [chosen, setChosen] = useState<Record<string, string>>({}); // spider name to the location picked in its row
  const anyActive = sources.data?.some((source) => source.latest_run && isActive(source.latest_run.status)) ?? false;
  const now = useNow(anyActive);
  const busy = start.isPending || stop.isPending || retry.isPending;

  return (
    <>
      <div>
        <h1>Spiders</h1>
        <p className="sp-lede">Start a crawl, watch it as it runs, and check a spider against its site with a trial run before you trust it.</p>
      </div>
      {sources.isPending ? (
        <p role="status" className="muted">
          Loading spiders…
        </p>
      ) : sources.isError ? (
        <div className="error-box" role="alert">
          {messageOf(sources.error)}{" "}
          <button className="link" type="button" onClick={() => sources.refetch()}>
            Try again
          </button>
        </div>
      ) : sources.data.length === 0 ? (
        <p className="sp-empty">No spiders are registered. Add one to <code>SPIDERS</code> in <code>scraper/pipeline/sources</code> and restart the API.</p>
      ) : (
        <div className="sp-table-wrap">
          <table className="sp-table sp-list">
            <thead>
              <tr>
                <th scope="col">Spider</th>
                <th scope="col">Last run</th>
                <th scope="col">Records</th>
                <th scope="col">
                  <span className="sr">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {sources.data.map(({ name, latest_run: run, locations }) => {
                const place = locations.some((option) => option.id === chosen[name]) ? chosen[name] : locations[0]?.id;
                return (
                  <tr key={name}>
                    <td className="name">
                      <Link href={`/spiders/${name}`}>{name}</Link>
                      {locations.length > 1 ? (
                        <select
                          className="sp-where"
                          aria-label={`Location for ${name}`}
                          value={place}
                          onChange={(event) => setChosen((current) => ({ ...current, [name]: event.target.value }))}
                        >
                          {locations.map((option) => (
                            <option key={option.id} value={option.id}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      ) : locations.length === 1 ? (
                        <span className="sp-where"> {locations[0].label}</span>
                      ) : null}
                    </td>
                    <td>
                      {run ? (
                        <>
                          <StatusPill run={run} /> <span className="muted small">{ago(run.finished_at ?? run.started_at ?? run.queued_at, now)}</span>
                        </>
                      ) : (
                        <span className="muted">Never run</span>
                      )}
                    </td>
                    <td className="muted">{run ? recordsNote(run) : "None yet"}</td>
                    <td className="act">
                      {run && isActive(run.status) ? (
                        <button className="btn btn-small" type="button" disabled={busy || !canStop(run)} onClick={() => stop.mutate(run.id)}>
                          {isStopping(run) ? "Stopping…" : "Stop"}
                        </button>
                      ) : (
                        <>
                          {run && canRetry(run) ? (
                            <button className="btn btn-small" type="button" disabled={busy} onClick={() => retry.mutate(run.id)}>
                              Retry
                            </button>
                          ) : null}
                          <button className="btn btn-small btn-primary" type="button" disabled={busy} onClick={() => start.mutate({ name, kind: "live", location: place })}>
                            Run now
                          </button>
                          <button className="btn btn-small" type="button" disabled={busy} onClick={() => start.mutate({ name, kind: "trial", location: place })}>
                            Trial run
                          </button>
                        </>
                      )}
                      <Link className="btn btn-small btn-quiet" href={`/spiders/${name}/settings`}>
                        Settings
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <Toast message={toast} />
    </>
  );
}
