from __future__ import annotations

import pytest

from app.sources.ids import arxiv_source_id, cache_key, canonical_url, claim_id, url_source_id


@pytest.mark.parametrize(
    "raw",
    [
        "http://arxiv.org/abs/2301.12345v2",
        "https://arxiv.org/abs/2301.12345",
        "2301.12345v7",
        "arXiv:2301.12345",
    ],
)
def test_arxiv_ids_are_version_free(raw: str) -> None:
    assert arxiv_source_id(raw) == "arxiv:2301.12345"


def test_arxiv_old_style_ids() -> None:
    assert arxiv_source_id("http://arxiv.org/abs/hep-th/9901001v1") == "arxiv:hep-th/9901001"


def test_arxiv_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        arxiv_source_id("http://arxiv.org/abs/broken")


def test_url_ids_are_stable_and_ignore_tracking_noise() -> None:
    a = url_source_id("wikipedia", "https://en.wikipedia.org/wiki/Chinese_room")
    b = url_source_id(
        "wikipedia", "HTTPS://EN.wikipedia.org/wiki/Chinese_room/?utm_source=x#History"
    )
    assert a == b
    assert a.startswith("wikipedia:") and len(a) == len("wikipedia:") + 16
    assert url_source_id("wikipedia", "https://en.wikipedia.org/wiki/Other") != a


def test_canonical_url_sorts_query_and_strips_fragment() -> None:
    assert canonical_url("https://Example.org/a/?b=2&a=1#frag") == "https://example.org/a?a=1&b=2"


def test_claim_ids_are_scoped_to_their_source() -> None:
    assert claim_id("arxiv:2301.12345", 1) == "arxiv:2301.12345#1"
    with pytest.raises(ValueError):
        claim_id("arxiv:2301.12345", 0)


def test_cache_key_normalises_whitespace_and_case() -> None:
    assert cache_key("arxiv", "  Do LLMs   understand? ", 5) == cache_key(
        "arxiv", "do llms understand?", 5
    )
    assert cache_key("arxiv", "q", 5) != cache_key("arxiv", "q", 6)
    assert cache_key("arxiv", "q", 5) != cache_key("wikipedia", "q", 5)
