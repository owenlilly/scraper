# Spider settings: editable per-spider settings, locations and visible fields

Status: design, awaiting review. Branch: `realtor_ca_spider`. Builds on `2026-10-12-spiders-page-design.md` (the spiders page, queue, worker and `/pipeline` API, all merged).

## 1. Goal

An administrator edits each spider's settings from the web app. Settings are saved in the database, validated, and used by runs. This covers:

- **Visible fields:** which item fields the run tables show, and in what order.
- **Trial default:** the number of requests a trial run starts with.
- **Crawl limits:** a request cap for live runs, the delay between requests, and how many requests run at once.
- **Spider-specific settings:** stored data that a spider's crawl is built from. The first case is realtor.ca's **locations**: a list of places, each with a `geo_id` and a `geo_name`.

A run crawls one location, chosen when it is started.

Success: an admin can add Ottawa to realtor's locations, trial-run it, see only the listing fields they chose in the same order they chose, and the run's page says which location and settings it used. A typo cannot reach a crawl. A new spider declares its settings in a few lines and needs no new page code.

## 2. Non-goals

- No autofill of a `geo_name` from a `geo_id` (or the reverse). It needs realtor.ca's own lookup, which we do not have a specification for. A lookup can be added later without changing how settings are stored.
- No validation of the format of a GeoId or GeoName. The format is not ours to define.
- No per-user settings. Settings belong to the spider and are shared by all administrators.
- No settings history, no concurrent-edit protection (the last save wins), no import or export.
- Records do not store the location they came from. A listing seen again under another location is the same record.
- Scheduling, automatic retry and the data browser stay out, as in the earlier spec.

## 3. Decisions

| Decision | Choice |
|---|---|
| Where settings live | The existing `pipeline.source.settings` JSON column. No new table and no migration. |
| Who defines a spider's settings | The spider class: a pydantic settings model with defaults, beside its item schema. |
| What a run uses | A snapshot of the settings taken when the run is queued, kept in `run.options`. A later edit never changes a run already queued or running. |
| What one run crawls | One location. Starting a run picks it. |
| Bound values (for example `geo_id` with `geo_name`) | One record in a list, edited and saved as a unit, with uniqueness enforced on both fields. |
| Retry | Keeps the location (by id) and the trial's request count, and takes the current settings. |
| Visible fields | Applied when a run is viewed, from the current settings. A display choice, not part of a run's snapshot. |

## 4. Settings model

`SpiderSettings` (new, in `scraper/pipeline/settings.py`) holds the fields every spider has. A spider subclasses it to add its own.

| Field | Type and range | Default | Meaning |
|---|---|---|---|
| `visible_fields` | list of item field names, no duplicates | empty | Fields shown in a run's Parsed and Rejected tables, in this order. Empty shows every field in the item schema's order. |
| `trial_max_requests` | integer 1 to 20 | 5 | The request cap a trial run starts with. |
| `max_requests` | integer 1 to 100000, or none | none | A cap for live runs. None means no cap. |
| `request_delay` | number 0 to 60 seconds, or none | none | A minimum delay between requests. None keeps the spider's own. |
| `concurrency` | integer 1 to 8, or none | none | Requests at once. None keeps the spider's own. |

Unknown keys are refused. A spider that declares no settings of its own uses `SpiderSettings` as it is, so `books_toscrape` needs no change.

**Realtor adds:**

| Field | Type and range | Default |
|---|---|---|
| `locations` | list of `Location`, at most 100 | one entry: `geo_id` `g30_dpz89rm7`, `geo_name` `Toronto, ON` |
| `max_pages` | integer 1 to 10 | 3 |

`Location` has two fields, `geo_id` and `geo_name`. Each is a non-empty string of at most 100 characters after trimming. There is no format check. The model also rejects two entries with the same `geo_id` and two with the same `geo_name`, so the mapping stays one to one. The message names the entry: "Location 3 repeats the GeoId of location 1."

`max_pages` is the number of result pages a run fetches, and so the number of browser sessions the spider opens. It replaces the constant `MAX_PAGES`.

**Declaring places a run can target.** A spider names the setting that holds its locations and which two fields of an entry are its id and its label. For realtor: `locations_setting = "locations"`, `location_id_field = "geo_id"`, `location_label_field = "geo_name"`. A spider that does not declare it has no locations and runs as it does today.

**Rules across fields** belong to the spider's model, as ordinary pydantic validators. The API turns any failure into a 422 that names the fields involved.

