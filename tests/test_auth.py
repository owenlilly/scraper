import time

import jwt
import pytest

from jwks_support import AUDIENCE, ISSUER, JWKSServer, Signer
from scraper.api.auth import AuthError, AuthUnavailable, JWKSVerifier


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


# ---- the key cache outlives an outage and follows the key server ---------------------------------------------------------


def test_cached_keys_keep_working_past_the_cache_lifetime_while_the_key_server_is_down(idp):
    signer, server = idp
    now = [1000.0]
    v = verifier(server, clock=lambda: now[0])
    v.user_id(signer.token())
    server.down = True
    now[0] += 3600  # an hour later: well past the 5 minute cache lifetime
    assert v.user_id(signer.token("still-here")) == "still-here"


def test_a_failed_refresh_is_not_retried_on_every_request(idp):
    signer, server = idp
    now = [1000.0]
    v = verifier(server, clock=lambda: now[0])
    v.user_id(signer.token())
    server.down = True
    now[0] += 3600
    before = server.requests
    for _ in range(25):
        v.user_id(signer.token())
    assert server.requests - before == 1  # one attempt, then the failure is remembered for a while
    now[0] += 20
    v.user_id(signer.token())
    assert server.requests - before == 2


def test_keys_are_refreshed_after_their_lifetime_and_a_withdrawn_key_stops_working(idp):
    signer, server = idp
    now = [1000.0]
    v = verifier(server, clock=lambda: now[0])
    assert v.user_id(signer.token()) == "user-1"
    replacement = Signer("k2")
    server.keys = [replacement.jwk]  # k1 has been withdrawn
    assert v.user_id(signer.token("cached")) == "cached"  # still inside the cache lifetime
    now[0] += 301
    assert v.user_id(replacement.token("fresh")) == "fresh"
    with pytest.raises(AuthError):
        v.user_id(signer.token())


def test_a_recovered_key_server_is_used_again(idp):
    signer, server = idp
    now = [1000.0]
    v = verifier(server, clock=lambda: now[0])
    v.user_id(signer.token())
    server.down = True
    now[0] += 400
    v.user_id(signer.token())  # served from the old keys
    server.down = False
    replacement = Signer("k2")
    server.keys = [replacement.jwk]
    now[0] += 60
    assert v.user_id(replacement.token("recovered")) == "recovered"


def test_a_token_without_a_key_id_is_rejected(idp):
    signer, server = idp
    import jwt as pyjwt

    token = pyjwt.encode({"sub": "u", "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 100}, "x" * 32, algorithm="HS256")
    with pytest.raises(AuthError):
        verifier(server).user_id(token)
