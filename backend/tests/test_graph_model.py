from __future__ import annotations

import pytest

from app.evidence.models import Stance
from app.graph.builder import build_graph
from app.graph.model import CitationGraph, EdgeType, GraphError, NodeKind
from tests.graph_fixtures import CLAIMS, QUESTION, SOURCES, arxiv, claim


def test_graph_contains_refutes_edges_by_construction() -> None:
    graph = build_graph([QUESTION], SOURCES, CLAIMS)
    summary = graph.summary()
    assert summary["questions"] == 1 and summary["sources"] == 4 and summary["claims"] == 5
    assert summary["supports_edges"] == 2
    assert summary["refutes_edges"] == 1  # the defect the rebuild exists to fix
    assert summary["extends_edges"] == 0 and summary["supersedes_edges"] == 0


def test_neutral_claims_are_context_without_stance_edges() -> None:
    graph = build_graph([QUESTION], SOURCES, CLAIMS)
    assert graph.g.out_degree("wikipedia:Large_language_model#1") == 0
    assert graph.g.nodes["wikipedia:Large_language_model#1"]["stance"] == "neutral"


def test_conflict_detected_when_both_stances_come_from_different_sources() -> None:
    graph = build_graph([QUESTION], SOURCES, CLAIMS)
    conflicts = graph.conflicts()
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.question_index == 0 and c.question == QUESTION
    assert c.supporting == ["arxiv:2001.00002#1", "arxiv:2301.00001#1"]
    assert c.refuting == ["arxiv:2405.00003#1"]
    assert c.supporting_sources == ["arxiv:2001.00002", "arxiv:2301.00001"]
    assert graph.summary()["conflicts"] == 1


def test_no_conflict_with_one_sided_evidence() -> None:
    one_sided = [c for c in CLAIMS if c.stance is not Stance.REFUTES]
    assert build_graph([QUESTION], SOURCES, one_sided).conflicts() == []


def test_a_single_source_contradicting_itself_is_not_a_conflict() -> None:
    source = arxiv("2401.00009", 2024)
    claims = [
        claim(source.source_id, 1, Stance.SUPPORTS, "Yes in one setting."),
        claim(source.source_id, 2, Stance.REFUTES, "No in another setting."),
    ]
    assert build_graph([QUESTION], [source], claims).conflicts() == []


def test_conflicts_are_per_sub_question() -> None:
    questions = [QUESTION, "Are benchmarks reliable?"]
    claims = CLAIMS + [
        claim(
            "arxiv:2301.00001",
            2,
            Stance.SUPPORTS,
            "Benchmarks correlate with human judgement.",
            q=1,
        ),
        claim("arxiv:2405.00003", 3, Stance.REFUTES, "Benchmarks leak into training data.", q=1),
    ]
    conflicts = build_graph(questions, SOURCES, claims).conflicts()
    assert [c.question_index for c in conflicts] == [0, 1]


def test_builder_skips_claims_whose_source_or_question_is_missing() -> None:
    orphan = claim("arxiv:9999.99999", 1, Stance.SUPPORTS, "Orphan claim with no source.")
    wrong_q = claim("arxiv:2301.00001", 9, Stance.SUPPORTS, "Refers to a missing question.", q=7)
    graph = build_graph([QUESTION], SOURCES, [*CLAIMS, orphan, wrong_q])
    assert graph.summary()["claims"] == 5


def test_link_validates_endpoints_types_and_weights() -> None:
    graph = build_graph([QUESTION], SOURCES, CLAIMS)
    with pytest.raises(GraphError):
        graph.link("arxiv:2301.00001#1", "nope", EdgeType.EXTENDS)
    with pytest.raises(GraphError):
        graph.link("arxiv:2301.00001#1", "arxiv:2301.00001#1", EdgeType.EXTENDS)
    with pytest.raises(GraphError):
        graph.link("arxiv:2301.00001#1", "arxiv:2405.00003#1", EdgeType.EXTENDS, weight=1.5)
    edge = graph.link(
        "arxiv:2301.00001#1", "arxiv:2405.00003#1", EdgeType.EXTENDS, 0.7, "same topic"
    )
    assert edge.edge_type is EdgeType.EXTENDS and graph.summary()["extends_edges"] == 1


def test_serialisation_round_trip_preserves_nodes_edges_and_conflicts() -> None:
    graph = build_graph([QUESTION], SOURCES, CLAIMS)
    graph.link("arxiv:2301.00001#1", "arxiv:2405.00003#1", EdgeType.EXTENDS, 0.7, "same topic")
    data = graph.to_dict()
    restored = CitationGraph.from_dict(data)
    assert restored.summary() == graph.summary()
    assert restored.to_dict() == data
    assert [c.model_dump() for c in restored.conflicts()] == [
        c.model_dump() for c in graph.conflicts()
    ]
    assert {e.edge_type for e in restored.edges()} == {
        EdgeType.SUPPORTS,
        EdgeType.REFUTES,
        EdgeType.EXTENDS,
    }


def test_from_dict_rejects_malformed_nodes() -> None:
    with pytest.raises(GraphError):
        CitationGraph.from_dict({"nodes": [{"kind": "claim"}], "edges": []})


def test_edges_map_to_database_rows() -> None:
    graph = build_graph([QUESTION], SOURCES, CLAIMS)
    rows = [e.model_dump(mode="json") for e in graph.edges()]
    assert {"source_node", "target_node", "edge_type", "weight", "explanation"} <= set(rows[0])
    assert all(r["edge_type"] in {"supports", "refutes", "extends", "supersedes"} for r in rows)
    assert all(0 <= r["weight"] <= 1 for r in rows)
    assert len(graph.nodes_of(NodeKind.CLAIM)) == 5
