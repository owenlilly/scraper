import json

import pytest

from api_support import A, B, MODEL, WEB, frames, names
from fakes import ai, call
from scraper.llm import Event


# ---- the work routes ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/scrape", "/scrape/cancel", "/session/new", "/ask", "/ask/retry", "/agent/run", "/agent/resume", "/agent/proceed", "/agent/stop", "/agent/discard", "/agent/steer"],
)
async def test_every_work_route_needs_a_token(api, path):
    response = await api.client.post(path, json={})
    assert response.status_code == 401 and response.json()["error"]["code"] == "unauthorized"


async def test_oversized_and_unknown_fields_are_rejected_without_echoing_the_input(api):
    secret = "SECRET-" + "x" * 9000
    response = await api.client.post("/ask", json={"question": secret, "model": MODEL}, headers=api.auth())
    assert response.status_code == 422 and "SECRET" not in response.text
    response = await api.client.post("/scrape", json={"url": A, "surprise": 1}, headers=api.auth())
    assert response.status_code == 422
    response = await api.client.post("/scrape", content=b"not json", headers={**api.auth(), "Content-Type": "application/json"})
    assert response.status_code == 422


# ---- scraping ------------------------------------------------------------------------------


async def test_scrape_returns_the_pages_it_fetched(api):
    response = await api.client.post("/scrape", json={"url": A}, headers=api.auth())
    assert response.status_code == 200 and [p["url"] for p in response.json()["pages"]] == [A]
    assert (await api.client.get("/pages/0", headers=api.auth())).json()["markdown"] == WEB[A].markdown


async def test_a_failed_fetch_is_a_400_with_the_hint(api):
    response = await api.client.post("/scrape", json={"url": "https://site.test/missing"}, headers=api.auth())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "fetch_failed" and "404" in response.json()["error"]["message"]


async def test_a_mode_this_server_does_not_offer_is_a_400(api):
    response = await api.client.post("/scrape", json={"url": A, "mode": "stealthy"}, headers=api.auth())
    assert response.status_code == 400 and "not available" in response.json()["error"]["message"]


async def test_private_addresses_cannot_be_scraped(make_api):
    from scraper.netguard import check_public_url

    api = make_api(url_check=check_public_url)  # the real guard, not the test stand-in
    for url in ["http://127.0.0.1/", "http://localhost:8080/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/"]:
        response = await api.client.post("/scrape", json={"url": url}, headers=api.auth())
        assert response.status_code == 400, url
        assert "public address" in response.json()["error"]["message"] or "Could not find" in response.json()["error"]["message"]


async def test_a_busy_session_refuses_a_second_scrape_with_a_409(api):
    entry = await api.deps.registry.get("alice")
    entry.session.busy = True
    response = await api.client.post("/scrape", json={"url": A}, headers=api.auth())
    assert response.status_code == 409 and response.json()["error"]["code"] == "busy"


async def test_the_daily_page_limit_is_a_429_with_the_reset_time(make_api):
    api = make_api(daily_pages=1)
    assert (await api.client.post("/scrape", json={"url": A}, headers=api.auth())).status_code == 200
    response = await api.client.post("/scrape", json={"url": B}, headers=api.auth())
    assert response.status_code == 429 and "00:00 UTC" in response.json()["error"]["message"]


# ---- asking -----------------------------------------------------------------------------------


async def test_ask_streams_reasoning_then_the_answer_then_an_end_frame(api):
    await api.client.post("/scrape", json={"url": A}, headers=api.auth())
    response = await api.client.post("/ask", json={"question": "price?", "model": MODEL}, headers=api.auth())
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert names(response) == ["reasoning", "answer", "end"]
    end = frames(response)[-1][1]
    assert end["interrupted"] is False and end["usage"]["model_calls"] == 1
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert [m["role"] for m in state["history"]] == ["user", "assistant"] and state["history"][1]["content"] == "Ten dollars."
    assert state["pages"][0]["used"] is True


