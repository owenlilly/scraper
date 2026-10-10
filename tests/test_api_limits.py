import httpx

from api_support import MODEL


def post_scope(path="/ask", content_length=None):
    headers = [(b"content-type", b"application/json")]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "headers": headers, "client": ("127.0.0.1", 1), "server": ("api.test", 80),
    }


async def test_a_huge_body_is_refused_with_a_413_and_no_token_is_needed_to_be_refused(api):
    response = await api.client.post("/ask", content=b"x" * 1_000_000, headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "too_large"


async def test_a_declared_huge_body_is_refused_without_reading_any_of_it(api):
    async def receive():
        raise AssertionError("the body was read although its declared size is over the limit")

    sent = []

    async def send(message):
        sent.append(message)

    await api.app(post_scope(content_length=50_000_000), receive, send)
    assert sent[0]["status"] == 413


async def test_a_body_streamed_without_a_length_is_cut_off_when_it_passes_the_limit(api):
    async def chunks():
        for _ in range(40):
            yield b"x" * 10_000  # 400 KB in all, sent with no Content-Length

    response = await api.client.post("/ask", content=chunks(), headers={"Content-Type": "application/json"})
    assert response.status_code == 413 and response.json()["error"]["code"] == "too_large"


async def test_the_refusal_does_not_echo_the_body(api):
    response = await api.client.post("/ask", content=b"SECRET" * 100_000, headers={"Content-Type": "application/json"})
    assert response.status_code == 413 and "SECRET" not in response.text


async def test_ordinary_requests_under_the_limit_still_work(api):
    body = {"question": "q" * 7000, "model": MODEL}  # the largest question the schema allows is well under the limit
    response = await api.client.post("/ask", json=body, headers=api.auth())
    assert response.status_code != 413
