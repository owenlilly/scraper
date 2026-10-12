from api_support import A, MODEL, Harness
from fakes import ai
from scraper.session import agent_system_prompt
from scraper.config import Config


async def run_once(h, user="alice"):
    h.model.script = [ai("Done.")]
    response = await h.client.post("/agent/run", json={"instruction": f"look at {A}", "model": MODEL}, headers=h.auth(user))
    assert response.status_code == 200
    return h.model.calls[-1]["tools"]


async def state(h, user="alice"):
    return (await h.client.get("/state", headers=h.auth(user))).json()


async def test_the_state_says_whether_the_browser_is_available_and_whether_this_person_has_them_on(api):
    body = await state(api)
    assert body["browser_available"] is True and body["settings"]["browser"] == ""  # unset means on


async def test_the_person_can_switch_the_browser_off_and_on(api):
    assert (await api.client.put("/settings/browser", json={"value": "0"}, headers=api.auth())).status_code == 204
    assert (await state(api))["settings"]["browser"] == "0"
    assert "browser_submit_form" not in await run_once(api)
    await api.client.put("/settings/browser", json={"value": "1"}, headers=api.auth())
    assert "browser_submit_form" in await run_once(api)


async def test_forms_are_on_for_a_person_who_never_chose(api):
    assert "browser_submit_form" in await run_once(api)


async def test_one_persons_choice_is_not_anothers(api):
    await api.client.put("/settings/browser", json={"value": "0"}, headers=api.auth("alice"))
    assert "browser_submit_form" in await run_once(api, "bob")


async def test_the_operator_can_switch_the_browser_off_for_everyone(pg):
    h = Harness(pg, browser=False).build()
    try:
        assert (await state(h))["browser_available"] is False
        await h.client.put("/settings/browser", json={"value": "1"}, headers=h.auth())
        assert "browser_submit_form" not in await run_once(h)  # a person cannot switch on what the operator switched off
    finally:
        await h.close()


def test_the_prompt_advice_follows_the_persons_choice():
    assert "browser_submit_form" in agent_system_prompt(Config(), browser=True)
    assert "browser_submit_form" not in agent_system_prompt(Config(), browser=False)
    assert "browser_submit_form" not in agent_system_prompt(Config(browser=False), browser=True)
