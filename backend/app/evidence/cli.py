"""Manual check of Phases 2-3 against the live providers.

    docker compose run --rm backend python -m app.evidence.cli "Do LLMs understand?" --graph

Searches arXiv and Wikipedia, extracts claims with the configured model and,
with --graph, builds the citation graph, detects conflicts and resolves them.
Not part of the API; the pipeline (Phase 4) does this per run.
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.evidence.extractor import ClaimExtractor, ExtractionError
from app.evidence.models import Claim
from app.llm.base import LLMError, UsageLedger
from app.llm.factory import build_provider
from app.sources.factory import build_source_service
from app.sources.models import Source, SourceKind


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ArguMind check: search + extract (+ graph)")
    parser.add_argument("question")
    parser.add_argument("--arxiv", type=int, default=3)
    parser.add_argument("--wikipedia", type=int, default=2)
    parser.add_argument(
        "--graph", action="store_true", help="build the graph and resolve conflicts"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    service = build_source_service(settings)  # memory cache: no database needed for the CLI
    provider = build_provider(settings)
    ledger = UsageLedger()
    extractor = ClaimExtractor(provider, ledger)

    # A limit of 0 skips that source entirely (useful while arXiv is rate limiting us).
    limits = {SourceKind.ARXIV: args.arxiv, SourceKind.WIKIPEDIA: args.wikipedia}
    outcomes = [service.search(kind, args.question, n) for kind, n in limits.items() if n > 0]
    sources: list[Source] = []
    claims: list[Claim] = []
    for outcome in outcomes:
        flags = (" (cached)" if outcome.cached else "") + (
            f" ERROR {outcome.error}" if outcome.error else ""
        )
        print(f"\n== {outcome.kind.value}: {len(outcome.sources)} sources{flags}")
        for source in outcome.sources:
            sources.append(source)
            print(f"-- [{source.source_id}] {source.title} ({source.published_year or 'n/a'})")
            try:
                extracted = extractor.extract(source, args.question, 0)
            except (ExtractionError, LLMError) as exc:
                print(f"   extraction failed: {exc}")
                continue
            claims.extend(extracted)
            for claim in extracted:
                print(
                    f"   {claim.claim_id:32s} {claim.stance.value:8s} "
                    f"{claim.confidence:.2f} {claim.text[:110]}"
                )
    print(f"\n{len(claims)} claims")

    if args.graph:
        from app.graph.builder import build_graph
        from app.reasoning.conflicts import ConflictResolver

        graph = build_graph([args.question], sources, claims)
        print(f"\n== graph: {graph.summary()}")
        report = ConflictResolver(provider, ledger).resolve(graph, sources, claims)
        print(f"== conflicts: {report.summary()}")
        for r in report.resolutions:
            print(
                f"-- q{r.question_index}: winner={r.winner} method={r.method} "
                f"margin={r.margin:+.2f} confidence={r.confidence:.2f}"
            )
            print(f"   support={r.support.model_dump()}\n   refute={r.refute.model_dump()}")
            print(f"   reasoning: {r.reasoning[:300]}")
            if r.minority_report:
                m = r.minority_report
                print(f"   minority ({m.stance}, {len(m.claim_ids)} claims): {m.summary[:200]}")
            for newer, older in r.superseded:
                print(f"   supersedes: {newer} -> {older}")

    print(f"\nusage: {ledger.summary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
