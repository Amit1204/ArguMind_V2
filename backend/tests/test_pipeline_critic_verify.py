from __future__ import annotations

from app.evidence.models import Stance
from app.pipeline.critic import critique
from app.pipeline.verify import verify_citations
from app.reasoning.models import ConflictReport, MinorityReport, Resolution, SideScore
from tests.graph_fixtures import claim

CONSENSUS = {"strength": "moderate", "confidence": 0.7}


def resolution(winner: str) -> Resolution:
    return Resolution(
        question_index=0,
        question="q",
        winner=winner,  # type: ignore[arg-type]
        method="heuristic",
        support=SideScore(score=1, claims=1, sources=1),
        refute=SideScore(score=1, claims=1, sources=1),
        margin=0.0,
        confidence=0.5,
        reasoning="r",
        minority_report=MinorityReport(stance="refutes", claim_ids=[], source_ids=[], summary="")
        if winner != "inconclusive"
        else None,
    )


def test_critic_passes_with_enough_independent_evidence() -> None:
    claims = [
        claim("arxiv:2301.00001", 1, Stance.SUPPORTS, "Evidence for the claim."),
        claim("arxiv:2405.00003", 1, Stance.REFUTES, "Evidence against the claim."),
    ]
    verdict = critique(claims, CONSENSUS, ConflictReport(), 0, 1, 2, 2)
    assert verdict.passed and verdict.recommendation == "pass" and verdict.issues == []
    assert verdict.evidence_claims == 2 and verdict.evidence_sources == 2
    assert 0 < verdict.confidence <= 0.7


def test_critic_retries_once_then_declares_inconclusive() -> None:
    thin = [claim("arxiv:2301.00001", 1, Stance.SUPPORTS, "Only one source says yes.")]
    first = critique(
        thin,
        CONSENSUS,
        ConflictReport(),
        iteration=0,
        max_iterations=1,
        min_claims=2,
        min_sources=2,
    )
    assert first.recommendation == "retry" and "need 2" in first.issues[0]
    second = critique(
        thin,
        CONSENSUS,
        ConflictReport(),
        iteration=1,
        max_iterations=1,
        min_claims=2,
        min_sources=2,
    )
    assert second.recommendation == "inconclusive"
    no_retry = critique(thin, CONSENSUS, ConflictReport(), 0, 1, 2, 2, can_retry=False)
    assert no_retry.recommendation == "inconclusive"


def test_critic_declares_inconclusive_on_absent_consensus_or_unresolved_weakness() -> None:
    claims = [
        claim("arxiv:2301.00001", 1, Stance.SUPPORTS, "Evidence for the claim."),
        claim("arxiv:2405.00003", 1, Stance.REFUTES, "Evidence against the claim."),
    ]
    absent = critique(
        claims, {"strength": "absent", "confidence": 0.0}, ConflictReport(), 0, 1, 2, 2
    )
    assert absent.recommendation == "inconclusive"
    weak = {"strength": "weak", "confidence": 0.2}
    report = ConflictReport(resolutions=[resolution("inconclusive")])
    verdict = critique(claims, weak, report, 0, 1, 2, 2)
    assert verdict.recommendation == "inconclusive" and verdict.unresolved_conflicts == 1
    resolved = ConflictReport(resolutions=[resolution("supports")])
    assert critique(claims, weak, resolved, 0, 1, 2, 2).recommendation == "pass"


def test_verify_keeps_valid_citations_and_removes_invented_ones() -> None:
    valid = {"arxiv:2301.00001", "wikipedia:0123456789abcdef"}
    answer = (
        "Models pass benchmarks [arxiv:2301.00001]. They also fail [arxiv:9999.99999] , "
        "see [wikipedia:0123456789abcdef] and [arxiv:2301.00001#2]."
    )
    cleaned, result = verify_citations(answer, valid)
    assert result.citations_found == 4
    assert result.valid_citations == ["arxiv:2301.00001", "wikipedia:0123456789abcdef"]
    assert result.invalid_removed == ["arxiv:9999.99999"]
    assert result.has_valid_citation
    assert "[arxiv:9999.99999]" not in cleaned
    assert "They also fail," in cleaned  # stray space before punctuation tidied
    assert cleaned.endswith("[arxiv:2301.00001].")  # claim id rewritten to its source id


def test_verify_accepts_comma_separated_citation_lists() -> None:
    """Baseline finding (comparative-002): the model wrote
    `[wikipedia:a66…#1, wikipedia:a66…#3]` and the verifier, parsing one id per
    bracket, graded the answer as uncited."""
    valid = {"wikipedia:a665c398b96149c1", "arxiv:2301.00001"}
    answer = (
        "RLHF aligns models [wikipedia:a665c398b96149c1#1, wikipedia:a665c398b96149c1#3]. "
        "Others disagree [arxiv:2301.00001, arxiv:9999.99999#2]. Nothing here [arxiv:0000.1]."
    )
    cleaned, result = verify_citations(answer, valid)
    assert result.has_valid_citation is True
    assert result.valid_citations == ["wikipedia:a665c398b96149c1", "arxiv:2301.00001"]
    assert result.invalid_removed == ["arxiv:9999.99999", "arxiv:0000.1"]
    assert result.citations_found == 5
    assert "[wikipedia:a665c398b96149c1]." in cleaned  # list collapsed to its source, once
    assert "[arxiv:2301.00001]." in cleaned and "9999" not in cleaned
    assert cleaned.endswith("Nothing here.")


def test_confidence_caps_for_inconclusive_runs_and_forecasts() -> None:
    from app.pipeline.verify import cap_confidence, is_forecast_question

    assert cap_confidence(0.823, "inconclusive", forecast=False) == (
        0.5,
        cap_confidence(0.823, "inconclusive", forecast=False)[1],
    )
    assert "inconclusive" in (cap_confidence(0.823, "inconclusive", forecast=False)[1] or "")
    assert cap_confidence(0.4, "inconclusive", forecast=False) == (0.4, None)
    assert cap_confidence(0.9, "answered", forecast=True)[0] == 0.7
    assert "future outcome" in (cap_confidence(0.9, "answered", forecast=True)[1] or "")
    assert cap_confidence(0.9, "answered", forecast=False) == (0.9, None)
    assert cap_confidence(0.65, "answered", forecast=True) == (0.65, None)

    assert is_forecast_question("Will commercial fusion deliver grid power before 2040?")
    assert is_forecast_question("Is room-temperature superconductivity confirmed by 2035?")
    assert is_forecast_question("Do LLMs replace most engineering jobs within ten years?")
    assert not is_forecast_question("Does dropout reduce overfitting?")
    assert not is_forecast_question("Did the 2016 result replicate?")


def test_verify_reports_answers_without_citations() -> None:
    cleaned, result = verify_citations("No citations here.", {"arxiv:1"})
    assert cleaned == "No citations here." and not result.has_valid_citation
