import Link from "next/link";
import type { ReactNode } from "react";
import { Mark } from "../Mark";
import "./spiders.css";

export function SpidersFrame({ children }: { children: ReactNode }) {
  return (
    <div className="page">
      <header className="topbar">
        <Link className="wordmark" href="/">
          <Mark />
          Page Chat
        </Link>
        <Link className="btn btn-quiet" href="/">
          Back to workspace
        </Link>
      </header>
      <main className="spiders">
        <div className="spiders-inner">{children}</div>
      </main>
    </div>
  );
}
