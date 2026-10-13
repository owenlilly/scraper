"use client";

import { useEffect, useState } from "react";

const IDLE_MS = 30_000;

/** The current time in milliseconds. It moves every second while `live`, so "40s ago" and the elapsed time keep counting, and every
 *  half minute otherwise, so "5s ago" on a finished run does not stay put for ever. */
export function useNow(live: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), live ? 1000 : IDLE_MS);
    return () => clearInterval(timer);
  }, [live]);
  return now;
}
