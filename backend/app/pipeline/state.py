"""LangGraph state for one run. Plain JSON-able values only (ADR-003)."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class RunState(TypedDict, total=False):
    question: str
    sub_questions: list[str]
    plan: dict
    sources: list[dict]  # Source.model_dump(mode="json"), with sub_question_index set
    claims: list[dict]  # Claim.model_dump(mode="json")
    graph: dict  # CitationGraph.to_dict()
    conflict_report: dict  # ConflictReport.model_dump()
    clusters: list[dict]
    consensus: dict
    critic: dict
    iteration: int  # number of critic passes so far
    failed_source_kinds: list[str]  # sources that failed in this run; not tried again
    answer: dict  # AnswerOutput.model_dump()
    verification: dict
    caveats: Annotated[list[str], operator.add]
    status: str  # answered | inconclusive | failed
    outcome_reason: str