async def test_a_model_that_is_not_offered_is_refused_before_anything_streams(make_api):
    api = make_api(allowed_models=MODEL)
    await api.client.post("/scrape", json={"url": A}, headers=api.auth())
    response = await api.client.post("/ask", json={"question": "price?", "model": "other-model"}, headers=api.auth())
    assert response.status_code == 400 and response.json()["error"]["code"] == "unknown_model"
    assert api.llm.calls == []


async def test_asking_with_no_pages_streams_an_error_event(api):
    response = await api.client.post("/ask", json={"question": "price?", "model": MODEL}, headers=api.auth())
    assert names(response) == ["error", "end"] and "Scrape a page first" in frames(response)[0][1]["text"]


async def test_the_daily_model_call_limit_refuses_up_front_with_a_429(make_api):
    api = make_api(daily_model_calls=1)
    await api.client.post("/scrape", json={"url": A}, headers=api.auth())
    assert (await api.client.post("/ask", json={"question": "one?", "model": MODEL}, headers=api.auth())).status_code == 200
    response = await api.client.post("/ask", json={"question": "two?", "model": MODEL}, headers=api.auth())
    assert response.status_code == 429 and response.json()["error"]["code"] == "limit"
    assert len(api.llm.calls) == 1


async def test_a_full_server_refuses_new_streams_with_a_429_and_frees_up_afterwards(make_api):
    api = make_api(max_concurrent_runs=1)
    await api.client.post("/scrape", json={"url": A}, headers=api.auth())
    with api.deps.slots.hold():
        response = await api.client.post("/ask", json={"question": "price?", "model": MODEL}, headers=api.auth())
        assert response.status_code == 429 and response.json()["error"]["code"] == "server_busy"
    assert (await api.client.post("/ask", json={"question": "price?", "model": MODEL}, headers=api.auth())).status_code == 200
    assert api.deps.slots.active == 0


async def test_retrying_with_nothing_interrupted_is_a_409(api):
    response = await api.client.post("/ask/retry", json={"model": MODEL}, headers=api.auth())
    assert response.status_code == 409 and response.json()["error"]["code"] == "nothing_to_retry"


async def test_an_interrupted_answer_can_be_retried(api):
    await api.client.post("/scrape", json={"url": A}, headers=api.auth())
    api.llm.events = [Event("answer", "Ten dol"), Event("error", "Cannot reach Mistral")]
    first = await api.client.post("/ask", json={"question": "price?", "model": MODEL}, headers=api.auth())
    assert frames(first)[-1][1]["interrupted"] is True
    api.llm.events = [Event("answer", "Ten dollars.")]
    again = await api.client.post("/ask/retry", json={"model": MODEL}, headers=api.auth())
    assert names(again) == ["answer", "end"]
    history = (await api.client.get("/state", headers=api.auth())).json()["history"]
    assert [m["content"] for m in history] == ["price?", "Ten dollars."]


# ---- the agent -----------------------------------------------------------------------------------


async def test_an_agent_run_streams_its_steps_and_fills_the_results_table(api):
    api.model.script = [
        ai("", call("fetch_page", "c1", url=A)),
        ai("", call("add_rows", "c2", rows=[{"plan": "Starter", "price": "$10"}])),
        ai("Found one plan."),
    ]
    response = await api.client.post("/agent/run", json={"instruction": f"collect plans from {A}", "model": MODEL}, headers=api.auth())
    kinds = names(response)
    assert kinds[-2:] == ["done", "end"] and "tool_start" in kinds and "tool_result" in kinds and "rows_added" in kinds
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert state["rows"] == [{"plan": "Starter", "price": "$10"}] and state["run"]["status"] == "done"
    assert state["history"][-1]["note"] == "1 rows added to the results table"
    csv = await api.client.get("/rows.csv", headers=api.auth())
    assert csv.text == "plan,price\nStarter,$10\n" and "attachment" in csv.headers["content-disposition"]
    assert json.loads((await api.client.get("/rows.json", headers=api.auth())).text) == [{"plan": "Starter", "price": "$10"}]


