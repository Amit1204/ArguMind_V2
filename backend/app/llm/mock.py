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


DEFAULT_HANDLERS: dict[str, Handler] = {
    "extract_claims": default_extract_claims,
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
