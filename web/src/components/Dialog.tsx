"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";

/** A modal built on the browser's own <dialog>: it traps focus, closes on Escape and puts focus back where it was. */
export function Dialog({ open, title, onClose, children, actions }: { open: boolean; title: string; onClose: () => void; children?: ReactNode; actions: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog ref={ref} aria-labelledby={titleId} onClose={onClose}>
      <h2 id={titleId}>{title}</h2>
      {children}
      <div className="actions">{actions}</div>
    </dialog>
  );
}