async def test_a_paused_run_waits_for_an_answer_and_resumes_with_it(api):
    api.model.script = [ai("", call("ask_user", "c1", question="Which plan?", options=["Starter", "Team"])), ai("Starter it is.")]
    first = await api.client.post("/agent/run", json={"instruction": "pick a plan", "model": MODEL}, headers=api.auth())
    pause = next(d for n, d in frames(first) if n == "needs_input")
    assert pause["payload"]["question"] == "Which plan?" and names(first)[-1] == "end"
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert state["run"]["status"] == "paused" and state["run"]["pending"]["options"] == ["Starter", "Team"]
    resumed = await api.client.post("/agent/resume", json={"value": "Starter"}, headers=api.auth())
    assert names(resumed)[-2:] == ["done", "end"]


@pytest.mark.parametrize(
    "value",
    [{"action": "teleport"}, {"allow": "yes"}, {"note": "x"}, {"action": "continue", "extra": 1}, "", "x" * 2001, {"action": "continue", "note": "n" * 2001}, 5, None],
)
async def test_resume_answers_are_validated(api, value):
    response = await api.client.post("/agent/resume", json={"value": value}, headers=api.auth())
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid"


async def test_resume_without_a_paused_run_is_a_409(api):
    response = await api.client.post("/agent/resume", json={"value": "yes"}, headers=api.auth())
    assert response.status_code == 409 and response.json()["error"]["code"] == "no_paused_run"
    assert (await api.client.post("/agent/proceed", headers=api.auth())).status_code == 409


async def test_a_run_stopped_by_the_user_can_be_proceeded(api):
    api.model.script = [ai("", call("ask_user", "c1", question="Which?")), ai("Done.")]
    await api.client.post("/agent/run", json={"instruction": "x", "model": MODEL}, headers=api.auth())
    discard = await api.client.post("/agent/discard", headers=api.auth())
    assert discard.json() == {"drafts": []}
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert state["run"]["status"] == "ended" and state["run"]["resumable"] is False


async def test_steering_queues_a_message_only_while_a_run_is_live(api):
    assert (await api.client.post("/agent/steer", json={"text": "also the SKU"}, headers=api.auth())).json()["status"] == "no_run"
    api.model.script = [ai("", call("ask_user", "c1", question="Which?"))]
    await api.client.post("/agent/run", json={"instruction": "x", "model": MODEL}, headers=api.auth())
    queued = (await api.client.post("/agent/steer", json={"text": "also the SKU"}, headers=api.auth())).json()
    assert queued["status"] == "queued" and queued["id"]
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert state["run"]["followups"] == [{"id": queued["id"], "text": "also the SKU", "delivered": False}]


async def test_the_daily_model_limit_ends_an_agent_run_with_an_error_event_and_keeps_it_resumable(make_api):
    api = make_api(daily_model_calls=1)
    api.model.script = [ai("", call("fetch_page", "c1", url=A)), ai("Done.")]
    response = await api.client.post("/agent/run", json={"instruction": f"read {A}", "model": MODEL}, headers=api.auth())
    errors = [d for n, d in frames(response) if n == "error"]
    assert errors and "model-call limit" in errors[0]["text"]
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert state["run"]["status"] == "error" and state["run"]["resumable"] is True


# ---- users never see each other -----------------------------------------------------------------------------


