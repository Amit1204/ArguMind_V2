from __future__ import annotations

import pytest

from app.evidence.models import Stance
from app.graph.builder import build_graph
from app.llm.base import LLMError, LLMRequest, UsageLedger
from app.llm.mock import MockProvider
from app.reasoning.conflicts import EVIDENCE_WEIGHT, ConflictResolver, recency_factor
from tests.graph_fixtures import CLAIMS, QUESTION, SOURCES, arxiv, claim

TODAY = 2026


def resolve(provider=None, claims=CLAIMS, sources=SOURCES, **kwargs):  # noqa: ANN001, ANN003
    graph = build_graph([QUESTION], sources, claims)
    resolver = ConflictResolver(provider, today_year=TODAY, **kwargs)
    return graph, resolver.resolve(graph, sources, claims)


# ---------------------------------------------------------------- scoring


def test_recency_factor_curve() -> None:
    assert recency_factor(2026, TODAY) == 1.0
    assert recency_factor(2024, TODAY) == 1.0
    assert recency_factor(2023, TODAY) == 0.95
    assert recency_factor(2010, TODAY) == 0.5  # floor
    assert recency_factor(None, TODAY) == 0.8


def test_evidence_weights_prefer_empirical() -> None:
    assert EVIDENCE_WEIGHT["empirical"] > EVIDENCE_WEIGHT["review"] > EVIDENCE_WEIGHT["theoretical"]
    assert EVIDENCE_WEIGHT["opinion"] < EVIDENCE_WEIGHT["other"]


def test_side_scores_are_recorded_and_explainable() -> None:
    _, report = resolve()
    r = report.resolutions[0]
    # supports: 2023 paper 0.8*0.75*0.95*1.0 = 0.570 + 2020 review 0.6*0.75*0.80*0.9 = 0.324
    # refutes:  2024 paper 0.9*0.75*1.0*1.0 = 0.675
    assert r.support.score == pytest.approx(0.894, abs=1e-3)
    assert r.refute.score == pytest.approx(0.675, abs=1e-3)
    assert r.support.claims == 2 and r.support.sources == 2 and r.support.newest_year == 2023
    assert r.refute.newest_year == 2024
    assert r.margin == pytest.approx((0.894 - 0.675) / (0.894 + 0.675), abs=1e-3)


# ---------------------------------------------------------------- heuristic verdicts


def test_clear_margin_is_decided_without_the_model() -> None:
    strong = [
        claim("arxiv:2301.00001", 1, Stance.SUPPORTS, "Strong empirical support.", 0.95),
        claim("arxiv:2405.00003", 1, Stance.REFUTES, "A weak opinion against.", 0.3, "opinion"),
    ]
    provider = MockProvider()
    _, report = resolve(provider, strong)
    r = report.resolutions[0]
    assert r.winner == "supports" and r.method == "heuristic"
    assert provider.calls == []  # decisive margin: no model call
    assert r.minority_report is not None and r.minority_report.stance == "refutes"
    assert r.minority_report.claim_ids == ["arxiv:2405.00003#1"]
    assert "weak opinion" in r.minority_report.summary


def test_balanced_evidence_without_a_provider_is_inconclusive() -> None:
    balanced = [
        claim("arxiv:2301.00001", 1, Stance.SUPPORTS, "Yes, models understand.", 0.8),
        claim("arxiv:2405.00003", 1, Stance.REFUTES, "No, models do not understand.", 0.8),
    ]
    sources = [arxiv("2301.00001", 2024), arxiv("2405.00003", 2024)]
    _, report = resolve(None, balanced, sources)
    r = report.resolutions[0]
    assert r.winner == "inconclusive" and r.method == "heuristic" and r.minority_report is None
    assert report.summary() == {
        "conflicts": 1,
        "resolved": 0,
        "inconclusive": 1,
        "model_arbitrations": 0,
    }


