"""Fakes for pipeline tests: a canned source service and a runner factory."""

from __future__ import annotations

from collections.abc import Callable

from app.config import Settings
from app.llm.base import LLMProvider
from app.llm.mock import MockProvider
from app.pipeline.runner import PipelineRunner
from app.pipeline.store import MemoryRunStore
from app.sources.models import Source, SourceKind
from app.sources.service import SearchOutcome

SUPPORTING = Source(
    source_id="arxiv:2301.00001",
    kind=SourceKind.ARXIV,
    title="Compositional benchmarks show understanding",
    url="https://arxiv.org/abs/2301.00001",
    authors=["Ada Lovelace"],
    published_year=2023,
    summary=(
        "We evaluate large language models on compositional benchmarks. Results show the "
        "models pass most held-out compositions with high accuracy. The experiments indicate "
        "genuine generalisation beyond memorised patterns."
    ),
    authority=0.75,
)
REFUTING = Source(
    source_id="arxiv:2405.00003",
    kind=SourceKind.ARXIV,
    title="Systematic failures on novel compositions",
    url="https://arxiv.org/abs/2405.00003",
    authors=["Grace Hopper"],
    published_year=2024,
    summary=(
        "Our study finds that models fail systematically on novel compositions. The results "
        "show no evidence of understanding beyond surface statistics. Benchmark leakage cannot "
        "explain the failures we measured."
    ),
    authority=0.75,
)
CONTEXT = Source(
    source_id="wikipedia:0123456789abcdef",
    kind=SourceKind.WIKIPEDIA,
    title="Large language model",
    url="https://en.wikipedia.org/wiki/Large_language_model",
    summary=(
        "A large language model is a language model trained with self-supervised learning on "
        "vast amounts of text. Such models are widely used for language generation tasks."
    ),
    authority=0.6,
)


class FakeSourceService:
    """Returns canned sources; optionally fails a kind or returns nothing until broadened."""

    def __init__(
        self,
        arxiv: list[Source] | None = None,
        wikipedia: list[Source] | None = None,
        arxiv_error: str | None = None,
        empty_until_broadened: bool = False,
    ) -> None:
        self.arxiv = arxiv if arxiv is not None else [SUPPORTING, REFUTING]
        self.wikipedia = wikipedia if wikipedia is not None else [CONTEXT]
        self.arxiv_error = arxiv_error
        self.empty_until_broadened = empty_until_broadened
        # (question, limits-or-None) per search_all-equivalent; (kind, question, limit) per search
        self.queries: list[tuple[str, dict | None]] = []
        self.kind_queries: list[tuple[str, str, int | None]] = []

    @property
    def kinds(self) -> list[SourceKind]:
        return [SourceKind.ARXIV, SourceKind.WIKIPEDIA]

    def search(
        self, kind: SourceKind, question: str, max_results: int | None = None
    ) -> SearchOutcome:
        # The pipeline passes an explicit limit only on the broadened retry.
        self.kind_queries.append((kind.value, question, max_results))
        self.queries.append((question, {kind: max_results} if max_results else None))
        broadened = max_results is not None
        if self.empty_until_broadened and not broadened:
            return SearchOutcome(kind=kind)
        if kind is SourceKind.ARXIV:
            if self.arxiv_error:
                return SearchOutcome(kind=kind, error=self.arxiv_error)
            return SearchOutcome(kind=kind, sources=list(self.arxiv))
        return SearchOutcome(kind=kind, sources=list(self.wikipedia))

    def search_all(self, question: str, limits: dict | None = None) -> list[SearchOutcome]:
        return [self.search(k, question, (limits or {}).get(k)) for k in self.kinds]


def make_runner(
    provider: LLMProvider | None = None,
    sources: FakeSourceService | None = None,
    clock: Callable[[], float] | None = None,
    **overrides,  # noqa: ANN003
) -> tuple[PipelineRunner, MemoryRunStore, FakeSourceService, LLMProvider]:
    settings = Settings(app_env="test", llm_provider="mock", **overrides)
    provider = provider or MockProvider()
    sources = sources or FakeSourceService()
    store = MemoryRunStore()
    kwargs = {"clock": clock} if clock else {}
    return PipelineRunner(settings, provider, sources, store, **kwargs), store, sources, provider
