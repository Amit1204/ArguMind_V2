"""Default mock handlers must produce output that validates against the real schemas."""

from __future__ import annotations

from app.llm.base import LLMRequest
from app.llm.mock import MockProvider
from app.reasoning.models import ArbitrationOutput
from app.reasoning.prompts import resolve_prompt


def test_default_resolve_conflict_follows_the_heuristic_scores() -> None:
    provider = MockProvider()

    def arbitrate(support: float, refute: float) -> ArbitrationOutput:
        prompt = resolve_prompt("q?", ["- [a#1] yes"], ["- [b#1] no"], support, refute)
        response = provider.complete(LLMRequest("s", prompt, purpose="resolve_conflict"))
        return response.parse(ArbitrationOutput)

    assert arbitrate(0.9, 0.5).winner == "supports"
    assert arbitrate(0.4, 0.8).winner == "refutes"
    assert arbitrate(0.5, 0.52).winner == "inconclusive"
    assert 0 <= arbitrate(1.0, 0.0).confidence <= 1
