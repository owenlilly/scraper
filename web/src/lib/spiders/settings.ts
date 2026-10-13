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
