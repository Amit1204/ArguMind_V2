"""The whole LangGraph pipeline with the mock model, canned sources and the memory store."""

from __future__ import annotations

from app.llm.base import LLMError
from app.llm.mock import MockProvider
from app.pipeline.detail import build_graph_response, build_run_detail
from tests.pipeline_fakes import CONTEXT, FakeSourceService, make_runner

QUESTION = "Do large language models understand language?"


def test_full_run_answers_with_verified_citations_and_records_every_stage() -> None:
    runner, store, sources, provider = make_runner()
    run_id = runner.execute(QUESTION, request_id="req-1", client_id="tests")
    detail = build_run_detail(store, run_id)
    assert detail is not None
    assert detail.status == "answered" and detail.answer and detail.confidence is not None
    assert [s.name for s in detail.stages] == [
        "plan", "gather", "extract", "build_graph", "resolve_conflicts", "cluster",
        "consensus", "critic", "answer", "verify",
    ]  # fmt: skip
    assert all(s.status == "ok" for s in detail.stages)
    assert detail.sub_questions[0] == QUESTION and len(detail.sub_questions) == 2
    # three canned sources, each retrieved for both sub-questions, stored once per run
    assert {s.source_id for s in detail.sources} == {
        "arxiv:2301.00001", "arxiv:2405.00003", "wikipedia:0123456789abcdef"
    }  # fmt: skip
    assert len(detail.sources) == 3
    gather = next(s for s in detail.stages if s.name == "gather")
    assert gather.detail["new_sources"] == 6  # (source, sub-question) pairs analysed
    stances = {c.stance.value for c in detail.claims}
    assert "supports" in stances and "refutes" in stances
    assert detail.graph_summary["refutes_edges"] >= 1 and detail.graph_summary["conflicts"] >= 1
    assert detail.resolutions and detail.resolutions[0]["winner"] in {
        "supports",
        "refutes",
        "inconclusive",
    }
    assert detail.consensus and detail.consensus["method"] == "model"
    assert detail.critic and detail.critic["recommendation"] == "pass"
    assert detail.verification["has_valid_citation"] is True
    assert detail.verification["invalid_removed"] == []
    assert detail.usage.llm_calls == len([c for c in provider.calls])
    assert detail.usage.llm_calls >= 1 + 6 + 1 + 1  # plan + extractions + consensus + answer
    assert detail.latency_ms is not None and detail.iteration_count == 1
    assert detail.request_id == "req-1"


def test_graph_endpoint_view_rebuilds_from_persisted_evidence() -> None:
    runner, store, _, _ = make_runner()
    run_id = runner.execute(QUESTION)
    graph = build_graph_response(store, run_id)
    assert graph is not None
    kinds = {n["kind"] for n in graph.nodes}
    assert kinds == {"question", "source", "claim"}
    assert graph.summary["refutes_edges"] >= 1
    edge_types = {e["edge_type"] for e in graph.edges}
    assert "supports" in edge_types and "refutes" in edge_types


def test_invented_citations_are_removed_and_flagged() -> None:
    provider = MockProvider.scripted(
        answer=lambda r: {
            "answer": (
                "Models understand language [arxiv:2301.00001] but see [arxiv:0000.00000] too."
            ),
            "confidence": 0.8,
        }
    )
    runner, store, _, _ = make_runner(provider=provider)
    detail = build_run_detail(store, runner.execute(QUESTION))
    assert detail is not None and detail.status == "answered"
    assert "[arxiv:0000.00000]" not in (detail.answer or "")
    assert detail.verification["invalid_removed"] == ["arxiv:0000.00000"]
    assert any("not retrieved in this run were removed" in c for c in detail.caveats)


def test_uncited_answer_is_flagged_and_confidence_halved() -> None:
    provider = MockProvider.scripted(
        answer=lambda r: {
            "answer": "A confident answer with no citations at all, sadly.",
            "confidence": 0.8,
        }
    )
    runner, store, _, _ = make_runner(provider=provider)
    detail = build_run_detail(store, runner.execute(QUESTION))
    assert detail is not None
    assert detail.confidence == 0.4
    assert any("no verifiable citation" in c for c in detail.caveats)


def test_thin_evidence_triggers_one_broadened_retry() -> None:
    sources = FakeSourceService(empty_until_broadened=True)
    runner, store, sources, _ = make_runner(sources=sources)
    detail = build_run_detail(store, runner.execute(QUESTION))
    assert detail is not None
    gathers = [s for s in detail.stages if s.name == "gather"]
    assert len(gathers) == 2 and gathers[1].attempt == 2 and gathers[1].detail["broadened"] is True
    assert sources.queries[-1][1] is not None  # broadened limits were passed
    assert detail.iteration_count == 2 and detail.status == "answered"
    assert any("gathering was repeated" in c for c in detail.caveats)


def test_no_evidence_at_all_is_inconclusive_without_an_answer_model_call() -> None:
    sources = FakeSourceService(arxiv=[], wikipedia=[])
    runner, store, _, provider = make_runner(sources=sources)
    detail = build_run_detail(store, runner.execute(QUESTION))
    assert detail is not None
    assert detail.status == "inconclusive"
    assert detail.outcome_reason and "need 2" in detail.outcome_reason
    assert detail.answer and detail.answer.startswith(
        "The evidence retrieved for this question is inconclusive"
    )
    assert "answer" not in {c.purpose for c in provider.calls}
    assert detail.critic["recommendation"] == "inconclusive"


