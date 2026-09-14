"""Build the configured provider: budget guard, then metrics and circuit breaker."""

from __future__ import annotations

from app.config import Settings
from app.llm.base import LLMConfigurationError, LLMProvider, LLMRequest, LLMResponse, ModelTier
from app.llm.budget import DailyRequestBudget
from app.llm.instrumented import InstrumentedProvider
from app.llm.mock import MockProvider
from app.llm.pricing import PriceTable
from app.reliability.circuit import REGISTRY as BREAKERS


class BudgetedProvider(LLMProvider):
    """Consumes one unit of the daily budget per call before delegating."""

    def __init__(self, inner: LLMProvider, budget: DailyRequestBudget) -> None:
        self.inner = inner
        self.budget = budget
        self.name = inner.name

    def resolve_model(self, tier: ModelTier) -> str:
        return self.inner.resolve_model(tier)

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.budget.consume()
        return self.inner.complete(request)


def build_provider(settings: Settings, instrument: bool = True) -> LLMProvider:
    budget = DailyRequestBudget(settings.llm_daily_request_limit)
    if settings.llm_provider == "mock":
        inner: LLMProvider = MockProvider()
    elif settings.llm_provider == "gemini":
        # Imported lazily so the mock path never needs the SDK's network client.
        from app.llm.gemini import GeminiProvider

        inner = GeminiProvider(
            api_key=settings.llm_api_key.get_secret_value(),
            models={
                ModelTier.FAST: settings.llm_model_fast,
                ModelTier.STANDARD: settings.llm_model_standard,
            },
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            price_table=PriceTable.from_json(settings.llm_price_table_json),
            thinking_level=settings.llm_thinking_level,
            min_interval_seconds=settings.llm_min_interval_seconds,
        )
    else:
        raise LLMConfigurationError(f"unknown LLM provider: {settings.llm_provider}")

    budgeted = BudgetedProvider(inner, budget)
    if not instrument:
        return budgeted
    breaker = BREAKERS.get(
        f"llm:{settings.llm_provider}",
        failure_threshold=settings.circuit_failure_threshold,
        recovery_seconds=settings.circuit_recovery_seconds,
    )
    return InstrumentedProvider(budgeted, breaker)
