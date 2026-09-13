from __future__ import annotations

from app.evidence.models import Stance
from app.graph.builder import build_graph
from app.reasoning.clustering import (
    add_extends_edges,
    cluster_claims,
    cosine,
    tfidf_vectors,
    tokenize,
)
from tests.graph_fixtures import QUESTION, SOURCES, claim


def test_tokenize_drops_stopwords_and_short_words() -> None:
    assert tokenize("The models do NOT fail on novel compositions!") == [
        "models",
        "fail",
        "novel",
        "compositions",
    ]


def test_similar_claims_cluster_and_different_ones_do_not() -> None:
    claims = [
        claim(
            "arxiv:2301.00001",
            1,
            Stance.SUPPORTS,
            "Models pass compositional generalisation benchmarks.",
        ),
        claim(
            "arxiv:2405.00003",
            1,
            Stance.REFUTES,
            "Models fail compositional generalisation benchmarks.",
        ),
        claim(
            "arxiv:2001.00002",
            1,
            Stance.NEUTRAL,
            "Training corpora contain billions of web tokens.",
        ),
    ]
    clusters = cluster_claims(claims, threshold=0.35)
    assert len(clusters) == 2
    first = clusters[0]
    assert first.claim_ids == ["arxiv:2301.00001#1", "arxiv:2405.00003#1"]
    assert first.contested and first.supports == 1 and first.refutes == 1
    assert "compositional" in first.label
    assert clusters[1].claim_ids == ["arxiv:2001.00002#1"] and not clusters[1].contested


def test_clustering_is_deterministic_and_handles_empty_input() -> None:
    claims = [
        claim("arxiv:2301.00001", 1, Stance.SUPPORTS, "Alpha beta gamma results."),
        claim("arxiv:2405.00003", 1, Stance.REFUTES, "Alpha beta gamma failures."),
    ]
    assert [c.model_dump() for c in cluster_claims(claims)] == [
        c.model_dump() for c in cluster_claims(claims)
    ]
    assert cluster_claims([]) == []


def test_extends_edges_link_related_claims_from_different_sources() -> None:
    claims = [
        claim(
            "arxiv:2301.00001",
            1,
            Stance.SUPPORTS,
            "Models pass compositional generalisation benchmarks.",
        ),
        claim(
            "arxiv:2405.00003",
            1,
            Stance.REFUTES,
            "Models fail compositional generalisation benchmarks.",
        ),
        claim(
            "arxiv:2301.00001",
            2,
            Stance.SUPPORTS,
            "Compositional generalisation benchmarks are passed.",
        ),
    ]
    graph = build_graph([QUESTION], SOURCES, claims)
    clusters = cluster_claims(claims, threshold=0.3)
    added = add_extends_edges(graph, clusters, claims)
    assert added == 1  # same-source pairs are not linked
    edge = next(e for e in graph.edges() if e.edge_type.value == "extends")
    assert edge.source_node == "arxiv:2301.00001#1" and edge.target_node == "arxiv:2405.00003#1"
    assert 0 < edge.weight <= 1


def test_cosine_of_identical_vectors_is_one() -> None:
    a, b = tfidf_vectors(["alpha beta gamma", "alpha beta gamma"])
    assert abs(cosine(a, b) - 1.0) < 1e-9
