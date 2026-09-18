"""Citation verification and the honesty caps applied to the final answer.

The answer may only cite sources retrieved in this run; an inconclusive run may
not report high confidence; a forecast may not be answered with certainty.
"""

from __future__ import annotations

import re

from app.pipeline.schemas import VerificationResult

# One source id: kind:identifier, optionally followed by #n (claim id -> its source).
_ID = r"[a-z_]+:[^\]\s,#]+(?:#\d+)?"
# A bracket holding one id or a comma-separated list of ids:
#   [arxiv:2301.12345]   [wikipedia:3f9a…#1, wikipedia:3f9a…#3, arxiv:2301.12345]
# Models write lists when several claims of one source back a sentence; the first
# baseline graded such an answer as "uncited" because only single ids were parsed.
_CITATION = re.compile(rf"\[({_ID}(?:\s*,\s*{_ID})*)\]")
_CLAIM_SUFFIX = re.compile(r"#\d+$")

# Confidence caps (ADR-007: inconclusive is a first-class outcome; a forecast is
# never settled by the literature). Values found by the baseline: an inconclusive
# run reported 0.823 (the consensus strength of the retrieved material), a
# forecast was answered at 0.9.
INCONCLUSIVE_CONFIDENCE_CAP = 0.5
FORECAST_CONFIDENCE_CAP = 0.7

_FORECAST = re.compile(
    r"\bwill\b|\bwon't\b|\bby (?:the year )?20[3-9]\d\b|\bbefore 20[3-9]\d\b"
    r"|\bwithin (?:the next )?(?:\w+|\d+) (?:years|decades)\b"
    r"|\bin the (?:next|coming) \w+ (?:years|decades)\b",
    re.IGNORECASE,
)


def is_forecast_question(question: str) -> bool:
    """Heuristic used when the planner does not flag a forecast: the question asks
    whether something *will* happen or names a future year/horizon."""
    return bool(_FORECAST.search(question or ""))


def verify_citations(answer: str, valid_source_ids: set[str]) -> tuple[str, VerificationResult]:
    found: list[str] = []
    valid: list[str] = []
    invalid: list[str] = []

    def replace(match: re.Match[str]) -> str:
        kept: list[str] = []
        for token in match.group(1).split(","):
            source_id = _CLAIM_SUFFIX.sub("", token.strip())
            if not source_id:
                continue
            found.append(source_id)
            if source_id in valid_source_ids:
                if source_id not in valid:
                    valid.append(source_id)
                if source_id not in kept:
                    kept.append(source_id)
            elif source_id not in invalid:
                invalid.append(source_id)
        return f"[{', '.join(kept)}]" if kept else ""

    cleaned = _CITATION.sub(replace, answer)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([.,;:])", r"\1", cleaned).strip()
    return cleaned, VerificationResult(
        citations_found=len(found),
        valid_citations=valid,
        invalid_removed=invalid,
        has_valid_citation=bool(valid),
    )


def cap_confidence(confidence: float, status: str, forecast: bool) -> tuple[float, str | None]:
    """Apply the honesty caps. Returns (confidence, caveat or None)."""
    value = float(confidence or 0.0)
    if status == "inconclusive" and value > INCONCLUSIVE_CONFIDENCE_CAP:
        return INCONCLUSIVE_CONFIDENCE_CAP, (
            f"Confidence capped at {INCONCLUSIVE_CONFIDENCE_CAP}: the run is inconclusive, so "
            "the strength of the retrieved material is not confidence in an answer."
        )
    if forecast and status == "answered" and value > FORECAST_CONFIDENCE_CAP:
        return FORECAST_CONFIDENCE_CAP, (
            f"Confidence capped at {FORECAST_CONFIDENCE_CAP}: the question asks about a future "
            "outcome, which published evidence cannot settle."
        )
    return round(value, 3), None
