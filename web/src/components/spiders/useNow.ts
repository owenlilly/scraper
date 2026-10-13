"use client";

import { useEffect, useState } from "react";

/** The current time in milliseconds, refreshed every second while `live`, so "40s ago" and the elapsed time keep moving. */
export function useNow(live: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [live]);
  return now;
}
