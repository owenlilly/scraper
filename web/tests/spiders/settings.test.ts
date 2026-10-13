import { describe, expect, it } from "vitest";
import {
  addRecord, asList, asStrings, capChoices, columnsFor, describeCellProblem, drawnKeys, fieldsNote, isChanged, locationOptions, moveRow, numberFrom,
  parseProblem, pickKnown, problemIsDrawn, removeRecord, rowsFromSettings, runBlock, setCell, singular, toggleRow, visibleFromRows,
} from "../../src/lib/spiders/settings";
import type { FormField, SettingsView } from "../../src/lib/spiders/types";

const FIELDS = ["id", "address", "price", "mls_no"];

describe("fields shown", () => {
  it("shows every field in the item's own order when none is chosen", () => {
    expect(rowsFromSettings(FIELDS, [])).toEqual(FIELDS.map((name) => ({ name, shown: true })));
  });
  it("puts the chosen fields first, in their order, and leaves the rest unchecked", () => {
    expect(rowsFromSettings(FIELDS, ["price", "id"])).toEqual([
      { name: "price", shown: true }, { name: "id", shown: true }, { name: "address", shown: false }, { name: "mls_no", shown: false },
    ]);
  });
  it("ignores a chosen name that is not a field of the item", () => {
    expect(rowsFromSettings(FIELDS, ["ghost", "id"]).map((row) => row.name)).toEqual(["id", "address", "price", "mls_no"]);
  });
  it("saves the checked rows in their order, and saves nothing when all are shown in the item's order or none are", () => {
    expect(visibleFromRows(rowsFromSettings(FIELDS, ["price", "id"]), FIELDS)).toEqual(["price", "id"]);
    expect(visibleFromRows(rowsFromSettings(FIELDS, []), FIELDS)).toEqual([]);
    expect(visibleFromRows(FIELDS.map((name) => ({ name, shown: false })), FIELDS)).toEqual([]);
    expect(visibleFromRows([...rowsFromSettings(FIELDS, [])].reverse(), FIELDS)).toEqual([...FIELDS].reverse()); // all shown but reordered is a choice
  });
  it("toggles and moves a row without changing the original, and stops at the ends", () => {
    const rows = rowsFromSettings(FIELDS, []);
    expect(toggleRow(rows, 1)[1]).toEqual({ name: "address", shown: false });
    expect(rows[1].shown).toBe(true);
    expect(moveRow(rows, 1, -1).map((row) => row.name)).toEqual(["address", "id", "price", "mls_no"]);
    expect(moveRow(rows, 0, -1)).toBe(rows);
    expect(moveRow(rows, 3, 1)).toBe(rows);
  });
  it("reads the table's columns from the choice, or every field when there is none", () => {
    expect(columnsFor(FIELDS, ["price", "id"])).toEqual(["price", "id"]);
    expect(columnsFor(FIELDS, [])).toEqual(FIELDS);
    expect(columnsFor(FIELDS, ["ghost"])).toEqual(FIELDS);
    expect(fieldsNote(2, 4)).toBe("Showing 2 of 4 fields");
    expect(fieldsNote(4, 4)).toBeNull();
  });
});

describe("locations and the trial choices", () => {
  const view = (settings: Record<string, unknown>, extra: Partial<SettingsView> = {}): SettingsView => ({
    settings, defaults: {}, problems: [], form: [], fields: FIELDS, locations_setting: "locations", location_id_field: "geo_id", location_label_field: "geo_name", ...extra,
  });
  it("lists a spider's locations as id and label, using the spider's own field names", () => {
    expect(locationOptions(view({ locations: [{ geo_id: "g1", geo_name: "Toronto, ON" }, { geo_id: "g2", geo_name: "Ottawa, ON" }] }))).toEqual([
      { id: "g1", label: "Toronto, ON" }, { id: "g2", label: "Ottawa, ON" },
    ]);
  });
  it("lists nothing for a spider without locations, before the settings load, or when the saved value is not a list", () => {
    expect(locationOptions(view({}, { locations_setting: null }))).toEqual([]);
    expect(locationOptions(undefined)).toEqual([]);
    expect(locationOptions(view({ locations: "x" }))).toEqual([]);
  });
  it("offers the usual trial sizes plus a saved one that is not among them", () => {
    expect(capChoices(5)).toEqual([1, 3, 5, 10, 20]);
    expect(capChoices(7)).toEqual([1, 3, 5, 7, 10, 20]);
  });
  it("reads a list of strings from whatever was saved", () => {
    expect(asStrings(["a", 1, "b"])).toEqual(["a", "b"]);
    expect(asStrings(undefined)).toEqual([]);
  });
});

