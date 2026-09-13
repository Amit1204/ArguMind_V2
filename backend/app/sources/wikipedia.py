"""Wikipedia client via the MediaWiki API (no key required).

Two calls per search: `list=search` for matching page ids, then
`prop=extracts` for the plain-text lead section of each page.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

from app.sources.http import HttpFetcher, SourceResponseError
from app.sources.ids import url_source_id
from app.sources.models import AUTHORITY, Source, SourceKind

log = logging.getLogger(__name__)

WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
_TAGS = re.compile(r"<[^>]+>")


def page_url(title: str) -> str:
    return "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))


def parse_search(payload: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        hits = payload["query"]["search"]
    except (KeyError, TypeError) as exc:
        raise SourceResponseError("wikipedia search payload missing query.search") from exc
    return [h for h in hits if isinstance(h, dict) and h.get("pageid") and h.get("title")]


def parse_extracts(payload: dict[str, Any]) -> dict[int, str]:
    try:
        pages = payload["query"]["pages"]
    except (KeyError, TypeError) as exc:
        raise SourceResponseError("wikipedia extracts payload missing query.pages") from exc
    out: dict[int, str] = {}
    items = pages.values() if isinstance(pages, dict) else pages
    for page in items:
        if isinstance(page, dict) and page.get("pageid"):
            out[int(page["pageid"])] = " ".join(str(page.get("extract", "")).split())
    return out


class WikipediaClient:
    kind = SourceKind.WIKIPEDIA

    def __init__(self, fetcher: HttpFetcher, base_url: str = WIKIPEDIA_API_URL) -> None:
        self._fetcher = fetcher
        self._base_url = base_url

    def search(self, question: str, max_results: int = 3) -> list[Source]:
        limit = max(1, min(max_results, 10))
        search = self._fetcher.get(
            self._base_url,
            params={
                "action": "query",
                "list": "search",
                "srsearch": question,
                "srlimit": limit,
                "format": "json",
                "utf8": 1,
            },
        )
        hits = parse_search(_json(search))
        if not hits:
            log.info("wikipedia: no results for %r", question[:60])
            return []
        page_ids = [int(h["pageid"]) for h in hits[:limit]]
        extracts = self._fetcher.get(
            self._base_url,
            params={
                "action": "query",
                "prop": "extracts",
                "exintro": 1,
                "explaintext": 1,
                "exlimit": len(page_ids),
                "pageids": "|".join(str(p) for p in page_ids),
                "format": "json",
                "utf8": 1,
            },
        )
        summaries = parse_extracts(_json(extracts))
        sources: list[Source] = []
        for hit in hits[:limit]:
            title = str(hit["title"])
            url = page_url(title)
            summary = summaries.get(int(hit["pageid"])) or _TAGS.sub(
                "", str(hit.get("snippet", ""))
            )
            sources.append(
                Source(
                    source_id=url_source_id(SourceKind.WIKIPEDIA.value, url),
                    kind=SourceKind.WIKIPEDIA,
                    title=title,
                    url=url,
                    summary=summary[:6000],
                    authority=AUTHORITY[SourceKind.WIKIPEDIA],
                )
            )
        log.info("wikipedia: %d results for %r", len(sources), question[:60])
        return sources


def _json(response) -> dict[str, Any]:  # noqa: ANN001 - httpx.Response
    try:
        data = response.json()
    except ValueError as exc:
        raise SourceResponseError("wikipedia returned non-JSON") from exc
    if not isinstance(data, dict):
        raise SourceResponseError("wikipedia returned an unexpected payload")
    return data
