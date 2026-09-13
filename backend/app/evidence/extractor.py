"""Claim extraction: one structured model call per (source, sub-question)."""

from __future__ import annotations

import logging
import re

from app.evidence.models import Claim, ClaimExtraction
from app.evidence.prompts import EXTRACT_SYSTEM, extract_prompt
from app.llm.base import LLMProvider, LLMRequest, LLMResponseFormatError, ModelTier, UsageLedger
from app.sources.ids import claim_id
from app.sources.models import Source

log = logging.getLogger(__name__)
_NON_WORD = re.compile(r"[^a-z0-9 ]+")


class ExtractionError(Exception):
    """The model answered but not with a valid claim list; the stage records this."""


def _normalise(text: str) -> str:
    return _NON_WORD.sub("", " ".join(text.lower().split()))


class ClaimExtractor:
    def __init__(
        self,
        provider: LLMProvider,
        ledger: UsageLedger | None = None,
        max_claims: int = 6,
        tier: ModelTier = ModelTier.FAST,
    ) -> None:
        self._provider = provider
        self._ledger = ledger
        self._max_claims = max_claims
        self._tier = tier

    def extract(
        self, source: Source, sub_question: str, sub_question_index: int | None = None
    ) -> list[Claim]:
        if not source.summary.strip():
            return []
        request = LLMRequest(
            system=EXTRACT_SYSTEM.format(max_claims=self._max_claims),
            prompt=extract_prompt(sub_question, source),
            tier=self._tier,
            response_schema=ClaimExtraction,
            purpose="extract_claims",
            max_output_tokens=2048,
        )
        response = self._provider.complete(request)
        if self._ledger is not None:
            self._ledger.record(response.usage)
        try:
            extraction = response.parse(ClaimExtraction)
        except LLMResponseFormatError as exc:
            raise ExtractionError(f"{source.source_id}: {exc}") from exc

        claims: list[Claim] = []
        seen: set[str] = set()
        for item in extraction.claims:
            key = _normalise(item.text)
            if not key or key in seen:
                continue
            seen.add(key)
            claims.append(
                Claim(
                    claim_id=claim_id(source.source_id, len(claims) + 1),
                    source_id=source.source_id,
                    sub_question_index=sub_question_index,
                    text=item.text.strip(),
                    stance=item.stance,
                    confidence=round(item.confidence, 3),
                    evidence_type=item.evidence_type,
                )
            )
            if len(claims) >= self._max_claims:
                break
        log.info("extracted %d claims from %s", len(claims), source.source_id)
        return claims
