"""Deterministic topic clustering of claims with TF-IDF cosine similarity.

No model call and no external dependency: the aim is to group claims that talk
about the same thing so (a) the graph gets `extends` edges between related
claims from different sources and (b) clusters where stances disagree are
surfaced to the consensus stage as contested topics.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from app.evidence.models import Claim
from app.graph.model import CitationGraph, EdgeType
from app.pipeline.schemas import Cluster

_WORD = re.compile(r"[a-z][a-z0-9\-]{2,}")
_STOPWORDS = frozenset(
    "the and for that this with from are was were been being have has had not but can "
    "could would should may might will their there these those which what when where "
    "who whom whose how why into onto than then them they its also such more most very "
    "our out over under between about after before while does did doing each other some "
    "any all both few many much own same too only".split()
)


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]


def tfidf_vectors(texts: list[str]) -> list[dict[str, float]]:
    tokenized = [tokenize(t) for t in texts]
    df: Counter[str] = Counter()
    for tokens in tokenized:
        df.update(set(tokens))
    n = len(texts)
    vectors: list[dict[str, float]] = []
    for tokens in tokenized:
        tf = Counter(tokens)
        vec = {
            term: (count / len(tokens)) * (math.log((1 + n) / (1 + df[term])) + 1.0)
            for term, count in tf.items()
        }
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors.append({k: v / norm for k, v in vec.items()})
    return vectors


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())


def _centroid(vectors: list[dict[str, float]]) -> dict[str, float]:
    total: dict[str, float] = {}
    for vec in vectors:
        for k, v in vec.items():
            total[k] = total.get(k, 0.0) + v
    norm = math.sqrt(sum(v * v for v in total.values())) or 1.0
    return {k: v / norm for k, v in total.items()}


def cluster_claims(claims: list[Claim], threshold: float = 0.35) -> list[Cluster]:
    """Greedy single-pass clustering in claim order (deterministic for a given input)."""
    if not claims:
        return []
    vectors = tfidf_vectors([c.text for c in claims])
    groups: list[list[int]] = []
    centroids: list[dict[str, float]] = []
    for i, vec in enumerate(vectors):
        best, best_sim = -1, threshold
        for j, centroid in enumerate(centroids):
            sim = cosine(vec, centroid)
            if sim >= best_sim:
                best, best_sim = j, sim
        if best == -1:
            groups.append([i])
            centroids.append(dict(vec))
        else:
            groups[best].append(i)
            centroids[best] = _centroid([vectors[k] for k in groups[best]])

    clusters: list[Cluster] = []
    for n, (members, centroid) in enumerate(zip(groups, centroids, strict=True), start=1):
        member_claims = [claims[i] for i in members]
        label = " ".join(k for k, _ in sorted(centroid.items(), key=lambda kv: -kv[1])[:3])
        clusters.append(
            Cluster(
                cluster_id=f"c{n}",
                label=label or "misc",
                claim_ids=[c.claim_id for c in member_claims],
                source_ids=sorted({c.source_id for c in member_claims}),
                supports=sum(1 for c in member_claims if c.stance.value == "supports"),
                refutes=sum(1 for c in member_claims if c.stance.value == "refutes"),
                neutral=sum(1 for c in member_claims if c.stance.value == "neutral"),
            )
        )
    return clusters


def add_extends_edges(graph: CitationGraph, clusters: list[Cluster], claims: list[Claim]) -> int:
    """Link the first claim of each multi-source cluster to the others from other sources."""
    by_id = {c.claim_id: c for c in claims}
    texts = {c.claim_id: c.text for c in claims}
    added = 0
    for cluster in clusters:
        if len(cluster.source_ids) < 2:
            continue
        head = cluster.claim_ids[0]
        head_source = by_id[head].source_id
        vectors = tfidf_vectors([texts[cid] for cid in cluster.claim_ids])
        for cid, vec in zip(cluster.claim_ids[1:], vectors[1:], strict=True):
            if by_id[cid].source_id == head_source:
                continue
            weight = max(0.0, min(1.0, round(cosine(vectors[0], vec), 3)))
            graph.link(
                head,
                cid,
                EdgeType.EXTENDS,
                weight=weight,
                explanation=f"same topic cluster '{cluster.label}'",
            )
            added += 1
    return added
