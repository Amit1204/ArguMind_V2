"""arXiv API client (Atom feed, no key required)."""

from __future__ import annotations

import logging
import re

from defusedxml import ElementTree

from app.sources.http import HttpFetcher, SourceResponseError
from app.sources.ids import arxiv_source_id
from app.sources.models import AUTHORITY, Source, SourceKind

log = logging.getLogger(__name__)

ARXIV_API_URL = "https://export.arxiv.org/api/query"
_NS = {"atom": "http://www.w3.org/2005/Atom"}
_STOPWORDS = frozenset(
    "a an the of in on for to and or is are was were be been do does did what which who "
    "how why when where with without from by as at into than that this these those it its "
    "can could should would will may might must not no true truly really about".split()
)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")


def build_search_query(question: str, max_terms: int = 6) -> str:
    """Turn a natural-language question into an arXiv `all:` boolean query.

    arXiv matches literal terms, so stopwords and question words are dropped
    and the remaining terms are ANDed. Falls back to the whole phrase when
    nothing survives.
    """
    terms = [w.lower() for w in _WORD.findall(question) if w.lower() not in _STOPWORDS]
    seen: list[str] = []
    for term in terms:
        if term not in seen:
            seen.append(term)
    if not seen:
        return f'all:"{question.strip()}"'
    return " AND ".join(f"all:{t}" for t in seen[:max_terms])


def parse_atom(xml_text: str) -> list[Source]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise SourceResponseError(f"arXiv returned unparseable XML: {exc}") from exc
    sources: list[Source] = []
    for entry in root.findall("atom:entry", _NS):
        raw_id = (entry.findtext("atom:id", default="", namespaces=_NS) or "").strip()
        title = _collapse(entry.findtext("atom:title", default="", namespaces=_NS))
        if not raw_id or not title:
            continue
        try:
            source_id = arxiv_source_id(raw_id)
        except ValueError:
            log.warning("skipping arXiv entry with unexpected id %r", raw_id)
            continue
        published = entry.findtext("atom:published", default="", namespaces=_NS) or ""
        year = int(published[:4]) if published[:4].isdigit() else None
        authors = [
            _collapse(a.findtext("atom:name", default="", namespaces=_NS))
            for a in entry.findall("atom:author", _NS)
        ]
        sources.append(
            Source(
                source_id=source_id,
                kind=SourceKind.ARXIV,
                title=title,
                url=f"https://arxiv.org/abs/{source_id.removeprefix('arxiv:')}",
                authors=[a for a in authors if a],
                published_year=year,
                summary=_collapse(entry.findtext("atom:summary", default="", namespaces=_NS))[
                    :6000
                ],
                authority=AUTHORITY[SourceKind.ARXIV],
            )
        )
    return sources


def _collapse(text: str | None) -> str:
    return " ".join((text or "").split())


class ArxivClient:
    kind = SourceKind.ARXIV

    def __init__(self, fetcher: HttpFetcher, base_url: str = ARXIV_API_URL) -> None:
        self._fetcher = fetcher
        self._base_url = base_url

    def search(self, question: str, max_results: int = 5) -> list[Source]:
        params = {
            "search_query": build_search_query(question),
            "start": 0,
            "max_results": max(1, min(max_results, 25)),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        response = self._fetcher.get(self._base_url, params=params)
        sources = parse_atom(response.text)
        log.info("arxiv: %d results for %r", len(sources), question[:60])
        return sources
