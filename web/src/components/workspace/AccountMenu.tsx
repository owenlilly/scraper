"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import type { Usage } from "../../lib/api/types";
import { meterShare, resetNote } from "../../lib/workspace/ledger";
import { SignOutButton } from "../SignOutButton";

export type Person = { name: string; email: string; isAdmin: boolean };

function Meter({ label, used, limit }: { label: string; used: number; limit: number }) {
  return (
    <>
      <div className="row">
        <span>{label}</span>
        <span>
          {used} of {limit}
        </span>
      </div>
      <div className="bar" role="presentation">
        <i style={{ width: `${meterShare(used, limit)}%` }} />
      </div>
    </>
  );
}

export function AccountMenu({ person, usage, onDelete }: { person: Person; usage: Usage | null; onDelete?: () => void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const close = (event: Event) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", close);
    document.addEventListener("pointerdown", close);
    return () => {
      document.removeEventListener("keydown", close);
      document.removeEventListener("pointerdown", close);
    };
  }, [open]);

  return (
    <div className="menu" ref={root}>
      <button className="btn account" type="button" aria-label="Account menu" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((value) => !value)}>
        <span className="initial" aria-hidden="true">
          {(person.name || person.email).trim().charAt(0).toUpperCase()}
        </span>
        {person.name.split(" ")[0] || person.email}
      </button>
      {open ? (
        <div className="menu-panel" id={panelId}>
          <div className="who">
            <b>{person.name}</b>
            <span>{person.email}</span>
          </div>
          {usage ? (
            <div className="meter" role="group" aria-label="Today's allowance">
              <Meter label="Pages read today" used={usage.pages} limit={usage.pages_limit} />
              <Meter label="Questions to the model today" used={usage.model_calls} limit={usage.model_calls_limit} />
              <span className="muted">{resetNote(usage)}</span>
            </div>
          ) : null}
          <hr />
          {person.isAdmin ? (
            <>
              <Link className="item" href="/spiders">
                Spiders
              </Link>
              <Link className="item" href="/admin">
                Invitations and people
              </Link>
            </>
          ) : null}
          <SignOutButton className="item" />
          {onDelete ? (
            <button className="item btn-danger" type="button" onClick={() => { setOpen(false); onDelete(); }}>
              Delete my account
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
