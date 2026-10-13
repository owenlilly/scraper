"""The spiders page's routes: administrators only, and every refusal says what is wrong."""

import pytest

from scraper.pipeline.uow import UnitOfWork


@pytest.fixture
async def admin(pipeline_api):
    pipeline_api.add_user("boss", role="admin")
    return pipeline_api, pipeline_api.auth("boss")


def error_code(response):
    return response.json()["error"]["code"]


async def start(api, headers, name="books_toscrape", **body):
    return await api.client.post(f"/pipeline/sources/{name}/runs", headers=headers, json={"kind": "live", **body})


async def test_only_an_active_administrator_gets_in(pipeline_api):
    pipeline_api.add_user("boss", role="admin")
    pipeline_api.add_user("member")
    pipeline_api.add_user("exiled", role="admin", banned=True)
    assert (await pipeline_api.client.get("/pipeline/sources")).status_code == 401
    for user, status in (("boss", 200), ("member", 403), ("exiled", 403), ("stranger", 403)):
        response = await pipeline_api.client.get("/pipeline/sources", headers=pipeline_api.auth(user))
        assert response.status_code == status, user
        if status == 403:
            assert error_code(response) == "forbidden"


async def test_the_spiders_are_listed_with_their_latest_run(admin):
    api, headers = admin
    listed = (await api.client.get("/pipeline/sources", headers=headers)).json()["sources"]
    assert listed == [{"name": "books_toscrape", "latest_run": None, "locations": [], "trial_max_requests": 5}]
    run = (await start(api, headers)).json()
    listed = (await api.client.get("/pipeline/sources", headers=headers)).json()["sources"]
    assert listed[0]["latest_run"]["id"] == run["id"] and listed[0]["latest_run"]["status"] == "queued"


async def test_starting_a_run_queues_it_and_a_second_start_is_refused(admin):
    api, headers = admin
    response = await start(api, headers)
    run = response.json()
    assert response.status_code == 201
    assert (run["source"], run["kind"], run["status"], run["retry_of"]) == ("books_toscrape", "live", "queued", None)
    assert "max_requests" not in run["options"] and "location" not in run["options"] and run["location"] is None
    assert run["queued_at"] and run["started_at"] is None and run["heartbeat_at"] is None
    again = await start(api, headers)
    assert again.status_code == 409 and error_code(again) == "source_busy"


async def test_a_trial_carries_its_cap_and_defaults_to_five(admin):
    api, headers = admin
    first = (await start(api, headers, kind="trial", max_requests=3)).json()
    assert first["options"]["max_requests"] == 3
    await api.client.post(f"/pipeline/runs/{first['id']}/stop", headers=headers)  # frees the source for the next one
    assert (await start(api, headers, kind="trial")).json()["options"]["max_requests"] == 5


@pytest.mark.parametrize("body", [{"kind": "trial", "max_requests": 0}, {"kind": "trial", "max_requests": 21}, {"kind": "live", "max_requests": 3}, {"kind": "odd"}, {"extra": 1}])
async def test_a_bad_start_request_is_refused_with_422(admin, body):
    api, headers = admin
    response = await api.client.post("/pipeline/sources/books_toscrape/runs", headers=headers, json=body)
    assert response.status_code == 422 and error_code(response) == "invalid"


async def test_an_unknown_spider_or_run_is_a_404(admin):
    api, headers = admin
    for response in (
        await start(api, headers, name="nope"),
        await api.client.get("/pipeline/sources/nope/runs", headers=headers),
        await api.client.get("/pipeline/runs/999999", headers=headers),
        await api.client.get("/pipeline/runs/999999/fetches", headers=headers),
        await api.client.post("/pipeline/runs/999999/stop", headers=headers),
        await api.client.post("/pipeline/runs/999999/retry", headers=headers),
    ):
        assert response.status_code == 404 and error_code(response) == "not_found"
    assert (await api.client.get("/pipeline/runs/1/fetches?after=-1", headers=headers)).status_code == 422


async def test_stopping_a_queued_run_cancels_it_and_a_finished_run_cannot_be_stopped(admin, pipeline, blobs):
    api, headers = admin
    run = (await start(api, headers)).json()
    stopped = await api.client.post(f"/pipeline/runs/{run['id']}/stop", headers=headers)
    assert stopped.status_code == 202 and stopped.json()["status"] == "cancelled"
    again = await api.client.post(f"/pipeline/runs/{run['id']}/stop", headers=headers)
    assert again.status_code == 409 and error_code(again) == "not_active"


