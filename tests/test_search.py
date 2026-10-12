"""The web search client: Exa's hosted MCP server, called over plain HTTP the way opencode does."""

import json

import httpx
import pytest

from scraper.search import SearchError, SearchResult, web_search

# What the real server sent back for a small search, kept so the parser is tested on the real shape.
REAL = (
    'event: message\ndata: {"result":{"content":[{"type":"text","text":"Title: The Python Tutorial \\u2014 Python 3.14 documentation\\n'
    "URL: https://docs.python.org/3/tutorial/index.html\\nPublished: N/A\\nAuthor: N/A\\nHighlights:\\nThe Python Tutorial\\n...\\n"
    "This tutorial is designed for programmers that are new to the Python language.\\n\\n---\\n\\nTitle: 3.15.0 Documentation\\n"
    'URL: https://docs.python.org/\\nPublished: 2026-10-03T16:09:05.000Z\\nAuthor: N/A\\nHighlights:\\nWelcome! This is the official documentation.","_meta":{"searchTime":1195.2}}]},"jsonrpc":"2.0","id":1}\n\n'
)


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def answering(body, status=200, content_type="text/event-stream"):
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, content=body, headers={"content-type": content_type})

    return client_for(handler), seen


def test_it_parses_the_titles_addresses_dates_and_highlights_of_the_real_reply():
    client, _ = answering(REAL)
    results = web_search("python tutorial", client=client)
    assert [r.url for r in results] == ["https://docs.python.org/3/tutorial/index.html", "https://docs.python.org/"]
    assert results[0].title == "The Python Tutorial — Python 3.14 documentation" and results[0].published == ""
    assert "designed for programmers" in results[0].highlights and results[1].published == "2026-10-03T16:09:05.000Z"


def test_it_asks_for_the_search_the_way_the_server_expects():
    client, seen = answering(REAL)
    web_search("find docs", num_results=3, objective="the official page", client=client)
    request = seen[0]
    body = json.loads(request.content)
    assert request.method == "POST" and str(request.url) == "https://mcp.exa.ai/mcp"
    assert body["method"] == "tools/call" and body["params"]["name"] == "web_search_exa" and body["jsonrpc"] == "2.0"
    assert body["params"]["arguments"] == {"query": "find docs", "objective": "the official page", "numResults": 3}
    assert "text/event-stream" in request.headers["accept"] and request.headers["content-type"] == "application/json"
    assert "x-api-key" not in request.headers


def test_the_objective_defaults_to_the_query_because_the_server_asks_for_one():
    client, seen = answering(REAL)
    web_search("find docs", client=client)
    assert json.loads(seen[0].content)["params"]["arguments"]["objective"] == "find docs"


def test_a_key_is_sent_only_when_one_is_set_and_the_address_can_be_changed():
    client, seen = answering(REAL)
    web_search("q", client=client, api_key="k-123", url="https://exa.example/mcp")
    assert seen[0].headers["x-api-key"] == "k-123" and str(seen[0].url) == "https://exa.example/mcp"


def test_a_plain_json_reply_works_as_well_as_a_streamed_one():
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "Title: A\nURL: https://a.test/\nHighlights:\nhello"}]}})
    client, _ = answering(body, content_type="application/json")
    assert [r.url for r in web_search("q", client=client)] == ["https://a.test/"]


def test_a_reply_that_is_not_in_the_expected_shape_is_kept_as_text_not_lost():
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "Some free text with no fields."}]}})
    client, _ = answering(body, content_type="application/json")
    results = web_search("q", client=client)
    assert len(results) == 1 and results[0].url == "" and "Some free text" in results[0].highlights


def test_no_results_is_an_empty_list_not_an_error():
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": []}})
    client, _ = answering(body, content_type="application/json")
    assert web_search("q", client=client) == []


@pytest.mark.parametrize(
    "body, message",
    [
        ({"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "Invalid arguments"}}, "Invalid arguments"),
        ({"jsonrpc": "2.0", "id": 1, "result": {"isError": True, "content": [{"type": "text", "text": "Search failed upstream"}]}}, "Search failed upstream"),
    ],
)
def test_an_error_the_server_reports_is_passed_on_in_its_words(body, message):
    client, _ = answering(json.dumps(body), content_type="application/json")
    with pytest.raises(SearchError, match=message):
        web_search("q", client=client)


def test_a_rate_limit_says_so_and_how_to_get_more():
    client, _ = answering("slow down", status=429, content_type="text/plain")
    with pytest.raises(SearchError) as caught:
        web_search("q", client=client)
    assert "busy" in caught.value.message.lower() and "SCRAPER_EXA_API_KEY" in (caught.value.hint or "")


def test_other_http_failures_do_not_show_the_servers_body():
    client, _ = answering("<html>SECRET internals</html>", status=500, content_type="text/html")
    with pytest.raises(SearchError) as caught:
        web_search("q", client=client)
    assert "SECRET" not in caught.value.message and "500" in caught.value.message


def test_an_unreachable_service_is_a_plain_message():
    def refuse(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(SearchError, match="Could not reach the search service"):
        web_search("q", client=client_for(refuse))


def test_a_reply_that_is_not_json_at_all_is_an_error_not_a_crash():
    client, _ = answering("event: message\ndata: not json\n\n")
    with pytest.raises(SearchError, match="could not be read"):
        web_search("q", client=client)


def test_the_number_of_results_is_kept_within_bounds():
    client, seen = answering(REAL)
    web_search("q", num_results=500, client=client)
    web_search("q", num_results=0, client=client)
    assert [json.loads(r.content)["params"]["arguments"]["numResults"] for r in seen] == [10, 1]


def test_a_result_is_a_value():
    assert SearchResult("T", "https://a.test/", "", "x").title == "T"
