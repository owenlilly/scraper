# Spider Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Administrators edit per-spider settings (visible fields, trial default, crawl limits, and spider-specific data such as realtor.ca locations) from the spiders page, and runs use a snapshot of them for one chosen location.

**Architecture:** Each spider declares a pydantic settings model. Saved settings live in the existing `pipeline.source.settings` column and are validated on every write. Starting a run snapshots the validated settings and the chosen location into `run.options`; the worker builds the spider from that snapshot. The page's settings form is drawn from a description the API derives from the model, so a new spider's settings need no new page code.

**Tech Stack:** Python 3.14, pydantic, SQLAlchemy 2.1, Scrapling, FastAPI, pytest against real Postgres; Next.js 16, React 19, TanStack Query 5, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-13-spider-settings-design.md` (read it first; this plan implements it).

**Branch:** create `spider_settings` from the current `HEAD` of `realtor_ca_spider` in a worktree (use `superpowers:using-git-worktrees`; the worktree directory `.claude/worktrees/` is excluded locally in `.git/info/exclude`). The realtor conversion is committed (`ecf57b1`), so the worktree has it. Never add a `Co-Authored-By` trailer. Stage only the files each task names.

**Test setup (once per shell):**

```bash
make test-db   # skip if the scraper-test-db container is already running
export SCRAPER_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/scraper_test
export WEB_TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/scraper_web_test
export E2E_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/scraper_e2e_test
```

Python tests: `uv run pytest -q <file>`. Web tests: `npm --prefix web test -- <file>`; typecheck: `npm --prefix web run typecheck`. In a worktree, run commands as plain, separate commands from the worktree root (the session's guard refuses compound commands that name other paths).

## Global Constraints

- Common settings and their ranges: `visible_fields` (list of item field names, no repeats, default empty), `trial_max_requests` 1 to 20 (default 5), `max_requests` 1 to 100000 or none, `request_delay` 0 to 60 seconds or none, `concurrency` 1 to 8 or none. Unknown keys are refused.
- Realtor adds `locations` (at most 100; each `geo_id` and `geo_name` a non-empty string of at most 100 characters after trimming; **no format check**; unique by `geo_id` and unique by `geo_name`) and `max_pages` 1 to 10 (default 3). The default location is `geo_id` `g30_dpz89rm7`, `geo_name` `Toronto, ON`.
- Settings are stored in `pipeline.source.settings`. No new table and no migration.
- A run uses a snapshot taken when it is queued (`run.options.settings`, `run.options.location`, and for a trial `run.options.max_requests`). A later edit never changes a queued or running run.
- Retry keeps the old run's `kind`, location id and trial `max_requests`, and takes the current settings.
- `geo_id` and `geo_name` are URL-encoded (`quote(value, safe="")`) whenever they are placed in a request address. The host is fixed in the spider.
- Visible fields apply when a run is viewed, from the current settings. Rejected items use the same choice, but the reason it was rejected always shows.
- The spider stores its settings before calling Scrapling's `__init__`, because Scrapling calls `configure_sessions` from inside it.
- New 422 codes on starting a run: `unknown_location`, `no_locations`, `invalid_settings`. Retry answers 409 `unknown_location` when the location is gone or the old run recorded none.
- All pipeline SQL stays in repositories. API routes are plain `def`.
- Error shape is the existing `{"error": {"code", "message"}}`. Copy is sentence case with no em dashes. An action keeps one name everywhere ("Save settings", "Run now", "Trial run").
- No live sites in tests. Next.js 16: `params` is a `Promise`.

**Deviation from spec section 7, deliberate:** run views gain a top-level `location: {id, label} | null`, computed on the server from the spider's declared id and label fields, so the page and run lists can show a location without knowing each spider's field names.

## Review Focus

Failure modes the spec implies but the tasks' main tests would not reach on their own. Each has a test in the task named.

1. Saved settings that no longer validate (hand-edited in the database, or a model changed): the settings screen shows why, starting a run is refused with 422, and a worker given such a snapshot fails the run with the message instead of crashing (Tasks 3 and 5).
2. A location whose values contain characters that would change a URL (`&`, `#`, `%`, `/`, spaces, non-ASCII): they are encoded and cannot alter the rest of the address (Task 4).
3. Settings edited while a run is queued or running: that run still uses its snapshot (Task 5).
4. A location list that breaks the one-to-one mapping, has 101 entries, or has an enormous string: refused with a message naming the entry (Tasks 2 and 5).
5. Retry after a location was removed, or on an old run that recorded no location: 409 with a plain message, never a 500 (Task 5).

## File Structure

Python (`src/scraper/pipeline/`):
- `settings.py` (new): `SpiderSettings`, `load_settings`, `run_options`, `describe_form`, location helpers, `SettingsInvalid`, `LocationProblem`, `UnsupportedSetting`.
- `spider.py`: `RunContext` gains `settings` and `location`; `SourceSpider` declares the settings contract, applies the limits, generalises the cap.
- `runner.py`: `run_source` resolves settings for the command line; `execute` builds the context from `run.options`.
- `repositories/sources.py`: `set_settings`.
- `sources/spiders/realtor_ca.py`: `Location`, `RealtorSettings`, requests built from the location.
- `__main__.py`: refuses cleanly on bad settings or a missing location.

API (`src/scraper/api/pipeline.py`): settings routes, location on start and retry, location summary on the list, `location` on run views.

Web (`web/src/`):
- `lib/spiders/types.ts`, `derive.ts` (edits); `lib/spiders/settings.ts` (new, pure helpers).
- `components/spiders/queries.ts` (edits), `SettingsForm.tsx` (new), `SpiderPage.tsx`, `SpiderTable.tsx`, `RunList.tsx`, `RunHeader.tsx`, `TrialResults.tsx`, `spiders.css` (edits).
- `app/spiders/[name]/settings/page.tsx` (new).

Tests: `tests/test_pipeline_settings.py`, `tests/test_pipeline_spider_settings.py`, `tests/test_api_pipeline_settings.py` (new); edits to `tests/test_pipeline_realtor.py`, `tests/test_pipeline_repositories.py`, `tests/conftest.py`; `web/tests/spiders/settings.test.ts` (new), `web/tests/spiders/derive.test.ts` (edit); `web/e2e/specs/settings.spec.ts` (new).

---

### Task 1: The settings model and its helpers

**Files:**
- Create: `src/scraper/pipeline/settings.py`
- Test: `tests/test_pipeline_settings.py` (new)

**Interfaces:**
- Produces:
  - `SpiderSettings` (pydantic, `extra="forbid"`) with the five common fields; `COMMON_FIELDS: frozenset[str]`.
  - `SettingsInvalid(ValueError)` with `.message`; `LocationProblem(ValueError)` with `.code` and `.message`; `UnsupportedSetting(TypeError)`.
  - `load_settings(spider_cls, raw: dict | None) -> SpiderSettings` (raises `SettingsInvalid`; also checks `visible_fields` against the item schema).
  - `item_fields(spider_cls) -> list[str]`; `settings_defaults(spider_cls) -> dict`.
  - `locations_of(spider_cls, settings) -> list[dict]`; `location_options(spider_cls, settings) -> list[{"id", "label"}]`.
  - `run_options(spider_cls, settings, *, location_id, kind, max_requests=None) -> dict` (raises `LocationProblem`).
  - `describe_form(spider_cls) -> list[dict]` (raises `UnsupportedSetting`).
- A "spider class" here is anything with the attributes `item_schema`, `settings_model`, `locations_setting`, `location_id_field`, `location_label_field`. `SourceSpider` gets them in Task 3. This module imports no spider code.

- [ ] **Step 1: Write the failing tests** `tests/test_pipeline_settings.py`

```python
"""What an administrator can change about a spider: the common settings, loading and checking them, picking a location for a run,
and describing a spider's own settings for the page's form. No database."""

import pytest
from pydantic import BaseModel, Field

from scraper.pipeline.settings import (
    LocationProblem, SettingsInvalid, SpiderSettings, UnsupportedSetting, describe_form, item_fields, load_settings,
    location_options, locations_of, run_options, settings_defaults,
)


class Item(BaseModel):
    id: str
    title: str
    price: str | None = None


class Place(BaseModel):
    code: str = Field(title="Code")
    label: str = Field(title="Label")


class ExampleSettings(SpiderSettings):
    places: list[Place] = Field(default_factory=lambda: [Place(code="a", label="Alpha")], max_length=5, title="Places")
    depth: int = Field(default=2, ge=1, le=9, title="Depth")


class Example:
    """Stands in for a spider class: the settings code reads only these attributes."""

    item_schema = Item
    settings_model = ExampleSettings
    locations_setting = "places"
    location_id_field = "code"
    location_label_field = "label"


class Plain:
    item_schema = Item
    settings_model = SpiderSettings
    locations_setting = None
    location_id_field = ""
    location_label_field = ""


def test_the_common_defaults_are_the_documented_ones():
    assert SpiderSettings().model_dump() == {
        "visible_fields": [], "trial_max_requests": 5, "max_requests": None, "request_delay": None, "concurrency": None,
    }


@pytest.mark.parametrize(
    "field, bad",
    [("trial_max_requests", 0), ("trial_max_requests", 21), ("max_requests", 0), ("max_requests", 100_001), ("request_delay", -1),
     ("request_delay", 61), ("concurrency", 0), ("concurrency", 9)],
)
def test_a_value_outside_its_range_is_refused_by_field_name(field, bad):
    with pytest.raises(SettingsInvalid) as caught:
        load_settings(Plain, {field: bad})
    assert caught.value.message.startswith(f"{field}:")


@pytest.mark.parametrize(
    "field, good",
    [("trial_max_requests", 1), ("trial_max_requests", 20), ("max_requests", 100_000), ("request_delay", 0), ("request_delay", 60), ("concurrency", 8)],
)
def test_the_ends_of_every_range_are_allowed(field, good):
    assert getattr(load_settings(Plain, {field: good}), field) == good


def test_an_unknown_key_is_refused_by_name():
    with pytest.raises(SettingsInvalid) as caught:
        load_settings(Plain, {"colour": "red"})
    assert caught.value.message == "colour: Extra inputs are not permitted"


def test_visible_fields_must_be_item_fields_and_must_not_repeat():
    assert load_settings(Plain, {"visible_fields": ["title", "id"]}).visible_fields == ["title", "id"]
    with pytest.raises(SettingsInvalid) as unknown:
        load_settings(Plain, {"visible_fields": ["title", "nope"]})
    assert unknown.value.message == "visible_fields: nope is not a field of this spider's items"
    with pytest.raises(SettingsInvalid) as repeated:
        load_settings(Plain, {"visible_fields": ["id", "id"]})
    assert repeated.value.message == "visible_fields: id is listed twice"


def test_nothing_saved_means_every_default():
    assert load_settings(Plain, {}) == SpiderSettings() and load_settings(Plain, None) == SpiderSettings()
    assert load_settings(Example, {}).depth == 2


def test_item_fields_come_in_declaration_order_and_defaults_are_plain_json():
    assert item_fields(Plain) == ["id", "title", "price"]
    assert settings_defaults(Example)["depth"] == 2 and settings_defaults(Example)["places"] == [{"code": "a", "label": "Alpha"}]


def test_a_spiders_locations_are_listed_as_entries_and_as_picker_options():
    settings = load_settings(Example, {})
    assert locations_of(Example, settings) == [{"code": "a", "label": "Alpha"}]
    assert location_options(Example, settings) == [{"id": "a", "label": "Alpha"}]
    assert locations_of(Plain, SpiderSettings()) == [] and location_options(Plain, SpiderSettings()) == []


def test_run_options_snapshot_the_settings_and_the_whole_chosen_location():
    settings = load_settings(Example, {"places": [{"code": "a", "label": "Alpha"}, {"code": "b", "label": "Beta"}], "depth": 4})
    options = run_options(Example, settings, location_id="b", kind="live")
    assert options["location"] == {"code": "b", "label": "Beta"}
    assert options["settings"]["depth"] == 4 and options["settings"]["places"][1] == {"code": "b", "label": "Beta"}
    assert "max_requests" not in options


def test_a_trial_takes_its_request_count_from_the_request_or_else_the_saved_default():
    settings = load_settings(Plain, {"trial_max_requests": 7})
    assert run_options(Plain, settings, location_id=None, kind="trial")["max_requests"] == 7
    assert run_options(Plain, settings, location_id=None, kind="trial", max_requests=3)["max_requests"] == 3


def test_the_location_rules_each_have_a_code_and_a_plain_message():
    settings = load_settings(Example, {})
    with pytest.raises(LocationProblem) as unknown:
        run_options(Example, settings, location_id="zzz", kind="live")
    assert unknown.value.code == "unknown_location" and unknown.value.message == "That location is not in this spider's settings."
    with pytest.raises(LocationProblem) as missing:
        run_options(Example, settings, location_id=None, kind="live")
    assert missing.value.code == "unknown_location" and missing.value.message == "Choose a location to run."
    with pytest.raises(LocationProblem) as none_saved:
        run_options(Example, load_settings(Example, {"places": []}), location_id=None, kind="live")
    assert none_saved.value.code == "no_locations" and none_saved.value.message == "Add a location in Settings to run this spider."
    with pytest.raises(LocationProblem) as unwanted:
        run_options(Plain, SpiderSettings(), location_id="a", kind="live")
    assert unwanted.value.code == "unknown_location" and unwanted.value.message == "This spider has no locations."


def test_the_form_description_covers_only_the_spiders_own_settings():
    form = describe_form(Example)
    assert [entry["key"] for entry in form] == ["places", "depth"]
    places, depth = form
    assert (depth["type"], depth["minimum"], depth["maximum"], depth["default"], depth["label"], depth["nullable"]) == ("integer", 1, 9, 2, "Depth", False)
    assert places["type"] == "array" and places["maxItems"] == 5 and places["default"] == [{"code": "a", "label": "Alpha"}]
    assert [(item["key"], item["type"], item["label"]) for item in places["items"]] == [("code", "string", "Code"), ("label", "string", "Label")]
    assert describe_form(Plain) == []


def test_a_setting_the_form_cannot_draw_is_refused_when_it_is_described():
    class Tags(SpiderSettings):
        tags: list[str] = []

    class Mapping(SpiderSettings):
        by_name: dict[str, int] = {}

    class Holder(BaseModel):
        place: Place

    class Nested(SpiderSettings):
        rows: list[Holder] = []

    for model in (Tags, Mapping, Nested):
        spider = type("Bad", (Plain,), {"settings_model": model})
        with pytest.raises(UnsupportedSetting):
            describe_form(spider)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q tests/test_pipeline_settings.py`
Expected: FAIL at import (`scraper.pipeline.settings` does not exist).

- [ ] **Step 3: Write `src/scraper/pipeline/settings.py`**

