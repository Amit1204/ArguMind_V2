"""API models for runs."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.evidence.models import Claim
from app.pipeline.schemas import RunStatus, RunUsage, StageRecord
from app.sources.models import Source


class RunCreate(BaseModel):
    question: str = Field(min_length=10, max_length=500)
    client_id: str | None = Field(default=None, max_length=100)

    @field_validator("question")
    @classmethod
    def _meaningful(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 10:
            raise ValueError("question must contain at least 10 non-blank characters")
        return cleaned


class RunSummary(BaseModel):
    id: str
    question: str
    status: RunStatus
    confidence: float | None = None
    latency_ms: int | None = None
    llm_calls: int = 0
    started_at: datetime
    finished_at: datetime | None = None


class RunList(BaseModel):
    runs: list[RunSummary]
    total: int
    limit: int
    offset: int


class RunDetail(BaseModel):
    id: str
    request_id: str | None = None
    question: str
    status: RunStatus
    outcome_reason: str | None = None
    answer: str | None = None
    confidence: float | None = None
    iteration_count: int = 0
    usage: RunUsage
    latency_ms: int | None = None
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    sub_questions: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    resolutions: list[dict] = Field(default_factory=list)
    clusters: list[dict] = Field(default_factory=list)
    consensus: dict | None = None
    critic: dict | None = None
    verification: dict | None = None
    caveats: list[str] = Field(default_factory=list)
    stages: list[StageRecord] = Field(default_factory=list)
    graph_summary: dict = Field(default_factory=dict)


class GraphResponse(BaseModel):
    run_id: str
    nodes: list[dict]
    edges: list[dict]
    summary: dict
