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