```python
"""What an administrator can change about a spider.

Every spider has a settings model (this module's `SpiderSettings`, or a subclass that adds the spider's own). The model is the one
place that says what is allowed: the API validates every save against it, a run is given a validated snapshot of it, and the page's
form is drawn from a description of it. Nothing here imports spider code: a "spider class" is anything with the attributes read below."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

TRIAL_MAX = 20
SCALARS = {"integer", "number", "string", "boolean"}


class SpiderSettings(BaseModel):
    """The settings every spider has. A spider that needs more subclasses this."""

    model_config = ConfigDict(extra="forbid")

    visible_fields: list[str] = Field(default_factory=list, title="Fields shown in results")
    trial_max_requests: int = Field(default=5, ge=1, le=TRIAL_MAX, title="Trial run requests")
    max_requests: int | None = Field(
        default=None, ge=1, le=100_000, title="Max requests per run",
        description="Approximate when several requests run at once: those already started finish.",
    )
    request_delay: float | None = Field(default=None, ge=0, le=60, title="Delay between requests (seconds)")
    concurrency: int | None = Field(default=None, ge=1, le=8, title="Requests at once")

    @field_validator("visible_fields")
    @classmethod
    def _no_repeats(cls, names: list[str]) -> list[str]:
        for position, name in enumerate(names):
            if name in names[:position]:
                raise ValueError(f"{name} is listed twice")
        return names


COMMON_FIELDS = frozenset(SpiderSettings.model_fields)


class SettingsInvalid(ValueError):
    """The settings do not validate. `message` names the field and says what is wrong."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LocationProblem(ValueError):
    """A run cannot be started for the location asked for. `code` is the API's error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class UnsupportedSetting(TypeError):
    """A spider declared a setting the page's form cannot draw."""


def explain(error: ValidationError) -> str:
    """The first problem, as `field.path: what is wrong`. Never the value: it may be long, or something that should not be echoed."""
    first = error.errors(include_url=False, include_context=False, include_input=False)[0]
    where = ".".join(str(part) for part in first["loc"])
    message = first["msg"].removeprefix("Value error, ")
    return f"{where}: {message}" if where else message


def item_fields(spider_cls: Any) -> list[str]:
    return list(spider_cls.item_schema.model_fields)


def settings_defaults(spider_cls: Any) -> dict:
    return spider_cls.settings_model().model_dump(mode="json")


def load_settings(spider_cls: Any, raw: dict | None) -> SpiderSettings:
    """Validate `raw` against the spider's settings model and its item fields. Nothing saved is every default."""
    try:
        settings = spider_cls.settings_model.model_validate(raw or {})
    except ValidationError as error:
        raise SettingsInvalid(explain(error)) from None
    known = item_fields(spider_cls)
    for name in settings.visible_fields:
        if name not in known:
            raise SettingsInvalid(f"visible_fields: {name} is not a field of this spider's items")
    return settings


def locations_of(spider_cls: Any, settings: SpiderSettings) -> list[dict]:
    """The entries a run can target, as plain dicts. Empty for a spider that has no locations."""
    if not spider_cls.locations_setting:
        return []
    return [entry.model_dump(mode="json") for entry in getattr(settings, spider_cls.locations_setting)]


def location_options(spider_cls: Any, settings: SpiderSettings) -> list[dict]:
    id_field, label_field = spider_cls.location_id_field, spider_cls.location_label_field
    return [{"id": entry[id_field], "label": entry[label_field]} for entry in locations_of(spider_cls, settings)]


def run_options(spider_cls: Any, settings: SpiderSettings, *, location_id: str | None, kind: str, max_requests: int | None = None) -> dict:
    """What a run is queued with: a snapshot of the settings, the whole chosen location (so every bound value is kept), and for a
    trial its request count."""
    options: dict = {"settings": settings.model_dump(mode="json")}
    entries = locations_of(spider_cls, settings)
    if spider_cls.locations_setting:
        if not entries:
            raise LocationProblem("no_locations", "Add a location in Settings to run this spider.")
        if location_id is None:
            raise LocationProblem("unknown_location", "Choose a location to run.")
        chosen = next((entry for entry in entries if entry[spider_cls.location_id_field] == location_id), None)
        if chosen is None:
            raise LocationProblem("unknown_location", "That location is not in this spider's settings.")
        options["location"] = chosen
    elif location_id is not None:
        raise LocationProblem("unknown_location", "This spider has no locations.")
    if kind == "trial":
        options["max_requests"] = max_requests or settings.trial_max_requests
    return options


# ---- describing a spider's own settings for the page's form --------------------------------------------------------------------


def _resolve(schema: dict, defs: dict) -> tuple[dict, bool]:
    """Follow a `$ref`, and turn "this type or none" into the type plus a `nullable` flag."""
    if "$ref" in schema:
        schema = {**schema, **defs[schema["$ref"].rsplit("/", 1)[-1]]}
        schema.pop("$ref", None)
    nullable = False
    if "anyOf" in schema:
        options = [option for option in schema["anyOf"] if option.get("type") != "null"]
        nullable = len(options) != len(schema["anyOf"])
        if len(options) != 1:
            raise UnsupportedSetting("a setting may be one type, or that type or none")
        inner, _ = _resolve(options[0], defs)
        schema = {**{key: value for key, value in schema.items() if key != "anyOf"}, **inner}
    return schema, nullable


def _describe(key: str, schema: dict, defs: dict, default: Any, nested: bool = False) -> dict:
    schema, nullable = _resolve(schema, defs)
    kind = schema.get("type")
    entry = {
        "key": key, "label": schema.get("title") or key, "description": schema.get("description"), "type": kind, "nullable": nullable,
        "default": default, "minimum": schema.get("minimum"), "maximum": schema.get("maximum"),
        "minLength": schema.get("minLength"), "maxLength": schema.get("maxLength"), "maxItems": schema.get("maxItems"),
    }
    if kind in SCALARS:
        return entry
    if kind == "array" and not nested:
        item, _ = _resolve(schema.get("items", {}), defs)
        if item.get("type") != "object":
            raise UnsupportedSetting(f"{key}: a list must hold records, not bare values")
        entry["items"] = [_describe(name, sub, defs, None, nested=True) for name, sub in item.get("properties", {}).items()]
        return entry
    raise UnsupportedSetting(f"{key}: a {kind or 'composite'} setting cannot be drawn in the form")


def describe_form(spider_cls: Any) -> list[dict]:
    """The spider's own settings (not the common ones, which the page draws itself), described for the form: key, label, type, range,
    default, and for a list the fields of each record. Raises `UnsupportedSetting` for a setting the form cannot draw."""
    model = spider_cls.settings_model
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})
    defaults = settings_defaults(spider_cls)
    return [
        _describe(key, prop, defs, defaults.get(key))
        for key, prop in schema.get("properties", {}).items()
        if key not in COMMON_FIELDS
    ]
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q tests/test_pipeline_settings.py`
Expected: PASS (all). If a message assertion differs by wording from Pydantic's, fix the helper to produce the text the test states; the texts in the tests are the contract.

- [ ] **Step 5: Commit**

```bash
git add src/scraper/pipeline/settings.py tests/test_pipeline_settings.py
git commit -m "Add the spider settings model: validation, run snapshots and the form description"
```

---

### Task 2: Realtor's settings (locations and pages)

**Files:**
- Modify: `src/scraper/pipeline/sources/spiders/realtor_ca.py`
- Test: `tests/test_pipeline_realtor.py`

**Interfaces:**
- Consumes: Task 1's `SpiderSettings`, `load_settings`, `describe_form`, `SettingsInvalid`.
- Produces: `Location` (`geo_id`, `geo_name`), `RealtorSettings(SpiderSettings)` with `locations` and `max_pages`, `DEFAULT_PAGES = 3`; `RealtorSpider.settings_model`, `.locations_setting = "locations"`, `.location_id_field = "geo_id"`, `.location_label_field = "geo_name"`. The spider does not use them yet (Task 4).

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_pipeline_realtor.py`, and extend its imports with `from scraper.pipeline.settings import SettingsInvalid, describe_form, load_settings` and `Location, RealtorSettings` in the existing `realtor_ca` import:

```python
def test_the_default_settings_are_toronto_and_three_pages():
    settings = RealtorSettings()
    assert [(place.geo_id, place.geo_name) for place in settings.locations] == [("g30_dpz89rm7", "Toronto, ON")]
    assert settings.max_pages == 3


def test_a_location_needs_both_values_trimmed_and_bounded():
    for bad in ({"geo_id": "", "geo_name": "X"}, {"geo_id": "   ", "geo_name": "X"}, {"geo_id": "a", "geo_name": ""}, {"geo_id": "x" * 101, "geo_name": "X"}):
        with pytest.raises(ValidationError):
            Location(**bad)
    kept = Location(geo_id="  g1  ", geo_name=" Ottawa, ON ")
    assert (kept.geo_id, kept.geo_name) == ("g1", "Ottawa, ON")


@pytest.mark.parametrize("odd", ["g30_dpz89rm7", "12345", "weird id with spaces & symbols #?", "ünïcode"])
def test_no_geoid_format_is_enforced_because_the_format_is_not_ours(odd):
    assert Location(geo_id=odd, geo_name="X").geo_id == odd


def test_the_geoid_and_geoname_mapping_is_one_to_one():
    place = lambda geo_id, geo_name: {"geo_id": geo_id, "geo_name": geo_name}
    with pytest.raises(SettingsInvalid) as same_id:
        load_settings(RealtorSpider, {"locations": [place("a", "A"), place("b", "B"), place("a", "C")]})
    assert same_id.value.message == "locations: Location 3 repeats the GeoId of location 1."
    with pytest.raises(SettingsInvalid) as same_name:
        load_settings(RealtorSpider, {"locations": [place("a", "A"), place("b", "A")]})
    assert same_name.value.message == "locations: Location 2 repeats the GeoName of location 1."


def test_at_most_a_hundred_locations_and_none_is_allowed():
    place = lambda n: {"geo_id": f"g{n}", "geo_name": f"City {n}"}
    assert len(load_settings(RealtorSpider, {"locations": [place(n) for n in range(100)]}).locations) == 100
    with pytest.raises(SettingsInvalid) as caught:
        load_settings(RealtorSpider, {"locations": [place(n) for n in range(101)]})
    assert caught.value.message.startswith("locations:")
    assert load_settings(RealtorSpider, {"locations": []}).locations == []


@pytest.mark.parametrize("pages, ok", [(0, False), (1, True), (10, True), (11, False)])
def test_pages_per_run_is_one_to_ten(pages, ok):
    if ok:
        assert load_settings(RealtorSpider, {"max_pages": pages}).max_pages == pages
    else:
        with pytest.raises(SettingsInvalid):
            load_settings(RealtorSpider, {"max_pages": pages})


def test_the_form_description_names_the_locations_table_and_the_pages_field():
    locations, pages = describe_form(RealtorSpider)
    assert (locations["key"], locations["label"], locations["maxItems"]) == ("locations", "Locations", 100)
    assert [(item["key"], item["label"], item["maxLength"]) for item in locations["items"]] == [("geo_id", "GeoId", 100), ("geo_name", "GeoName", 100)]
    assert (pages["key"], pages["label"], pages["minimum"], pages["maximum"], pages["default"]) == ("max_pages", "Pages per run", 1, 10, 3)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_pipeline_realtor.py`
Expected: FAIL at import (`cannot import name 'Location'` or `RealtorSettings`).

- [ ] **Step 3: Add the models to `realtor_ca.py`.** Change the imports and constants at the top:

```python
import re
from decimal import Decimal
from typing import Annotated, Any
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from scrapling import Selector
from scrapling.fetchers import AsyncStealthySession
from scrapling.spiders import Response, SessionManager

from ...settings import SpiderSettings
from ...spider import SourceSpider

DEFAULT_PAGES = 3
MAX_PAGES = DEFAULT_PAGES  # until Task 4 replaces its uses with the setting
SITE = "https://www.realtor.ca"
TEXT = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
```

Add after the `Listing` class:

```python
class Location(BaseModel):
    """A place to search, as realtor.ca knows it. The two values go together: one is the id the search uses, the other its name. The
    formats are the site's, not ours, so they are not checked beyond being present and a sane length."""

    model_config = ConfigDict(extra="forbid")

    geo_id: TEXT = Field(title="GeoId")
    geo_name: TEXT = Field(title="GeoName")


def _toronto() -> list[Location]:
    return [Location(geo_id="g30_dpz89rm7", geo_name="Toronto, ON")]


class RealtorSettings(SpiderSettings):
    locations: list[Location] = Field(default_factory=_toronto, max_length=100, title="Locations")
    max_pages: int = Field(default=DEFAULT_PAGES, ge=1, le=10, title="Pages per run")

    @field_validator("locations")
    @classmethod
    def _one_to_one(cls, locations: list[Location]) -> list[Location]:
        """Each GeoId belongs to one GeoName and each GeoName to one GeoId, so neither may repeat."""
        for field_name, label in (("geo_id", "GeoId"), ("geo_name", "GeoName")):
            first_seen: dict[str, int] = {}
            for position, location in enumerate(locations, start=1):
                value = getattr(location, field_name)
                if value in first_seen:
                    raise ValueError(f"Location {position} repeats the {label} of location {first_seen[value]}.")
                first_seen[value] = position
        return locations
```

In `RealtorSpider`, after `parser_version = "1"` add:

```python
    settings_model = RealtorSettings
    locations_setting = "locations"
    location_id_field = "geo_id"
    location_label_field = "geo_name"
```

(`SourceSpider` has no such attributes yet; declaring them on the subclass is fine until Task 3.)

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q tests/test_pipeline_realtor.py tests/test_pipeline_settings.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/scraper/pipeline/sources/spiders/realtor_ca.py tests/test_pipeline_realtor.py
git commit -m "Give the realtor spider its settings: locations with a one-to-one GeoId and GeoName, and pages per run"
```

---

### Task 3: Spiders and runs use their settings

**Files:**
- Modify: `src/scraper/pipeline/spider.py`, `src/scraper/pipeline/runner.py`, `src/scraper/pipeline/repositories/sources.py`, `src/scraper/pipeline/__main__.py`
- Test: `tests/test_pipeline_spider_settings.py` (new), `tests/test_pipeline_repositories.py`

