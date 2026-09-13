"""Rule-based quality gate (ADR-007).

Deterministic on purpose: the decision to retry gathering, to answer, or to
declare the evidence inconclusive must be explainable and reproducible, and
must not spend model budget.
"""

from __future__ import annotations

from app.evidence.models import Claim
from app.pipeline.schemas import CriticOutput
from app.reasoning.models import ConflictReport


def critique(
    claims: list[Claim],
    consensus: dict,
    report: ConflictReport,
    iteration: int,
    max_iterations: int,
    min_claims: int,
    min_sources: int,
    can_retry: bool = True,
) -> CriticOutput:
    stance_claims = [c for c in claims if c.stance.value != "neutral"]
    sources = {c.source_id for c in stance_claims}
    unresolved = report.inconclusive_count
    issues: list[str] = []

    if len(stance_claims) < min_claims:
        issues.append(f"only {len(stance_claims)} claim(s) take a position (need {min_claims})")
    if len(sources) < min_sources:
        issues.append(f"only {len(sources)} source(s) provide evidence (need {min_sources})")
    strength = consensus.get("strength", "absent")
    consensus_conf = float(consensus.get("confidence", 0.0) or 0.0)
    if report.conflict_count and unresolved == report.conflict_count:
        issues.append("every detected conflict is unresolved")
    if strength == "absent":
        issues.append("no consensus could be formed")

    insufficient = len(stance_claims) < min_claims or len(sources) < min_sources
    if insufficient:
        if can_retry and iteration < max_iterations:
            recommendation = "retry"
        else:
            recommendation = "inconclusive"
    elif strength in {"absent"} or (
        strength == "weak" and consensus_conf < 0.4 and unresolved == report.conflict_count > 0
    ):
        recommendation = "inconclusive"
    else:
        recommendation = "pass"

    # Confidence: the consensus confidence, damped when evidence is thin.
    evidence_factor = min(1.0, len(sources) / max(min_sources + 1, 1))
    confidence = round(consensus_conf * (0.6 + 0.4 * evidence_factor), 3)
    return CriticOutput(
        passed=recommendation == "pass",
        recommendation=recommendation,
        issues=issues,
        evidence_claims=len(stance_claims),
        evidence_sources=len(sources),
        unresolved_conflicts=unresolved,
        confidence=confidence,
    )
