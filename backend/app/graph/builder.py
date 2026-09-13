"""Assemble a CitationGraph from the outputs of the gather and extract stages."""

from __future__ import annotations

import logging

from app.evidence.models import Claim
from app.graph.model import CitationGraph, GraphError
from app.sources.models import Source

log = logging.getLogger(__name__)


def build_graph(
    sub_questions: list[str], sources: list[Source], claims: list[Claim]
) -> CitationGraph:
    graph = CitationGraph()
    for index, text in enumerate(sub_questions):
        graph.add_question(index, text)
    known: set[str] = set()
    for source in sources:
        if source.source_id in known:
            continue  # the same paper can surface for several sub-questions
        graph.add_source(source)
        known.add(source.source_id)
    skipped = 0
    for claim in claims:
        try:
            graph.add_claim(claim)
        except GraphError as exc:
            skipped += 1
            log.warning("skipping claim: %s", exc)
    if skipped:
        log.warning("%d claims skipped while building the graph", skipped)
    return graph