**Interfaces:**
- Consumes: Task 1 (`SpiderSettings`, `load_settings`, `run_options`, `location_options`, `describe_form`, `SettingsInvalid`, `LocationProblem`).
- Produces:
  - `RunContext(engine, blobs, source_id, run_id, kind="live", options={}, settings=None, location=None)`.
  - `SourceSpider.settings` (a `SpiderSettings`, or the spider's subclass) and `.location` (a dict or `None`), set **before** Scrapling's `__init__`. Class attributes `settings_model = SpiderSettings`, `locations_setting = None`, `location_id_field = ""`, `location_label_field = ""`.
  - `SourceSpider.request_cap` (`None` or an int): a trial's `options.max_requests`, else `settings.max_requests`. `stats.stopped_at_cap` is present whenever a cap is set.
  - `SourceRepository.set_settings(source_id, settings: dict) -> SourceConfig`.
  - `run_source` fills in `options.settings` (the saved settings) and `options.location` (the first location, if the spider has locations) when the caller gave no `settings`; it raises `SettingsInvalid` or `LocationProblem` before queuing anything.
  - `execute` builds the context from `run.options`; settings that no longer validate end the run `failed`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_pipeline_repositories.py`:

```python
def test_set_settings_replaces_the_saved_settings_and_ensure_leaves_them_alone(uow):
    with uow() as work:
        source = work.sources.ensure("shop", {"delay": 1})
        saved = work.sources.set_settings(source.id, {"concurrency": 2})
    assert saved.settings == {"concurrency": 2}
    with uow() as work:
        assert work.sources.ensure("shop", {"delay": 99}).settings == {"concurrency": 2}  # a code default never overwrites what an admin saved
        work.sources.set_settings(source.id, {})
        assert work.sources.get("shop").settings == {}
```

Create `tests/test_pipeline_spider_settings.py`:

```python
"""A spider's settings: held before its sessions are configured, applied as limits, snapshotted into a run, and the request cap."""

import pytest
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from pipeline_support import book, local_books, serve, write_site
from scraper.pipeline.models import RecordRow
from scraper.pipeline.runner import run_source
from scraper.pipeline.settings import LocationProblem, SettingsInvalid, SpiderSettings, describe_form
from scraper.pipeline.sources import SPIDERS
from scraper.pipeline.spider import RunContext
from scraper.pipeline.uow import UnitOfWork

BLANK = RunContext(engine=None, blobs=None, source_id=1, run_id=1)


def test_a_spider_has_its_settings_and_location_before_its_sessions_are_configured():
    seen = []

    class Probe(local_books("http://x")):
        def configure_sessions(self, manager):
            seen.append((self.settings, self.location))
            super().configure_sessions(manager)

    settings = SpiderSettings(concurrency=3)
    Probe(ctx=RunContext(None, None, 1, 1, settings=settings, location={"id": "a"}))
    assert seen == [(settings, {"id": "a"})]


def test_without_settings_a_spider_uses_its_models_defaults_and_has_no_location():
    spider = local_books("http://x")(ctx=BLANK)
    assert spider.settings == SpiderSettings() and spider.location is None and spider.request_cap is None


def test_concurrency_and_delay_are_applied_and_left_alone_when_unset():
    cls = local_books("http://x")
    tuned = cls(ctx=RunContext(None, None, 1, 1, settings=SpiderSettings(concurrency=3, request_delay=1.5)))
    assert tuned.concurrent_requests == 3 and tuned.download_delay == 1.5
    untouched = cls(ctx=BLANK)
    assert untouched.concurrent_requests == cls.concurrent_requests and untouched.download_delay == cls.download_delay


def test_a_trial_runs_one_request_at_a_time_whatever_the_concurrency_setting():
    trial = RunContext(None, None, 1, 1, kind="trial", options={"max_requests": 4}, settings=SpiderSettings(concurrency=6))
    spider = local_books("http://x")(ctx=trial)
    assert spider.concurrent_requests == 1 and spider.request_cap == 4


def test_a_live_run_stops_at_its_request_cap_keeps_what_it_fetched_and_says_so(pipeline, blobs, tmp_path):
    write_site(tmp_path / "long", [[book(n)] for n in range(1, 9)])
    with serve(tmp_path / "long") as base:
        run = run_source(local_books(base), pipeline, blobs, options={"settings": {"max_requests": 2}})
    assert run.status == "completed" and run.error is None
    assert run.stats["stopped_at_cap"] is True and run.stats["requests_count"] == 2 and run.stats["records_created"] == 2


def test_a_live_run_without_a_cap_has_no_cap_marker(pipeline, blobs, tmp_path):
    write_site(tmp_path / "short", [[book(1)]])
    with serve(tmp_path / "short") as base:
        run = run_source(local_books(base), pipeline, blobs)
    assert "stopped_at_cap" not in run.stats and run.options["settings"] == SpiderSettings().model_dump(mode="json")


def test_the_spider_is_built_from_the_runs_snapshot(pipeline, blobs, tmp_path):
    captured = {}
    write_site(tmp_path / "one", [[book(1)]])
    with serve(tmp_path / "one") as base:
        class Spy(local_books(base)):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                captured["settings"], captured["location"] = self.settings, self.location

        run_source(Spy, pipeline, blobs, options={"settings": {"visible_fields": ["title"], "concurrency": 2}, "location": {"id": "x"}})
    assert captured["settings"].visible_fields == ["title"] and captured["settings"].concurrency == 2 and captured["location"] == {"id": "x"}


def test_the_command_line_uses_the_saved_settings(pipeline, blobs, tmp_path):
    write_site(tmp_path / "long", [[book(n)] for n in range(1, 6)])
    with UnitOfWork(pipeline, blobs) as uow:
        uow.sources.set_settings(uow.sources.ensure("books_toscrape").id, {"max_requests": 1})
    with serve(tmp_path / "long") as base:
        run = run_source(local_books(base), pipeline, blobs)
    assert run.options["settings"]["max_requests"] == 1 and run.stats["requests_count"] == 1 and run.stats["stopped_at_cap"] is True


def test_settings_that_stopped_validating_after_they_were_saved_refuse_to_start_a_command_line_run(pipeline, blobs):
    with UnitOfWork(pipeline, blobs) as uow:
        uow.sources.set_settings(uow.sources.ensure("books_toscrape").id, {"concurrency": 99})
    with pytest.raises(SettingsInvalid) as caught:
        run_source(local_books("http://x"), pipeline, blobs)
    assert caught.value.message.startswith("concurrency:")
    with UnitOfWork(pipeline, blobs) as uow:
        assert uow.runs.recent(uow.sources.get("books_toscrape").id) == []  # nothing was queued


def test_a_snapshot_that_does_not_validate_fails_the_run_with_the_reason(pipeline, blobs):
    run = run_source(local_books("http://x"), pipeline, blobs, options={"settings": {"concurrency": 99}})
    assert run.status == "failed" and run.error.startswith("the settings for this run are not valid: concurrency:")
    with UnitOfWork(pipeline, blobs) as uow:
        assert uow.session.scalar(select(func.count()).select_from(RecordRow)) == 0


class Place(BaseModel):
    code: str
    label: str


class PlaceSettings(SpiderSettings):
    places: list[Place] = Field(default_factory=lambda: [Place(code="a", label="Alpha"), Place(code="b", label="Beta")])


def located(base):
    return type("Located", (local_books(base),), {
        "settings_model": PlaceSettings, "locations_setting": "places", "location_id_field": "code", "location_label_field": "label",
    })


def test_the_command_line_runs_a_spider_with_locations_on_its_first_one(pipeline, blobs, tmp_path):
    write_site(tmp_path / "one", [[book(1)]])
    with serve(tmp_path / "one") as base:
        run = run_source(located(base), pipeline, blobs)
    assert run.options["location"] == {"code": "a", "label": "Alpha"}


def test_the_command_line_refuses_a_spider_with_locations_when_none_are_saved(pipeline, blobs):
    with UnitOfWork(pipeline, blobs) as uow:
        uow.sources.set_settings(uow.sources.ensure("books_toscrape").id, {"places": []})
    with pytest.raises(LocationProblem) as caught:
        run_source(located("http://x"), pipeline, blobs)
    assert caught.value.code == "no_locations"


def test_every_registered_spiders_settings_can_be_drawn_by_the_form():
    for name, spider_cls in SPIDERS.items():
        assert isinstance(describe_form(spider_cls), list), name
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_pipeline_spider_settings.py tests/test_pipeline_repositories.py`
Expected: FAIL (`RunContext` has no `settings`; `set_settings` is missing).

- [ ] **Step 3: Add `set_settings`** to `src/scraper/pipeline/repositories/sources.py`. Change the first import to `from sqlalchemy import select, update` and append to `SourceRepository`:

```python
    def set_settings(self, source_id: int, settings: dict) -> SourceConfig:
        """Replace the saved settings. The only writer: the API validates before calling it."""
        row = self.session.scalars(
            update(SourceRow).where(SourceRow.id == source_id).values(settings=settings).returning(SourceRow).execution_options(populate_existing=True)
        ).one()
        return _source(row)
```

- [ ] **Step 4: Update `src/scraper/pipeline/spider.py`.**

(a) After `from .items import prepare` add `from .settings import SpiderSettings`.

(b) Replace the `RunContext` dataclass with:

```python
@dataclass(frozen=True)
class RunContext:
    engine: Engine
    blobs: FilesystemBlobStore
    source_id: int
    run_id: int
    kind: str = "live"  # "trial" crawls a few pages and stores no records
    options: dict = field(default_factory=dict)
    settings: SpiderSettings | None = None  # the run's validated snapshot; None means the spider's own defaults
    location: dict | None = None  # the entry this run crawls, for a spider that has locations
```

(c) In `SourceSpider`, after `parser_version: str` add the contract, and replace `__init__` from its `def` line through `self._last_snapshot: dict = {}`:

```python
    # What an administrator can change. A spider with settings of its own sets `settings_model` to a subclass of SpiderSettings; one
    # with places to crawl also names the list that holds them and which two fields of an entry are its id and its label.
    settings_model: type[SpiderSettings] = SpiderSettings
    locations_setting: str | None = None
    location_id_field: str = ""
    location_label_field: str = ""

    def __init__(self, *, ctx: RunContext, crawldir=None, interval: float = 300.0) -> None:
        # Settings first: Scrapling calls `configure_sessions` from inside its own __init__, before this one would store anything.
        self.ctx = ctx
        self.settings = ctx.settings if ctx.settings is not None else self.settings_model()
        self.location = ctx.location
        super().__init__(crawldir=crawldir, interval=interval)
        self._buffer: list[RecordItem] = []
        self._written = UpsertResult()
        self._rejected = 0
        self._unarchived = 0
        self._rejected_fields: Counter[str] = Counter()
        self._rejected_samples: list[dict] = []
        self.write_error: str | None = None
        self.trial = ctx.kind == "trial"
        if self.settings.concurrency is not None:
            self.concurrent_requests = self.settings.concurrency
        if self.settings.request_delay is not None:
            self.download_delay = self.settings.request_delay
        if self.trial:
            self.request_cap: int | None = int(ctx.options.get("max_requests", TRIAL_DEFAULT_REQUESTS))
            # The cap is checked as each response arrives, by which time Scrapling has started as many requests as it may run at
            # once. One at a time makes a trial's cap mean what the page says it means, on a live site.
            self.concurrent_requests = 1
        else:
            self.request_cap = self.settings.max_requests  # approximate when several requests run at once
        self.stopped_at_cap = False
        self.pause_was_requested = False
        self._trial_items: list[dict] = []
        self._stop_lock = threading.Lock()
        self._stop_asked = False
        self._last_snapshot: dict = {}
```

(d) Rename the cap check. In `is_blocked` change `self._stop_at_trial_cap()` to `self._stop_at_request_cap()`, and replace the `_stop_at_trial_cap` method with:

```python
    def _stop_at_request_cap(self) -> None:
        if self.request_cap is not None and not self.stopped_at_cap and self.stats.requests_count >= self.request_cap:
            self.stopped_at_cap = True
            self.stop_once()
```

(e) In `pipeline_stats`, replace the `if self.trial:` block at the end with:

```python
        if self.trial:
            stats["trial_items"] = list(self._trial_items)
        if self.request_cap is not None:
            stats["stopped_at_cap"] = self.stopped_at_cap
        return stats
```

- [ ] **Step 5: Update `src/scraper/pipeline/runner.py`.** Add `from .settings import SettingsInvalid, load_settings, location_options, run_options`. Replace `run_source` and `execute` (everything from `def run_source` to just before `def _beat`) with:

```python
def run_source(
    spider_cls: type[SourceSpider], engine: Engine, blobs: FilesystemBlobStore, *,
    worker_id: str = "cli", kind: str = "live", options: dict | None = None, beat_seconds: float = BEAT_SECONDS,
) -> Run:
    """Blocks until the crawl ends. The run row says how it ended: `completed`, `paused` (stopped; nothing is lost, the records so
    far are saved) or `failed` (storage broke, or the crawl itself raised). Raises `SourceBusy` if the source already has a run in
    flight, and `SettingsInvalid` or `LocationProblem` (before queuing anything) when the saved settings cannot start a run."""
    with UnitOfWork(engine, blobs) as uow:
        source = uow.sources.ensure(spider_cls.name)
        options = _with_settings(spider_cls, source.settings, kind, options)
        run = uow.runs.start(uow.runs.enqueue(source.id, kind=kind, options=options).id, worker_id)
    return execute(spider_cls, engine, blobs, source.id, run, beat_seconds=beat_seconds)


def _with_settings(spider_cls: type[SourceSpider], saved: dict, kind: str, options: dict | None) -> dict:
    """The options a run needs. The API gives them all; the command line and the tests give none, so the saved settings (and, for a
    spider with locations, its first location) are used."""
    options = dict(options or {})
    if "settings" in options:
        return options
    settings = load_settings(spider_cls, saved)
    entries = location_options(spider_cls, settings)
    return {**options, **run_options(spider_cls, settings, location_id=entries[0]["id"] if entries else None, kind=kind, max_requests=options.get("max_requests"))}


def execute(
    spider_cls: type[SourceSpider], engine: Engine, blobs: FilesystemBlobStore, source_id: int, run: Run, *, beat_seconds: float = BEAT_SECONDS
) -> Run:
    """Crawl for a run that is already `running`, reporting progress as it goes, and finish it. The command line and the worker's
    child process both come through here, so both heartbeat and both can be stopped from the page."""
    try:
        settings = load_settings(spider_cls, run.options.get("settings"))
    except SettingsInvalid as error:
        with UnitOfWork(engine, blobs) as uow:
            return uow.runs.finish(run.id, "failed", {}, f"the settings for this run are not valid: {error.message}")
    try:
        spider = spider_cls(ctx=RunContext(engine, blobs, source_id, run.id, run.kind, run.options, settings, run.options.get("location")))
    except Exception as error:
        with UnitOfWork(engine, blobs) as uow:
            return uow.runs.finish(run.id, "failed", {}, f"could not start the spider: {error!r}")
    stop = threading.Event()
    beat = threading.Thread(target=_beat, args=(stop, spider, engine, blobs, run.id, beat_seconds), name=f"heartbeat-{run.id}", daemon=True)
    beat.start()
    try:
        result = spider.start()
    except Exception as error:
        status, stats, message = "failed", spider.pipeline_stats(), f"crawl raised: {error!r}"
    else:
        stats = {**result.stats.to_dict(), **spider.pipeline_stats()}
        status = _status(spider, result.paused or spider.pause_was_requested)
        message = spider.write_error
    finally:
        stop.set()
        beat.join(timeout=10)
    with UnitOfWork(engine, blobs) as uow:
        return uow.runs.finish(run.id, status, stats, message)
```

(Keep `_beat` and `_status` exactly as they are. `run_source` lets `SettingsInvalid` and `LocationProblem` propagate to its caller.)

- [ ] **Step 6: Make the command line say what is wrong.** In `src/scraper/pipeline/__main__.py` add `from .settings import LocationProblem, SettingsInvalid` and replace the `try: finished = run_source(...) except SourceBusy:` block with:

```python
        try:
            finished = run_source(SPIDERS[args.source], engine, blobs)
        except SourceBusy:
            print("A run is already queued or running for this source.", file=sys.stderr)
            return 2
        except SettingsInvalid as error:
            print(f"The saved settings are not valid ({error.message}). Fix them on the spider's Settings page.", file=sys.stderr)
            return 2
        except LocationProblem as error:
            print(error.message, file=sys.stderr)
            return 2
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest -q tests/test_pipeline_spider_settings.py tests/test_pipeline_repositories.py tests/test_pipeline_trial.py tests/test_pipeline_run.py tests/test_pipeline_execution.py tests/test_pipeline_worker.py tests/test_pipeline_cli.py`
Expected: PASS. The trial tests must still pass unchanged: a trial's cap, 25 items and one-at-a-time behaviour are the same.

- [ ] **Step 8: Run the whole Python suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/scraper/pipeline/spider.py src/scraper/pipeline/runner.py src/scraper/pipeline/repositories/sources.py src/scraper/pipeline/__main__.py tests/test_pipeline_spider_settings.py tests/test_pipeline_repositories.py
git commit -m "Spiders and runs use their settings: limits, request cap, snapshot and location"
```

---

### Task 4: Realtor builds its requests from a location

**Files:**
- Modify: `src/scraper/pipeline/sources/spiders/realtor_ca.py`
- Test: `tests/test_pipeline_realtor.py`

**Interfaces:**
- Consumes: Task 2 (`RealtorSettings`, `DEFAULT_PAGES`), Task 3 (`self.settings`, `self.location`).
- Produces: `RealtorSpider.page_url(page) -> str`; `start_requests` yields page 1 for the run's location; sessions and paging follow `settings.max_pages`. With no location (a spider built without one) it uses the default Toronto entry. `MAX_PAGES` and `start_urls` are removed.

- [ ] **Step 1: Update the existing tests that name `MAX_PAGES` or `start_urls`, and add the new ones.** In `tests/test_pipeline_realtor.py`:

Change the import line to `from scraper.pipeline.sources.spiders.realtor_ca import DEFAULT_PAGES, Listing, Location, RealtorSettings, RealtorSpider` (it already imports `Listing`, `Location`, `RealtorSettings`; replace `MAX_PAGES` with `DEFAULT_PAGES` and delete the `MAX_PAGES` name from uses). Add after the `CTX` line:

```python
def toronto(page: int) -> str:
    return (
        "https://www.realtor.ca/map#view=list&CurrentPage=%d&Sort=6-D&GeoIds=g30_dpz89rm7&GeoName=Toronto%%2C%%20ON"
        "&PropertyTypeGroupID=1&TransactionTypeId=2&PropertySearchTypeId=1&Currency=CAD" % page
    )


def located(location: dict | None = None, **settings) -> RunContext:
    return RunContext(engine=None, blobs=None, source_id=1, run_id=1, settings=RealtorSettings(**settings), location=location)


def starts(spider) -> list:
    async def run():
        return [request async for request in spider.start_requests()]

    return asyncio.run(run())
```

Replace `test_the_spider_declares_its_site_and_start_page` with:

```python
def test_the_spider_declares_its_site_and_starts_from_the_first_page_of_its_location(spider):
    assert spider.allowed_domains == {"realtor.ca"}
    (first,) = starts(spider)
    assert first.url == toronto(1) and first.sid == "stealth1" and first.meta == {"page": 1}
```

Replace `MAX_PAGES` with `DEFAULT_PAGES` in `test_paging_stops_at_max_pages`, `test_every_page_up_to_max_pages_has_a_session_and_no_page_shares_one` and `test_every_page_session_skips_images_fonts_stylesheets_and_ads`. Append:

```python
def test_the_default_run_builds_exactly_the_addresses_it_always_did():
    spider = RealtorSpider(ctx=CTX)
    assert spider.page_url(1) == toronto(1) and spider.page_url(2) == toronto(2)


def test_a_locations_values_are_url_encoded_and_cannot_change_the_rest_of_the_address():
    place = {"geo_id": "g1&Sort=1#x/y%z", "geo_name": "Saint-Jean, QC & co/ünï"}
    url = RealtorSpider(ctx=located(place)).page_url(2)
    assert "GeoIds=g1%26Sort%3D1%23x%2Fy%25z&GeoName=Saint-Jean%2C%20QC%20%26%20co%2F%C3%BCn%C3%AF&" in url
    assert url.startswith("https://www.realtor.ca/map#view=list&CurrentPage=2&Sort=6-D&GeoIds=")
    assert url.count("#") == 1 and url.count("&Sort=") == 1 and url.endswith("&PropertyTypeGroupID=1&TransactionTypeId=2&PropertySearchTypeId=1&Currency=CAD")


def test_the_run_crawls_its_own_location_from_the_first_page_to_the_next():
    ottawa = {"geo_id": "g2_abc", "geo_name": "Ottawa, ON"}
    spider = RealtorSpider(ctx=located(ottawa))
    (first,) = starts(spider)
    assert "GeoIds=g2_abc&GeoName=Ottawa%2C%20ON" in first.url
    follow = [out for out in collect(spider, page(2, current=1)) if isinstance(out, Request)]
    assert len(follow) == 1 and "CurrentPage=2" in follow[0].url and "GeoIds=g2_abc" in follow[0].url


def test_pages_per_run_sets_how_many_sessions_open_and_where_paging_stops(monkeypatch):
    created = []

    class Recorder:
        def __init__(self, **options):
            created.append(options)

    monkeypatch.setattr(realtor_ca, "AsyncStealthySession", Recorder)
    spider = RealtorSpider(ctx=located(None, max_pages=5))
    assert len(created) == 5
    assert [out for out in collect(spider, page(2, current=4)) if isinstance(out, Request)][0].meta["page"] == 5
    assert [out for out in collect(spider, page(2, current=5)) if isinstance(out, Request)] == []
```


- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_pipeline_realtor.py`
Expected: FAIL (`DEFAULT_PAGES` imports fine, but `page_url` and `start_requests` do not exist, and `start_urls` assertions are gone).

- [ ] **Step 3: Update `realtor_ca.py`.** Remove the transitional `MAX_PAGES = DEFAULT_PAGES` line. Add `from urllib.parse import quote, urljoin` (replacing the `urljoin`-only import) and `Request` to the scrapling import: `from scrapling.spiders import Request, Response, SessionManager`. In `RealtorSpider` delete the `start_urls = [...]` line and replace the `next_url` line with:

```python
    next_url = (
        "https://www.realtor.ca/map#view=list&CurrentPage={page}&Sort=6-D&GeoIds={geo_id}&GeoName={geo_name}"
        "&PropertyTypeGroupID=1&TransactionTypeId=2&PropertySearchTypeId=1&Currency=CAD"
    )
```

Add, after `max_blocked_retries = 1`:

```python
    def page_url(self, page: int) -> str:
        """The address of results page `page` for this run's location. Both values are encoded so that nothing in them can change the
        rest of the address; with no location (a spider built without one) it is the default, Toronto."""
        place = self.location or RealtorSettings().locations[0].model_dump()
        return self.next_url.format(page=page, geo_id=quote(place["geo_id"], safe=""), geo_name=quote(place["geo_name"], safe=""))

    async def start_requests(self):
        yield Request(self.page_url(1), sid="stealth1", wait_selector=".listingCard", meta={"page": 1})
```

In `configure_sessions` change `range(1, MAX_PAGES + 1)` to `range(1, self.settings.max_pages + 1)`. In `parse` change:

```python
        if page < self.settings.max_pages:
            yield response.follow(self.page_url(page + 1), sid=f"stealth{page + 1}",
                                  wait_selector='.listingCard', callback=self.parse, meta={'page': page + 1})
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q tests/test_pipeline_realtor.py tests/test_pipeline_spider_settings.py`
Expected: PASS.

- [ ] **Step 5: Run the whole Python suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/scraper/pipeline/sources/spiders/realtor_ca.py tests/test_pipeline_realtor.py
git commit -m "Realtor crawls the location a run was started for, with encoded addresses and pages per run from its settings"
```

---

### Task 5: The settings routes, and locations on runs

**Files:**
- Modify: `src/scraper/api/pipeline.py` (full replacement below)
- Modify: `tests/conftest.py` (fixture `settings_api`), `tests/test_api_pipeline.py` (one expectation)
- Test: `tests/test_api_pipeline_settings.py` (new)

**Interfaces:**
- Consumes: Tasks 1 to 4 (`load_settings`, `run_options`, `describe_form`, `location_options`, `settings_defaults`, `item_fields`, `SourceRepository.set_settings`).
- Produces (all admin-only, existing error shape):
  - `GET /pipeline/sources/{name}/settings` -> `{settings, defaults, problems, form, fields, locations_setting, location_id_field, location_label_field}`.
  - `PUT /pipeline/sources/{name}/settings` (body: the whole settings object) -> the same view; 422 `invalid_settings` with `field.path: message`.
  - `POST /pipeline/sources/{name}/runs` gains `location` (an entry id); 422 `unknown_location`, `no_locations`, `invalid_settings`. A trial's `max_requests` defaults to the saved `trial_max_requests`.
  - `GET /pipeline/sources` entries gain `locations: [{id, label}]` and `trial_max_requests`.
  - Every run view gains `location: {id, label} | null`.
  - `POST /pipeline/runs/{id}/retry` keeps the location and the trial count and takes the current settings; 409 `unknown_location` if the location is gone or was never recorded.

- [ ] **Step 1: Add the fixture and write the failing tests.** Append to `tests/conftest.py`:

```python
@pytest.fixture
async def settings_api(pg, pipeline, blobs, auth_users):
    """The API with both real spiders wired to the scratch pipeline schema, and an administrator called "boss"."""
    from api_support import Harness
    from scraper.api.deps import PipelineAccess
    from scraper.pipeline.sources.books_toscrape import BooksToScrape
    from scraper.pipeline.sources.spiders.realtor_ca import RealtorSpider

    harness = Harness(pg, pipeline=PipelineAccess(pipeline, blobs, {"books_toscrape": BooksToScrape, "realtor_ca": RealtorSpider})).build()
    auth_users("boss", role="admin")
    auth_users("member")
    yield harness
    await harness.close()
```

In `tests/test_api_pipeline.py`, change the expectation in `test_the_spiders_are_listed_with_their_latest_run`:

```python
    assert listed == [{"name": "books_toscrape", "latest_run": None, "locations": [], "trial_max_requests": 5}]
```

Create `tests/test_api_pipeline_settings.py`:

```python
"""Editing a spider's settings, choosing a location for a run, and what a run keeps of both."""

import pytest

from scraper.pipeline.uow import UnitOfWork

TORONTO = {"geo_id": "g30_dpz89rm7", "geo_name": "Toronto, ON"}
OTTAWA = {"geo_id": "g2_x", "geo_name": "Ottawa, ON"}


def error_of(response):
    return response.json()["error"]


async def view(api, spider="realtor_ca"):
    return await api.client.get(f"/pipeline/sources/{spider}/settings", headers=api.auth("boss"))


async def save(api, body, spider="realtor_ca"):
    return await api.client.put(f"/pipeline/sources/{spider}/settings", headers=api.auth("boss"), json=body)


async def start(api, spider="realtor_ca", **body):
    return await api.client.post(f"/pipeline/sources/{spider}/runs", headers=api.auth("boss"), json={"kind": "live", **body})


async def test_the_settings_routes_are_for_administrators_and_known_spiders(settings_api):
    api = settings_api
    assert (await api.client.get("/pipeline/sources/realtor_ca/settings")).status_code == 401
    assert (await api.client.get("/pipeline/sources/realtor_ca/settings", headers=api.auth("member"))).status_code == 403
    assert (await api.client.put("/pipeline/sources/realtor_ca/settings", headers=api.auth("member"), json={})).status_code == 403
    assert (await view(api, "nope")).status_code == 404 and (await save(api, {}, "nope")).status_code == 404


async def test_the_view_has_the_saved_values_the_defaults_the_form_and_the_item_fields(settings_api):
    body = (await view(settings_api)).json()
    assert body["settings"]["max_pages"] == 3 and body["settings"]["locations"] == [TORONTO] and body["problems"] == []
    assert body["defaults"] == body["settings"]
    assert [entry["key"] for entry in body["form"]] == ["locations", "max_pages"]
    assert body["fields"] == ["id", "address", "price", "details_url", "image_url", "mls_no", "data_value"]
    assert (body["locations_setting"], body["location_id_field"], body["location_label_field"]) == ("locations", "geo_id", "geo_name")
    books = (await view(settings_api, "books_toscrape")).json()
    assert books["form"] == [] and books["locations_setting"] is None and books["fields"] == ["title", "url", "price", "rating", "in_stock"]


async def test_a_valid_save_is_stored_in_full_and_read_back(settings_api, pipeline, blobs):
    body = {"visible_fields": ["address", "price"], "trial_max_requests": 2, "max_pages": 4, "locations": [TORONTO, OTTAWA]}
    saved = await save(settings_api, body)
    assert saved.status_code == 200 and saved.json()["settings"]["max_pages"] == 4 and saved.json()["settings"]["concurrency"] is None
    again = (await view(settings_api)).json()["settings"]
    assert again == saved.json()["settings"] and again["visible_fields"] == ["address", "price"] and again["locations"] == [TORONTO, OTTAWA]
    with UnitOfWork(pipeline, blobs) as uow:
        assert uow.sources.get("realtor_ca").settings == again


@pytest.mark.parametrize(
    "body, starts",
    [
        ({"concurrency": 99}, "concurrency:"),
        ({"visible_fields": ["nope"]}, "visible_fields: nope is not a field"),
        ({"colour": 1}, "colour:"),
        ({"locations": [{"geo_id": "a", "geo_name": "A"}, {"geo_id": "a", "geo_name": "B"}]}, "locations: Location 2 repeats the GeoId of location 1."),
        ({"locations": [{"geo_id": "", "geo_name": "A"}]}, "locations.0.geo_id:"),
        ({"locations": [{"geo_id": str(n), "geo_name": str(n)} for n in range(101)]}, "locations:"),
        ({"locations": [{"geo_id": "x" * 5000, "geo_name": "A"}]}, "locations.0.geo_id:"),
    ],
)
async def test_a_bad_save_is_refused_naming_the_field_and_changes_nothing(settings_api, body, starts):
    refused = await save(settings_api, body)
    assert refused.status_code == 422 and error_of(refused)["code"] == "invalid_settings" and error_of(refused)["message"].startswith(starts)
    assert (await view(settings_api)).json()["settings"]["max_pages"] == 3  # still the defaults


async def test_a_body_that_is_not_a_json_object_is_a_plain_422(settings_api):
    refused = await save(settings_api, [])
    assert refused.status_code == 422 and error_of(refused)["code"] == "invalid"


async def test_stored_settings_that_stopped_validating_show_why_and_block_runs(settings_api, pipeline, blobs):
    with UnitOfWork(pipeline, blobs) as uow:
        uow.sources.set_settings(uow.sources.ensure("realtor_ca").id, {"max_pages": 99})
    body = (await view(settings_api)).json()
    assert body["problems"] == ["max_pages: Input should be less than or equal to 10"] and body["settings"] == {"max_pages": 99}
    refused = await start(settings_api, location="g30_dpz89rm7")
    assert refused.status_code == 422 and error_of(refused)["code"] == "invalid_settings"
    assert error_of(refused)["message"].startswith("The saved settings are not valid (max_pages:")
    listed = {entry["name"]: entry for entry in (await settings_api.client.get("/pipeline/sources", headers=settings_api.auth("boss"))).json()["sources"]}
    assert listed["realtor_ca"]["locations"] == []


async def test_a_run_for_a_spider_with_locations_needs_one_and_snapshots_it_with_the_settings(settings_api):
    await save(settings_api, {"trial_max_requests": 2, "max_pages": 4, "locations": [TORONTO, OTTAWA]})
    for body, message in (({}, "Choose a location to run."), ({"location": "nope"}, "That location is not in this spider's settings.")):
        refused = await start(settings_api, **body)
        assert refused.status_code == 422 and error_of(refused) == {"code": "unknown_location", "message": message}
    started = await start(settings_api, kind="trial", location="g2_x")
    run = started.json()
    assert started.status_code == 201 and run["location"] == {"id": "g2_x", "label": "Ottawa, ON"}
    assert run["options"]["location"] == OTTAWA and run["options"]["max_requests"] == 2 and run["options"]["settings"]["max_pages"] == 4


async def test_the_location_rules_for_a_spider_without_locations_and_for_one_with_none_saved(settings_api):
    refused = await start(settings_api, "books_toscrape", location="a")
    assert refused.status_code == 422 and error_of(refused) == {"code": "unknown_location", "message": "This spider has no locations."}
    await save(settings_api, {"locations": []})
    none_saved = await start(settings_api)
    assert none_saved.status_code == 422 and error_of(none_saved) == {"code": "no_locations", "message": "Add a location in Settings to run this spider."}


async def test_a_trial_defaults_to_the_saved_count_and_an_explicit_count_wins(settings_api):
    await save(settings_api, {"trial_max_requests": 9}, "books_toscrape")
    first = (await start(settings_api, "books_toscrape", kind="trial")).json()
    assert first["options"]["max_requests"] == 9
    await settings_api.client.post(f"/pipeline/runs/{first['id']}/stop", headers=settings_api.auth("boss"))
    assert (await start(settings_api, "books_toscrape", kind="trial", max_requests=3)).json()["options"]["max_requests"] == 3


async def test_editing_the_settings_after_queuing_does_not_change_the_queued_run(settings_api):
    run = (await start(settings_api, location="g30_dpz89rm7")).json()
    await save(settings_api, {"max_pages": 9})
    seen = (await settings_api.client.get(f"/pipeline/runs/{run['id']}", headers=settings_api.auth("boss"))).json()
    assert seen["options"]["settings"]["max_pages"] == 3


async def finish_failed(api, pipeline, blobs, run_id):
    with UnitOfWork(pipeline, blobs) as uow:
        uow.runs.finish(uow.runs.start(run_id, "w").id, "failed", {}, "boom")


async def test_retry_keeps_the_location_and_the_trial_count_and_takes_the_current_settings(settings_api, pipeline, blobs):
    await save(settings_api, {"locations": [TORONTO, OTTAWA]})
    old = (await start(settings_api, kind="trial", location="g2_x", max_requests=4)).json()
    await finish_failed(settings_api, pipeline, blobs, old["id"])
    await save(settings_api, {"max_pages": 6, "locations": [TORONTO, {"geo_id": "g2_x", "geo_name": "Ottawa, Ontario"}]})
    retried = await settings_api.client.post(f"/pipeline/runs/{old['id']}/retry", headers=settings_api.auth("boss"))
    run = retried.json()
    assert retried.status_code == 201 and run["retry_of"] == old["id"] and run["kind"] == "trial"
    assert run["location"] == {"id": "g2_x", "label": "Ottawa, Ontario"} and run["options"]["max_requests"] == 4
    assert run["options"]["settings"]["max_pages"] == 6


async def test_retry_after_the_location_was_removed_is_a_409_with_a_plain_message(settings_api, pipeline, blobs):
    await save(settings_api, {"locations": [TORONTO, OTTAWA]})
    old = (await start(settings_api, location="g2_x")).json()
    await finish_failed(settings_api, pipeline, blobs, old["id"])
    await save(settings_api, {"locations": [TORONTO]})
    refused = await settings_api.client.post(f"/pipeline/runs/{old['id']}/retry", headers=settings_api.auth("boss"))
    assert refused.status_code == 409 and error_of(refused) == {"code": "unknown_location", "message": "That location is not in this spider's settings."}


async def test_retry_of_a_run_that_recorded_no_location_is_a_409(settings_api, pipeline, blobs):
    with UnitOfWork(pipeline, blobs) as uow:
        source = uow.sources.ensure("realtor_ca")
        old = uow.runs.enqueue(source.id, kind="live", options={})
    await finish_failed(settings_api, pipeline, blobs, old.id)
    refused = await settings_api.client.post(f"/pipeline/runs/{old.id}/retry", headers=settings_api.auth("boss"))
    assert refused.status_code == 409 and error_of(refused) == {
        "code": "unknown_location", "message": "That run did not record a location. Start a new run and pick one.",
    }


async def test_the_spider_list_offers_each_spiders_locations_and_trial_default(settings_api):
    await save(settings_api, {"trial_max_requests": 3, "locations": [TORONTO, OTTAWA]})
    listed = {entry["name"]: entry for entry in (await settings_api.client.get("/pipeline/sources", headers=settings_api.auth("boss"))).json()["sources"]}
    assert listed["realtor_ca"]["locations"] == [{"id": "g30_dpz89rm7", "label": "Toronto, ON"}, {"id": "g2_x", "label": "Ottawa, ON"}]
    assert listed["realtor_ca"]["trial_max_requests"] == 3
    assert listed["books_toscrape"]["locations"] == [] and listed["books_toscrape"]["trial_max_requests"] == 5


async def test_the_run_history_shows_each_runs_location_and_none_for_a_spider_without(settings_api):
    run = (await start(settings_api, location="g30_dpz89rm7")).json()
    history = (await settings_api.client.get("/pipeline/sources/realtor_ca/runs", headers=settings_api.auth("boss"))).json()["runs"]
    assert history[0]["id"] == run["id"] and history[0]["location"] == {"id": "g30_dpz89rm7", "label": "Toronto, ON"}
    books = (await start(settings_api, "books_toscrape")).json()
    assert books["location"] is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/test_api_pipeline_settings.py tests/test_api_pipeline.py`
Expected: FAIL (the routes do not exist; `locations` is missing from the list).

- [ ] **Step 3: Replace `src/scraper/api/pipeline.py`** with:

```python
"""The spiders page's routes: what the pipeline can run, how its runs are going, the buttons that start, stop and retry them, and
each spider's settings. All of them are for administrators. They only read and write `pipeline` rows; a worker does the crawling."""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Body as JsonBody, Depends, Query
from pydantic import Field, model_validator

from scraper.pipeline.domain import FetchSummary, Run
from scraper.pipeline.repositories import SourceBusy
from scraper.pipeline.settings import (
    LocationProblem, SettingsInvalid, SpiderSettings, describe_form, item_fields, load_settings, location_options, run_options,
    settings_defaults,
)
from scraper.pipeline.spider import SourceSpider
from scraper.pipeline.uow import UnitOfWork

from .admins import AdminCheckUnavailable, is_admin
from .deps import Deps, PipelineAccess, current_user, get_deps
from .errors import ApiError
from .schemas import Body

router = APIRouter(prefix="/pipeline")
HISTORY_DEFAULT, HISTORY_MAX = 30, 100
FETCH_PAGE = 200
TRIAL_MAX = 20
HEAVY_STATS = ("trial_items", "rejected_samples")  # only the run's own view carries these
RETRYABLE = ("failed", "paused")


def runnable_spiders() -> dict[str, type[SourceSpider]]:
    """The registered spiders that run through the pipeline. (A spider that is not a `SourceSpider` has nowhere to store anything.)"""
    from scraper.pipeline.sources import SPIDERS

    return {name: cls for name, cls in SPIDERS.items() if isinstance(cls, type) and issubclass(cls, SourceSpider)}


def require_admin(user_id: str = Depends(current_user), deps: Deps = Depends(get_deps)) -> str:
    try:
        allowed = is_admin(deps.pool, user_id)
    except AdminCheckUnavailable:
        raise ApiError(503, "admin_unavailable", "Administrator checks are unavailable right now. Try again shortly.") from None
    if not allowed:
        raise ApiError(403, "forbidden", "Only administrators can use this.")
    return user_id


def pipeline_of(deps: Deps = Depends(get_deps)) -> PipelineAccess:
    if deps.pipeline is None:
        raise ApiError(503, "pipeline_unavailable", "The scraping pipeline is not set up on this server.")
    return deps.pipeline


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _location_of(spider_cls: type[SourceSpider] | None, run: Run) -> dict | None:
    """The run's location as `{id, label}`, read with the spider's own field names, so the page never needs to know them."""
    entry = run.options.get("location")
    if not isinstance(entry, dict) or spider_cls is None or not spider_cls.locations_setting:
        return None
    return {"id": entry.get(spider_cls.location_id_field), "label": entry.get(spider_cls.location_label_field)}


def run_view(run: Run, source: str, spider_cls: type[SourceSpider] | None = None, *, detail: bool = False) -> dict:
    stats = run.stats if detail else {key: value for key, value in run.stats.items() if key not in HEAVY_STATS}
    return {
        "id": run.id, "source": source, "kind": run.kind, "status": run.status, "queued_at": _iso(run.queued_at),
        "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at), "heartbeat_at": _iso(run.heartbeat_at),
        "retry_of": run.retry_of, "cancel_requested": run.cancel_requested, "options": run.options, "stats": stats, "error": run.error,
        "location": _location_of(spider_cls, run),
    }


def fetch_view(fetch: FetchSummary) -> dict:
    return {
        "id": fetch.id, "url": fetch.url, "final_url": fetch.final_url, "status": fetch.status,
        "fetched_at": _iso(fetch.fetched_at), "duration_ms": fetch.duration_ms, "size": fetch.size,
    }


def _busy() -> ApiError:
    return ApiError(409, "source_busy", "A run is already queued or running for this spider.")


def _no_run() -> ApiError:
    return ApiError(404, "not_found", "There is no such run.")


def _known(access: PipelineAccess, name: str) -> type[SourceSpider]:
    if name not in access.spiders:
        raise ApiError(404, "not_found", "There is no such spider.")
    return access.spiders[name]


def _summary(spider_cls: type[SourceSpider], raw: dict) -> dict:
    """What the list page needs from a spider's settings. Settings that do not validate offer no locations; the settings screen says why."""
    try:
        settings = load_settings(spider_cls, raw)
    except SettingsInvalid:
        return {"locations": [], "trial_max_requests": SpiderSettings().trial_max_requests}
    return {"locations": location_options(spider_cls, settings), "trial_max_requests": settings.trial_max_requests}


def settings_view(spider_cls: type[SourceSpider], raw: dict) -> dict:
    """The saved settings with defaults filled in. If what is stored no longer validates, the stored values come back as they are,
    with `problems` saying why, so the form can show them and the administrator can fix them."""
    problems: list[str] = []
    try:
        settings: dict = load_settings(spider_cls, raw).model_dump(mode="json")
    except SettingsInvalid as error:
        settings, problems = raw, [error.message]
    return {
        "settings": settings, "defaults": settings_defaults(spider_cls), "problems": problems, "form": describe_form(spider_cls),
        "fields": item_fields(spider_cls), "locations_setting": spider_cls.locations_setting,
        "location_id_field": spider_cls.location_id_field, "location_label_field": spider_cls.location_label_field,
    }


def _options_for(
    spider_cls: type[SourceSpider], raw: dict, *, kind: str, location_id: str | None, max_requests: int | None, location_status: int = 422
) -> dict:
    """The snapshot a run is queued with, or the reason it cannot be."""
    try:
        settings = load_settings(spider_cls, raw)
    except SettingsInvalid as error:
        raise ApiError(422, "invalid_settings", f"The saved settings are not valid ({error.message}). Fix them in Settings.") from None
    try:
        return run_options(spider_cls, settings, location_id=location_id, kind=kind, max_requests=max_requests)
    except LocationProblem as error:
        raise ApiError(location_status, error.code, error.message) from None


class StartBody(Body):
    kind: Literal["live", "trial"] = "live"
    max_requests: int | None = Field(default=None, ge=1, le=TRIAL_MAX)
    location: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _only_trials_have_a_cap(self):
        if self.kind == "live" and self.max_requests is not None:
            raise ValueError("max_requests applies to trial runs only")
        return self


@router.get("/sources")
def sources(access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)) -> dict:
    with UnitOfWork(access.engine, access.blobs) as uow:
        known = {source.name: source for source in uow.sources.all()}
        for name in access.spiders:
            if name not in known:
                known[name] = uow.sources.ensure(name)
        latest = uow.runs.latest_by_source()
        listed = [
            {
                "name": name,
                **_summary(access.spiders[name], known[name].settings),
                "latest_run": run_view(latest[known[name].id], name, access.spiders[name]) if known[name].id in latest else None,
            }
            for name in sorted(access.spiders)
        ]
    return {"sources": listed}


@router.get("/sources/{name}/settings")
def get_settings(name: str, access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)) -> dict:
    spider_cls = _known(access, name)
    with UnitOfWork(access.engine, access.blobs) as uow:
        raw = uow.sources.ensure(name).settings
    return settings_view(spider_cls, raw)


@router.put("/sources/{name}/settings")
def put_settings(
    name: str, body: dict[str, Any] = JsonBody(...), access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)
) -> dict:
    spider_cls = _known(access, name)
    try:
        settings = load_settings(spider_cls, body)
    except SettingsInvalid as error:
        raise ApiError(422, "invalid_settings", error.message) from None
    saved = settings.model_dump(mode="json")
    with UnitOfWork(access.engine, access.blobs) as uow:
        uow.sources.set_settings(uow.sources.ensure(name).id, saved)
    return settings_view(spider_cls, saved)


@router.get("/sources/{name}/runs")
def history(
    name: str, limit: int = Query(HISTORY_DEFAULT, ge=1, le=HISTORY_MAX), access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)
) -> dict:
    spider_cls = _known(access, name)
    with UnitOfWork(access.engine, access.blobs) as uow:
        source = uow.sources.get(name)
        runs = uow.runs.recent(source.id, limit) if source else []
    return {"runs": [run_view(run, name, spider_cls) for run in runs]}


@router.post("/sources/{name}/runs", status_code=201)
def start(name: str, body: StartBody, access: PipelineAccess = Depends(pipeline_of), user_id: str = Depends(require_admin)) -> dict:
    spider_cls = _known(access, name)
    try:
        with UnitOfWork(access.engine, access.blobs) as uow:
            source = uow.sources.ensure(name)
            options = _options_for(spider_cls, source.settings, kind=body.kind, location_id=body.location, max_requests=body.max_requests)
            run = uow.runs.enqueue(source.id, kind=body.kind, requested_by=user_id, options=options)
    except SourceBusy:
        raise _busy() from None
    return run_view(run, name, spider_cls)


@router.get("/runs/{run_id}")
def one_run(run_id: int, access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)) -> dict:
    with UnitOfWork(access.engine, access.blobs) as uow:
        run = uow.runs.get(run_id)
        source = uow.sources.by_id(run.source_id) if run else None
    if run is None or source is None:
        raise _no_run()
    return run_view(run, source.name, access.spiders.get(source.name), detail=True)


@router.get("/runs/{run_id}/fetches")
def fetches(run_id: int, after: int = Query(0, ge=0), access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)) -> dict:
    with UnitOfWork(access.engine, access.blobs) as uow:
        if uow.runs.get(run_id) is None:
            raise _no_run()
        rows = uow.fetches.since(run_id, after, FETCH_PAGE)
    return {"fetches": [fetch_view(row) for row in rows]}


@router.post("/runs/{run_id}/stop", status_code=202)
def stop(run_id: int, access: PipelineAccess = Depends(pipeline_of), _: str = Depends(require_admin)) -> dict:
    with UnitOfWork(access.engine, access.blobs) as uow:
        before = uow.runs.get(run_id)
        if before is None:
            raise _no_run()
        run = uow.runs.request_cancel(run_id)
        source = uow.sources.by_id(before.source_id)
    if run is None:
        raise ApiError(409, "not_active", "This run has already finished.")
    return run_view(run, source.name, access.spiders.get(source.name))


@router.post("/runs/{run_id}/retry", status_code=201)
def retry(run_id: int, access: PipelineAccess = Depends(pipeline_of), user_id: str = Depends(require_admin)) -> dict:
    try:
        with UnitOfWork(access.engine, access.blobs) as uow:
            old = uow.runs.get(run_id)
            source = uow.sources.by_id(old.source_id) if old else None
            if old is None or source is None:
                raise _no_run()
            if old.status not in RETRYABLE:
                raise ApiError(409, "not_retryable", "Only a failed or stopped run can be retried.")
            spider_cls = _known(access, source.name)
            entry = old.options.get("location")
            location_id = entry.get(spider_cls.location_id_field) if isinstance(entry, dict) else None
            if spider_cls.locations_setting and location_id is None:
                raise ApiError(409, "unknown_location", "That run did not record a location. Start a new run and pick one.")
            options = _options_for(
                spider_cls, source.settings, kind=old.kind, location_id=location_id, max_requests=old.options.get("max_requests"), location_status=409
            )
            run = uow.runs.enqueue(old.source_id, kind=old.kind, retry_of=old.id, requested_by=user_id, options=options)
    except SourceBusy:
        raise _busy() from None
    return run_view(run, source.name, spider_cls)
```

- [ ] **Step 4: Run the API tests**

Run: `uv run pytest -q tests/test_api_pipeline_settings.py tests/test_api_pipeline.py`
Expected: PASS. If the over-long `geo_id` case is refused by the request-size limit rather than by validation (a different status), lower the string to `"x" * 101` so it reaches the model; the test's point is the field path.

- [ ] **Step 5: Run the whole Python suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/scraper/api/pipeline.py tests/conftest.py tests/test_api_pipeline.py tests/test_api_pipeline_settings.py
git commit -m "Add the spider settings routes, and locations on runs"
```

---

### Task 6: Web foundation: types, pure helpers and hooks for settings

**Files:**
- Modify: `web/src/lib/spiders/types.ts` (full replacement), `web/src/lib/spiders/derive.ts` (`endNote`), `web/src/components/spiders/queries.ts`
- Create: `web/src/lib/spiders/settings.ts`
- Test: `web/tests/spiders/settings.test.ts` (new), `web/tests/spiders/derive.test.ts` (edit)

**Interfaces:**
- Consumes: Task 5's route shapes.
- Produces (in `lib/spiders/settings.ts`; all pure):
  - `FieldRow = { name: string; shown: boolean }`; `rowsFromSettings(fields, visible)`, `visibleFromRows(rows, fields)`, `toggleRow(rows, index)`, `moveRow(rows, index, delta)`.
  - `columnsFor(fields, visible) -> string[]`, `fieldsNote(shown, total) -> string | null`.
  - `locationOptions(view) -> LocationOption[]`, `capChoices(saved) -> number[]`, `asStrings(value) -> string[]`.
  - `numberFrom(text) -> number | null`, `isChanged(a, b) -> boolean`, `singular(label) -> string`.
  - `addRecord(list, fields)`, `removeRecord(list, index)`, `setCell(list, index, key, value)`.
  - `parseProblem(message) -> { key, row, field } | null`.
  - Hooks: `useSettings(name)`, `useSaveSettings(name, onSaved, onError)`; `useRunActions().start` accepts `location`.
  - `endNote(run)` now reports a cap for any run kind: "Stopped at the request cap (N requests)."

- [ ] **Step 1: Write the failing tests.** Create `web/tests/spiders/settings.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  addRecord, asStrings, capChoices, columnsFor, fieldsNote, isChanged, locationOptions, moveRow, numberFrom, parseProblem, removeRecord,
  rowsFromSettings, setCell, singular, toggleRow, visibleFromRows,
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
```

In `web/tests/spiders/derive.test.ts`: add `location: null,` to the `run()` fixture's object (after `error: null`), and replace the `endNote` test with:

```ts
  it("explains how a run ended at its request cap, whichever kind it was", () => {
    expect(endNote(run({ kind: "trial", status: "completed", options: { max_requests: 5 }, stats: { stopped_at_cap: true } }))).toBe("Stopped at the request cap (5 requests).");
    expect(endNote(run({ status: "completed", options: { settings: { max_requests: 40 } }, stats: { stopped_at_cap: true } }))).toBe("Stopped at the request cap (40 requests).");
    expect(endNote(run({ status: "completed", stats: { stopped_at_cap: true } }))).toBe("Stopped at the request cap.");
    expect(endNote(run({ status: "completed" }))).toBeNull();
  });
```

- [ ] **Step 2: Run to verify they fail**

Run: `npm --prefix web test -- tests/spiders`
Expected: FAIL (`settings.ts` does not exist; the `endNote` text differs).

- [ ] **Step 3: Replace `web/src/lib/spiders/types.ts`:**

```ts
/** What the Python API's /pipeline routes send. */

export type RunStatus = "queued" | "running" | "completed" | "paused" | "failed" | "cancelled";
export type RunKind = "live" | "trial";

export type RejectedSample = { item: Record<string, string>; errors: string[] };

export type RunStats = {
  requests_count?: number;
  blocked_requests_count?: number;
  failed_requests_count?: number;
  items_scraped?: number;
  records_created?: number;
  records_changed?: number;
  records_unchanged?: number;
  items_rejected?: number;
  rejected_by_field?: Record<string, number>;
  /** Only on a run's own view (GET /pipeline/runs/{id}), not in lists. */
  rejected_samples?: RejectedSample[];
  trial_items?: Record<string, unknown>[];
  stopped_at_cap?: boolean;
};

/** A place a run can crawl, as the person sees it: the id a run is started with and the name shown for it. */
export type LocationOption = { id: string; label: string };

export type RunOptions = {
  /** A trial's request count. */
  max_requests?: number;
  /** The whole entry the run crawled, with every one of its bound values. */
  location?: Record<string, unknown>;
  /** The settings the run was queued with. */
  settings?: Record<string, unknown>;
};

export type Run = {
  id: number;
  source: string;
  kind: RunKind;
  status: RunStatus;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  heartbeat_at: string | null;
  retry_of: number | null;
  cancel_requested: boolean;
  options: RunOptions;
  stats: RunStats;
  error: string | null;
  location: LocationOption | null;
};

export type SourceSummary = { name: string; latest_run: Run | null; locations: LocationOption[]; trial_max_requests: number };

export type FetchRow = { id: number; url: string; final_url: string; status: number; fetched_at: string; duration_ms: number | null; size: number };

/** How the API describes one of a spider's own settings, so the form can be drawn without knowing the spider. */
export type FormField = {
  key: string;
  label: string;
  description: string | null;
  type: "integer" | "number" | "string" | "boolean" | "array";
  nullable: boolean;
  default: unknown;
  minimum: number | null;
  maximum: number | null;
  minLength: number | null;
  maxLength: number | null;
  maxItems: number | null;
  /** For a list: the fields of each record. */
  items?: FormField[];
};

export type SettingsView = {
  /** The saved values with defaults filled in, or the stored values as they are when they no longer validate (see `problems`). */
  settings: Record<string, unknown>;
  defaults: Record<string, unknown>;
  problems: string[];
  form: FormField[];
  /** The names of the spider's item fields, in the item's own order. */
  fields: string[];
  locations_setting: string | null;
  location_id_field: string;
  location_label_field: string;
};
```

- [ ] **Step 4: Update `endNote` in `web/src/lib/spiders/derive.ts`** to:

```ts
export function endNote(run: Run): string | null {
  if (!run.stats.stopped_at_cap) return null;
  const saved = run.options.settings?.max_requests;
  const cap = run.kind === "trial" ? run.options.max_requests : typeof saved === "number" ? saved : undefined;
  return cap ? `Stopped at the request cap (${cap} requests).` : "Stopped at the request cap.";
}
```

- [ ] **Step 5: Create `web/src/lib/spiders/settings.ts`:**

```ts
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
```

- [ ] **Step 6: Add the hooks to `web/src/components/spiders/queries.ts`.** Change the type import to `import type { FetchRow, Run, SettingsView, SourceSummary } from "../../lib/spiders/types";`, add `settings: (name: string) => ["pipeline", "settings", name] as const,` to `keys`, and add:

```ts
/** One spider's saved settings, with the description of its form. Not polled: it changes only when someone saves. */
export function useSettings(name: string) {
  const api = useApi();
  return useQuery({
    queryKey: keys.settings(name),
    queryFn: () => api.get<SettingsView>(`/pipeline/sources/${encodeURIComponent(name)}/settings`),
  });
}

/** Save the whole settings object. On success the saved view replaces the cached one and the lists refresh (the location picker reads them). */
export function useSaveSettings(name: string, onSaved: () => void, onError: (message: string) => void) {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (settings: Record<string, unknown>) => api.put<SettingsView>(`/pipeline/sources/${encodeURIComponent(name)}/settings`, settings),
    onSuccess: (view) => {
      client.setQueryData(keys.settings(name), view);
      void client.invalidateQueries({ queryKey: ["pipeline", "sources"] });
      onSaved();
    },
    onError: (error) => onError(messageOf(error)),
  });
}
```

In `useRunActions`, change the `start` mutation to take a location:

```ts
  const start = useMutation({
    mutationFn: ({ name, kind, maxRequests, location }: { name: string; kind: "live" | "trial"; maxRequests?: number; location?: string }) =>
      api.post<Run>(`/pipeline/sources/${encodeURIComponent(name)}/runs`, {
        kind,
        ...(kind === "trial" && maxRequests ? { max_requests: maxRequests } : {}),
        ...(location ? { location } : {}),
      }),
    onSuccess: (run) => {
      void refresh();
      onStarted?.(run);
    },
    onError: settle.onError,
  });
