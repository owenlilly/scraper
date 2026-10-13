import { statusLabel } from "../../lib/spiders/derive";
import type { Run } from "../../lib/spiders/types";

export function StatusPill({ run }: { run: Pick<Run, "status" | "kind"> }) {
  return (
    <>
      <span className={`sp-pill ${run.status}`}>{statusLabel(run.status)}</span>
      {run.kind === "trial" ? <span className="sp-badge">Trial</span> : null}
    </>
  );
}
