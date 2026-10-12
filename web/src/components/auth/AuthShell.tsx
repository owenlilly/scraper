import Link from "next/link";
import type { ReactNode } from "react";

/** The frame around every account screen: the wordmark, then one narrow column. */
export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="page">
      <header className="topbar">
        <Link className="wordmark" href="/sign-in">
          Page Chat
        </Link>
      </header>
      <main className="auth">
        <div className="auth-box">{children}</div>
      </main>
    </div>
  );
}
