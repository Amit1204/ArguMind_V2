from __future__ import annotations

from datetime import date

import pytest
from pydantic import BaseModel

from app.config import Settings
from app.llm.base import (
    LLMBudgetExceededError,
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMResponseFormatError,
    LLMUsage,
    ModelTier,
    UsageLedger,
    extract_json,
)
from app.llm.budget import DailyRequestBudget
from app.llm.factory import BudgetedProvider, build_provider
from app.llm.mock import MockProvider
from app.llm.pricing import PriceTable


class Answer(BaseModel):
    value: int


def usage(**overrides) -> LLMUsage:  # noqa: ANN003
    base = dict(
        provider="mock", model="m", tier="fast", purpose="p", input_tokens=10, output_tokens=5,
        thinking_tokens=0, latency_ms=1, estimated_cost_usd=0.0,
    )  # fmt: skip
    base.update(overrides)
    return LLMUsage(**base)


# ---------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    "text",
    ['{"value": 3}', '```json\n{"value": 3}\n```', 'Sure! Here it is: {"value": 3} hope it helps'],
)
def test_parse_tolerates_fences_and_prose(text: str) -> None:
    assert LLMResponse(text=text, usage=usage()).parse(Answer).value == 3


def test_parse_reports_schema_mismatch() -> None:
    with pytest.raises(LLMResponseFormatError):
        LLMResponse(text='{"value": "three"}', usage=usage()).parse(Answer)


def test_extract_json_rejects_empty_and_non_json() -> None:
    for bad in ("", "   ", "no json here"):
        with pytest.raises(LLMResponseFormatError):
            extract_json(bad)


# ---------------------------------------------------------------- mock provider


def test_mock_extracts_claims_deterministically_from_the_summary() -> None:
    provider = MockProvider()
    prompt = (
        "SUB-QUESTION:\nDo LLMs understand language?\n\nSOURCE SUMMARY:\n"
        "We show strong results on in-distribution benchmarks. However, models fail "
        "systematically on novel compositions, which suggests no real understanding.\n\n"
        "OUTPUT:\nJSON"
    )
    request = LLMRequest(system="s", prompt=prompt, purpose="extract_claims")
    first = provider.complete(request).text
    second = provider.complete(request).text
    assert first == second
    parsed = __import__("json").loads(first)["claims"]
    assert [c["stance"] for c in parsed] == ["supports", "refutes"]
    assert parsed[0]["evidence_type"] == "empirical"


def test_mock_scripted_handlers_and_failures() -> None:
    provider = MockProvider.scripted(answer=lambda r: {"value": 42})
    assert provider.complete(LLMRequest("s", "p", purpose="answer")).parse(Answer).value == 42
    provider.fail_with("answer", LLMError("boom"))
    with pytest.raises(LLMError):
        provider.complete(LLMRequest("s", "p", purpose="answer"))
    with pytest.raises(LLMError):
        provider.complete(LLMRequest("s", "p", purpose="unknown_purpose"))


# ---------------------------------------------------------------- budget and pricing


def test_daily_budget_refuses_then_resets_next_day() -> None:
    clock = {"day": date(2026, 9, 13)}
    budget = DailyRequestBudget(2, today=lambda: clock["day"])
    budget.consume()
    budget.consume()
    assert budget.remaining == 0
    with pytest.raises(LLMBudgetExceededError):
        budget.consume()
    clock["day"] = date(2026, 9, 14)
    budget.consume()
    assert budget.used == 1


def test_budgeted_provider_counts_every_call() -> None:
    provider = BudgetedProvider(MockProvider.scripted(p=lambda r: {}), DailyRequestBudget(1))
    provider.complete(LLMRequest("s", "p", purpose="p"))
    with pytest.raises(LLMBudgetExceededError):
        provider.complete(LLMRequest("s", "p", purpose="p"))


def test_price_table_estimates_and_defaults_to_free() -> None:
    table = PriceTable.from_json('{"gemini-x": {"input": 0.30, "output": 2.50}}')
    assert table.estimate("gemini-x", 1_000_000, 100_000) == pytest.approx(0.55)
    assert table.estimate("other", 1_000_000, 1_000_000) == 0.0
    assert PriceTable.from_json("").estimate("gemini-x", 10, 10) == 0.0
    with pytest.raises(ValueError):
        PriceTable.from_json("[1, 2]")


def test_usage_ledger_totals() -> None:
    ledger = UsageLedger()
    ledger.record(
        usage(input_tokens=10, output_tokens=5, thinking_tokens=2, estimated_cost_usd=0.001)
    )
    ledger.record(usage(input_tokens=1, output_tokens=1))
    assert ledger.summary() == {
        "llm_calls": 2,
        "input_tokens": 11,
        "output_tokens": 8,
        "estimated_cost_usd": 0.001,
    }


# ---------------------------------------------------------------- factory


def test_factory_builds_mock_without_a_key() -> None:
    provider = build_provider(Settings(llm_provider="mock"))
    assert provider.name == "mock"
    assert provider.resolve_model(ModelTier.FAST) == "mock-fast"


def test_factory_requires_a_key_for_gemini() -> None:
    from app.llm.base import LLMConfigurationError

    with pytest.raises(LLMConfigurationError):
        build_provider(Settings(llm_provider="gemini", llm_api_key=""))
