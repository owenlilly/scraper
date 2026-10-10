import time

import jwt
import pytest

from jwks_support import AUDIENCE, ISSUER, JWKSServer, Signer
from scraper_api.auth import AuthError, AuthUnavailable, JWKSVerifier


@pytest.fixture
def idp():
    signer = Signer("k1")
    server = JWKSServer(signer)
    yield signer, server
    server.close()


def verifier(server, clock=time.monotonic, **kw):
    return JWKSVerifier(server.url, ISSUER, AUDIENCE, clock=clock, **kw)


def test_a_valid_token_gives_the_user_id(idp):
    signer, server = idp
    assert verifier(server).user_id(signer.token("user-42")) == "user-42"


def test_the_key_set_is_fetched_once_and_cached(idp):
    signer, server = idp
    v = verifier(server)
    for _ in range(5):
        v.user_id(signer.token())
    assert server.requests == 1


@pytest.mark.parametrize(
    "overrides",
    [{"exp": 1}, {"aud": "https://elsewhere.test"}, {"iss": "https://evil.test"}, {"sub": None}, {"sub": ""}, {"exp": None}, {"aud": None}, {"iss": None}],
)
def test_tokens_that_fail_any_check_are_rejected(idp, overrides):
    signer, server = idp
    with pytest.raises(AuthError):
        verifier(server).user_id(signer.token(**overrides))


def test_a_small_clock_skew_is_tolerated_but_not_a_large_one(idp):
    signer, server = idp
    v = verifier(server)
    assert v.user_id(signer.token(exp=int(time.time()) - 5)) == "user-1"
    with pytest.raises(AuthError):
        v.user_id(signer.token(exp=int(time.time()) - 120))


def test_a_token_signed_by_another_key_with_the_same_kid_is_rejected(idp):
    _, server = idp
    forger = Signer("k1")
    with pytest.raises(AuthError):
        verifier(server).user_id(forger.token())


def test_other_algorithms_are_rejected_including_none_and_a_shared_secret(idp):
    signer, server = idp
    v = verifier(server)
    claims = {"sub": "u", "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 100}
    hs = jwt.encode(claims, "secret" * 8, algorithm="HS256", headers={"kid": "k1"})
    unsigned = jwt.encode(claims, None, algorithm="none", headers={"kid": "k1"})
    for bad in (hs, unsigned):
        with pytest.raises(AuthError):
            v.user_id(bad)


@pytest.mark.parametrize("junk", ["", "abc", "a.b.c", "Bearer x", "x" * 10_000, "\x00"])
def test_garbage_is_rejected_without_crashing(idp, junk):
    _, server = idp
    with pytest.raises(AuthError):
        verifier(server).user_id(junk)


def test_a_rotated_in_key_is_picked_up_without_waiting_for_the_cache_to_expire(idp):
    signer, server = idp
    v = verifier(server)
    v.user_id(signer.token())  # caches k1
    newer = Signer("k2")
    server.keys = [signer.jwk, newer.jwk]
    assert v.user_id(newer.token("rotated")) == "rotated"
    assert server.requests == 2


def test_unknown_key_ids_cannot_make_the_server_hammer_the_identity_provider(idp):
    signer, server = idp
    now = [1000.0]
    v = verifier(server, clock=lambda: now[0])
    v.user_id(signer.token())
    stranger = Signer("nobody")
    for _ in range(20):
        with pytest.raises(AuthError):
            v.user_id(stranger.token())
    assert server.requests == 2  # the first fetch plus a single forced refresh
    now[0] += 31
    with pytest.raises(AuthError):
        v.user_id(stranger.token())
    assert server.requests == 3  # allowed again after the interval


def test_an_unreachable_identity_provider_is_a_service_problem_not_a_bad_token(idp):
    signer, server = idp
    server.down = True
    with pytest.raises(AuthUnavailable):
        verifier(server).user_id(signer.token())


def test_a_key_already_cached_keeps_working_if_the_provider_goes_down(idp):
    signer, server = idp
    v = verifier(server)
    v.user_id(signer.token())
    server.down = True
    assert v.user_id(signer.token("still-here")) == "still-here"