async def test_stopping_a_running_run_flags_it_and_stopping_twice_is_fine(admin, pipeline, blobs):
    api, headers = admin
    run = (await start(api, headers)).json()
    with UnitOfWork(pipeline, blobs) as uow:
        uow.runs.start(run["id"], "w")
    for _ in range(2):
        response = await api.client.post(f"/pipeline/runs/{run['id']}/stop", headers=headers)
        assert response.status_code == 202 and response.json()["cancel_requested"] is True and response.json()["status"] == "running"


async def test_only_a_failed_or_stopped_run_can_be_retried_and_the_retry_points_back(admin, pipeline, blobs):
    api, headers = admin
    run = (await start(api, headers, kind="trial", max_requests=2)).json()
    refused = await api.client.post(f"/pipeline/runs/{run['id']}/retry", headers=headers)
    assert refused.status_code == 409 and error_code(refused) == "not_retryable"
    with UnitOfWork(pipeline, blobs) as uow:
        uow.runs.finish(uow.runs.start(run["id"], "w").id, "failed", {}, "boom")
    retry = await api.client.post(f"/pipeline/runs/{run['id']}/retry", headers=headers)
    body = retry.json()
    assert retry.status_code == 201 and body["retry_of"] == run["id"] and body["kind"] == "trial" and body["options"]["max_requests"] == 2
    busy = await api.client.post(f"/pipeline/runs/{run['id']}/retry", headers=headers)
    assert busy.status_code == 409 and error_code(busy) == "source_busy"


async def test_history_and_the_run_view_split_the_heavy_stats(admin, pipeline, blobs):
    api, headers = admin
    run = (await start(api, headers, kind="trial")).json()
    stats = {"requests_count": 2, "trial_items": [{"title": "x"}], "rejected_samples": [{"item": {}, "errors": ["e"]}]}
    with UnitOfWork(pipeline, blobs) as uow:
        uow.runs.finish(uow.runs.start(run["id"], "w").id, "completed", stats)
    listed = (await api.client.get("/pipeline/sources/books_toscrape/runs", headers=headers)).json()["runs"]
    assert listed[0]["stats"] == {"requests_count": 2}
    assert (await api.client.get(f"/pipeline/runs/{run['id']}", headers=headers)).json()["stats"] == stats
    assert (await api.client.get("/pipeline/sources/books_toscrape/runs?limit=0", headers=headers)).status_code == 422


async def test_the_fetch_feed_returns_only_what_is_newer_than_the_cursor(admin, pipeline, blobs):
    api, headers = admin
    run = (await start(api, headers)).json()
    with UnitOfWork(pipeline, blobs) as uow:
        uow.runs.start(run["id"], "w")
        ids = [uow.fetches.save(run["id"], url=f"https://x.test/{n}", final_url=f"https://x.test/{n}", status=200, headers={}, body=b"b" * n, fetcher="t").id for n in (1, 2)]
    feed = (await api.client.get(f"/pipeline/runs/{run['id']}/fetches", headers=headers)).json()["fetches"]
    assert [f["id"] for f in feed] == ids and feed[0]["url"] == "https://x.test/1" and feed[1]["size"] == 2 and feed[0]["status"] == 200
    later = (await api.client.get(f"/pipeline/runs/{run['id']}/fetches?after={ids[0]}", headers=headers)).json()["fetches"]
    assert [f["id"] for f in later] == [ids[1]]


async def test_a_server_without_the_pipeline_says_so(pg, auth_users):
    from api_support import Harness

    harness = Harness(pg).build()
    try:
        auth_users("boss", role="admin")
        response = await harness.client.get("/pipeline/sources", headers=harness.auth("boss"))
        assert response.status_code == 503 and error_code(response) == "pipeline_unavailable"
    finally:
        await harness.close()


def test_only_real_pipeline_spiders_are_runnable(monkeypatch):
    from scraper.api.pipeline import runnable_spiders
    from scraper.pipeline.sources import SPIDERS

    class Plain:
        name = "plain"

    monkeypatch.setitem(SPIDERS, "plain", Plain)
    assert "plain" not in runnable_spiders() and "books_toscrape" in runnable_spiders()
