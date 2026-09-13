"""Claims: what a source says about a sub-question, with an explicit stance."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

EvidenceType = Literal["empirical", "review", "theoretical", "opinion", "other"]


class Stance(str, Enum):
    SUPPORTS = "supports"
    REFUTES = "refutes"
    NEUTRAL = "neutral"


class ExtractedClaim(BaseModel):
    """What the model returns for one claim (no ids: those are assigned by code)."""

    text: str = Field(min_length=5, max_length=500)
    stance: Stance
    confidence: float = Field(ge=0, le=1)
    evidence_type: EvidenceType = "other"


class ClaimExtraction(BaseModel):
    """Structured output schema for the extract_claims call."""

    claims: list[ExtractedClaim] = Field(default_factory=list, max_length=8)


class Claim(BaseModel):
    claim_id: str
    source_id: str
    sub_question_index: int | None = None
    text: str = Field(min_length=5, max_length=500)
    stance: Stance
    confidence: float = Field(ge=0, le=1)
    evidence_type: EvidenceType = "other"
