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
