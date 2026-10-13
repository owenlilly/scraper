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
