import json

import pytest

from api_support import A, MODEL, WEB, seed_page


# ---- sign-in ------------------------------------------------------------------------


async def test_health_is_open_and_everything_else_needs_a_token(api):
    assert (await api.client.get("/health")).json() == {"ok": True}
    for method, path in [("GET", "/state"), ("GET", "/rows.csv"), ("GET", "/rows.json"), ("GET", "/models"), ("GET", "/pages/0"), ("GET", "/context"), ("PUT", "/model"), ("PUT", "/settings/url")]:
        response = await api.client.request(method, path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "unauthorized"
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["", "Bearer", "Bearer ", "Basic abc", "Bearer not.a.token", "token abc"])
async def test_malformed_credentials_are_rejected(api, header):
    response = await api.client.get("/state", headers={"Authorization": header} if header else {})
    assert response.status_code == 401


async def test_an_expired_or_foreign_token_is_rejected_without_saying_why(api):
    for headers in (api.auth(exp=1), api.auth(aud="https://elsewhere.test"), api.auth(iss="https://evil.test")):
        response = await api.client.get("/state", headers=headers)
        assert response.status_code == 401 and "expired" in response.json()["error"]["message"].lower()


async def test_a_valid_token_reaches_the_route(api):
    assert (await api.client.get("/state", headers=api.auth())).status_code == 200


async def test_an_unreachable_key_server_is_a_503_not_a_401(api):
    api.idp.down = True
    response = await api.client.get("/state", headers=api.auth())
    assert response.status_code == 503 and response.json()["error"]["code"] == "auth_unavailable"


async def test_the_per_user_request_rate_is_limited_with_a_retry_after(make_api):
    api = make_api(requests_per_minute=3)
    for _ in range(3):
        assert (await api.client.get("/state", headers=api.auth("alice"))).status_code == 200
    blocked = await api.client.get("/state", headers=api.auth("alice"))
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1
    assert (await api.client.get("/state", headers=api.auth("bob"))).status_code == 200


# ---- state ------------------------------------------------------------------------------


async def test_a_new_users_state_is_empty_and_lists_models_usage_and_modes(api):
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert state["user"] == {"id": "alice"}
    assert (state["pages"], state["rows"], state["history"], state["run"]) == ([], [], [], None)
    assert [m["id"] for m in state["models"]] == [MODEL, "other-model"] and state["models_error"] is None
    assert state["fetch_modes"] == ["fast"]
    assert state["usage"]["model_calls"] == 0 and state["usage"]["pages_limit"] == 300
    assert state["busy"] is False


async def test_the_model_list_can_be_narrowed_by_configuration(make_api):
    api = make_api(allowed_models=MODEL)
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert [m["id"] for m in state["models"]] == [MODEL]


async def test_state_describes_pages_without_sending_their_text(api):
    await seed_page(api)
    state = (await api.client.get("/state", headers=api.auth())).json()
    assert state["pages"] == [{"url": A, "title": "Page A", "chars": len(WEB[A].markdown), "links": 0, "looks_empty": False, "used": False}]
    assert "markdown" not in json.dumps(state)


# ---- reading pages, context and results -----------------------------------------------------------------------------


async def test_a_pages_text_is_available_for_preview_by_position(api):
    await seed_page(api)
    preview = (await api.client.get("/pages/0", headers=api.auth())).json()
    assert preview == {"url": A, "title": "Page A", "markdown": WEB[A].markdown}
    for bad in ("/pages/1", "/pages/-1"):
        assert (await api.client.get(bad, headers=api.auth())).status_code == 404
    assert (await api.client.get("/pages/x", headers=api.auth())).status_code == 422


async def test_the_context_is_empty_until_a_question_has_been_asked(api):
    assert (await api.client.get("/context", headers=api.auth())).json() == {"text": "", "sources": [], "mode": None}


async def test_results_download_as_csv_and_json_and_spreadsheet_formulas_are_defused(api):
    entry = await api.deps.registry.get("alice")
    entry.session.rows.extend([{"plan": "Starter", "price": "$10"}, {"plan": "=HYPERLINK(\"http://evil.test\")", "price": "$40"}])
    csv = await api.client.get("/rows.csv", headers=api.auth())
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv.headers["content-disposition"] and csv.headers["x-content-type-options"] == "nosniff"
    assert csv.text.splitlines()[1] == "Starter,$10" and csv.text.splitlines()[2].startswith("\"'=HYPERLINK")
    assert json.loads((await api.client.get("/rows.json", headers=api.auth())).text)[0] == {"plan": "Starter", "price": "$10"}
    assert (await api.client.get("/rows.csv", headers=api.auth("bob"))).text == ""


# ---- settings and the model choice ------------------------------------------------------------------------------


async def test_form_settings_and_the_chosen_model_are_remembered_per_user(api):
    assert (await api.client.put("/settings/url", json={"value": "https://site.test/a"}, headers=api.auth())).status_code == 204
    assert (await api.client.put("/model", json={"id": MODEL}, headers=api.auth())).status_code == 204
    alice = (await api.client.get("/state", headers=api.auth())).json()
    assert alice["settings"]["url"] == "https://site.test/a" and alice["model"] == MODEL
    bob = (await api.client.get("/state", headers=api.auth("bob"))).json()
    assert bob["settings"]["url"] == "" and bob["model"] is None