```

- [ ] **Step 7: Run the tests and the typecheck**

Run: `npm --prefix web test -- tests/spiders` then `npm --prefix web run typecheck`
Expected: tests PASS. The typecheck FAILS in `SpiderPage.tsx`/`SpiderTable.tsx` only if they use removed types; they do not yet use the new required `location` field, so it should pass. If it names `derive.test.ts`, add `location: null` to its fixture.

- [ ] **Step 8: Commit**

```bash
git add web/src/lib/spiders web/src/components/spiders/queries.ts web/tests/spiders
git commit -m "Add the settings types, pure helpers and hooks for the spiders page"
```

---

### Task 7: The settings screen, the location picker and visible fields

**Files:**
- Create: `web/src/components/spiders/SettingsForm.tsx`, `web/src/app/spiders/[name]/settings/page.tsx`
- Modify: `web/src/components/spiders/SpiderPage.tsx`, `SpiderTable.tsx`, `RunList.tsx`, `RunHeader.tsx`, `TrialResults.tsx`, `spiders.css`

**Interfaces:**
- Consumes: Task 6 (types, helpers, hooks). The app's `Toast`, `useToast`, `WorkspaceProviders`, `requireAdmin`, `getServices`, `SpidersFrame`.
- Produces: the page `/spiders/<name>/settings` (admin-only, covered by the proxy matcher already); the location select and Settings link on the spider page; a location select and Settings link on each list row; the run list and header show a run's location; the Parsed and Rejected tables follow the visible fields. Text the e2e spec relies on: "Settings saved" toast; buttons "Save settings", "Discard changes", "Add location", "Remove location 1"; labels "GeoId" and "GeoName"; the `Location` select (accessible name "Location"); "Showing N of M fields".

There is no component test harness (Vitest runs in `node`); behaviour that can be tested lives in `settings.ts` (Task 6). These screens are covered by the Playwright spec in Task 8 and the visual check there.

- [ ] **Step 1: Add the styles.** Append to `web/src/components/spiders/spiders.css`:

```css
/* Settings */
.sp-form { display: grid; gap: 1.75rem; max-width: 46rem; }
.sp-section { display: grid; gap: 0.7rem; }
.sp-section > h2 { font-size: var(--step-1); }
.sp-section .hint { color: var(--muted); font-size: var(--step--1); }
.sp-grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); gap: 0.9rem; }
.sp-field { display: grid; gap: 0.3rem; font-weight: 600; font-size: var(--step--1); }
.sp-field input { font-weight: 400; font-size: var(--step-0); }
.sp-field .hint { font-weight: 400; }
.sp-field .bad, .sp-bad { color: var(--bad); font-weight: 500; font-size: var(--step--1); }
.sp-rows { list-style: none; margin: 0; padding: 0; border: 1px solid var(--rule); border-radius: var(--radius-l); background: var(--sheet); overflow: hidden; }
.sp-rows li { display: flex; align-items: center; gap: 0.6rem; padding: 0.45rem 0.8rem; border-bottom: 1px solid var(--ledger-line); }
.sp-rows li:last-child { border-bottom: 0; }
.sp-rows .name { flex: 1; font-weight: 560; }
.sp-rows .off .name { color: var(--muted); font-weight: 400; }
.sp-rows .moves { display: flex; gap: 0.25rem; }
.sp-records { width: 100%; border-collapse: collapse; }
.sp-records th { text-align: left; font-size: var(--step--1); color: var(--muted); font-weight: 650; padding: 0.4rem 0.5rem; border-bottom: 2px solid var(--ink); }
.sp-records td { padding: 0.35rem 0.5rem; border-bottom: 1px solid var(--ledger-line); }
.sp-records input { width: 100%; }
.sp-records td.act { width: 1%; white-space: nowrap; }
.sp-savebar { display: flex; flex-wrap: wrap; align-items: center; gap: 0.6rem; padding-top: 0.4rem; }
.sp-where { color: var(--muted); font-size: var(--step--1); }
.sp-note-link { font-size: var(--step--1); color: var(--muted); }
@media (max-width: 40rem) {
  .sp-records thead { display: none; }
  .sp-records tr { display: grid; gap: 0.3rem; padding: 0.6rem 0; border-bottom: 1px solid var(--ledger-line); }
  .sp-records td { display: block; border: 0; padding: 0 0.25rem; }
  .sp-records td.act { width: auto; }
}
```

- [ ] **Step 2: Create `web/src/components/spiders/SettingsForm.tsx`:**

```tsx
"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import {
  addRecord, asStrings, isChanged, moveRow, numberFrom, parseProblem, removeRecord, rowsFromSettings, setCell, singular, toggleRow,
  visibleFromRows, type FieldRow,
} from "../../lib/spiders/settings";
import type { FormField, SettingsView } from "../../lib/spiders/types";
import { Toast, useToast } from "../Toast";
import { messageOf, useSaveSettings, useSettings } from "./queries";

