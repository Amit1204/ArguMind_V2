"""Deterministic identifiers for sources and claims (ADR-005).

The first ArguMind used Python's `hash()` for web sources (different every
process) and let the model choose claim ids (`claim_1` for every paper, so
claims overwrote each other). Ids here are pure functions of stable inputs.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# New-style 2007+ ids (2301.12345, optionally v2) and old-style archive/YYMMNNN ids.
_ARXIV_NEW = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")
_ARXIV_OLD = re.compile(r"([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?")
_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid"}


def arxiv_source_id(raw: str) -> str:
    """`http://arxiv.org/abs/2301.12345v2` or `2301.12345` -> `arxiv:2301.12345`.

    The version suffix is dropped so revisions of one paper share an id.
    """
    for pattern in (_ARXIV_NEW, _ARXIV_OLD):
        match = pattern.search(raw)
        if match:
            return f"arxiv:{match.group(1)}"
    raise ValueError(f"not an arXiv identifier: {raw!r}")


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
               if k.lower() not in _TRACKING_PARAMS)
    )  # fmt: skip
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def url_source_id(kind: str, url: str) -> str:
    """`wikipedia:3f9a...` - a stable 16-hex digest of the canonical URL."""
    digest = hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:16]
    return f"{kind}:{digest}"


def claim_id(source_id: str, index: int) -> str:
    """`arxiv:2301.12345#1` - unique per source within a run."""
    if index < 1:
        raise ValueError("claim index starts at 1")
    return f"{source_id}#{index}"


def cache_key(kind: str, query: str, max_results: int) -> str:
    normalised = " ".join(query.lower().split())
    return hashlib.sha256(f"{kind}|{max_results}|{normalised}".encode()).hexdigest()
