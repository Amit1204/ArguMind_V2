"""USD-per-million-token prices, optional. Empty table means a free tier ($0)."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field


class ModelPrice(BaseModel):
    input: float = Field(ge=0)  # USD per 1M input tokens
    output: float = Field(ge=0)  # USD per 1M output tokens


class PriceTable:
    def __init__(self, prices: dict[str, ModelPrice]) -> None:
        self._prices = prices

    @classmethod
    def from_json(cls, raw: str | None) -> PriceTable:
        if not raw or not raw.strip():
            return cls({})
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("LLM_PRICE_TABLE_JSON must be an object keyed by model name")
        return cls({model: ModelPrice.model_validate(p) for model, p in data.items()})

    def estimate(self, model: str, input_tokens: int, output_tokens: int) -> float:
        price = self._prices.get(model)
        if price is None:
            return 0.0
        return round((input_tokens * price.input + output_tokens * price.output) / 1_000_000, 6)

    def __contains__(self, model: str) -> bool:
        return model in self._prices