async def test_only_known_settings_and_listed_models_can_be_saved(api):
    assert (await api.client.put("/settings/provider", json={"value": "x"}, headers=api.auth())).status_code == 404
    assert (await api.client.put("/settings/../x", json={"value": "x"}, headers=api.auth())).status_code in (404, 405)
    assert (await api.client.put("/model", json={"id": "gpt-9000"}, headers=api.auth())).status_code == 400
    assert (await api.client.put("/settings/url", json={"value": "x" * 3000}, headers=api.auth())).status_code == 422


# ---- bad input and headers -------------------------------------------------------------------------------------


async def test_responses_are_never_cached_and_not_sniffed(api):
    response = await api.client.get("/state", headers=api.auth())
    assert response.headers["cache-control"] == "no-store" and response.headers["x-content-type-options"] == "nosniff"


async def test_the_api_documentation_pages_are_not_served(api):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await api.client.get(path)).status_code == 404


async def test_only_the_configured_web_origin_may_call_the_api_from_a_browser(api):
    allowed = await api.client.options(
        "/state", headers={"Origin": "https://web.test", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"}
    )
    assert allowed.headers["access-control-allow-origin"] == "https://web.test"
    assert "access-control-allow-credentials" not in allowed.headers
    denied = await api.client.options("/state", headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in denied.headers


# ---- deleting an account ----------------------------------------------------------------------------------------------


async def test_account_data_is_deleted_for_that_user_only_and_needs_the_service_secret(api):
    for user in ("alice", "bob"):
        await seed_page(api, user)
    for headers in ({}, {"X-Service-Secret": "wrong"}, {"X-Service-Secret": ""}):
        refused = await api.client.request("DELETE", "/account-data", json={"user_id": "alice"}, headers=headers)
        assert refused.status_code == 401
    assert (await api.client.request("DELETE", "/account-data", json={"user_id": "alice"}, headers={"X-Service-Secret": "s3cret"})).status_code == 204
    assert (await api.client.get("/state", headers=api.auth("alice"))).json()["pages"] == []
    assert len((await api.client.get("/state", headers=api.auth("bob"))).json()["pages"]) == 1
    with api.pool.connection() as conn:
        assert conn.execute("SELECT count(*) FROM app.pages WHERE user_id = 'alice'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM app.pages WHERE user_id = 'bob'").fetchone()[0] == 1


async def test_a_user_token_cannot_delete_account_data(api):
    response = await api.client.request("DELETE", "/account-data", json={"user_id": "bob"}, headers=api.auth("alice"))
    assert response.status_code == 401


# ---- deleting an account while a run is live -----------------------------------------------------------------------------------


async def test_deleting_an_account_in_the_middle_of_a_run_leaves_nothing_behind(api):
    import asyncio
    import threading

    from fakes import ai, call
    from scraper.api.db import USER_TABLES
    from scraper.api.streams import stream_response

    started, release = threading.Event(), threading.Event()
    api.before_fetch = lambda url: (started.set(), release.wait(10))
    api.model.script = [ai("", call("fetch_page", "c1", url=A)), ai("", call("add_rows", "c2", rows=[{"late": 1}])), ai("All done.")]
    entry = await api.deps.registry.get("alice")

    async def drain():
        return [c async for c in stream_response(api.deps, entry, lambda: entry.session.run_agent(f"read {A}", MODEL)).body_iterator]

    reader = asyncio.create_task(drain())
    assert await asyncio.get_running_loop().run_in_executor(None, started.wait, 5)  # the run is now inside the fetch tool
    assert entry.session.busy
    deletion = asyncio.create_task(api.client.request("DELETE", "/account-data", json={"user_id": "alice"}, headers={"X-Service-Secret": "s3cret"}))
    await asyncio.sleep(0.2)
    assert not deletion.done()  # it waits for the run to stop rather than racing it
    release.set()
    response = await deletion
    await reader
    assert response.status_code == 204
    with api.pool.connection() as conn:
        for table in USER_TABLES:
            assert conn.execute(f"SELECT count(*) FROM app.{table} WHERE user_id = 'alice'").fetchone()[0] == 0, table
    assert api.deps.registry.peek("alice") is None


async def test_deletion_says_to_try_again_when_a_run_will_not_stop(api, monkeypatch):
    import scraper.api.routes as routes

    monkeypatch.setattr(routes, "DELETE_WAIT_SECONDS", 0.2)
    await seed_page(api)
    entry = await api.deps.registry.get("alice")
    entry.session.busy = True  # a run that never stops
    response = await api.client.request("DELETE", "/account-data", json={"user_id": "alice"}, headers={"X-Service-Secret": "s3cret"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "busy"
    with api.pool.connection() as conn:
        assert conn.execute("SELECT count(*) FROM app.pages WHERE user_id = 'alice'").fetchone()[0] == 1  # nothing was deleted yet