async def test_one_users_pages_rows_history_and_runs_are_invisible_to_another(api):
    api.model.script = [ai("", call("ask_user", "c1", question="Which?"))]
    await api.client.post("/scrape", json={"url": A}, headers=api.auth("alice"))
    await api.client.post("/agent/run", json={"instruction": "x", "model": MODEL}, headers=api.auth("alice"))
    bob = (await api.client.get("/state", headers=api.auth("bob"))).json()
    assert (bob["pages"], bob["rows"], bob["history"], bob["run"]) == ([], [], [], None)
    assert (await api.client.get("/pages/0", headers=api.auth("bob"))).status_code == 404
    assert (await api.client.get("/rows.csv", headers=api.auth("bob"))).text == ""
    assert (await api.client.post("/agent/steer", json={"text": "hi"}, headers=api.auth("bob"))).json()["status"] == "no_run"
    assert (await api.client.post("/agent/resume", json={"value": "x"}, headers=api.auth("bob"))).status_code == 409
    assert (await api.client.post("/agent/proceed", headers=api.auth("bob"))).status_code == 409
    assert (await api.client.get("/context", headers=api.auth("bob"))).json()["sources"] == []


async def test_one_users_new_session_leaves_the_other_untouched(api):
    for user in ("alice", "bob"):
        await api.client.post("/scrape", json={"url": A}, headers=api.auth(user))
    assert (await api.client.post("/session/new", headers=api.auth("alice"))).status_code == 204
    assert (await api.client.get("/state", headers=api.auth("alice"))).json()["pages"] == []
    assert len((await api.client.get("/state", headers=api.auth("bob"))).json()["pages"]) == 1


async def test_a_users_daily_limits_are_their_own(make_api):
    api = make_api(daily_pages=1)
    assert (await api.client.post("/scrape", json={"url": A}, headers=api.auth("alice"))).status_code == 200
    assert (await api.client.post("/scrape", json={"url": B}, headers=api.auth("alice"))).status_code == 429
    assert (await api.client.post("/scrape", json={"url": A}, headers=api.auth("bob"))).status_code == 200


# ---- the browser goes away, or the server fails mid-stream -------------------------------------------------------------


async def test_a_dropped_stream_stops_the_run_frees_the_slot_and_leaves_it_resumable(api):
    from scraper_api.streams import stream_response

    api.model.script = [ai("", call("fetch_page", "c1", url=A)), ai("All done.")]
    entry = await api.deps.registry.get("alice")
    stream = stream_response(api.deps, entry, lambda: entry.session.run_agent(f"read {A}", MODEL)).body_iterator
    assert (await anext(stream)).startswith(b"event: ")  # the browser reads a frame...
    assert api.deps.slots.active == 1
    await stream.aclose()  # ...and goes away
    assert api.deps.slots.active == 0 and entry.session.busy is False
    assert entry.session.run.status == "stopped" and entry.session.resumable
    resumed = await api.client.post("/agent/proceed", headers=api.auth())
    assert names(resumed)[-2:] == ["done", "end"]


async def test_an_unexpected_failure_mid_stream_becomes_a_plain_error_frame_that_leaks_nothing(api):
    from scraper_api.streams import stream_response

    async def explode():
        raise RuntimeError("secret page text that must not reach anyone")
        yield  # pragma: no cover - makes this an async generator

    entry = await api.deps.registry.get("alice")
    chunks = [chunk async for chunk in stream_response(api.deps, entry, explode).body_iterator]
    text = b"".join(chunks).decode()
    assert "server_error" in text and "secret page text" not in text
    assert api.deps.slots.active == 0



async def test_a_stream_that_loses_the_race_for_the_session_leaves_the_live_run_alone(api):
    from scraper.store import RunRecord
    from scraper_api.streams import stream_response

    entry = await api.deps.registry.get("alice")
    entry.session.run = RunRecord(number=1, instruction="long task", status="running", followups=[{"id": "f1", "text": "also the SKU", "delivered": False}])
    entry.session.busy = True  # another stream owns the session and is running
    chunks = [c async for c in stream_response(api.deps, entry, lambda: entry.session.ask("q?", MODEL)).body_iterator]
    text = b"".join(chunks).decode()
    assert '"code": "busy"' in text
    assert entry.session.run.status == "running"  # the loser must not mark the winner's run as stopped
    assert entry.session.run.followups[0]["delivered"] is False and entry.session.take_stopped_drafts() == []
    assert api.deps.slots.active == 0