def test_context_only_sources_yield_inconclusive_not_a_fabricated_answer() -> None:
    sources = FakeSourceService(arxiv=[], wikipedia=[CONTEXT])
    runner, store, _, _ = make_runner(sources=sources)
    detail = build_run_detail(store, runner.execute(QUESTION))
    assert detail is not None and detail.status == "inconclusive"


def test_source_failures_become_caveats_not_run_failures() -> None:
    sources = FakeSourceService(arxiv_error="arXiv: HTTP 429 after 3 attempts")
    runner, store, _, _ = make_runner(sources=sources)
    detail = build_run_detail(store, runner.execute(QUESTION))
    assert detail is not None
    assert detail.status in {"answered", "inconclusive"}
    assert any("arxiv unavailable" in c for c in detail.caveats)
    gather = next(s for s in detail.stages if s.name == "gather")
    assert gather.detail["errors"] and gather.status == "ok"  # wikipedia still delivered


def test_a_failing_source_is_not_retried_for_later_sub_questions_in_the_same_run() -> None:
    sources = FakeSourceService(arxiv_error="arXiv: HTTP 429 after 3 attempts")
    runner, store, sources, _ = make_runner(sources=sources)
    detail = build_run_detail(store, runner.execute(QUESTION))
    assert detail is not None and len(detail.sub_questions) == 2
    arxiv_queries = [q for q in sources.kind_queries if q[0] == "arxiv"]
    wikipedia_queries = [q for q in sources.kind_queries if q[0] == "wikipedia"]
    # arXiv failed once and was skipped for the second sub-question and the broadened
    # retry (context-only Wikipedia evidence triggers the retry); Wikipedia was asked
    # for both sub-questions and again on the retry.
    assert len(arxiv_queries) == 1
    assert len(wikipedia_queries) == 3
    gathers = [s for s in detail.stages if s.name == "gather"]
    assert gathers[0].detail["skipped_kinds"] == ["arxiv"]
    assert gathers[1].detail["skipped_kinds"] == ["arxiv"]


def test_gather_stops_issuing_searches_once_half_the_budget_is_spent() -> None:
    clock = {"t": 0.0}

    def tick() -> float:
        # Every clock read advances 8 s. Budget 100 s: gather may use 50 s, which is
        # reached after the first sub-question's searches; the whole budget is not spent.
        clock["t"] += 8.0
        return clock["t"]

    runner, store, sources, _ = make_runner(clock=tick, run_timeout_seconds=100)
    detail = build_run_detail(store, runner.execute(QUESTION))
    assert detail is not None
    gather = next(s for s in detail.stages if s.name == "gather")
    assert gather.detail["stopped_for_time"] is True
    assert gather.detail["new_sources"] >= 1  # the first search completed before the cut-off
    assert any("Gathering stopped early" in c for c in detail.caveats)
    # the run still completed every stage and ended in a defined outcome
    assert [s.name for s in detail.stages][-2:] == ["answer", "verify"]
    assert detail.status in {"answered", "inconclusive"}


def test_model_failures_degrade_to_deterministic_fallbacks() -> None:
    provider = MockProvider()
    provider.fail_with("plan", LLMError("quota"))
    provider.fail_with("consensus", LLMError("quota"))
    provider.fail_with("answer", LLMError("quota"))
    runner, store, _, _ = make_runner(provider=provider)
    detail = build_run_detail(store, runner.execute(QUESTION))
    assert detail is not None and detail.status == "answered"
    assert detail.sub_questions == [QUESTION]
    by_name = {s.name: s for s in detail.stages}
    assert by_name["plan"].status == "failed" and by_name["consensus"].status == "failed"
    assert (
        by_name["answer"].status == "failed"
        and by_name["answer"].detail["method"] == "fallback_template"
    )
    assert "[arxiv:2301.00001]" in (detail.answer or "")
    assert len([c for c in detail.caveats if "failed" in c]) >= 3


def test_time_budget_exhaustion_skips_work_and_still_finishes() -> None:
    clock = {"t": 0.0}

    def tick() -> float:
        clock["t"] += 40.0  # every clock read advances 40 s; the budget is 60 s
        return clock["t"]

    runner, store, _, _ = make_runner(clock=tick, run_timeout_seconds=60)
    detail = build_run_detail(store, runner.execute(QUESTION))
    assert detail is not None
    assert detail.status in {"answered", "inconclusive"}
    assert any("time budget" in c for c in detail.caveats)


def test_unexpected_exception_marks_the_run_failed() -> None:
    class Broken(FakeSourceService):
        def search(self, kind, question, max_results=None):  # noqa: ANN001, ANN201
            raise RuntimeError("boom")

    runner, store, _, _ = make_runner(sources=Broken())
    detail = build_run_detail(store, runner.execute(QUESTION))
    assert detail is not None
    assert detail.status == "failed" and detail.error and "RuntimeError: boom" in detail.error
    assert [s.name for s in detail.stages] == ["plan", "gather"]
    assert detail.stages[-1].status == "failed"
