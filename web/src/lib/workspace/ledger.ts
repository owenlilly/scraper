/** Small helpers for the results table and the allowance meter. */
import type { Usage } from "../api/types";

/** Every key the rows use, in the order they first appear. */
export function ledgerColumns(rows: Record<string, unknown>[]): string[] {
  const columns = new Set<string>();
  for (const row of rows) for (const key of Object.keys(row)) columns.add(key);
  return [...columns];
}

export function cellText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** The used part of an allowance as a whole percentage from 0 to 100. */
export function meterShare(used: number, limit: number): number {
  if (!(limit > 0)) return 0;
  return Math.min(100, Math.max(0, Math.round((used / limit) * 100)));
}

export function resetNote(usage: Usage, locale?: string, timeZone?: string): string {
  const at = new Date(usage.resets_at);
  if (Number.isNaN(at.getTime())) return "Starts again at midnight UTC.";
  const time = new Intl.DateTimeFormat(locale, { hour: "numeric", minute: "2-digit", timeZone, timeZoneName: "short" }).format(at);
  return `Starts again at ${time}.`;
}

const NUMERIC = /^[-+]?[$€£¥]?\d[\d,]*(\.\d+)?%?$/;

/** Cells like `$49`, `1,250` and `40%` line up on the right, so a column of figures reads down. */
export function looksNumeric(text: string): boolean {
  return NUMERIC.test(text);
}
