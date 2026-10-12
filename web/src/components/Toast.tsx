"use client";

import { useEffect, useState } from "react";

const SHOW_MS = 3200;

/** A short message that clears itself. Render `<Toast message={toast} />` and call `setToast("...")` to show one. */
export function useToast() {
  const [toast, setToast] = useState<string | null>(null);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), SHOW_MS);
    return () => clearTimeout(timer);
  }, [toast]);
  return [toast, setToast] as const;
}

export function Toast({ message }: { message: string | null }) {
  return message ? (
    <div className="toast" role="status">
      {message}
    </div>
  ) : null;
}
