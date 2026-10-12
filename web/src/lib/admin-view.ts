/** The words and dates on the administrator's page. */
import type { InviteStatus } from "./invites";

const STATUS: Record<InviteStatus, string> = { pending: "Waiting", used: "Joined", revoked: "Revoked", expired: "Expired" };

export const inviteStatusLabel = (status: InviteStatus): string => STATUS[status];
export const roleLabel = (role: string | null | undefined): string => (role === "admin" ? "Administrator" : "Member");
export const personStatus = (person: { banned?: boolean | null }): "Banned" | "Active" => (person.banned ? "Banned" : "Active");

const DAY = new Intl.DateTimeFormat("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });

/** `Oct 10, 2026`, in UTC, so the server and the browser print the same thing. */
export function formatDay(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : DAY.format(date);
}
