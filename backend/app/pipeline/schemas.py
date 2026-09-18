"""Structured outputs of pipeline stages (model outputs are validated against these)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

RunStatus = Literal["queued", "running", "answered", "inconclusive", "failed"]
StageStatus = Literal["ok", "failed", "skipped", "timeout"]


class PlanOutput(BaseModel):
    sub_questions: list[str] = Field(min_length=1, max_length=4)
    domains: list[str] = Field(default_factory=list, max_length=6)
    complexity: Literal["simple", "moderate", "complex"] = "moderate"
    # True when the question asks about a future outcome or date; the answer's
    # confidence is then capped (published evidence cannot settle a forecast).
    forecast: bool = False


class ConsensusOutput(BaseModel):
    overall: str = Field(min_length=5, max_length=1500)
    strength: Literal["strong", "moderate", "weak", "absent"]
    key_agreements: list[str] = Field(default_factory=list, max_length=5)
    key_disagreements: list[str] = Field(default_factory=list, max_length=5)
    research_gaps: list[str] = Field(default_factory=list, max_length=5)
    confidence: float = Field(ge=0, le=1)


class AnswerOutput(BaseModel):
    answer: str = Field(min_length=20, max_length=8000)
    confidence: float = Field(ge=0, le=1)


class CriticOutput(BaseModel):
    passed: bool
    recommendation: Literal["pass", "retry", "inconclusive"]
    issues: list[str] = Field(default_factory=list)
    evidence_claims: int
    evidence_sources: int
    unresolved_conflicts: int
    confidence: float = Field(ge=0, le=1)


class Cluster(BaseModel):
    cluster_id: str
    label: str
    claim_ids: list[str]
    source_ids: list[str]
    supports: int = 0
    refutes: int = 0
    neutral: int = 0

    @property
    def contested(self) -> bool:
        return self.supports > 0 and self.refutes > 0


class VerificationResult(BaseModel):
    citations_found: int
    valid_citations: list[str]
    invalid_removed: list[str]
    has_valid_citation: bool


class StageRecord(BaseModel):
    name: str
    attempt: int = 1
    status: StageStatus
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    detail: dict = Field(default_factory=dict)
    error: str | None = None


class RunUsage(BaseModel):
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