type Values = Record<string, unknown>;

export function SettingsForm({ name }: { name: string }) {
  const query = useSettings(name);
  if (query.isPending) {
    return (
      <p role="status" className="muted">
        Loading settings…
      </p>
    );
  }
  if (query.isError) {
    return (
      <div className="error-box" role="alert">
        {messageOf(query.error)}{" "}
        <button className="link" type="button" onClick={() => query.refetch()}>
          Try again
        </button>
      </div>
    );
  }
  // Keyed by the saved values, so after a save the form starts again from what was saved.
  return <Editor key={JSON.stringify(query.data.settings)} name={name} view={query.data} />;
}

function NumberInput({ field, value, onChange, id }: { field: FormField; value: unknown; onChange: (next: number | null) => void; id: string }) {
  return (
    <input
      id={id}
      type="number"
      inputMode={field.type === "integer" ? "numeric" : "decimal"}
      step={field.type === "integer" ? 1 : "any"}
      min={field.minimum ?? undefined}
      max={field.maximum ?? undefined}
      value={typeof value === "number" ? value : ""}
      placeholder={field.nullable ? "Default" : undefined}
      onChange={(event) => onChange(numberFrom(event.target.value))}
    />
  );
}

function RecordsEditor({ field, value, onChange, problem }: { field: FormField; value: Values[]; onChange: (next: Values[]) => void; problem: string | null }) {
  const items = field.items ?? [];
  const full = field.maxItems !== null && value.length >= field.maxItems;
  const noun = singular(field.label);
  return (
    <div className="sp-section">
      <h2>{field.label}</h2>
      {field.description ? <p className="hint">{field.description}</p> : null}
      {value.length === 0 ? (
        <p className="sp-empty">No {noun}s yet. Add one so this spider has somewhere to run.</p>
      ) : (
        <div className="sp-table-wrap">
          <table className="sp-records">
            <thead>
              <tr>
                {items.map((item) => (
                  <th key={item.key} scope="col">
                    {item.label}
                  </th>
                ))}
                <th scope="col">
                  <span className="sr">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {value.map((record, row) => (
                <tr key={row}>
                  {items.map((item) => (
                    <td key={item.key}>
                      <input
                        type="text"
                        aria-label={`${item.label} of ${noun} ${row + 1}`}
                        maxLength={item.maxLength ?? undefined}
                        value={String(record[item.key] ?? "")}
                        onChange={(event) => onChange(setCell(value, row, item.key, event.target.value))}
                      />
                    </td>
                  ))}
                  <td className="act">
                    <button className="btn btn-small btn-quiet" type="button" aria-label={`Remove ${noun} ${row + 1}`} onClick={() => onChange(removeRecord(value, row))}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {problem ? (
        <p className="sp-bad" role="alert">
          {problem}
        </p>
      ) : null}
      <div>
        <button className="btn btn-small" type="button" disabled={full} onClick={() => onChange(addRecord(value, items))}>
          Add {noun}
        </button>
      </div>
    </div>
  );
}

function Editor({ name, view }: { name: string; view: SettingsView }) {
  const [values, setValues] = useState<Values>(() => ({ ...view.settings }));
  const [rows, setRows] = useState<FieldRow[]>(() => rowsFromSettings(view.fields, asStrings(view.settings.visible_fields)));
  const [problem, setProblem] = useState<string | null>(null);
  const [toast, setToast] = useToast();
  const save = useSaveSettings(name, () => setToast("Settings saved"), setProblem);

  const next: Values = { ...values, visible_fields: visibleFromRows(rows, view.fields) };
  const dirty = isChanged(next, view.settings);
  const where = parseProblem(problem);
  const about = (key: string) => (where?.key === key ? problem : null);
  const set = (key: string, value: unknown) => setValues((current) => ({ ...current, [key]: value }));

  function submit(event: FormEvent) {
    event.preventDefault();
    setProblem(null);
    save.mutate(next);
  }

  function discard() {
    setValues({ ...view.settings });
    setRows(rowsFromSettings(view.fields, asStrings(view.settings.visible_fields)));
    setProblem(null);
  }

  const common: FormField[] = [
    { key: "trial_max_requests", label: "Trial run requests", description: "How many requests a trial run starts with.", type: "integer", nullable: false, default: 5, minimum: 1, maximum: 20, minLength: null, maxLength: null, maxItems: null },
    { key: "max_requests", label: "Max requests per run", description: "Approximate when several requests run at once. Blank means no limit.", type: "integer", nullable: true, default: null, minimum: 1, maximum: 100000, minLength: null, maxLength: null, maxItems: null },
    { key: "request_delay", label: "Delay between requests (seconds)", description: "Blank keeps the spider's own.", type: "number", nullable: true, default: null, minimum: 0, maximum: 60, minLength: null, maxLength: null, maxItems: null },
    { key: "concurrency", label: "Requests at once", description: "Blank keeps the spider's own.", type: "integer", nullable: true, default: null, minimum: 1, maximum: 8, minLength: null, maxLength: null, maxItems: null },
  ];

  return (
    <form className="sp-form" onSubmit={submit} noValidate>
      {view.problems.length > 0 ? (
        <div className="error-box" role="alert">
          The saved settings are not valid, so runs are refused until they are fixed: {view.problems.join(" ")}
        </div>
      ) : null}
      {problem && !where ? (
        <div className="error-box" role="alert">
          {problem}
        </div>
      ) : null}

      <section className="sp-section" aria-labelledby="sp-fields-title">
        <h2 id="sp-fields-title">Fields shown in results</h2>
        <p className="hint">Tick the fields to show and put them in order. If nothing is ticked, every field is shown.</p>
        <ul className="sp-rows">
          {rows.map((row, index) => (
            <li key={row.name} className={row.shown ? "" : "off"}>
              <label className="name">
                <input type="checkbox" checked={row.shown} onChange={() => setRows((current) => toggleRow(current, index))} /> {row.name}
              </label>
              <span className="moves">
                <button className="btn btn-small btn-quiet" type="button" disabled={index === 0} aria-label={`Move ${row.name} up`} onClick={() => setRows((current) => moveRow(current, index, -1))}>
                  Up
                </button>
                <button className="btn btn-small btn-quiet" type="button" disabled={index === rows.length - 1} aria-label={`Move ${row.name} down`} onClick={() => setRows((current) => moveRow(current, index, 1))}>
                  Down
                </button>
              </span>
            </li>
          ))}
        </ul>
        {about("visible_fields") ? (
          <p className="sp-bad" role="alert">
            {about("visible_fields")}
          </p>
        ) : null}
      </section>

      <section className="sp-section" aria-labelledby="sp-limits-title">
        <h2 id="sp-limits-title">Trial runs and crawl limits</h2>
        <div className="sp-grid2">
          {common.map((field) => (
            <label key={field.key} className="sp-field" htmlFor={`f-${field.key}`}>
              {field.label}
              <NumberInput id={`f-${field.key}`} field={field} value={values[field.key]} onChange={(number) => set(field.key, number)} />
              {field.description ? <span className="hint">{field.description}</span> : null}
              {about(field.key) ? <span className="bad">{about(field.key)}</span> : null}
            </label>
          ))}
        </div>
      </section>

      {view.form.length > 0 ? (
        <section className="sp-section" aria-label={`${name} settings`}>
          {view.form.map((field) =>
            field.type === "array" ? (
              <RecordsEditor key={field.key} field={field} value={(values[field.key] as Values[] | undefined) ?? []} onChange={(list) => set(field.key, list)} problem={about(field.key)} />
            ) : (
              <label key={field.key} className="sp-field" htmlFor={`f-${field.key}`}>
                {field.label}
                {field.type === "integer" || field.type === "number" ? (
                  <NumberInput id={`f-${field.key}`} field={field} value={values[field.key]} onChange={(number) => set(field.key, number)} />
                ) : (
                  <input id={`f-${field.key}`} type="text" maxLength={field.maxLength ?? undefined} value={String(values[field.key] ?? "")} onChange={(event) => set(field.key, event.target.value)} />
                )}
                {field.description ? <span className="hint">{field.description}</span> : null}
                {about(field.key) ? <span className="bad">{about(field.key)}</span> : null}
              </label>
            ),
          )}
        </section>
      ) : null}

      <div className="sp-savebar">
        <button className="btn btn-primary" type="submit" disabled={!dirty || save.isPending}>
          Save settings
        </button>
        <button className="btn btn-quiet" type="button" disabled={!dirty || save.isPending} onClick={discard}>
          Discard changes
        </button>
        <Link className="sp-note-link" href={`/spiders/${name}`}>
          Back to {name}
        </Link>
      </div>
      <Toast message={toast} />
    </form>
  );
}
```

- [ ] **Step 3: Create the page** `web/src/app/spiders/[name]/settings/page.tsx`:

```tsx
import type { Metadata } from "next";
import Link from "next/link";
import { SettingsForm } from "../../../../components/spiders/SettingsForm";
import { SpidersFrame } from "../../../../components/spiders/SpidersFrame";
import { WorkspaceProviders } from "../../../../components/workspace/ApiProvider";
import { getServices } from "../../../../lib/services";
import { requireAdmin } from "../../../../lib/session";

type Props = { params: Promise<{ name: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { name } = await params;
  return { title: `${name} settings` };
}

export default async function SpiderSettingsRoute({ params }: Props) {
  await requireAdmin();
  const { name } = await params;
  return (
    <WorkspaceProviders apiUrl={getServices().env.apiUrl}>
      <SpidersFrame>
        <div>
          <Link className="sp-back small" href={`/spiders/${name}`}>
            {name}
          </Link>
          <h1>Settings</h1>
          <p className="sp-lede">What shows in this spider's results, how its crawls are limited, and where it can run. Changes apply to the next run you start.</p>
        </div>
        <SettingsForm name={name} />
      </SpidersFrame>
    </WorkspaceProviders>
  );
}
```

- [ ] **Step 4: Update `RunList.tsx`.** Replace the button body and the option text:

```tsx
            <button type="button" aria-current={run.id === selectedId} onClick={() => onSelect(run.id)}>
              <b>#{run.id}</b>
              <StatusPill run={run} />
              {run.location ? <span className="sp-where">{run.location.label}</span> : null}
              <span className="when">{when(run, now)}</span>
            </button>
```

```tsx
          <option key={run.id} value={run.id}>
            #{run.id} {run.kind === "trial" ? "trial " : ""}
            {run.location ? `${run.location.label}, ` : ""}
            {statusLabel(run.status).toLowerCase()}, {when(run, now)}
          </option>
```

- [ ] **Step 5: Update `RunHeader.tsx`.** In the `meta` array add, after the `Retry of` entry:

```tsx
    run.location ? `Location: ${run.location.label}` : null,
```

- [ ] **Step 6: Replace `TrialResults.tsx`** so the tables follow the visible fields:

```tsx
"use client";

import Link from "next/link";
import { useState } from "react";
import { asStrings, columnsFor, fieldsNote } from "../../lib/spiders/settings";
import type { Run, SettingsView } from "../../lib/spiders/types";

const text = (value: unknown): string => (typeof value === "string" ? value : value === null || value === undefined ? "" : JSON.stringify(value));

/** What a trial parsed and what a run rejected. A live run shows only its rejected samples. The columns follow the spider's visible
 *  fields (every field when none are chosen). A rejected item's reason always shows, so a hidden field cannot hide the problem.
 *  Give it `key={run.id}` so each run starts on its own first tab. */
export function TrialResults({ run, view }: { run: Run; view?: SettingsView }) {
  const trial = run.kind === "trial";
  const items = run.stats.trial_items ?? [];
  const rejected = run.stats.rejected_samples ?? [];
  const [tab, setTab] = useState<"parsed" | "rejected">(trial ? "parsed" : "rejected");
  if (!trial && rejected.length === 0) return null;
  const known = view?.fields ?? [...new Set([...items.flatMap((item) => Object.keys(item)), ...rejected.flatMap((sample) => Object.keys(sample.item))])];
  const columns = columnsFor(known, asStrings(view?.settings.visible_fields));
  const note = fieldsNote(columns.length, known.length);
  const byField = Object.entries(run.stats.rejected_by_field ?? {}).map(([field, count]) => `${field} ${count}`).join(", ");

  return (
    <section aria-label="What the spider parsed">
      <div className="sp-tabs" role="tablist">
        {trial ? (
          <button type="button" role="tab" id="tab-parsed" aria-selected={tab === "parsed"} aria-controls="panel-results" onClick={() => setTab("parsed")}>
            Parsed items ({items.length})
          </button>
        ) : null}
        <button type="button" role="tab" id="tab-rejected" aria-selected={tab === "rejected"} aria-controls="panel-results" onClick={() => setTab("rejected")}>
          Rejected items ({run.stats.items_rejected ?? 0})
        </button>
      </div>
      {note ? (
        <p className="sp-note-link">
          {note}.{" "}
          <Link href={`/spiders/${run.source}/settings`}>Change in Settings</Link>
        </p>
      ) : null}
      <div role="tabpanel" id="panel-results" aria-labelledby={tab === "parsed" ? "tab-parsed" : "tab-rejected"}>
        {tab === "parsed" ? (
          items.length === 0 ? (
            <p className="sp-empty">Nothing was parsed. Check the pages fetched above for a block page, then the spider's selectors.</p>
          ) : (
            <div className="sp-table-wrap">
              <table className="sp-table">
                <thead>
                  <tr>
                    {columns.map((column) => (
                      <th key={column} scope="col">
                        {column}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {items.map((item, row) => (
                    <tr key={row}>
                      {columns.map((column) => (
                        <td key={column} title={text(item[column])}>
                          {text(item[column]).slice(0, 80)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : rejected.length === 0 ? (
          <p className="sp-empty">{(run.stats.items_rejected ?? 0) === 0 ? "No items were rejected." : "The rejected items were counted but no samples were kept."}</p>
        ) : (
          <>
            {byField ? <p className="muted small">Fields that failed: {byField}.</p> : null}
            <ul className="sp-rejects">
              {rejected.map((sample, index) => (
                <li key={index}>
                  <dl className="fields">
                    {columns
                      .filter((column) => column in sample.item)
                      .map((column) => (
                        <div key={column} style={{ display: "contents" }}>
                          <dt>{column}</dt>
                          <dd>{sample.item[column]}</dd>
                        </div>
                      ))}
                  </dl>
                  <p className="why">{sample.errors.join("; ")}</p>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </section>
  );
}
```

- [ ] **Step 7: Replace `SpiderPage.tsx`** (adds the Settings link, the location select, the saved trial default, and passes the settings to the results):

```tsx
"use client";

import Link from "next/link";
import { useState } from "react";
import { isActive } from "../../lib/spiders/derive";
import { capChoices, locationOptions } from "../../lib/spiders/settings";
import { Toast, useToast } from "../Toast";
import { messageOf, useFetchFeed, useRun, useRunActions, useRuns, useSettings } from "./queries";
import { RunHeader } from "./RunHeader";
import { RunLedger } from "./RunLedger";
import { RunList } from "./RunList";
import { TrialResults } from "./TrialResults";
import { useNow } from "./useNow";

export function SpiderPage({ name }: { name: string }) {
  const runs = useRuns(name);
  const settings = useSettings(name);
  const view = settings.data;
  const places = locationOptions(view);
  const hasLocations = Boolean(view?.locations_setting);
  const [picked, setPicked] = useState<number | null>(null); // null follows the newest run
  const [cap, setCap] = useState<number | null>(null); // null follows the saved trial default
  const [place, setPlace] = useState<string | null>(null); // null is the first location
  const [toast, setToast] = useToast();
  const selectedId = picked ?? runs.data?.[0]?.id ?? null;
  const run = useRun(selectedId);
  const current = run.data; // a local, so TypeScript keeps the narrowing inside the handlers below
  const running = current ? isActive(current.status) : false;
  const feed = useFetchFeed(selectedId, running);
  const { start, stop, retry } = useRunActions(setToast, (created) => setPicked(created.id));
  const inFlight = runs.data?.some((item) => isActive(item.status)) ?? false;
  const now = useNow(inFlight);
  const busy = start.isPending || stop.isPending || retry.isPending;

  const savedTrial = typeof view?.settings.trial_max_requests === "number" ? view.settings.trial_max_requests : 5;
  const trialCap = cap ?? savedTrial;
  const locationId = places.some((option) => option.id === place) ? place! : (places[0]?.id ?? undefined);
  const needsLocation = hasLocations && places.length === 0;
  const invalid = (view?.problems.length ?? 0) > 0;
  const blocked = busy || inFlight || needsLocation || invalid;

  return (
    <>
      <div className="sp-head">
        <div>
          <Link className="sp-back small" href="/spiders">
            Spiders
          </Link>
          <h1>{name}</h1>
        </div>
        <div className="sp-controls">
          {places.length > 0 ? (
            <label>
              Location
              <select value={locationId} onChange={(event) => setPlace(event.target.value)}>
                {places.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label>
            Trial pages
            <select value={trialCap} onChange={(event) => setCap(Number(event.target.value))}>
              {capChoices(savedTrial).map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <button className="btn" type="button" disabled={blocked} onClick={() => start.mutate({ name, kind: "trial", maxRequests: trialCap, location: locationId })}>
            Trial run
          </button>
          <button className="btn btn-primary" type="button" disabled={blocked} onClick={() => start.mutate({ name, kind: "live", location: locationId })}>
            Run now
          </button>
          <Link className="btn btn-quiet" href={`/spiders/${name}/settings`}>
            Settings
          </Link>
        </div>
      </div>
      {needsLocation ? <p className="muted small">Add a location in Settings to run this spider.</p> : null}
      {invalid ? <p className="sp-notice">The saved settings are not valid, so runs are refused. Fix them in Settings.</p> : null}
      {inFlight ? <p className="muted small">A run is already in flight, so a new one cannot start until it ends.</p> : null}

      {runs.isPending ? (
        <p role="status" className="muted">
          Loading runs…
        </p>
      ) : runs.isError ? (
        <div className="error-box" role="alert">
          {messageOf(runs.error)}{" "}
          <button className="link" type="button" onClick={() => runs.refetch()}>
            Try again
          </button>
        </div>
      ) : runs.data.length === 0 ? (
        <p className="sp-empty">No runs yet. Run a trial first to check this spider against the site.</p>
      ) : (
        <div className="sp-grid">
          <RunList runs={runs.data} selectedId={selectedId} onSelect={setPicked} now={now} />
          <div className="sp-main">
            {run.isError ? (
              <div className="error-box" role="alert">
                {messageOf(run.error)}{" "}
                <button className="link" type="button" onClick={() => run.refetch()}>
                  Try again
                </button>
              </div>
            ) : !current ? (
              <p role="status" className="muted">
                Loading run…
              </p>
            ) : (
              <>
                <RunHeader run={current} now={now} busy={busy} onStop={() => stop.mutate(current.id)} onRetry={() => retry.mutate(current.id)} />
                <RunLedger rows={feed.data} running={running} />
                <TrialResults key={current.id} run={current} view={view} />
              </>
            )}
          </div>
        </div>
      )}
      <Toast message={toast} />
    </>
  );
}
```

- [ ] **Step 8: Replace `SpiderTable.tsx`** (a location select for a spider with several locations, the location passed on start, and a Settings link on each row):

```tsx
"use client";

import Link from "next/link";
import { useState } from "react";
import { ago, canRetry, canStop, isActive, isStopping } from "../../lib/spiders/derive";
import type { Run } from "../../lib/spiders/types";
import { Toast, useToast } from "../Toast";
import { messageOf, useRunActions, useSources } from "./queries";
import { StatusPill } from "./StatusPill";
import { useNow } from "./useNow";

function recordsNote(run: Run): string {
  const s = run.stats;
  if (run.kind === "trial") return `${s.items_scraped ?? 0} parsed`;
  return `${s.records_created ?? 0} new, ${s.records_changed ?? 0} changed`;
}

export function SpiderTable() {
  const sources = useSources();
  const [toast, setToast] = useToast();
  const { start, stop, retry } = useRunActions(setToast);
  const [chosen, setChosen] = useState<Record<string, string>>({}); // spider name to the location picked in its row
  const anyActive = sources.data?.some((source) => source.latest_run && isActive(source.latest_run.status)) ?? false;
  const now = useNow(anyActive);
  const busy = start.isPending || stop.isPending || retry.isPending;

  return (
    <>
      <div>
        <h1>Spiders</h1>
        <p className="sp-lede">Start a crawl, watch it as it runs, and check a spider against its site with a trial run before you trust it.</p>
      </div>
      {sources.isPending ? (
        <p role="status" className="muted">
          Loading spiders…
        </p>
      ) : sources.isError ? (
        <div className="error-box" role="alert">
          {messageOf(sources.error)}{" "}
          <button className="link" type="button" onClick={() => sources.refetch()}>
            Try again
          </button>
        </div>
      ) : sources.data.length === 0 ? (
        <p className="sp-empty">No spiders are registered. Add one to <code>SPIDERS</code> in <code>scraper/pipeline/sources</code> and restart the API.</p>
      ) : (
        <div className="sp-table-wrap">
          <table className="sp-table sp-list">
            <thead>
              <tr>
                <th scope="col">Spider</th>
                <th scope="col">Last run</th>
                <th scope="col">Records</th>
                <th scope="col">
                  <span className="sr">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {sources.data.map(({ name, latest_run: run, locations }) => {
                const place = locations.some((option) => option.id === chosen[name]) ? chosen[name] : locations[0]?.id;
                return (
                  <tr key={name}>
                    <td className="name">
                      <Link href={`/spiders/${name}`}>{name}</Link>
                      {locations.length > 1 ? (
                        <select
                          className="sp-where"
                          aria-label={`Location for ${name}`}
                          value={place}
                          onChange={(event) => setChosen((current) => ({ ...current, [name]: event.target.value }))}
                        >
                          {locations.map((option) => (
                            <option key={option.id} value={option.id}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      ) : locations.length === 1 ? (
                        <span className="sp-where"> {locations[0].label}</span>
                      ) : null}
                    </td>
                    <td>
                      {run ? (
                        <>
                          <StatusPill run={run} /> <span className="muted small">{ago(run.finished_at ?? run.started_at ?? run.queued_at, now)}</span>
                        </>
                      ) : (
                        <span className="muted">Never run</span>
                      )}
                    </td>
                    <td className="muted">{run ? recordsNote(run) : "None yet"}</td>
                    <td className="act">
                      {run && isActive(run.status) ? (
                        <button className="btn btn-small" type="button" disabled={busy || !canStop(run)} onClick={() => stop.mutate(run.id)}>
                          {isStopping(run) ? "Stopping…" : "Stop"}
                        </button>
                      ) : (
                        <>
                          {run && canRetry(run) ? (
                            <button className="btn btn-small" type="button" disabled={busy} onClick={() => retry.mutate(run.id)}>
                              Retry
                            </button>
                          ) : null}
                          <button className="btn btn-small btn-primary" type="button" disabled={busy} onClick={() => start.mutate({ name, kind: "live", location: place })}>
                            Run now
                          </button>
                          <button className="btn btn-small" type="button" disabled={busy} onClick={() => start.mutate({ name, kind: "trial", location: place })}>
                            Trial run
                          </button>
                        </>
                      )}
                      <Link className="btn btn-small btn-quiet" href={`/spiders/${name}/settings`}>
                        Settings
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <Toast message={toast} />
    </>
  );
}
```

- [ ] **Step 9: Typecheck and run the web tests**

Run: `npm --prefix web run typecheck` then `npm --prefix web test`
Expected: PASS. Fix any type error in the file it names; do not loosen types with `any`.

- [ ] **Step 10: Commit**

```bash
git add web/src/components/spiders web/src/app/spiders
git commit -m "Add the spider settings screen, the location picker and visible fields on results"
```

---

### Task 8: Browser test, live check and final run

**Files:**
- Create: `web/e2e/specs/settings.spec.ts`

**Interfaces:**
- Consumes: Tasks 1 to 7; the e2e helpers `createMember`, `ownerCookie`, `signInBrowser`.
- Produces: a browser spec that runs against the real web app and API with no worker (runs stay queued), and a checked-by-hand record of one real worker run through the new first-page address.

- [ ] **Step 1: Write the browser spec** `web/e2e/specs/settings.spec.ts`:

```ts
import { expect, test } from "@playwright/test";
import { createMember, ownerCookie, signInBrowser } from "../support/journeys";

// The stack has no worker, so a run stays queued: enough to check the screens, the API and the access rules end to end.
test.describe("spider settings", () => {
  test("are for administrators only", async ({ page, context }) => {
    const member = await createMember("nosettings");
    await signInBrowser(context, member.cookie);
    await page.goto("/spiders/realtor_ca/settings");
    await expect(page).toHaveURL(/\/$/);
    await context.clearCookies();
    await page.goto("/spiders/realtor_ca/settings");
    await expect(page).toHaveURL(/\/sign-in/);
  });

  test("a bad value shows its message under the table and saves nothing", async ({ page, context }) => {
    await signInBrowser(context, await ownerCookie());
    await page.goto("/spiders/realtor_ca/settings");
    await page.getByLabel("GeoName of location 1").fill("");
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "locations.0.geo_name" })).toBeVisible();
    await page.reload();
    await expect(page.getByLabel("GeoName of location 1")).toHaveValue("Toronto, ON");
  });

  test("adds a location, keeps it after a reload, and starts a run for it", async ({ page, context }) => {
    await signInBrowser(context, await ownerCookie());
    await page.goto("/spiders/realtor_ca/settings");
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Save settings" })).toBeDisabled(); // nothing changed yet
    await page.getByRole("button", { name: "Add location" }).click();
    await page.getByLabel("GeoId of location 2").fill("g2_e2e");
    await page.getByLabel("GeoName of location 2").fill("Ottawa, ON");
    await page.getByLabel("Pages per run").fill("2");
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Settings saved" })).toBeVisible();
    await page.reload();
    await expect(page.getByLabel("GeoName of location 2")).toHaveValue("Ottawa, ON");
    await expect(page.getByLabel("Pages per run")).toHaveValue("2");

    await page.goto("/spiders/realtor_ca");
    await page.getByLabel("Location", { exact: true }).selectOption({ label: "Ottawa, ON" });
    await page.getByRole("button", { name: "Trial run" }).click();
    await expect(page.getByText("Location: Ottawa, ON").first()).toBeVisible();
    await page.getByRole("button", { name: "Stop" }).click();
    await expect(page.getByText("Cancelled").first()).toBeVisible();
  });

  test("chooses which fields the results show, and in what order", async ({ page, context }) => {
    await signInBrowser(context, await ownerCookie());
    await page.goto("/spiders/realtor_ca/settings");
    await page.getByRole("checkbox", { name: "mls_no" }).uncheck();
    await page.getByRole("button", { name: "Move price up" }).click();
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Settings saved" })).toBeVisible();
    await page.reload();
    const rows = page.locator(".sp-rows li");
    await expect(rows.nth(1)).toContainText("price");
    await expect(rows.last()).toContainText("mls_no");
    await expect(rows.last().getByRole("checkbox")).not.toBeChecked();
  });
});
```

- [ ] **Step 2: Run the spec**

Run: `npm --prefix web run e2e:ui -- e2e/specs/settings.spec.ts` (needs `make test-db` running and `E2E_DATABASE_URL` exported, as in the test setup).
Expected: 4 passed. If a selector is ambiguous (for example `Location` matching more than one control), narrow it with the accessible name from the screen, and do not weaken what the test asserts.

- [ ] **Step 3: Run the spiders spec too**, since the list and spider pages changed:

Run: `npm --prefix web run e2e:ui -- e2e/specs/spiders.spec.ts`
Expected: 3 passed.

- [ ] **Step 4: Check a real run by hand, through the new first-page address** (spec open item 1). This needs the live site, so it is a manual check and nothing in the test suite touches it.

1. Start a throwaway stack with a worker, on the scratch database, and create and confirm the first administrator (`owner@example.com`, password `correct horse battery staple`, confirmation link in the web server's log). Use `web/scripts/stack.sh` (`source` it, set `E2E_DATABASE_URL`, call `STACK_PROVIDER=lmstudio stack_up`), then start `python -m scraper.pipeline worker` with `SCRAPER_DATABASE_URL="$E2E_DATABASE_URL"` and a writable `SCRAPER_PIPELINE_BLOB_DIR`.
2. Open `/spiders/realtor_ca`, set Trial pages to 1, run a trial for Toronto. Expect: "Completed", 1 request, and 11 or more parsed listings with ids and prices, in about 10 seconds. If page 1 returns no cards through the map-list address, that is a finding: stop and report it, because the spec then calls for a per-location `start_url` setting.
3. Ask the person for a second city's GeoId and GeoName (these are theirs to supply). Add it in Settings, run a 1-page trial for it, and check the listings are for that city. If none is supplied, say so in the report: only Toronto was checked.
4. In Settings, tick only `address` and `price`, save, and open the trial's page: the Parsed items table shows exactly those two columns, in that order, with the note "Showing 2 of 7 fields". Then clear the ticks and confirm every field returns.
5. At widths 1280 px and 400 px, light and dark, look at `/spiders/realtor_ca/settings` and `/spiders/realtor_ca`: no horizontal page scroll, the locations table stacks under 40 rem, focus rings are visible.
6. Shut the stack and worker down and confirm the ports are free.

Say plainly in the report which of these checks could not be done.

- [ ] **Step 5: Run everything**

Run: `uv run pytest -q`, then `npm --prefix web run typecheck`, `npm --prefix web test`, and `npm --prefix web run e2e:ui`.
Expected: all PASS. A failure of `tests/test_session.py::test_a_follow_up_is_not_injected_twice_when_the_delivered_flag_is_lost_in_a_crash` under full-suite load is a known flake that this work does not touch: rerun it alone and the suite once more, and report it by name.

- [ ] **Step 6: Commit**

```bash
git add web/e2e/specs/settings.spec.ts
git commit -m "Test the spider settings screens in the browser"
```

---

## Spec coverage

| Spec section | Where |
|---|---|
| 3 Decisions (storage, spider-declared model, snapshot, one location per run, bound records, retry, visible fields) | Tasks 1 to 5 |
| 4 Settings model (common fields and ranges; realtor `locations`, `max_pages`; `Location`; one-to-one; declared location fields; drawable types; item fields) | Tasks 1, 2 |
| 5 Storage and validation (`set_settings`; PUT replaces; stored-invalid behaviour; URL safety) | Tasks 3, 4, 5 |
| 6 How a run uses settings (queue snapshot, retry, worker, settings before `configure_sessions`, limits, request cap, realtor requests) | Tasks 3, 4, 5 |
| 7 API (settings GET and PUT, run location, list locations, retry) | Task 5 |
| 8 The page (settings screen, location picker, visible fields, files) | Tasks 6, 7 |
| 9 Testing | Tasks 1 to 8 |
| 10 Open items: 1 realtor page 1 by the map address (Task 8, Step 4), 2 realtor conversion committed first (done, `ecf57b1`), 3 live cap reads as completed (Tasks 3, 6: `stopped_at_cap` and the note), 4 records carry no location, 5 last write wins | Tasks 3, 6, 8 |

Deviations from the spec, deliberate: run views carry a top-level `location: {id, label}` (computed on the server, so the page never needs a spider's field names); a live run's cap is documented in the setting's own description as approximate when several requests run at once, because the cap is checked as each response arrives; the realtor spider's `MAX_PAGES` constant is replaced by the `max_pages` setting, with `DEFAULT_PAGES` kept as its default.
