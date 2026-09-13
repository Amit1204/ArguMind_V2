"""Outputs of conflict resolution."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["supports", "refutes", "inconclusive"]


class SideScore(BaseModel):
    score: float = Field(ge=0)
    claims: int
    sources: int
    newest_year: int | None = None


class ArbitrationOutput(BaseModel):
    """Structured output requested from the model when the heuristics are close."""

    winner: Verdict
    reasoning: str = Field(min_length=5, max_length=1500)
    confidence: float = Field(ge=0, le=1)


class MinorityReport(BaseModel):
    """The losing side, kept visible instead of discarded."""

    stance: Literal["supports", "refutes"]
    claim_ids: list[str]
    source_ids: list[str]
    summary: str


class Resolution(BaseModel):
    question_index: int
    question: str
    winner: Verdict
    method: Literal["heuristic", "model", "heuristic_after_model_error"]
    support: SideScore
    refute: SideScore
    margin: float = Field(ge=-1, le=1)  # (support - refute) / (support + refute)
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    minority_report: MinorityReport | None = None
    superseded: list[tuple[str, str]] = Field(default_factory=list)  # (newer claim, older claim)


class ConflictReport(BaseModel):
    resolutions: list[Resolution] = Field(default_factory=list)

    @property
    def conflict_count(self) -> int:
        return len(self.resolutions)

    @property
    def inconclusive_count(self) -> int:
        return sum(1 for r in self.resolutions if r.winner == "inconclusive")

    def summary(self) -> dict[str, int]:
        return {
            "conflicts": self.conflict_count,
            "resolved": self.conflict_count - self.inconclusive_count,
            "inconclusive": self.inconclusive_count,
            "model_arbitrations": sum(1 for r in self.resolutions if r.method == "model"),
        }