describe("editing", () => {
  const items: FormField[] = [
    { key: "geo_id", label: "GeoId", description: null, type: "string", nullable: false, default: null, minimum: null, maximum: null, minLength: 1, maxLength: 100, maxItems: null },
    { key: "geo_name", label: "GeoName", description: null, type: "string", nullable: false, default: null, minimum: null, maximum: null, minLength: 1, maxLength: 100, maxItems: null },
  ];
  it("adds a blank record, removes one and sets one cell, never changing the original", () => {
    const list = [{ geo_id: "a", geo_name: "A" }];
    const added = addRecord(list, items);
    expect(added).toEqual([{ geo_id: "a", geo_name: "A" }, { geo_id: "", geo_name: "" }]);
    expect(list).toHaveLength(1);
    expect(setCell(added, 1, "geo_id", "b")[1]).toEqual({ geo_id: "b", geo_name: "" });
    expect(removeRecord(added, 0)).toEqual([{ geo_id: "", geo_name: "" }]);
  });
  it("reads a number box: blank is none, anything that is not a number is none", () => {
    expect(numberFrom("3")).toBe(3);
    expect(numberFrom("1.5")).toBe(1.5);
    expect(numberFrom("")).toBeNull();
    expect(numberFrom("  ")).toBeNull();
    expect(numberFrom("abc")).toBeNull();
  });
  it("knows whether anything changed", () => {
    expect(isChanged({ a: 1, b: [1] }, { a: 1, b: [1] })).toBe(false);
    expect(isChanged({ a: 1 }, { a: 2 })).toBe(true);
  });
  it("names one item of a list for the Add button", () => {
    expect(singular("Locations")).toBe("location");
    expect(singular("Pages")).toBe("page");
    expect(singular("Fields")).toBe("field");
  });
});

describe("a problem from the API", () => {
  it("finds the field, the row and the cell it names", () => {
    expect(parseProblem("locations.2.geo_id: String should have at least 1 character")).toEqual({ key: "locations", row: 2, field: "geo_id" });
    expect(parseProblem("locations: Location 3 repeats the GeoId of location 1.")).toEqual({ key: "locations", row: null, field: null });
    expect(parseProblem("max_pages: Input should be less than or equal to 10")).toEqual({ key: "max_pages", row: null, field: null });
  });
  it("says nothing about a message that names no field", () => {
    expect(parseProblem("The saved settings are not valid (max_pages: too big). Fix them in Settings.")).toBeNull();
    expect(parseProblem(null)).toBeNull();
    expect(parseProblem("Something went wrong")).toBeNull();
  });
});


const field = (key: string, label = key): FormField => ({
  key, label, description: null, type: "string", nullable: false, default: null, minimum: null, maximum: null, minLength: null, maxLength: null, maxItems: null,
});

describe("stored values the form cannot draw", () => {
  it("keeps only the settings the form draws, so a save replaces the whole object and drops the rest", () => {
    expect(pickKnown({ colour: 1, max_pages: 3, visible_fields: ["id"] }, ["max_pages", "visible_fields"])).toEqual({ max_pages: 3, visible_fields: ["id"] });
    expect(pickKnown({}, ["max_pages"])).toEqual({});
  });
  it("draws the five common settings and the spider's own", () => {
    expect(drawnKeys([field("locations"), field("max_pages")])).toEqual(["visible_fields", "trial_max_requests", "max_requests", "request_delay", "concurrency", "locations", "max_pages"]);
  });
  it("says whether a problem is about something on the screen", () => {
    const keys = drawnKeys([field("locations")]);
    expect(problemIsDrawn(parseProblem("locations.0.geo_id: too short"), keys)).toBe(true);
    expect(problemIsDrawn(parseProblem("colour: Extra inputs are not permitted"), keys)).toBe(false);
    expect(problemIsDrawn(null, keys)).toBe(false);
  });
  it("reads a list from whatever was stored, and nothing from a value that is not one", () => {
    expect(asList([{ a: 1 }, "x", null, { b: 2 }])).toEqual([{ a: 1 }, { b: 2 }]);
    expect(asList("x")).toEqual([]);
    expect(asList(undefined)).toEqual([]);
    expect(asList({ a: 1 })).toEqual([]);
  });
});

describe("a problem in one cell of a list", () => {
  const items = [field("geo_id", "GeoId"), field("geo_name", "GeoName")];
  it("counts rows from one, as the boxes are labelled, and names the column by its label", () => {
    expect(describeCellProblem("locations.1.geo_id: String should have at least 1 character", "location", items)).toBe("Location 2, GeoId: String should have at least 1 character");
    expect(describeCellProblem("locations.0.geo_name: too long", "location", items)).toBe("Location 1, GeoName: too long");
  });
  it("leaves a message that names no row as it is", () => {
    expect(describeCellProblem("locations: Location 3 repeats the GeoId of location 1.", "location", items)).toBe("locations: Location 3 repeats the GeoId of location 1.");
  });
});

describe("whether a run may be started", () => {
  const ready = { busy: false, inFlight: false, loading: false, failed: false, needsLocation: false, invalid: false };
  it("allows it when nothing stands in the way", () => expect(runBlock(ready)).toBeNull());
  it("waits for the settings to load, and says so if they could not", () => {
    expect(runBlock({ ...ready, loading: true })).toBe("loading");
    expect(runBlock({ ...ready, failed: true })).toBe("failed");
  });
  it("names the other reasons", () => {
    expect(runBlock({ ...ready, busy: true })).toBe("busy");
    expect(runBlock({ ...ready, inFlight: true })).toBe("in-flight");
    expect(runBlock({ ...ready, needsLocation: true })).toBe("no-locations");
    expect(runBlock({ ...ready, invalid: true })).toBe("invalid");
  });
  it("reports the first reason when there are several, loading before anything else", () => {
    expect(runBlock({ ...ready, loading: true, busy: true, invalid: true })).toBe("loading");
  });
});
