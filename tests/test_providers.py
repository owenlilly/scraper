import pytest

from api_support import Harness
from fakes import FakeLLM
from scraper.config import Config, load
from scraper.llm import ModelInfo

KEY = {"SCRAPER_API_KEY": "placeholder-not-a-real-key"}
HOSTED = {
    "SCRAPER_HOSTED": "true", "SCRAPER_DATABASE_URL": "postgresql://x", "SCRAPER_JWKS_URL": "http://w/jwks",
    "SCRAPER_JWT_ISSUER": "http://w", "SCRAPER_JWT_AUDIENCE": "http://a", "SCRAPER_SERVICE_SECRET": "s",
}


def test_a_hosted_deployment_offers_one_provider_unless_it_lists_more():
    assert load({**HOSTED, **KEY}).available_providers == ("mistral",)
    assert load({**HOSTED, "SCRAPER_PROVIDER": "lmstudio"}).available_providers == ("lmstudio",)


def test_it_can_offer_several_and_the_default_comes_first():
    cfg = load({**HOSTED, **KEY, "SCRAPER_PROVIDERS": "lmstudio, mistral"})
    assert cfg.available_providers == ("mistral", "lmstudio")
    cfg = load({**HOSTED, **KEY, "SCRAPER_PROVIDER": "lmstudio", "SCRAPER_PROVIDERS": "lmstudio,mistral,lmstudio"})
    assert cfg.available_providers == ("lmstudio", "mistral")


def test_an_unknown_provider_in_the_list_is_refused_by_name():
    with pytest.raises(ValueError, match="SCRAPER_PROVIDERS.*nope"):
        load({**HOSTED, **KEY, "SCRAPER_PROVIDERS": "mistral,nope"})


def test_offering_mistral_needs_its_key_even_when_it_is_not_the_default():
    with pytest.raises(ValueError, match="SCRAPER_API_KEY"):
        load({**HOSTED, "SCRAPER_PROVIDER": "lmstudio", "SCRAPER_PROVIDERS": "lmstudio,mistral"})


class SecondLLM(FakeLLM):
    key = "mistral"
    name = "Mistral"
    hosted = True
    base_url = "https://mistral.test/v1"


@pytest.fixture
async def both(pg):
    h = Harness(pg, providers="mistral", provider="lmstudio")
    h.other = SecondLLM(models=[ModelInfo("mistral-only", "llm", True, 100_000)])
    h.llms = {"lmstudio": h.llm, "mistral": h.other}
    h.build()
    yield h
    await h.close()


async def state(h, user="alice"):
    return (await h.client.get("/state", headers=h.auth(user))).json()


async def test_the_state_lists_what_can_be_chosen(both):
    body = await state(both)
    assert [p["key"] for p in body["providers"]] == ["lmstudio", "mistral"]
    assert body["providers"][1] == {"key": "mistral", "name": "Mistral", "hosted": True}
    assert body["provider"]["key"] == "lmstudio"


async def test_one_configured_provider_gives_a_list_of_one(pg):
    h = Harness(pg).build()
    try:
        assert [p["key"] for p in (await state(h))["providers"]] == ["lmstudio"]
    finally:
        await h.close()


async def test_choosing_a_provider_switches_the_models_and_is_remembered(both):
    assert [m["id"] for m in (await state(both))["models"]] == ["qwen-test", "other-model"]
    response = await both.client.put("/provider", json={"key": "mistral"}, headers=both.auth())
    assert response.status_code == 204
    body = await state(both)
    assert body["provider"]["key"] == "mistral" and [m["id"] for m in body["models"]] == ["mistral-only"]
    both.deps.registry.forget("alice")  # a new session, as after the person has been away
    assert (await state(both))["provider"]["key"] == "mistral"


async def test_the_choice_is_the_persons_own(both):
    await both.client.put("/provider", json={"key": "mistral"}, headers=both.auth("alice"))
    assert (await state(both, "bob"))["provider"]["key"] == "lmstudio"


async def test_a_model_must_belong_to_the_chosen_provider(both):
    ask = lambda model: both.client.post("/ask", json={"question": "hi", "model": model}, headers=both.auth())  # noqa: E731
    assert (await ask("mistral-only")).status_code == 400
    await both.client.put("/provider", json={"key": "mistral"}, headers=both.auth())
    assert (await ask("mistral-only")).status_code == 200
    assert (await ask("qwen-test")).status_code == 400


async def test_an_unknown_or_not_offered_provider_is_refused(pg):
    h = Harness(pg).build()  # offers only lmstudio
    try:
        for key in ("mistral", "nope"):
            response = await h.client.put("/provider", json={"key": key}, headers=h.auth())
            assert response.status_code == 400 and response.json()["error"]["code"] == "unknown_provider"
    finally:
        await h.close()


async def test_the_provider_cannot_change_while_a_run_is_working(both):
    entry = await both.deps.registry.get("alice")
    entry.session.busy = True
    response = await both.client.put("/provider", json={"key": "mistral"}, headers=both.auth())
    assert response.status_code == 409
    entry.session.busy = False
