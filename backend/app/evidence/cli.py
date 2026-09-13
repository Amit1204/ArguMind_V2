"""Manual check of Phase 2 against the live providers.

    docker compose run --rm backend python -m app.evidence.cli "Do LLMs understand language?"

Searches arXiv and Wikipedia, extracts claims with the configured model, and
prints claims with stance plus the usage summary. Not part of the API.
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.evidence.extractor import ClaimExtractor, ExtractionError
from app.llm.base import LLMError, UsageLedger
from app.llm.factory import build_provider
from app.sources.factory import build_source_service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ArguMind phase-2 check: search + extract")
    parser.add_argument("question")
    parser.add_argument("--arxiv", type=int, default=3)
    parser.add_argument("--wikipedia", type=int, default=2)
    args = parser.parse_args(argv)

    settings = get_settings()
    service = build_source_service(settings)  # memory cache: no database needed for the CLI
    provider = build_provider(settings)
    ledger = UsageLedger()
    extractor = ClaimExtractor(provider, ledger)

    from app.sources.models import SourceKind

    outcomes = service.search_all(
        args.question, {SourceKind.ARXIV: args.arxiv, SourceKind.WIKIPEDIA: args.wikipedia}
    )
    total_claims = 0
    for outcome in outcomes:
        flags = (" (cached)" if outcome.cached else "") + (
            f" ERROR {outcome.error}" if outcome.error else ""
        )
        print(f"\n== {outcome.kind.value}: {len(outcome.sources)} sources{flags}")
        for source in outcome.sources:
            print(f"-- [{source.source_id}] {source.title} ({source.published_year or 'n/a'})")
            try:
                claims = extractor.extract(source, args.question, 0)
            except (ExtractionError, LLMError) as exc:
                print(f"   extraction failed: {exc}")
                continue
            total_claims += len(claims)
            for claim in claims:
                print(
                    f"   {claim.claim_id:32s} {claim.stance.value:8s} "
                    f"{claim.confidence:.2f} {claim.text[:110]}"
                )
    print(f"\n{total_claims} claims; usage: {ledger.summary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
