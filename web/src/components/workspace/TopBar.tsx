"use client";

import type { Usage } from "../../lib/api/types";
import { Mark } from "../Mark";
import { AccountMenu, type Person } from "./AccountMenu";

export function TopBar({ person, usage, canReset, onNewSession, onDelete }: { person: Person; usage: Usage | null; canReset: boolean; onNewSession: () => void; onDelete?: () => void }) {
  return (
    <header className="topbar">
      <span className="wordmark">
        <Mark />
        Page Chat
      </span>
      {canReset ? (
        <button className="btn btn-quiet" type="button" onClick={onNewSession}>
          New session
        </button>
      ) : null}
      <AccountMenu person={person} usage={usage} onDelete={onDelete} />
    </header>
  );
}
