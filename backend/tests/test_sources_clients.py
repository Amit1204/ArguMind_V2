from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.reliability.retry import RetryPolicy
from app.sources.arxiv import ArxivClient, build_search_query, parse_atom
from app.sources.http import (
    HttpFetcher,
    SourceError,
    SourceResponseError,
    SourceTimeoutError,
    SourceUnavailableError,
)
from app.sources.models import SourceKind
from app.sources.wikipedia import WikipediaClient

FIXTURES = Path(__file__).parent / "fixtures"


def fetcher_for(handler, attempts: int = 3) -> HttpFetcher:  # noqa: ANN001
    return HttpFetcher(
        timeout_seconds=1,
        policy=RetryPolicy(attempts=attempts, base_delay=0.0, jitter=0.0),
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )


# ---------------------------------------------------------------- arXiv


def test_parse_atom_builds_typed_sources_and_skips_bad_entries() -> None:
    sources = parse_atom((FIXTURES / "arxiv_sample.xml").read_text())
    assert [s.source_id for s in sources] == ["arxiv:2301.12345", "arxiv:hep-th/9901001"]
    first = sources[0]
    assert first.title == "Do Language Models Understand Language? A Benchmark Study"
    assert first.authors == ["Ada Lovelace", "Grace Hopper"]
    assert first.published_year == 2023
    assert str(first.url) == "https://arxiv.org/abs/2301.12345"
    assert first.summary.startswith("We evaluate whether")
    assert first.kind is SourceKind.ARXIV and first.authority == 0.75
    assert first.citation_label == "Lovelace 2023"


def test_parse_atom_rejects_non_xml() -> None:
    with pytest.raises(SourceResponseError):
        parse_atom("<html>not a feed")


def test_search_query_drops_stopwords_and_ands_terms() -> None:
    assert (
        build_search_query("Do large language models truly understand language?")
        == "all:large AND all:language AND all:models AND all:understand"
    )
    assert build_search_query("the of and") == 'all:"the of and"'


def test_arxiv_client_sends_expected_params() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, text=(FIXTURES / "arxiv_sample.xml").read_text())

    client = ArxivClient(fetcher_for(handler))
    sources = client.search("Do language models understand language?", max_results=50)
    assert len(sources) == 2
    assert seen["max_results"] == "25"  # capped
    assert seen["search_query"].startswith("all:language AND all:models")


# ---------------------------------------------------------------- Wikipedia


def test_wikipedia_client_combines_search_and_extracts() -> None:
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        name = (
            "wikipedia_search.json" if params.get("list") == "search" else "wikipedia_extracts.json"
        )
        return httpx.Response(200, json=json.loads((FIXTURES / name).read_text()))

    sources = WikipediaClient(fetcher_for(handler)).search("Do LLMs understand language?", 3)
    assert len(calls) == 2
    assert calls[1]["pageids"] == "67059384|1226519|5951"
    assert [s.title for s in sources] == [
        "Large language model",
        "Natural-language understanding",
        "Chinese room",
    ]
    chinese_room = sources[2]
    assert str(chinese_room.url) == "https://en.wikipedia.org/wiki/Chinese_room"
    assert chinese_room.source_id.startswith("wikipedia:")
    assert "cannot have a mind" in chinese_room.summary
    assert chinese_room.authority == 0.6 and chinese_room.published_year is None


def test_wikipedia_client_returns_empty_on_no_hits() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"query": {"search": []}})

    assert WikipediaClient(fetcher_for(handler)).search("zzzz", 3) == []


def test_wikipedia_client_rejects_unexpected_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "bad"})

    with pytest.raises(SourceResponseError):
        WikipediaClient(fetcher_for(handler)).search("q", 3)


# ---------------------------------------------------------------- fetcher


def test_fetcher_retries_on_503_then_succeeds() -> None:
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(503) if state["n"] < 3 else httpx.Response(200, text="ok")

    assert fetcher_for(handler).get("https://x.test/").text == "ok"
    assert state["n"] == 3


def test_fetcher_retries_intermittent_406_from_arxiv_edge() -> None:
    """arXiv's CDN answered 406 with an empty body to requests that succeeded
    seconds later (2026-09-18). A 406 is retried like a 5xx; a 404 still is not."""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(406) if state["n"] == 1 else httpx.Response(200, text="atom")

    assert fetcher_for(handler).get("https://x.test/").text == "atom"
    assert state["n"] == 2


def test_fetcher_gives_up_after_policy_attempts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "1"})

    with pytest.raises(SourceUnavailableError):
        fetcher_for(handler, attempts=2).get("https://x.test/")


def test_fetcher_does_not_retry_client_errors() -> None:
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(404)

    with pytest.raises(SourceError):
        fetcher_for(handler).get("https://x.test/")
    assert state["n"] == 1


def test_fetcher_maps_timeouts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(SourceTimeoutError):
        fetcher_for(handler, attempts=2).get("https://x.test/")
