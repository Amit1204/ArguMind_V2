"""Deterministic mock provider for tests, CI and key-less local runs.

Every purpose has a default handler that derives a plausible structured answer
from the prompt text alone, so the whole pipeline can run without a network.
Tests can override handlers per purpose or inject failures.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.llm.base import LLMError, LLMProvider, LLMRequest, LLMResponse, LLMUsage, ModelTier

Handler = Callable[[LLMRequest], dict | list | str]

_NEGATION = re.compile(
    r"\b(not|no|never|fails?|failed|contradict\w*|unlikely|little evidence|does not|"
    r"cannot|insufficient|overstated|refute\w*|against)\b",
    re.IGNORECASE,
)
_EMPIRICAL = re.compile(
    r"\b(we show|we find|experiment\w*|study|studies|results?|trial|measured|evaluat\w+|"
    r"benchmark\w*|dataset)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _section(prompt: str, header: str) -> str:
    """Text following `HEADER:` up to the next all-caps header line or the end."""
    match = re.search(
        rf"{re.escape(header)}:\s*\n?(.*?)(?:\n[A-Z][A-Z _-]{{3,}}:|\Z)", prompt, re.S
    )
    return match.group(1).strip() if match else ""


def default_extract_claims(request: LLMRequest) -> dict:
    """Split the source summary into sentences; infer stance from negation words."""
    summary = _section(request.prompt, "SOURCE SUMMARY") or request.prompt
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(summary) if len(s.strip()) >= 20]
    claims = []
    for sentence in sentences[:3]:
        stance = "refutes" if _NEGATION.search(sentence) else "supports"
        claims.append(
            {
                "text": sentence[:400],
                "stance": stance,
                "confidence": 0.7 if _EMPIRICAL.search(sentence) else 0.6,
                "evidence_type": "empirical" if _EMPIRICAL.search(sentence) else "review",
            }
        )
    return {"claims": claims}


_SCORES = re.compile(r"support_score=([\d.]+)\s+refute_score=([\d.]+)")


def default_resolve_conflict(request: LLMRequest) -> dict:
    """Side with the heuristic scores echoed in the prompt; inconclusive when close."""
    match = _SCORES.search(request.prompt)
    support, refute = (float(match.group(1)), float(match.group(2))) if match else (0.0, 0.0)
    total = support + refute
    margin = (support - refute) / total if total else 0.0
    if abs(margin) < 0.1:
        winner, confidence = "inconclusive", 0.4
    else:
        winner, confidence = ("supports" if margin > 0 else "refutes"), round(0.5 + abs(margin), 2)
    return {
        "winner": winner,
        "reasoning": f"Mock arbitration from heuristic scores (margin {margin:+.2f}).",
        "confidence": min(confidence, 1.0),
    }


def default_plan(request: LLMRequest) -> dict:
    question = _section(request.prompt, "QUESTION") or request.prompt.strip()
    core = question.rstrip("?. ").strip()
    core = re.sub(r"^(do|does|is|are|can|could|will|should)\s+", "", core, flags=re.I)
    return {
        "sub_questions": [question, f"What evidence contradicts the claim that {core}?"],
        "domains": ["general"],
        "complexity": "moderate",
    }


_TALLY = re.compile(r"TALLY:\s*supports=(\d+)\s+refutes=(\d+)\s+neutral=(\d+)\s+sources=(\d+)")


def default_consensus(request: LLMRequest) -> dict:
    match = _TALLY.search(request.prompt)
    supports, refutes, _neutral, sources = (
        (int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4)))
        if match
        else (0, 0, 0, 0)
    )
    total = supports + refutes
    if total == 0:
        return {
            "overall": "No retrieved source takes a position on the question.",
            "strength": "absent",
            "key_agreements": [],
            "key_disagreements": [],
            "research_gaps": ["No evidence for or against was retrieved."],
            "confidence": 0.0,
        }
    margin = (supports - refutes) / total
    strength = (
        "strong"
        if abs(margin) > 0.6 and sources >= 3
        else "moderate"
        if abs(margin) > 0.3
        else "weak"
    )
    return {
        "overall": (
            f"Mock consensus: {supports} supporting vs {refutes} refuting claims "
            f"from {sources} sources."
        ),
        "strength": strength,
        "key_agreements": ["Mock agreement derived from the tally."],
        "key_disagreements": ["Mock disagreement derived from the tally."] if refutes else [],
        "research_gaps": [],
        "confidence": round(0.4 + abs(margin) * 0.5, 2),
    }


_SOURCE_LINE = re.compile(r"^- \[([a-z_]+:[^\]\s]+)\]", re.M)


def default_answer(request: LLMRequest) -> dict:
    ids = _SOURCE_LINE.findall(_section(request.prompt, "SOURCES") or request.prompt)[:3]
    cited = " ".join(f"[{i}]" for i in ids) or "[unknown:0000]"
    return {
        "answer": (
            "Mock answer. The retrieved evidence is summarised here with citations to the "
            f"sources gathered in this run {cited}. Contradicting evidence and the minority "
            "view are noted, and confidence reflects the evidence tally."
        ),
        "confidence": 0.6,
    }


DEFAULT_HANDLERS: dict[str, Handler] = {
    "extract_claims": default_extract_claims,
    "resolve_conflict": default_resolve_conflict,
    "plan": default_plan,
    "consensus": default_consensus,
    "answer": default_answer,
}


@dataclass
class MockProvider(LLMProvider):
    name = "mock"
    handlers: dict[str, Handler] = field(default_factory=lambda: dict(DEFAULT_HANDLERS))
    failures: dict[str, LLMError] = field(default_factory=dict)
    latency_ms: int = 1
    calls: list[LLMRequest] = field(default_factory=list)

    @classmethod
    def scripted(cls, **handlers: Handler) -> MockProvider:
        provider = cls()
        provider.handlers.update(handlers)
        return provider

    def fail_with(self, purpose: str, error: LLMError) -> None:
        self.failures[purpose] = error

    def resolve_model(self, tier: ModelTier) -> str:
        return f"mock-{tier.value}"

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        if request.purpose in self.failures:
            raise self.failures[request.purpose]
        handler = self.handlers.get(request.purpose)
        if handler is None:
            raise LLMError(f"mock provider has no handler for purpose '{request.purpose}'")
        payload = handler(request)
        text = payload if isinstance(payload, str) else json.dumps(payload)
        usage = LLMUsage(
            provider=self.name,
            model=self.resolve_model(request.tier),
            tier=request.tier.value,
            purpose=request.purpose,
            input_tokens=max(1, (len(request.system) + len(request.prompt)) // 4),
            output_tokens=max(1, len(text) // 4),
            thinking_tokens=0,
            latency_ms=self.latency_ms,
            estimated_cost_usd=0.0,
        )
        return LLMResponse(text=text, usage=usage)
