import { hostAndPath } from "../../lib/api/format";
import type { WorkspaceState } from "../../lib/api/types";

export function PagesList({ pages, failed }: Pick<WorkspaceState, "pages" | "failed">) {
  if (pages.length === 0 && failed.length === 0) return null;
  return (
    <section className="pages" aria-labelledby="pages-heading">
      <h3 id="pages-heading">Pages read</h3>
      <ul>
        {pages.map((page) => (
          <li key={page.url} className={page.used ? "used" : undefined}>
            <span className="t">{page.title || hostAndPath(page.url)}</span>
            <span className="meta">{page.looks_empty ? "Looks empty" : `${page.chars.toLocaleString("en-US")} characters`}</span>
            <span className="u">{hostAndPath(page.url)}</span>
          </li>
        ))}
        {failed.map((miss) => (
          <li key={miss.url} className="failed">
            <span className="t">Could not read</span>
            <span className="u">{hostAndPath(miss.url)}</span>
            <span className="meta">{miss.reason}</span>
          </li>
        ))}
      </ul>
      {pages.some((page) => page.used) ? <p className="muted small">Highlighted pages were used for the last answer.</p> : null}
    </section>
  );
}
