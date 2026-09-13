"""Citation graph: sub-questions, sources and claims, with explicit agreement and
disagreement (ADR-006).

Node kinds
    question  q:<index>          one per sub-question (the proposition being tested)
    source    <source_id>        a retrieved paper or article
    claim     <claim_id>         what a source says; carries its source_id

Edge types (the four the database accepts)
    claim  --supports-->  question     stance = supports
    claim  --refutes--->  question     stance = refutes
    claim  --extends--->  claim        semantically related claims across sources (Phase 4)
    claim  --supersedes-> claim        a newer winning claim over an older losing one (resolver)

Neutral claims are nodes without a stance edge: context, not evidence.
Provenance (source -> claim) is an attribute, not an edge, so every edge in
the graph is an argumentative relation and a conflict is simply a question
with both kinds of stance edge.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

import networkx as nx
from pydantic import BaseModel, Field

from app.evidence.models import Claim, Stance
from app.sources.models import Source


class EdgeType(str, Enum):
    SUPPORTS = "supports"
    REFUTES = "refutes"
    EXTENDS = "extends"
    SUPERSEDES = "supersedes"


class NodeKind(str, Enum):
    QUESTION = "question"
    SOURCE = "source"
    CLAIM = "claim"


class GraphError(ValueError):
    pass


class Edge(BaseModel):
    source_node: str
    target_node: str
    edge_type: EdgeType
    weight: float = Field(default=1.0, ge=0, le=1)
    explanation: str = ""


class Conflict(BaseModel):
    """A sub-question with evidence on both sides from different sources."""

    question_index: int
    question: str
    supporting: list[str]  # claim ids
    refuting: list[str]
    supporting_sources: list[str]
    refuting_sources: list[str]


def question_node(index: int) -> str:
    return f"q:{index}"


class CitationGraph:
    def __init__(self) -> None:
        self.g = nx.DiGraph()

    # ------------------------------------------------------------------ nodes
    def add_question(self, index: int, text: str) -> str:
        node = question_node(index)
        self.g.add_node(node, kind=NodeKind.QUESTION.value, index=index, text=text)
        return node

    def add_source(self, source: Source) -> str:
        self.g.add_node(
            source.source_id,
            kind=NodeKind.SOURCE.value,
            title=source.title,
            source_kind=source.kind.value,
            year=source.published_year,
            authority=source.authority,
            url=str(source.url),
            label=source.citation_label,
        )
        return source.source_id

    def add_claim(self, claim: Claim) -> str:
        if claim.source_id not in self.g:
            raise GraphError(f"claim {claim.claim_id} references unknown source {claim.source_id}")
        target = (
            question_node(claim.sub_question_index)
            if claim.sub_question_index is not None
            else None
        )
        if target is not None and target not in self.g:
            raise GraphError(f"claim {claim.claim_id} references unknown {target}")
        # Validate everything before mutating, so a rejected claim leaves no node behind.
        self.g.add_node(
            claim.claim_id,
            kind=NodeKind.CLAIM.value,
            source_id=claim.source_id,
            text=claim.text,
            stance=claim.stance.value,
            confidence=claim.confidence,
            evidence_type=claim.evidence_type,
            question_index=claim.sub_question_index,
        )
        if target is not None and claim.stance is not Stance.NEUTRAL:
            self.link(
                claim.claim_id,
                target,
                EdgeType(claim.stance.value),
                weight=claim.confidence,
                explanation=f"{claim.source_id} {claim.stance.value} the sub-question",
            )
        return claim.claim_id

    # ------------------------------------------------------------------ edges
    def link(
        self,
        source_node: str,
        target_node: str,
        edge_type: EdgeType,
        weight: float = 1.0,
        explanation: str = "",
    ) -> Edge:
        for node in (source_node, target_node):
            if node not in self.g:
                raise GraphError(f"unknown node {node!r}")
        if source_node == target_node:
            raise GraphError("self-loops are not allowed")
        if not 0 <= weight <= 1:
            raise GraphError("weight must be within [0, 1]")
        edge = Edge(
            source_node=source_node,
            target_node=target_node,
            edge_type=edge_type,
            weight=weight,
            explanation=explanation,
        )
        self.g.add_edge(
            source_node,
            target_node,
            edge_type=edge_type.value,
            weight=weight,
            explanation=explanation,
        )
        return edge

    # --------------------------------------------------------------- queries
    def nodes_of(self, kind: NodeKind) -> list[str]:
        return [n for n, d in self.g.nodes(data=True) if d.get("kind") == kind.value]

    def claims_for_question(self, index: int, edge_type: EdgeType) -> list[str]:
        target = question_node(index)
        if target not in self.g:
            return []
        return sorted(
            n
            for n in self.g.predecessors(target)
            if self.g[n][target].get("edge_type") == edge_type.value
        )

    def source_of(self, claim_id: str) -> str:
        return self.g.nodes[claim_id]["source_id"]

    def conflicts(self) -> list[Conflict]:
        """Every sub-question with supporting and refuting claims from different sources."""
        out: list[Conflict] = []
        for node in self.nodes_of(NodeKind.QUESTION):
            index = self.g.nodes[node]["index"]
            supporting = self.claims_for_question(index, EdgeType.SUPPORTS)
            refuting = self.claims_for_question(index, EdgeType.REFUTES)
            sup_sources = sorted({self.source_of(c) for c in supporting})
            ref_sources = sorted({self.source_of(c) for c in refuting})
            # A single source contradicting itself is noise, not a conflict of evidence.
            if sup_sources and ref_sources and set(sup_sources) != set(ref_sources):
                out.append(
                    Conflict(
                        question_index=index,
                        question=self.g.nodes[node]["text"],
                        supporting=supporting,
                        refuting=refuting,
                        supporting_sources=sup_sources,
                        refuting_sources=ref_sources,
                    )
                )
        return out

    def edges(self) -> list[Edge]:
        return [
            Edge(
                source_node=u,
                target_node=v,
                edge_type=EdgeType(d["edge_type"]),
                weight=d.get("weight", 1.0),
                explanation=d.get("explanation", ""),
            )
            for u, v, d in self.g.edges(data=True)
        ]

    def summary(self) -> dict[str, int]:
        counts = {e.value: 0 for e in EdgeType}
        for _, _, d in self.g.edges(data=True):
            counts[d["edge_type"]] += 1
        return {
            "questions": len(self.nodes_of(NodeKind.QUESTION)),
            "sources": len(self.nodes_of(NodeKind.SOURCE)),
            "claims": len(self.nodes_of(NodeKind.CLAIM)),
            "conflicts": len(self.conflicts()),
            **{f"{k}_edges": v for k, v in counts.items()},
        }

    # ----------------------------------------------------------- serialisation
    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [{"id": n, **d} for n, d in self.g.nodes(data=True)],
            "edges": [e.model_dump(mode="json") for e in self.edges()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CitationGraph:
        graph = cls()
        for node in data.get("nodes", []):
            attrs = dict(node)
            node_id = attrs.pop("id", None)
            if node_id is None or "kind" not in attrs:
                raise GraphError("node without id or kind")
            graph.g.add_node(node_id, **attrs)
        for edge in data.get("edges", []):
            e = Edge.model_validate(edge)
            graph.link(e.source_node, e.target_node, e.edge_type, e.weight, e.explanation)
        return graph
