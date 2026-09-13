"""Assemble the API view of a run from what the store holds."""

from __future__ import annotations

from app.graph.builder import build_graph
from app.graph.model import CitationGraph, EdgeType
from app.pipeline.store import RunStore
from app.schemas.runs import GraphResponse, RunDetail, RunSummary


def _last_detail(stages, name: str) -> dict:  # noqa: ANN001
    for stage in reversed(stages):
        if stage.name == name:
            return stage.detail
    return {}


def build_run_detail(store: RunStore, run_id: str) -> RunDetail | None:
    row = store.get_run(run_id)
    if row is None:
        return None
    stages = store.get_stages(run_id)
    plan = _last_detail(stages, "plan")
    verify = _last_detail(stages, "verify")
    resolve = _last_detail(stages, "resolve_conflicts")
    cluster = _last_detail(stages, "cluster")
    consensus = _last_detail(stages, "consensus")
    critic = _last_detail(stages, "critic")
    graph_summary = _last_detail(stages, "build_graph")
    return RunDetail(
        id=row.id,
        request_id=row.request_id,
        question=row.question,
        status=row.status,  # type: ignore[arg-type]
        outcome_reason=row.outcome_reason,
        answer=row.answer,
        confidence=row.confidence,
        iteration_count=row.iteration_count,
        usage=row.usage,
        latency_ms=row.latency_ms,
        error=row.error,
        started_at=row.started_at,
        finished_at=row.finished_at,
        sub_questions=list(plan.get("sub_questions", [])),
        sources=store.get_sources(run_id),
        claims=store.get_claims(run_id),
        resolutions=list(resolve.get("resolutions", [])),
        clusters=list(cluster.get("cluster_details", [])),
        consensus=_consensus_view(consensus) if consensus else None,
        critic=_without(critic, {"detail"}) if critic else None,
        verification=_without(verify, {"caveats"}) if verify else None,
        caveats=list(verify.get("caveats", [])),
        stages=stages,
        graph_summary={k: v for k, v in graph_summary.items() if k != "resolutions"},
    )


def _consensus_view(detail: dict) -> dict:
    """The full consensus (overall text, agreements, gaps, ...) plus how it was produced."""
    consensus = dict(detail.get("consensus") or {})
    consensus.setdefault("strength", detail.get("strength"))
    consensus.setdefault("confidence", detail.get("confidence"))
    consensus["method"] = detail.get("method")
    return consensus


def _without(d: dict, keys: set[str]) -> dict:
    return {k: v for k, v in d.items() if k not in keys}


def run_summary(row) -> RunSummary:  # noqa: ANN001 - RunRow
    return RunSummary(
        id=row.id,
        question=row.question,
        status=row.status,
        confidence=row.confidence,
        latency_ms=row.latency_ms,
        llm_calls=row.usage.llm_calls,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def build_graph_response(store: RunStore, run_id: str) -> GraphResponse | None:
    row = store.get_run(run_id)
    if row is None:
        return None
    stages = store.get_stages(run_id)
    sub_questions = list(_last_detail(stages, "plan").get("sub_questions", [])) or [row.question]
    graph: CitationGraph = build_graph(
        sub_questions, store.get_sources(run_id), store.get_claims(run_id)
    )
    for edge in store.get_edges(run_id):
        if edge.edge_type in (EdgeType.EXTENDS, EdgeType.SUPERSEDES):
            try:
                graph.link(
                    edge.source_node,
                    edge.target_node,
                    edge.edge_type,
                    edge.weight,
                    edge.explanation,
                )
            except ValueError:
                continue
    data = graph.to_dict()
    return GraphResponse(
        run_id=run_id, nodes=data["nodes"], edges=data["edges"], summary=graph.summary()
    )
