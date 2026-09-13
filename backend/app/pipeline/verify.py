"""Citation verification: the answer may only cite sources retrieved in this run."""

from __future__ import annotations

import re

from app.pipeline.schemas import VerificationResult

# [arxiv:2301.12345]  [wikipedia:3f9a1c2b4d5e6f70]  [arxiv:2301.12345#2] (claim id -> source)
_CITATION = re.compile(r"\[([a-z_]+:[^\]\s]+?)(?:#\d+)?\]")


def verify_citations(answer: str, valid_source_ids: set[str]) -> tuple[str, VerificationResult]:
    found: list[str] = []
    valid: list[str] = []
    invalid: list[str] = []

    def replace(match: re.Match[str]) -> str:
        source_id = match.group(1)
        found.append(source_id)
        if source_id in valid_source_ids:
            if source_id not in valid:
                valid.append(source_id)
            return f"[{source_id}]"
        if source_id not in invalid:
            invalid.append(source_id)
        return ""

    cleaned = _CITATION.sub(replace, answer)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([.,;:])", r"\1", cleaned).strip()
    return cleaned, VerificationResult(
        citations_found=len(found),
        valid_citations=valid,
        invalid_removed=invalid,
        has_valid_citation=bool(valid),
    )
