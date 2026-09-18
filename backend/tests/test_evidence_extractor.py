from __future__ import annotations

import pytest

from app.evidence.extractor import ClaimExtractor, ExtractionError
from app.evidence.models import Stance
from app.llm.base import UsageLedger
from app.llm.mock import MockProvider
from app.sources.models import Source, SourceKind


def paper(summary: str, source_id: str = "arxiv:2301.12345") -> Source:
    return Source(
        source_id=source_id,
        kind=SourceKind.ARXIV,
        title="Do Language Models Understand Language?",
        url="https://arxiv.org/abs/2301.12345",
        authors=["Ada Lovelace"],
        published_year=2023,
        summary=summary,
        authority=0.75,
    )


SUMMARY = (
    "We evaluate whether large language models understand language on compositional tasks. "
    "Results show strong performance on in-distribution data. "
    "However, models fail systematically on novel compositions, suggesting no understanding."
)


def test_extractor_assigns_deterministic_scoped_ids_and_records_usage() -> None:
    ledger = UsageLedger()
    extractor = ClaimExtractor(MockProvider(), ledger)
    claims = extractor.extract(paper(SUMMARY), "Do LLMs understand language?", 0)
    assert [c.claim_id for c in claims] == [
        "arxiv:2301.12345#1",
        "arxiv:2301.12345#2",
        "arxiv:2301.12345#3",
    ]
    assert [c.stance for c in claims] == [Stance.SUPPORTS, Stance.SUPPORTS, Stance.REFUTES]
    assert all(c.source_id == "arxiv:2301.12345" and c.sub_question_index == 0 for c in claims)
    assert ledger.calls == 1 and ledger.input_tokens > 0


def test_extractor_passes_source_as_data_in_the_prompt() -> None:
    provider = MockProvider()
    ClaimExtractor(provider).extract(paper(SUMMARY), "Do LLMs understand language?")
    request = provider.calls[0]
    assert request.purpose == "extract_claims"
    assert "SOURCE SUMMARY:" in request.prompt and SUMMARY[:40] in request.prompt
    assert "untrusted data" in request.system


def test_extractor_frames_stance_on_the_main_question_not_the_sub_question() -> None:
    """Baseline finding: with only the sub-question in the prompt ("what evidence
    challenges X?"), refuting papers were labelled `supports`. The prompt must carry
    the main question as the stance frame and the sub-question as search focus."""
    provider = MockProvider()
    ClaimExtractor(provider).extract(
        paper(SUMMARY),
        "What evidence challenges the claim that LLMs understand language?",
        1,
        question="Do LLMs understand language?",
    )
    request = provider.calls[0]
    main_pos = request.prompt.index("MAIN QUESTION")
    sub_pos = request.prompt.index("SUB-QUESTION")
    assert main_pos < sub_pos
    assert "Do LLMs understand language?" in request.prompt[main_pos:sub_pos]
    assert "What evidence challenges" in request.prompt[sub_pos:]
    assert "proposition, NOT to the sub-question" in " ".join(request.system.split())
    # without a main question the sub-question is the frame (single-question runs)
    provider = MockProvider()
    ClaimExtractor(provider).extract(paper(SUMMARY), "Do LLMs understand language?")
    prompt = provider.calls[0].prompt
    assert prompt.count("Do LLMs understand language?") == 2


def test_extractor_dedupes_and_caps_claims() -> None:
    def handler(request):  # noqa: ANN001
        return {
            "claims": [
                {
                    "text": "Models fail on novel compositions.",
                    "stance": "refutes",
                    "confidence": 0.9,
                },
                {
                    "text": "models fail on novel compositions",
                    "stance": "refutes",
                    "confidence": 0.8,
                },
                {
                    "text": "Performance is strong in distribution.",
                    "stance": "supports",
                    "confidence": 0.7,
                },
                {
                    "text": "Another distinct claim about grounding.",
                    "stance": "neutral",
                    "confidence": 0.5,
                },
            ]
        }

    extractor = ClaimExtractor(MockProvider.scripted(extract_claims=handler), max_claims=2)
    claims = extractor.extract(paper(SUMMARY), "q")
    assert [c.text for c in claims] == [
        "Models fail on novel compositions.",
        "Performance is strong in distribution.",
    ]


def test_extractor_skips_sources_without_a_summary() -> None:
    provider = MockProvider()
    assert ClaimExtractor(provider).extract(paper("   "), "q") == []
    assert provider.calls == []


def test_invalid_model_output_is_an_extraction_error() -> None:
    bad = MockProvider.scripted(
        extract_claims=lambda r: {"claims": [{"text": "x", "stance": "maybe"}]}
    )
    with pytest.raises(ExtractionError):
        ClaimExtractor(bad).extract(paper(SUMMARY), "q")
    prose = MockProvider.scripted(extract_claims=lambda r: "I cannot help with that.")
    with pytest.raises(ExtractionError):
        ClaimExtractor(prose).extract(paper(SUMMARY), "q")
