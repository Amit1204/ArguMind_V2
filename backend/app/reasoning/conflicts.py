"""Conflict resolution: deterministic scoring first, model arbitration only when close.

For each conflict the resolver
1. scores both sides from the sources' authority, recency, evidence type and
   the claims' own confidence (all recorded in the resolution);
2. decides by margin when the heuristics are clear;
3. otherwise asks the model to arbitrate with the claims as data, validating
   the structured answer and falling back to the heuristics if the call fails;
4. always keeps the losing side as a minority report, and adds `supersedes`
   edges when a clearly newer winning claim beats an older losing one.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.evidence.models import Claim
from app.graph.model import CitationGraph, Conflict, EdgeType
from app.llm.base import LLMError, LLMProvider, LLMRequest, ModelTier, UsageLedger
from app.reasoning.models import (
    ArbitrationOutput,
    ConflictReport,
    MinorityReport,
    Resolution,
    SideScore,
    Verdict,
)
from app.reasoning.prompts import RESOLVE_SYSTEM, resolve_prompt
from app.sources.models import Source

log = logging.getLogger(__name__)

EVIDENCE_WEIGHT: dict[str, float] = {
    "empirical": 1.0,
    "review": 0.9,
    "theoretical": 0.7,
    "opinion": 0.5,
    "other": 0.6,
}
UNKNOWN_YEAR_RECENCY = 0.8
SUPERSEDE_YEAR_GAP = 2


def recency_factor(year: int | None, today_year: int | None = None) -> float:
    """1.0 for the last two years, then -0.05 per year down to 0.5; unknown = 0.8."""
    if year is None:
        return UNKNOWN_YEAR_RECENCY
    current = today_year or datetime.now(UTC).year
    age = max(0, current - year - 2)
    return max(0.5, round(1.0 - 0.05 * age, 3))


class ConflictResolver:
    def __init__(
        self,
        provider: LLMProvider | None = None,
        ledger: UsageLedger | None = None,
        decisive_margin: float = 0.35,
        inconclusive_margin: float = 0.15,
        today_year: int | None = None,
    ) -> None:
        """Margins are on (support - refute) / (support + refute):
        |margin| >= decisive_margin decides without the model; otherwise the model
        arbitrates when available, else |margin| < inconclusive_margin is inconclusive."""
        if not 0 <= inconclusive_margin <= decisive_margin <= 1:
            raise ValueError("expected 0 <= inconclusive_margin <= decisive_margin <= 1")
        self._provider = provider
        self._ledger = ledger
        self._decisive = decisive_margin
        self._inconclusive = inconclusive_margin
        self._today_year = today_year

    # ------------------------------------------------------------------ public
    def resolve(
        self, graph: CitationGraph, sources: list[Source], claims: list[Claim]
    ) -> ConflictReport:
        source_by_id = {s.source_id: s for s in sources}
        claim_by_id = {c.claim_id: c for c in claims}
        report = ConflictReport()
        for conflict in graph.conflicts():
            resolution = self._resolve_one(conflict, source_by_id, claim_by_id)
            self._apply_supersedes(graph, resolution, conflict, source_by_id)
            report.resolutions.append(resolution)
        log.info("conflicts resolved: %s", report.summary())
        return report

    # ---------------------------------------------------------------- scoring
    def _score_side(
        self, claim_ids: list[str], sources: dict[str, Source], claims: dict[str, Claim]
    ) -> SideScore:
        total = 0.0
        years: list[int] = []
        source_ids: set[str] = set()
        for cid in claim_ids:
            claim = claims[cid]
            source = sources.get(claim.source_id)
            authority = source.authority if source else 0.5
            year = source.published_year if source else None
            if year:
                years.append(year)
            source_ids.add(claim.source_id)
            total += (
                claim.confidence
                * authority
                * recency_factor(year, self._today_year)
                * EVIDENCE_WEIGHT.get(claim.evidence_type, EVIDENCE_WEIGHT["other"])
            )
        return SideScore(
            score=round(total, 4),
            claims=len(claim_ids),
            sources=len(source_ids),
            newest_year=max(years) if years else None,
        )

    def _resolve_one(
        self, conflict: Conflict, sources: dict[str, Source], claims: dict[str, Claim]
    ) -> Resolution:
        support = self._score_side(conflict.supporting, sources, claims)
        refute = self._score_side(conflict.refuting, sources, claims)
        total = support.score + refute.score
        margin = round((support.score - refute.score) / total, 4) if total else 0.0

        if abs(margin) >= self._decisive or self._provider is None:
            winner, method, confidence, reasoning = self._heuristic_verdict(margin)
        else:
            winner, method, confidence, reasoning = self._arbitrate(
                conflict, sources, claims, support, refute, margin
            )

        minority = self._minority_report(winner, conflict, claims)
        return Resolution(
            question_index=conflict.question_index,
            question=conflict.question,
            winner=winner,
            method=method,
            support=support,
            refute=refute,
            margin=margin,
            confidence=confidence,
            reasoning=reasoning,
            minority_report=minority,
        )

    def _heuristic_verdict(self, margin: float) -> tuple[Verdict, str, float, str]:
        if abs(margin) < self._inconclusive:
            return (
                "inconclusive",
                "heuristic",
                round(1 - abs(margin), 3) * 0.5,
                f"Evidence is balanced (margin {margin:+.2f}); no side is clearly stronger.",
            )
        winner: Verdict = "supports" if margin > 0 else "refutes"
        return (
            winner,
            "heuristic",
            round(min(1.0, 0.5 + abs(margin) / 2), 3),
            f"The {winner} side outweighs the other on authority, recency, evidence type "
            f"and stated confidence (margin {margin:+.2f}).",
        )

    def _arbitrate(
        self,
        conflict: Conflict,
        sources: dict[str, Source],
        claims: dict[str, Claim],
        support: SideScore,
        refute: SideScore,
        margin: float,
    ) -> tuple[Verdict, str, float, str]:
        assert self._provider is not None  # caller checked
        request = LLMRequest(
            system=RESOLVE_SYSTEM,
            prompt=resolve_prompt(
                conflict.question,
                [self._describe(claims[c], sources) for c in conflict.supporting],
                [self._describe(claims[c], sources) for c in conflict.refuting],
                support.score,
                refute.score,
            ),
            tier=ModelTier.STANDARD,
            response_schema=ArbitrationOutput,
            purpose="resolve_conflict",
            max_output_tokens=1024,
        )
        try:
            response = self._provider.complete(request)
            if self._ledger is not None:
                self._ledger.record(response.usage)
            verdict = response.parse(ArbitrationOutput)
        except LLMError as exc:
            log.warning("model arbitration failed (%s); using heuristics", type(exc).__name__)
            winner, _, confidence, reasoning = self._heuristic_verdict(margin)
            return winner, "heuristic_after_model_error", confidence, reasoning
        return verdict.winner, "model", round(verdict.confidence, 3), verdict.reasoning.strip()

    @staticmethod
    def _describe(claim: Claim, sources: dict[str, Source]) -> str:
        source = sources.get(claim.source_id)
        meta = (
            f"{source.kind.value} {source.published_year or 'n/a'} authority={source.authority:.2f}"
            if source
            else "unknown source"
        )
        return (
            f"- [{claim.claim_id}] ({meta}, {claim.evidence_type}, "
            f"confidence={claim.confidence:.2f}) {claim.text}"
        )

    # --------------------------------------------------------- minority report
    @staticmethod
    def _minority_report(
        winner: Verdict, conflict: Conflict, claims: dict[str, Claim]
    ) -> MinorityReport | None:
        if winner == "inconclusive":
            return None
        losing_ids = conflict.refuting if winner == "supports" else conflict.supporting
        losing_stance = "refutes" if winner == "supports" else "supports"
        texts = [claims[c].text for c in losing_ids[:3]]
        return MinorityReport(
            stance=losing_stance,
            claim_ids=losing_ids,
            source_ids=sorted({claims[c].source_id for c in losing_ids}),
            summary=" ".join(texts)[:600],
        )

    # ------------------------------------------------------------- supersedes
    def _apply_supersedes(
        self,
        graph: CitationGraph,
        resolution: Resolution,
        conflict: Conflict,
        sources: dict[str, Source],
    ) -> None:
        if resolution.winner == "inconclusive":
            return
        winning = conflict.supporting if resolution.winner == "supports" else conflict.refuting
        losing = conflict.refuting if resolution.winner == "supports" else conflict.supporting

        def year_of(claim_id: str) -> int | None:
            source = sources.get(graph.source_of(claim_id))
            return source.published_year if source else None

        dated_winners = [(year_of(c), c) for c in winning if year_of(c) is not None]
        if not dated_winners:
            return
        newest_year, newest_claim = max(dated_winners)
        for old in losing:
            old_year = year_of(old)
            if old_year is not None and newest_year - old_year >= SUPERSEDE_YEAR_GAP:
                graph.link(
                    newest_claim,
                    old,
                    EdgeType.SUPERSEDES,
                    weight=resolution.confidence,
                    explanation=f"{newest_year} evidence supersedes {old_year} claim "
                    f"after conflict resolution ({resolution.method})",
                )
                resolution.superseded.append((newest_claim, old))