**Which setting types a form can draw.** A spider's own settings may use integers, numbers, strings, booleans, and lists of records made of those. A test fails if a spider declares anything else, so a spider cannot ship a setting the page cannot edit.

**Item fields.** The names a spider's `visible_fields` may use are the fields of its `item_schema`, in declaration order. Every name in `visible_fields` must be one of them, checked when settings are saved.

## 5. Storage and validation

- Settings are the spider's settings model, dumped as JSON into `source.settings`. Defaults are filled in when read, and an empty column is the same as all defaults.
- `SourceRepository` gains `set_settings(source_id, settings)`. All writes go through it.
- `PUT` replaces the whole object. It must validate against the spider's model and the item field names, and the body must fit the API's body limit.
- **Stored settings that no longer validate** (hand-edited in the database, or a model changed): the page's `GET` returns the stored values with a `problems` list, and the settings form shows them. Starting a run is refused with a 422 that says the saved settings are not valid and to fix them in Settings. Nothing is silently ignored.
- **URL safety.** `geo_id` and `geo_name` are URL-encoded when placed into a request address. The host is fixed in the spider, so no setting can point a crawl at another site. Format validation is not what protects this; encoding and a fixed host are.

## 6. How a run uses settings

**Queuing** (`POST /pipeline/sources/{name}/runs`, and Retry):
1. Read and validate the saved settings. If invalid, refuse with 422.
2. If the spider declares locations: the request must carry `location`, the chosen entry's id. It must match a saved entry, else 422 `unknown_location`. A spider with locations but none saved answers 422 `no_locations` ("Add a location in Settings to run this spider.").
3. Store in `run.options`: `settings` (the full validated settings), `location` (the whole chosen entry, so both bound values are kept), and for a trial `max_requests` (the request's value, defaulting to the saved `trial_max_requests`).

**Retry** reads the old run's `kind`, `options.location` id and trial `max_requests`, then does step 1 to 3 against the current settings. If the old location is no longer saved, it answers 409 `unknown_location`.

**The worker** (`exec_run`) builds the spider from the run: `RunContext` gains `settings` (the validated model, from `options.settings`, or defaults for a run queued before this feature) and `location` (`options.location` or none). Settings that fail to validate at that point end the run `failed` with the message. The command line `run` command resolves the saved settings itself and takes no location (a spider with locations uses its first entry).

**In the spider** (`SourceSpider`):
- It stores `ctx` and `settings` **before** calling Scrapling's `__init__`, because Scrapling calls `configure_sessions` from inside `__init__`, before the spider would otherwise have its context. A test pins this order.
- It applies the common limits: `concurrency` to `concurrent_requests`, `request_delay` to `download_delay`.
- It generalises the trial cap into a request cap: a trial uses its run's `max_requests`; a live run uses `settings.max_requests` if set. Reaching it stops the crawl with the same single graceful stop, and the run ends `completed` with `stats.stopped_at_cap = true`. The run page says "Stopped at the request cap (N requests)" for either kind.

**Realtor** builds its requests from the run's location and `max_pages`:
- Page `n` is the existing map-list address with `CurrentPage=n`, `GeoIds=<geo_id>` and `GeoName=<geo_name>`, each URL-encoded.
- Page 1 uses the same address, replacing today's fixed Toronto landing page, so every location is built the same way. This must be confirmed live (open item 1).
- It opens one session per page, up to `max_pages`.

## 7. API

All routes are admin-only in the existing `/pipeline` router, with the existing error shape.

| Route | Behaviour |
|---|---|
| `GET /pipeline/sources/{name}/settings` | `{settings, defaults, problems, form, fields, locations_setting}`. `settings` is the saved values with defaults filled in, or the raw stored values when they do not validate. `problems` lists why, else empty. `form` describes the spider's own settings for the page: for each, `{key, label, type, minimum, maximum, default, items}` where `items` describes a list's record fields. `fields` is the item field names. 404 for an unknown spider. |
| `PUT /pipeline/sources/{name}/settings` | Body is the whole settings object. Validated against the model and `fields`. Saves and returns the same shape as `GET`. 422 with the first problem and the field path, for example `locations.2.geo_id: must not be empty`. |
| `POST /pipeline/sources/{name}/runs` | Gains optional `location` (an entry id). `max_requests` for a trial defaults to the saved `trial_max_requests`. New 422 codes: `unknown_location`, `no_locations`, `invalid_settings`. |
| `GET /pipeline/sources` | Each spider also lists `locations`: `[{id, label}]` (empty for a spider without them) and `trial_max_requests`. |
| `POST /pipeline/runs/{id}/retry` | As section 6. |

A run's view already returns `options`, so the page shows its location and settings from there.

## 8. The page

Reuses the app's design system. No new palette.

**Settings screen** at `/spiders/<name>/settings`, admin-only (`requireAdmin`; the proxy matcher already covers `/spiders/:path*`), linked from a "Settings" link in the spider page's header. One column of sections:

1. **Fields shown in results.** One row per item field with a checkbox and Move up and Move down buttons. The saved list is the checked rows in the rows' order. If nothing is checked, a note says every field shows.
2. **Trial runs.** The default request cap, 1 to 20.
3. **Crawl limits.** Max requests per run, delay between requests, requests at once. A blank means the spider's own default.
4. **The spider's own settings,** drawn from `form`. A list of records is an editable table with Add and Remove. For realtor that is **Locations** (GeoId, GeoName) and **Pages per run**.

"Save settings" is the primary button; "Discard changes" is quiet and returns to the saved values. Each 422 shows under the field it names, with a summary at the top (`role="alert"`). Success shows the toast "Settings saved". If `problems` came back, the top shows them.

**On the run screens:**
- **Location picker.** A spider with locations gets a **Location** select beside Trial run and Run now on the spider page, and in each list row. A spider with none saved shows both buttons disabled with "Add a location in Settings to run this spider."
- **Trial pages** select opens on `trial_max_requests`, including a saved value not among its usual choices.
- **The run list** shows each run's location under its status. The run header says "Location: <name>".
- **Visible fields.** The Parsed items table shows the chosen fields in the chosen order, with a note "Showing 3 of 7 fields" linking to Settings. Rejected items use the same choice, but the reason it was rejected always shows, so a hidden field cannot hide the problem. The pages-fetched ledger does not change.

**Files.** `web/src/lib/spiders/settings.ts` (pure helpers: which columns to show, reordering, form values to settings and back, whether anything changed), `web/src/components/spiders/SettingsForm.tsx` and small parts it needs, `web/src/app/spiders/[name]/settings/page.tsx`, and edits to `SpiderPage`, `SpiderTable`, `RunList`, `RunHeader`, `TrialResults`, `queries.ts` and `types.ts`.

## 9. Testing

Python, on the existing real-Postgres fixtures:
- **Settings model:** defaults; every range; unknown keys; `visible_fields` against item fields and duplicates; locations unique by id and by name; empty and over-long values; the 100-entry cap; the form-type rule.
- **Routes:** admin only, 404, `GET` shape (defaults, `form`, `fields`, `problems` for stored invalid settings), `PUT` saves and returns, 422 with the field path, body limit.
- **Runs:** `location` required and checked, `no_locations`, `invalid_settings`, the snapshot in `options`, trial default from settings, an edit after queuing does not change the queued run, Retry keeps the location and takes current settings, Retry with a removed location.
- **Spider:** settings applied before `configure_sessions`; concurrency and delay applied; the live request cap stops once and ends `completed` with `stopped_at_cap`; realtor builds URL-encoded addresses from a location, with a character such as `&` or a space in the name, and opens `max_pages` sessions.
- **Worker:** a run built from its snapshot; a run queued before this feature runs with defaults; invalid snapshot settings fail the run with the message.

Web: Vitest for the pure helpers. Playwright, with no worker as in the earlier spec: edit and save settings and see them again after a reload; add a location and see it in the picker; an invalid value shows its message; a queued run shows its location. No live sites in tests.

## 10. Dependencies and open items

1. **Realtor page 1 by the map address.** Confirm with live trials, once for Toronto through the new address and once for a second city the administrator supplies, that page 1 parses the same cards as today's landing page. If it does not, a spider-specific `start_url` setting is added to `Location`.
2. **Commit the realtor conversion first.** `realtor_ca.py`, `sources/__init__.py` and `tests/test_pipeline_realtor.py` are uncommitted. A new worktree starts without them, so they are committed before the build starts.
3. **A live request cap ends `completed`,** like a trial's, so a partial run reads as finished. When the later tombstone logic arrives it must treat a run with `stopped_at_cap` as incomplete. Noted here so it is not forgotten.
4. **Records carry no location** (section 2). If the same listing id ever appears under two locations, the data shows one record.
5. **Last write wins** for settings edits by two administrators at once.
