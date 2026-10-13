/** The pure parts of editing a spider's settings and of showing its results, apart from the screens so they can be tested. */
import type { FormField, LocationOption, SettingsView } from "./types";

export type FieldRow = { name: string; shown: boolean };

export const asStrings = (value: unknown): string[] => (Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : []);

/** The rows of the "fields shown" editor: the chosen fields first, in their order, then the rest unchecked. With none chosen every
 *  field is shown, in the item's own order. A chosen name that is not a field of the item is dropped. */
export function rowsFromSettings(fields: string[], visible: string[]): FieldRow[] {
  const chosen = visible.filter((name) => fields.includes(name));
  if (chosen.length === 0) return fields.map((name) => ({ name, shown: true }));
  return [...chosen.map((name) => ({ name, shown: true })), ...fields.filter((name) => !chosen.includes(name)).map((name) => ({ name, shown: false }))];
}

/** What to save: the checked rows in their order. Nothing checked, or every field in the item's own order, is saved as an empty list,
 *  which means "all of them", so a field the spider gains later shows up. */
export function visibleFromRows(rows: FieldRow[], fields: string[]): string[] {
  const shown = rows.filter((row) => row.shown).map((row) => row.name);
  const everything = shown.length === fields.length && shown.every((name, index) => name === fields[index]);
  return shown.length === 0 || everything ? [] : shown;
}

export const toggleRow = (rows: FieldRow[], index: number): FieldRow[] => rows.map((row, at) => (at === index ? { ...row, shown: !row.shown } : row));

export function moveRow(rows: FieldRow[], index: number, delta: number): FieldRow[] {
  const target = index + delta;
  if (target < 0 || target >= rows.length) return rows;
  const next = [...rows];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

/** The columns of a results table. */
export function columnsFor(fields: string[], visible: string[]): string[] {
  const chosen = visible.filter((name) => fields.includes(name));
  return chosen.length > 0 ? chosen : fields;
}

export const fieldsNote = (shown: number, total: number): string | null => (shown < total ? `Showing ${shown} of ${total} fields` : null);

/** The places a spider can crawl, read with the spider's own names for an entry's id and label. */
export function locationOptions(view: SettingsView | undefined): LocationOption[] {
  if (!view?.locations_setting) return [];
  const entries = view.settings[view.locations_setting];
  if (!Array.isArray(entries)) return [];
  return entries.map((entry: Record<string, unknown>) => ({ id: String(entry[view.location_id_field]), label: String(entry[view.location_label_field]) }));
}

export const capChoices = (saved: number): number[] => [...new Set([1, 3, 5, 10, 20, saved])].sort((a, b) => a - b);

export function numberFrom(text: string): number | null {
  if (text.trim() === "") return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

export const isChanged = (a: unknown, b: unknown): boolean => JSON.stringify(a) !== JSON.stringify(b);

/** "Locations" becomes "location", for "Add location". */
export const singular = (label: string): string => label.replace(/s$/i, "").toLowerCase();

export function addRecord(list: Record<string, unknown>[], fields: FormField[]): Record<string, unknown>[] {
  const blank = Object.fromEntries(fields.map((field) => [field.key, field.type === "string" ? "" : field.type === "boolean" ? false : null]));
  return [...list, blank];
}

export const removeRecord = (list: Record<string, unknown>[], index: number): Record<string, unknown>[] => list.filter((_, at) => at !== index);

export const setCell = (list: Record<string, unknown>[], index: number, key: string, value: unknown): Record<string, unknown>[] =>
  list.map((record, at) => (at === index ? { ...record, [key]: value } : record));

export type Problem = { key: string; row: number | null; field: string | null };

/** Which setting an API message is about. `locations.2.geo_id: ...` names a cell; `max_pages: ...` a field; anything else, none. */
export function parseProblem(message: string | null): Problem | null {
  if (!message) return null;
  const at = message.indexOf(": ");
  if (at < 0) return null;
  const [key, second, third] = message.slice(0, at).split(".");
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) return null;
  const row = second !== undefined && /^\d+$/.test(second) ? Number(second) : null;
  return { key, row, field: row !== null ? (third ?? null) : null };
}

// ---- stored values the form cannot draw, and which row a message means ------------------------------------------------------------

/** The settings the page draws itself; a spider's own come from the form description. */
const COMMON_KEYS = ["visible_fields", "trial_max_requests", "max_requests", "request_delay", "concurrency"];

export const drawnKeys = (form: FormField[]): string[] => [...COMMON_KEYS, ...form.map((field) => field.key)];

/** Only the settings the form can draw. A key the model no longer knows is dropped, so saving replaces the whole object and cannot
 *  be refused for something the administrator has no way to see or change. */
export const pickKnown = (settings: Record<string, unknown>, keys: string[]): Record<string, unknown> =>
  Object.fromEntries(Object.entries(settings).filter(([key]) => keys.includes(key)));

/** Whether a problem is about something on the screen. If not, it must be shown in the summary, or nobody would see it. */
export const problemIsDrawn = (problem: Problem | null, keys: string[]): boolean => problem !== null && keys.includes(problem.key);

/** A list of records from whatever was stored: anything that is not a list of objects is nothing, not a crash. */
export const asList = (value: unknown): Record<string, unknown>[] =>
  Array.isArray(value) ? value.filter((entry): entry is Record<string, unknown> => typeof entry === "object" && entry !== null && !Array.isArray(entry)) : [];

/** `locations.1.geo_id: ...` means the second row, whose boxes are labelled "location 2". Say it the way the boxes do. */
export function describeCellProblem(message: string, noun: string, items: FormField[]): string {
  const problem = parseProblem(message);
  if (!problem || problem.row === null) return message;
  const rest = message.slice(message.indexOf(": ") + 2);
  const column = items.find((item) => item.key === problem.field)?.label ?? problem.field;
  const who = `${noun.charAt(0).toUpperCase()}${noun.slice(1)} ${problem.row + 1}`;
  return column ? `${who}, ${column}: ${rest}` : `${who}: ${rest}`;
}

export type RunBlock = "loading" | "failed" | "busy" | "in-flight" | "no-locations" | "invalid";

/** Why a run cannot be started right now, or null if it can. The settings must have loaded: until they have, the spider's locations and
 *  validity are unknown, and a click would start a run that the API refuses. */
export function runBlock(state: { busy: boolean; inFlight: boolean; loading: boolean; failed: boolean; needsLocation: boolean; invalid: boolean }): RunBlock | null {
  if (state.loading) return "loading";
  if (state.failed) return "failed";
  if (state.busy) return "busy";
  if (state.inFlight) return "in-flight";
  if (state.needsLocation) return "no-locations";
  if (state.invalid) return "invalid";
  return null;
}