def test_refutes_can_win_on_recency_and_evidence_type() -> None:
    claims = [
        claim(
            "arxiv:2001.00002",
            1,
            Stance.SUPPORTS,
            "Old theoretical argument for.",
            0.8,
            "theoretical",
        ),
        claim("arxiv:2405.00003", 1, Stance.REFUTES, "Recent empirical result against.", 0.8),
    ]
    _, report = resolve(None, claims)
    assert report.resolutions[0].winner == "refutes"


# ---------------------------------------------------------------- model arbitration


def test_close_margin_asks_the_model_and_records_its_reasoning() -> None:
    provider = MockProvider.scripted(
        resolve_conflict=lambda r: {
            "winner": "refutes",
            "reasoning": (
                "arxiv:2405.00003#1 reports a direct empirical failure that the older "
                "results do not address."
            ),
            "confidence": 0.7,
        }
    )
    ledger = UsageLedger()
    graph = build_graph([QUESTION], SOURCES, CLAIMS)
    report = ConflictResolver(provider, ledger, today_year=TODAY).resolve(graph, SOURCES, CLAIMS)
    r = report.resolutions[0]
    assert r.method == "model" and r.winner == "refutes" and r.confidence == 0.7
    assert "arxiv:2405.00003#1" in r.reasoning
    assert ledger.calls == 1
    request: LLMRequest = provider.calls[0]
    assert request.purpose == "resolve_conflict" and request.tier.value == "standard"
    assert "HEURISTIC SCORES:" in request.prompt and "untrusted data" in request.system
    # the losing side is the supporting one now
    assert r.minority_report is not None and r.minority_report.stance == "supports"
    assert r.minority_report.source_ids == ["arxiv:2001.00002", "arxiv:2301.00001"]


def test_model_failure_falls_back_to_heuristics_and_is_labelled() -> None:
    provider = MockProvider()
    provider.fail_with("resolve_conflict", LLMError("quota"))
    _, report = resolve(provider)
    r = report.resolutions[0]
    assert r.method == "heuristic_after_model_error"
    assert r.winner in {"supports", "refutes", "inconclusive"}


def test_invalid_model_output_is_treated_as_a_model_error() -> None:
    provider = MockProvider.scripted(resolve_conflict=lambda r: {"winner": "maybe"})
    _, report = resolve(provider)
    assert report.resolutions[0].method == "heuristic_after_model_error"


# ---------------------------------------------------------------- supersedes edges


def test_newer_winning_claim_supersedes_much_older_losing_claim() -> None:
    claims = [
        claim("arxiv:2001.00002", 1, Stance.SUPPORTS, "Old claim for.", 0.7, "review"),
        claim("arxiv:2405.00003", 1, Stance.REFUTES, "New strong empirical claim against.", 0.95),
    ]
    graph, report = resolve(None, claims)
    r = report.resolutions[0]
    assert r.winner == "refutes"
    assert r.superseded == [("arxiv:2405.00003#1", "arxiv:2001.00002#1")]
    assert graph.summary()["supersedes_edges"] == 1
    edge = next(e for e in graph.edges() if e.edge_type.value == "supersedes")
    assert (
        edge.source_node == "arxiv:2405.00003#1"
        and "2024 evidence supersedes 2020" in edge.explanation
    )


def test_no_supersedes_edge_when_years_are_close_or_inconclusive() -> None:
    claims = [
        claim("arxiv:2301.00001", 1, Stance.SUPPORTS, "Strong evidence for.", 0.95),
        claim("arxiv:2405.00003", 1, Stance.REFUTES, "Against, weakly.", 0.3, "opinion"),
    ]
    graph, report = resolve(None, claims)
    assert report.resolutions[0].winner == "supports"
    assert graph.summary()["supersedes_edges"] == 0  # 2023 vs 2024: no two-year gap


def test_resolver_rejects_nonsense_margins() -> None:
    with pytest.raises(ValueError):
        ConflictResolver(decisive_margin=0.1, inconclusive_margin=0.5)
