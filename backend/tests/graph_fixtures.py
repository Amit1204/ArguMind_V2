"""Shared fixture data: one contested sub-question with evidence on both sides."""

from __future__ import annotations

from app.evidence.models import Claim, Stance
from app.sources.models import Source, SourceKind

QUESTION = "Do large language models understand language?"


def arxiv(n: str, year: int, title: str = "Paper") -> Source:
    return Source(
        source_id=f"arxiv:{n}",
        kind=SourceKind.ARXIV,
        title=title,
        url=f"https://arxiv.org/abs/{n}",
        authors=["A. Author"],
        published_year=year,
        summary="s",
        authority=0.75,
    )


def wiki(slug: str) -> Source:
    return Source(
        source_id=f"wikipedia:{slug}",
        kind=SourceKind.WIKIPEDIA,
        title=slug,
        url=f"https://en.wikipedia.org/wiki/{slug}",
        summary="s",
        authority=0.6,
    )


def claim(
    source_id: str,
    n: int,
    stance: Stance,
    text: str,
    confidence: float = 0.8,
    evidence_type: str = "empirical",
    q: int = 0,
) -> Claim:
    return Claim(
        claim_id=f"{source_id}#{n}",
        source_id=source_id,
        sub_question_index=q,
        text=text,
        stance=stance,
        confidence=confidence,
        evidence_type=evidence_type,  # type: ignore[arg-type]
    )


SOURCES = [
    arxiv("2301.00001", 2023, "Benchmarks show understanding"),
    arxiv("2001.00002", 2020, "Early positive results"),
    arxiv("2405.00003", 2024, "Systematic failures on novel compositions"),
    wiki("Large_language_model"),
]

CLAIMS = [
    claim("arxiv:2301.00001", 1, Stance.SUPPORTS, "Models pass compositional benchmarks.", 0.8),
    claim(
        "arxiv:2001.00002",
        1,
        Stance.SUPPORTS,
        "Early models show semantic competence.",
        0.6,
        "review",
    ),
    claim(
        "arxiv:2405.00003",
        1,
        Stance.REFUTES,
        "Models fail systematically on novel compositions.",
        0.9,
    ),
    claim(
        "arxiv:2405.00003", 2, Stance.NEUTRAL, "Benchmarks vary widely in design.", 0.7, "review"
    ),
    claim(
        "wikipedia:Large_language_model",
        1,
        Stance.NEUTRAL,
        "LLMs are trained on large corpora.",
        1.0,
        "other",
    ),
]
