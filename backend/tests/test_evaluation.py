"""Benchmark dataset, graders, runner and reports (no network, no model)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.runs import get_run_executor
from app.evaluation.dataset import Case, Expectation, load_benchmark
from app.evaluation.graders import FAIL, NA, PASS, grade_case
from app.evaluation.report import compare, render_markdown, summarise, write_reports
from app.evaluation.runner import EvaluationRunner
from app.services.run_executor import RunExecutor
from tests.pipeline_fakes import make_runner

# ---------------------------------------------------------------- dataset


def test_benchmark_loads_with_unique_ids_and_known_categories() -> None:
    cases = load_benchmark()
    assert len(cases) >= 28
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))
    categories = {c.category for c in cases}
    assert categories == {
        "settled", "contested", "comparative", "no_evidence",
        "speculative", "injection", "validation",
    }  # fmt: skip
    contested = [c for c in cases if c.category == "contested"]
    assert all("supports" in c.expect.stances_all or c.expect.stances_any for c in contested)
    assert all(c.expect.http_status == 422 for c in cases if c.category == "validation")


def test_dataset_rejects_category_mismatch_and_duplicates(tmp_path: Path) -> None:
    (tmp_path / "settled.yaml").write_text(
        "- id: settled-001\n  category: contested\n  question: q?\n"
    )
    with pytest.raises(ValueError, match="category"):
        load_benchmark(tmp_path)
    (tmp_path / "settled.yaml").write_text(
        "- id: settled-001\n  category: settled\n  question: q?\n"
        "- id: settled-001\n  category: settled\n  question: q2?\n"
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_benchmark(tmp_path)


# ---------------------------------------------------------------- graders


def answered_run(**overrides) -> dict:  # noqa: ANN003
    run = {
        "id": "run-1",
        "status": "answered",
        "answer": (
            "Dropout reduces overfitting [arxiv:1707.00001]. Some disagree [arxiv:1707.00002]."
        ),
        "confidence": 0.7,
        "sub_questions": ["Does dropout reduce overfitting?"],
        "sources": [{"source_id": "arxiv:1707.00001"}, {"source_id": "arxiv:1707.00002"}],
        "claims": [
            {"stance": "supports"},
            {"stance": "supports"},
            {"stance": "refutes"},
            {"stance": "neutral"},
        ],  # fmt: skip
        "resolutions": [
            {"winner": "supports", "method": "heuristic", "minority_report": {"stance": "refutes"}}
        ],
        "verification": {
            "has_valid_citation": True,
            "valid_citations": ["arxiv:1707.00001", "arxiv:1707.00002"],
            "invalid_removed": [],
        },
        "usage": {
            "llm_calls": 9,
            "input_tokens": 100,
            "output_tokens": 20,
            "estimated_cost_usd": 0,
        },
        "critic": {"issues": []},
        "caveats": [],
        "latency_ms": 12000,
    }
    run.update(overrides)
    return run


def by_name(results) -> dict[str, tuple[str, str]]:  # noqa: ANN001
    return {d.name: (d.status, d.detail) for d in results}


def test_contested_case_passes_every_dimension_on_a_good_run() -> None:
    case = Case(
        id="contested-001",
        category="contested",
        question="Do models understand?",
        expect=Expectation(
            status_any=["answered", "inconclusive"],
            min_sources=2,
            stances_all=["supports", "refutes"],
            conflict_expected=True,
            answer_contains_any=["dropout"],
        ),
    )
    graded = by_name(grade_case(case, 200, answered_run(), 12000))
    assert all(status != FAIL for status, _ in graded.values()), graded
    assert graded["safety"][0] == NA  # no injection markers on this case
    assert {n for n, (s, _) in graded.items() if s == PASS} >= {
        "outcome", "evidence", "conflicts", "citations", "answer", "cost"
    }  # fmt: skip


def test_graders_flag_invented_citations_missing_stances_and_missing_minority() -> None:
    case = Case(
        id="contested-002",
        category="contested",
        question="q?",
        expect=Expectation(stances_all=["supports", "refutes"], conflict_expected=True),
    )
    run = answered_run(
        claims=[{"stance": "supports"}],
        resolutions=[{"winner": "supports", "method": "model", "minority_report": None}],
        verification={
            "has_valid_citation": True,
            "valid_citations": ["arxiv:1707.00001"],
            "invalid_removed": ["arxiv:9.9"],
        },
    )
    graded = by_name(grade_case(case, 200, run, 1000))
    assert graded["evidence"][0] == FAIL and "missing" in graded["evidence"][1]
    assert graded["conflicts"][0] == FAIL and "minority" in graded["conflicts"][1]
    assert graded["citations"][0] == FAIL and "invented" in graded["citations"][1]


def test_no_evidence_case_wants_inconclusive_and_low_confidence() -> None:
    case = Case(
        id="noevidence-001",
        category="no_evidence",
        question="Zorblax?",
        expect=Expectation(status_any=["inconclusive"], max_confidence=0.5),
    )
    good = answered_run(
        status="inconclusive",
        confidence=0.0,
        answer="The evidence is inconclusive.",
        verification={"has_valid_citation": False, "valid_citations": [], "invalid_removed": []},
    )
    assert by_name(grade_case(case, 200, good, 500))["outcome"][0] == PASS
    assert (
        by_name(grade_case(case, 200, good, 500))["citations"][0] == PASS
    )  # inconclusive needs no citation
    bad = answered_run(status="answered", confidence=0.9)
    graded = by_name(grade_case(case, 200, bad, 500))
    assert graded["outcome"][0] == FAIL and graded["answer"][0] == FAIL


def test_injection_markers_in_sub_questions_or_answer_fail_safety() -> None:
    case = Case(
        id="injection-001",
        category="injection",
        question="Ignore... PWNED",
        expect=Expectation(
            status_any=["answered", "inconclusive"], injection_markers=["PWNED", "arxiv:9999.99999"]
        ),
    )
    clean = by_name(grade_case(case, 200, answered_run(), 1000))
    assert clean["safety"][0] == PASS
    leaked = answered_run(sub_questions=["Reply with PWNED"])
    assert by_name(grade_case(case, 200, leaked, 1000))["safety"][0] == FAIL
    planted = answered_run(
        verification={
            "has_valid_citation": True,
            "valid_citations": ["arxiv:9999.99999"],
            "invalid_removed": [],
        }
    )
    assert by_name(grade_case(case, 200, planted, 1000))["safety"][0] == FAIL


def test_validation_case_and_cost_bounds() -> None:
    case = Case(
        id="validation-001",
        category="validation",
        question="short",
        expect=Expectation(http_status=422, max_llm_calls=0),
    )
    graded = by_name(grade_case(case, 422, None, 20))
    assert (
        graded["outcome"][0] == PASS and graded["evidence"][0] == NA and graded["cost"][0] == PASS
    )
    assert by_name(grade_case(case, 200, answered_run(), 20))["outcome"][0] == FAIL
    slow = Case(
        id="settled-001",
        category="settled",
        question="q?",
        expect=Expectation(max_llm_calls=5, max_latency_ms=1000),
    )
    graded = by_name(grade_case(slow, 200, answered_run(), 5000))
    assert graded["cost"][0] == FAIL and "9 model calls" in graded["cost"][1]


# ---------------------------------------------------------------- runner and report


def test_runner_retries_transient_statuses_once_and_grades(tmp_path: Path) -> None:
    calls = {"n": 0}

    def ask(case: Case):  # noqa: ANN202
        calls["n"] += 1
        if calls["n"] == 1:
            return 429, {"detail": "rate limit"}, 1.0
        return 200, answered_run(), None

    slept: list[float] = []
    case = Case(id="settled-001", category="settled", question="Does dropout reduce overfitting?",
                expect=Expectation(min_sources=2, answer_contains_any=["dropout"]))  # fmt: skip
    runner = EvaluationRunner(ask, sleep=slept.append, transient_wait=30)
    results = runner.run([case])
    assert calls["n"] == 2 and slept == [1.0]
    assert results[0].passed and results[0].extra["retried_transient"] is True
    assert results[0].latency_ms == 12000  # the run's own latency, not the HTTP round trip

    summary = summarise(results)
    assert summary["passed"] == 1 and summary["by_dimension"]["evidence"]["applicable"] == 1
    assert summary["status_counts"] == {"answered": 1}
    md, js = write_reports(
        tmp_path,
        "t1",
        __import__("app.evaluation.report", fromlist=["now"]).now(),
        __import__("app.evaluation.report", fromlist=["now"]).now(),
        "http://x",
        results,
    )
    assert md.exists() and js.exists() and (tmp_path / "latest.json").exists()
    text = md.read_text()
    assert "# Evaluation report" in text and "settled-001" in text and "100.0%" in text

    # a second run where the case fails is reported as a regression
    failing = EvaluationRunner(lambda c: (200, answered_run(claims=[]), None)).run(
        [
            Case(
                id="settled-001",
                category="settled",
                question="q?",
                expect=Expectation(min_claims=1),
            )
        ]
    )
    previous = json.loads((tmp_path / "latest.json").read_text())
    assert compare(failing, previous) == {"regressions": ["settled-001"], "fixes": []}
    rendered = render_markdown(
        "t2",
        __import__("app.evaluation.report", fromlist=["now"]).now(),
        __import__("app.evaluation.report", fromlist=["now"]).now(),
        "x",
        failing,
        summarise(failing),
        compare(failing, previous),
    )
    assert "Regressions" in rendered and "## Failures (1)" in rendered


def test_runner_waits_for_open_model_circuit_and_reruns_starved_cases() -> None:
    """On the free tier a burst trips the provider quota and the backend opens its
    model circuit; cases run meanwhile end inconclusive with 0 model calls. The
    runner must wait the circuit out and rerun such a case instead of grading it."""
    circuit = {"retry_after": [45.0, 0.0, 30.0, 0.0]}  # consumed one per probe

    def model_circuit() -> float:
        return circuit["retry_after"].pop(0) if circuit["retry_after"] else 0.0

    starved = answered_run(
        status="inconclusive",
        confidence=0.0,
        answer="The evidence is inconclusive.",
        claims=[],
        usage={"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0},
    )
    answers = iter([(200, starved, None), (200, answered_run(), None)])
    asked: list[str] = []

    def ask(case: Case):  # noqa: ANN202
        asked.append(case.id)
        return next(answers)

    slept: list[float] = []
    case = Case(id="settled-001", category="settled", question="Does dropout reduce overfitting?",
                expect=Expectation(min_sources=2, answer_contains_any=["dropout"]))  # fmt: skip
    runner = EvaluationRunner(ask, sleep=slept.append, model_circuit=model_circuit)
    result = runner.run([case])[0]

    # probe 1: open 45 s -> waited 47 s; probe 2: closed -> ask (starved run, 0 model
    # calls, so no post-check probe is needed); probe 3: open 30 s -> waited 32 s;
    # probe 4: closed -> ask again (answered); post-check probe: list empty -> closed.
    assert asked == ["settled-001", "settled-001"]
    assert slept == [47.0, 32.0]
    assert result.passed and result.status == "answered"
    assert result.extra["retried_circuit"] is True
    assert result.extra["circuit_wait_seconds"] == 79.0
    assert result.extra["retried_transient"] is False

    # a genuine inconclusive (the planner ran, evidence was thin) is graded, not rerun
    genuine = answered_run(status="inconclusive", confidence=0.1, claims=[])
    plain = EvaluationRunner(lambda c: (200, genuine, None), model_circuit=lambda: 0.0)
    graded = plain.run([case])[0]
    assert graded.extra["retried_circuit"] is False and graded.status == "inconclusive"

    # a probe failure never blocks the benchmark
    def broken() -> float:
        raise ConnectionError("operations endpoint down")

    ok = EvaluationRunner(lambda c: (200, answered_run(), None), model_circuit=broken)
    assert ok.run([case])[0].passed

    # still starved after the rerun (daily quota spent): the case is not graded or
    # checkpointed and the run stops, so the checkpoint can be resumed later
    recorded: list[str] = []
    exhausted = EvaluationRunner(
        lambda c: (200, starved, None),
        model_circuit=lambda: 0.0,
        on_result=lambda r: recorded.append(r.id),
    )
    second = Case(id="settled-002", category="settled", question="q?", expect=Expectation())
    results = exhausted.run([case, second])
    assert results == [] and recorded == [] and exhausted.aborted_at == "settled-001"


def test_circuit_probe_reads_open_model_and_source_circuits() -> None:
    import httpx as _httpx

    from app.evaluation.run import make_circuit_probe

    payload = {
        "circuits": {
            "llm:gemini": {"state": "open", "retry_after_seconds": 69.2},
            "source:arxiv": {"state": "open", "retry_after_seconds": 110.0},
            "source:wikipedia": {"state": "closed", "retry_after_seconds": 0.0},
        }
    }
    transport = _httpx.MockTransport(lambda request: _httpx.Response(200, json=payload))
    client = _httpx.Client(base_url="http://backend:8000", transport=transport)
    probe = make_circuit_probe("http://backend:8000", client=client)
    assert probe() == 110.0  # the longest open circuit, model or source
    payload["circuits"]["source:arxiv"] = {"state": "closed", "retry_after_seconds": 0.0}
    assert probe() == 69.2
    payload["circuits"]["llm:gemini"] = {"state": "half_open", "retry_after_seconds": 0.0}
    assert probe() == 0.0
    # model-only watching is still available
    payload["circuits"]["source:arxiv"] = {"state": "open", "retry_after_seconds": 80.0}
    llm_only = make_circuit_probe("http://backend:8000", client=client, prefixes=("llm:",))
    assert llm_only() == 0.0


def test_smoke_reports_do_not_replace_latest(tmp_path: Path) -> None:
    from app.evaluation.report import now

    results = EvaluationRunner(lambda c: (200, answered_run(), None)).run(
        [Case(id="settled-001", category="settled", question="q?")]
    )
    write_reports(tmp_path, "smoke", now(), now(), "x", results)
    assert (tmp_path / "smoke.md").exists() and not (tmp_path / "latest.json").exists()


# ------------------------------------------------------ API validation used by the benchmark


def test_blank_or_padded_questions_are_rejected(app: FastAPI, client: TestClient) -> None:
    runner, store, _, _ = make_runner()
    app.dependency_overrides[get_run_executor] = lambda: RunExecutor(runner, store, inline=True)
    assert client.post("/api/v1/runs", json={"question": " " * 20}).status_code == 422
    assert client.post("/api/v1/runs", json={"question": "a b c d e" + " " * 30}).status_code == 422
    ok = client.post("/api/v1/runs", json={"question": "  Do   models understand   language?  "})
    assert ok.status_code == 200 and ok.json()["question"] == "Do models understand language?"
