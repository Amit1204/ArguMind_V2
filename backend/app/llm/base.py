"""Core model-layer abstractions shared by every provider."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class ModelTier(str, Enum):
    """Routing tiers: cheap extraction/classification, standard synthesis."""

    FAST = "fast"
    STANDARD = "standard"


class LLMError(Exception):
    """Base class for provider failures the pipeline can handle."""


class LLMTimeoutError(LLMError):
    pass


class LLMRateLimitError(LLMError):
    """Provider returned 429 or an equivalent quota signal."""


class LLMUnavailableError(LLMError):
    """Provider returned 5xx / high demand; retryable."""


class LLMBudgetExceededError(LLMError):
    """Local daily request budget exhausted; refused before calling the provider."""


class LLMResponseFormatError(LLMError):
    """The model returned text that does not match the requested schema."""


class LLMConfigurationError(LLMError):
    """Provider is not configured (missing API key, unknown provider name)."""


@dataclass(slots=True)
class LLMRequest:
    system: str
    prompt: str
    tier: ModelTier = ModelTier.FAST
    response_schema: type[BaseModel] | None = None
    temperature: float = 0.0
    max_output_tokens: int = 4096
    purpose: str = "general"  # e.g. extract_claims; used for logging, mocks and accounting


@dataclass(slots=True)
class LLMUsage:
    provider: str
    model: str
    tier: str
    purpose: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    latency_ms: int
    estimated_cost_usd: float
    attempts: int = 1
    fallback: bool = False  # served by a lower tier after the requested model failed


@dataclass(slots=True)
class LLMResponse:
    text: str
    usage: LLMUsage
    raw: object = field(default=None, repr=False)

    def parse(self, schema: type[T]) -> T:
        """Parse the response as JSON validated against `schema`.

        Tolerates markdown code fences and leading prose, which some models add
        even in JSON mode. Anything else is a format error the caller records.
        """
        payload = extract_json(self.text)
        try:
            return schema.model_validate_json(payload)
        except ValidationError as exc:
            raise LLMResponseFormatError(
                f"response did not match {schema.__name__}: {exc.errors()[:3]}"
            ) from exc


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> str:
    """Return the JSON object/array inside `text`, stripping fences or prose."""
    if not text or not text.strip():
        raise LLMResponseFormatError("empty response")
    candidate = text.strip()
    fenced = _FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        json.loads(candidate)
        return candidate
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = candidate.find(open_ch), candidate.rfind(close_ch)
        if start != -1 and end > start:
            span = candidate[start : end + 1]
            try:
                json.loads(span)
                return span
            except json.JSONDecodeError:
                continue
    raise LLMResponseFormatError("response contained no valid JSON")


class LLMProvider(ABC):
    """Interface every provider implements. Keep it small."""

    name: str = "abstract"

    @abstractmethod
    def resolve_model(self, tier: ModelTier) -> str:
        """Map a routing tier to a concrete model identifier."""

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        """Run one completion. Must raise an `LLMError` subclass on failure."""


class UsageLedger:
    """Collects the usage of every call made on behalf of one run."""

    def __init__(self) -> None:
        self.entries: list[LLMUsage] = []

    def record(self, usage: LLMUsage) -> None:
        self.entries.append(usage)

    @property
    def calls(self) -> int:
        return len(self.entries)

    @property
    def input_tokens(self) -> int:
        return sum(u.input_tokens for u in self.entries)

    @property
    def output_tokens(self) -> int:
        return sum(u.output_tokens + u.thinking_tokens for u in self.entries)

    @property
    def estimated_cost_usd(self) -> float:
        return round(sum(u.estimated_cost_usd for u in self.entries), 6)

    def summary(self) -> dict[str, float | int]:
        return {
            "llm_calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }
