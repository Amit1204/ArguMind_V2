"""Benchmark cases: YAML files, one per category, validated into typed expectations."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

BENCHMARK_DIR = Path(__file__).resolve().parent / "benchmark"

Category = Literal[
    "settled", "contested", "comparative", "no_evidence", "speculative", "injection", "validation"
]
RunStatus = Literal["answered", "inconclusive", "failed"]


class Expectation(BaseModel):
    # Outcome
    http_status: int = 200
    status_any: list[RunStatus] = Field(default_factory=lambda: ["answered"])
    # Evidence
    min_sources: int | None = None
    min_claims: int | None = None
    stances_any: list[str] = Field(default_factory=list)  # at least one of these stances present
    stances_all: list[str] = Field(default_factory=list)  # every one of these stances present
    conflict_expected: bool | None = (
        None  # True: at least one resolved conflict with a minority view
    )
    # Answer
    citations_required: bool | None = None  # default: required when answered
    answer_contains_any: list[str] = Field(default_factory=list)
    answer_excludes: list[str] = Field(default_factory=list)
    max_confidence: float | None = None
    # Safety
    injection_markers: list[str] = Field(default_factory=list)  # must not appear anywhere
    # Cost
    max_llm_calls: int = 30
    max_latency_ms: int = 300_000

    @field_validator("answer_contains_any", "answer_excludes", "injection_markers", mode="before")
    @classmethod
    def _strings(cls, value: list) -> list[str]:
        return [str(v) for v in value or []]


class Case(BaseModel):
    id: str = Field(pattern=r"^[a-z]+-\d{3}$")
    category: Category
    question: str = Field(min_length=1, max_length=2000)
    notes: str = ""
    expect: Expectation = Field(default_factory=Expectation)


def load_benchmark(directory: Path = BENCHMARK_DIR) -> list[Case]:
    cases: list[Case] = []
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if not isinstance(data, list):
            raise ValueError(f"{path.name}: expected a list of cases")
        for raw in data:
            case = Case.model_validate(raw)
            if case.category != path.stem:
                raise ValueError(f"{path.name}: case {case.id} has category {case.category}")
            cases.append(case)
    ids = [c.id for c in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate case ids: {duplicates}")
    return cases
