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


def test_verify_reports_answers_without_citations() -> None:
    cleaned, result = verify_citations("No citations here.", {"arxiv:1"})
    assert cleaned == "No citations here." and not result.has_valid_citation
