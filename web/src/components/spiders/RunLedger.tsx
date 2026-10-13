import { formatBytes, statusTone } from "../../lib/spiders/derive";
import type { FetchRow } from "../../lib/spiders/types";

const SHOWN = 200;

/** What tells one page of a crawl from the next: its path, query and fragment. The host is the same on nearly every row, so it
 *  is left to the tooltip. */
function where(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return url;
  }
}

/** Every page the run has fetched, newest first, numbered in the order they were fetched. */
export function RunLedger({ rows, running }: { rows: FetchRow[] | undefined; running: boolean }) {
  const all = rows ?? [];
  const shown = all.slice(-SHOWN).reverse();
  return (
    <section aria-labelledby="ledger-title">
      <h3 id="ledger-title">Pages fetched</h3>
      {shown.length === 0 ? (
        <p className="sp-empty">{running ? "Waiting for the first page…" : "This run fetched no pages."}</p>
      ) : (
        <div className="sp-table-wrap">
          <table className="sp-ledger">
            <thead>
              <tr>
                <th className="n" scope="col">
                  <span className="sr">Order fetched</span>
                  <span aria-hidden="true">#</span>
                </th>
                <th scope="col">Status</th>
                <th scope="col">Address</th>
                <th className="num" scope="col">
                  Size
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.map((row, index) => (
                <tr key={row.id}>
                  <td className="n">{all.length - index}</td>
                  <td>
                    <span className={`sp-code ${statusTone(row.status)}`}>{row.status}</span>
                  </td>
                  <td className="addr" title={row.url}>
                    {where(row.url)}
                  </td>
                  <td className="num">{formatBytes(row.size)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {all.length > SHOWN ? <p className="muted small">Showing the latest {SHOWN} of {all.length} pages.</p> : null}
    </section>
  );
}
