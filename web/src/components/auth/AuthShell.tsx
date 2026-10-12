import Link from "next/link";
import type { ReactNode } from "react";
import { Mark } from "../Mark";

/** The frame around every account screen: the wordmark, then one narrow column, with a specimen of the results table beside it on wide screens. */
export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="page">
      <header className="topbar">
        <Link className="wordmark" href="/sign-in">
          <Mark />
          Page Chat
        </Link>
      </header>
      <main className="auth">
        <div className="auth-form">
          <div className="auth-box">{children}</div>
        </div>
        <aside className="auth-specimen" aria-hidden="true">
          <div className="specimen-sheet">
            <header>
              Results <span>3 rows</span>
            </header>
            <table>
              <thead>
                <tr>
                  <th className="n">#</th>
                  <th>plan</th>
                  <th className="num">price</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td className="n">1</td>
                  <td>Solo</td>
                  <td className="num">$9</td>
                </tr>
                <tr>
                  <td className="n">2</td>
                  <td>Team</td>
                  <td className="num">$49</td>
                </tr>
                <tr>
                  <td className="n">3</td>
                  <td>Studio</td>
                  <td className="num">$99</td>
                </tr>
              </tbody>
            </table>
            <footer>
              <span>Pages read</span>
              <span className="used">Plans and pricing</span>
            </footer>
          </div>
        </aside>
      </main>
    </div>
  );
}
