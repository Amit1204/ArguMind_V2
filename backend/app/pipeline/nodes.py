"""Pipeline nodes: plain functions of (state, ctx) so they are testable without LangGraph.

Every node records exactly one stage, catches the failures it can reason
about (model, source and extraction errors) and turns them into caveats, and
never fabricates a result to hide a failure.
"""

from __future__ import annotations

import logging

from app.evidence.extractor import ExtractionError
from app.evidence.models import Claim
from app.graph.builder import build_graph
from app.graph.model import CitationGraph
from app.llm.base import LLMError, LLMRequest, ModelTier
from app.pipeline.context import PipelineContext
from app.pipeline.critic import critique
from app.pipeline.prompts import (
    ANSWER_SYSTEM,
    CONSENSUS_SYSTEM,
    PLAN_SYSTEM,
    answer_prompt,
    consensus_prompt,
    plan_prompt,
)
from app.pipeline.schemas import AnswerOutput, Cluster, ConsensusOutput, PlanOutput
from app.pipeline.state import RunState
from app.pipeline.verify import verify_citations
from app.reasoning.clustering import add_extends_edges, cluster_claims
from app.reasoning.models import ConflictReport
from app.sources.models import Source, SourceKind

log = logging.getLogger(__name__)


def _sources(state: RunState) -> list[Source]:
    return [Source.model_validate(s) for s in state.get("sources", [])]


def _claims(state: RunState) -> list[Claim]:
    return [Claim.model_validate(c) for c in state.get("claims", [])]


def _report(state: RunState) -> ConflictReport:
    return ConflictReport.model_validate(state.get("conflict_report") or {"resolutions": []})


# ------------------------------------------------------------------------- plan


def plan_node(state: RunState, ctx: PipelineContext) -> dict:
    question = state["question"]
    with ctx.stage("plan") as stage:
        sub_questions = [question]
        plan: dict = {"sub_questions": sub_questions, "domains": [], "complexity": "moderate"}
        caveats: list[str] = []
        try:
            response = ctx.provider.complete(
                LLMRequest(
                    system=PLAN_SYSTEM.format(max_sub_questions=ctx.settings.max_sub_questions),
                    prompt=plan_prompt(question),
                    tier=ModelTier.FAST,
                    response_schema=PlanOutput,
                    purpose="plan",
                    max_output_tokens=1024,
                )
            )
            ctx.ledger.record(response.usage)
            parsed = response.parse(PlanOutput)
            seen: list[str] = []
            for q in parsed.sub_questions:
                q = " ".join(q.split())
                if q and q.lower() not in {s.lower() for s in seen}:
                    seen.append(q)
            sub_questions = seen[: ctx.settings.max_sub_questions] or [question]
            plan = {**parsed.model_dump(), "sub_questions": sub_questions}
        except LLMError as exc:
            stage.fail(f"planning failed: {exc}")
            caveats.append(
                "Planning failed; the original question was used as the only sub-question."
            )
        stage.detail.update({"sub_questions": sub_questions, "complexity": plan.get("complexity")})
    return {"sub_questions": sub_questions, "plan": plan, "iteration": 0, "caveats": caveats}


# ----------------------------------------------------------------------- gather


def gather_node(state: RunState, ctx: PipelineContext) -> dict:
    iteration = state.get("iteration", 0)
    existing = list(state.get("sources", []))
    seen = {(s["source_id"], s.get("sub_question_index")) for s in existing}
    caveats: list[str] = []
    new_sources: list[dict] = []
    with ctx.stage("gather", attempt=iteration + 1) as stage:
        if ctx.expired():
            stage.skip("time budget exceeded before gathering", timeout=True)
            return {"caveats": ["Gathering skipped: the run's time budget was exhausted."]}
        if iteration == 0:
            queries = list(enumerate(state["sub_questions"]))
            limits = None
        else:
            # Broadened retry: the original question with doubled limits.
            queries = [(0, state["question"])]
            limits = {
                SourceKind.ARXIV: ctx.settings.arxiv_max_results * 2,
                SourceKind.WIKIPEDIA: ctx.settings.wikipedia_max_results * 2,
            }
        per_kind: dict[str, int] = {}
        cached = 0
        errors: list[str] = []
        # A source that failed once in this run is not tried again, for later
        # sub-questions or on the broadened retry: a throttled API must not
        # absorb the whole time budget.
        failed_kinds: set[SourceKind] = {
            SourceKind(k) for k in state.get("failed_source_kinds", [])
        }
        stopped_for_time = False
        for index, query in queries:
            for kind in ctx.sources.kinds:
                if kind in failed_kinds:
                    continue
                if ctx.gather_expired():
                    stopped_for_time = True
                    break
                outcome = ctx.sources.search(kind, query, (limits or {}).get(kind))
                if outcome.error:
                    failed_kinds.add(kind)
                    errors.append(
                        f"{kind.value} unavailable for sub-question {index + 1}: {outcome.error}"
                    )
                    continue
                cached += int(outcome.cached)
                for source in outcome.sources:
                    key = (source.source_id, index)
                    if key in seen:
                        continue
                    seen.add(key)
                    tagged = source.model_copy(update={"sub_question_index": index})
                    new_sources.append(tagged.model_dump(mode="json"))
                    per_kind[kind.value] = per_kind.get(kind.value, 0) + 1
            if stopped_for_time:
                caveats.append("Gathering stopped early: the run's time budget was exhausted.")
                break
        caveats.extend(errors)
        stage.detail.update(
            {
                "new_sources": len(new_sources),
                "by_kind": per_kind,
                "cached_lookups": cached,
                "errors": errors,
                "skipped_kinds": sorted(k.value for k in failed_kinds),
                "stopped_for_time": stopped_for_time,
                "broadened": iteration > 0,
            }
        )
        if not new_sources and errors:
            stage.fail("no sources retrieved: " + "; ".join(errors)[:800])
        ctx.store.save_sources(ctx.run_id, [Source.model_validate(s) for s in new_sources])
    return {
        "sources": existing + new_sources,
        "caveats": caveats,
        "failed_source_kinds": sorted(k.value for k in failed_kinds),
    }


# ---------------------------------------------------------------------- extract


def extract_node(state: RunState, ctx: PipelineContext) -> dict:
    iteration = state.get("iteration", 0)
    existing = list(state.get("claims", []))
    done = {(c["source_id"], c.get("sub_question_index")) for c in existing}
    sub_questions = state["sub_questions"]
    new_claims: list[dict] = []
    caveats: list[str] = []
    with ctx.stage("extract", attempt=iteration + 1) as stage:
        extractor = ctx.extractor()
        failures = 0
        skipped_for_time = 0
        by_stance = {"supports": 0, "refutes": 0, "neutral": 0}
        for raw in state.get("sources", []):
            source = Source.model_validate(raw)
            index = source.sub_question_index if source.sub_question_index is not None else 0
            if (source.source_id, index) in done:
                continue
            if ctx.expired():
                skipped_for_time += 1
                continue
            done.add((source.source_id, index))
            try:
                claims = extractor.extract(
                    source, sub_questions[min(index, len(sub_questions) - 1)], index
                )
            except (ExtractionError, LLMError) as exc:
                failures += 1
                log.warning("extraction failed for %s: %s", source.source_id, exc)
                continue
            for claim in claims:
                by_stance[claim.stance.value] += 1
                new_claims.append(claim.model_dump(mode="json"))
        if failures:
            caveats.append(f"Claim extraction failed for {failures} source(s); they were ignored.")
        if skipped_for_time:
            caveats.append(
                f"{skipped_for_time} source(s) were not analysed: time budget exhausted."
            )
        stage.detail.update(
            {
                "new_claims": len(new_claims),
                "by_stance": by_stance,
                "failed_sources": failures,
                "skipped_for_time": skipped_for_time,
            }
        )
        if failures and not new_claims and not existing:
            stage.fail("no claims could be extracted from any source")
        ctx.store.save_claims(ctx.run_id, [Claim.model_validate(c) for c in new_claims])
    return {"claims": existing + new_claims, "caveats": caveats}


# ------------------------------------------------------------------ graph/resolve


def build_graph_node(state: RunState, ctx: PipelineContext) -> dict:
    with ctx.stage("build_graph", attempt=state.get("iteration", 0) + 1) as stage:
        graph = build_graph(state["sub_questions"], _sources(state), _claims(state))
        stage.detail.update(graph.summary())
    return {"graph": graph.to_dict()}


def resolve_node(state: RunState, ctx: PipelineContext) -> dict:
    with ctx.stage("resolve_conflicts", attempt=state.get("iteration", 0) + 1) as stage:
        graph = CitationGraph.from_dict(state["graph"])
        report = ctx.resolver().resolve(graph, _sources(state), _claims(state))
        stage.detail.update(report.summary())
        stage.detail["resolutions"] = report.model_dump(mode="json")["resolutions"]
    return {"graph": graph.to_dict(), "conflict_report": report.model_dump(mode="json")}


def cluster_node(state: RunState, ctx: PipelineContext) -> dict:
    with ctx.stage("cluster", attempt=state.get("iteration", 0) + 1) as stage:
        claims = _claims(state)
        clusters = cluster_claims(claims, ctx.settings.cluster_similarity_threshold)
        graph = CitationGraph.from_dict(state["graph"])
        added = add_extends_edges(graph, clusters, claims)
        contested = [c.label for c in clusters if c.contested]
        stage.detail.update(
            {"clusters": len(clusters), "extends_edges_added": added, "contested": contested,
             "labels": [c.label for c in clusters]}
        )  # fmt: skip
        stage.detail["cluster_details"] = [c.model_dump() for c in clusters]
        ctx.store.replace_edges(ctx.run_id, graph.edges())
    return {"graph": graph.to_dict(), "clusters": [c.model_dump() for c in clusters]}


# -------------------------------------------------------------------- consensus


def _tally_consensus(claims: list[Claim], report: ConflictReport) -> dict:
    """Deterministic consensus used when there is no evidence or the model fails."""
    supports = sum(1 for c in claims if c.stance.value == "supports")
    refutes = sum(1 for c in claims if c.stance.value == "refutes")
    total = supports + refutes
    if total == 0:
        return ConsensusOutput(
            overall=(
                "No source took a position on the question; the retrieved material is "
                "context only."
            ),
            strength="absent",
            research_gaps=["No evidence for or against the proposition was retrieved."],
            confidence=0.0,
        ).model_dump()
    margin = (supports - refutes) / total
    side = "supports" if margin > 0 else "contradicts" if margin < 0 else "is split on"
    strength = "moderate" if abs(margin) >= 0.5 and total >= 4 else "weak"
    return ConsensusOutput(
        overall=f"The retrieved evidence {side} the proposition ({supports} supporting and "
        f"{refutes} refuting claims; {report.inconclusive_count} of {report.conflict_count} "
        f"conflicts unresolved).",
        strength=strength,
        confidence=round(min(0.7, 0.3 + abs(margin) * 0.4), 3),
    ).model_dump()


def consensus_node(state: RunState, ctx: PipelineContext) -> dict:
    caveats: list[str] = []
    with ctx.stage("consensus", attempt=state.get("iteration", 0) + 1) as stage:
        claims = _claims(state)
        report = _report(state)
        clusters = [Cluster.model_validate(c) for c in state.get("clusters", [])]
        stance_claims = [c for c in claims if c.stance.value != "neutral"]
        if not stance_claims:
            consensus = _tally_consensus(claims, report)
            stage.detail["method"] = "tally"
        elif ctx.expired():
            consensus = _tally_consensus(claims, report)
            stage.detail["method"] = "tally"
            caveats.append("Consensus was tallied without the model: time budget exhausted.")
        else:
            try:
                response = ctx.provider.complete(
                    LLMRequest(
                        system=CONSENSUS_SYSTEM,
                        prompt=consensus_prompt(
                            state["question"],
                            state["sub_questions"],
                            claims,
                            report.resolutions,
                            clusters,
                        ),
                        tier=ModelTier.STANDARD,
                        response_schema=ConsensusOutput,
                        purpose="consensus",
                        max_output_tokens=2048,
                    )
                )
                ctx.ledger.record(response.usage)
                consensus = response.parse(ConsensusOutput).model_dump()
                stage.detail["method"] = "model"
            except LLMError as exc:
                stage.fail(f"consensus model call failed: {exc}")
                consensus = _tally_consensus(claims, report)
                stage.detail["method"] = "tally_after_model_error"
                caveats.append(
                    "The consensus model call failed; a deterministic tally was used instead."
                )
        stage.detail.update(
            {
                "strength": consensus["strength"],
                "confidence": consensus["confidence"],
                "consensus": consensus,
            }
        )
    return {"consensus": consensus, "caveats": caveats}


# ----------------------------------------------------------------------- critic


def critic_node(state: RunState, ctx: PipelineContext) -> dict:
    iteration = state.get("iteration", 0)
    with ctx.stage("critic", attempt=iteration + 1) as stage:
        verdict = critique(
            _claims(state),
            state.get("consensus", {}),
            _report(state),
            iteration=iteration,
            max_iterations=ctx.settings.max_iterations,
            min_claims=ctx.settings.min_evidence_claims,
            min_sources=ctx.settings.min_evidence_sources,
            can_retry=not ctx.expired(),
        )
        stage.detail.update(verdict.model_dump())
    caveats = []
    if verdict.recommendation == "retry":
        caveats.append(
            "Evidence was thin after the first pass; gathering was repeated with a broader search."
        )
    return {"critic": verdict.model_dump(), "iteration": iteration + 1, "caveats": caveats}


def route_after_critic(state: RunState) -> str:
    return "gather" if state.get("critic", {}).get("recommendation") == "retry" else "answer"


# ----------------------------------------------------------------------- answer


def _inconclusive_answer(state: RunState, critic: dict, consensus: dict) -> str:
    issues = "; ".join(critic.get("issues", [])) or "the evidence does not support a conclusion"
    gaps = consensus.get("research_gaps") or []
    text = (
        f"The evidence retrieved for this question is inconclusive: {issues}. "
        f"{consensus.get('overall', '')}".strip()
    )
    if gaps:
        text += " Open questions: " + "; ".join(g.rstrip(". ") for g in gaps[:3]) + "."
    return text


def _fallback_answer(
    state: RunState, sources: list[Source], claims: list[Claim], consensus: dict
) -> str:
    parts = [consensus.get("overall", "The evidence is summarised below.")]
    for stance, heading in (("supports", "Evidence for"), ("refutes", "Evidence against")):
        picked = [c for c in claims if c.stance.value == stance][:3]
        if picked:
            parts.append(f"{heading}: " + " ".join(f"{c.text} [{c.source_id}]" for c in picked))
    return "\n\n".join(parts)


def answer_node(state: RunState, ctx: PipelineContext) -> dict:
    critic = state.get("critic", {})
    consensus = state.get("consensus", {})
    caveats: list[str] = []
    with ctx.stage("answer") as stage:
        sources = _sources(state)
        claims = _claims(state)
        report = _report(state)
        if critic.get("recommendation") == "inconclusive":
            answer = {
                "answer": _inconclusive_answer(state, critic, consensus),
                "confidence": float(critic.get("confidence", 0.0)),
            }
            stage.detail["method"] = "inconclusive_template"
            reason = "; ".join(critic.get("issues", [])) or "insufficient evidence"
            return {"answer": answer, "status": "inconclusive", "outcome_reason": reason}
        try:
            if ctx.expired():
                raise LLMError("time budget exhausted before the answer stage")
            response = ctx.provider.complete(
                LLMRequest(
                    system=ANSWER_SYSTEM,
                    prompt=answer_prompt(
                        state["question"], sources, claims, consensus, report.resolutions
                    ),
                    tier=ModelTier.STANDARD,
                    response_schema=AnswerOutput,
                    purpose="answer",
                    max_output_tokens=3072,
                )
            )
            ctx.ledger.record(response.usage)
            parsed = response.parse(AnswerOutput)
            answer = parsed.model_dump()
            stage.detail["method"] = "model"
        except LLMError as exc:
            stage.fail(f"answer model call failed: {exc}")
            answer = {
                "answer": _fallback_answer(state, sources, claims, consensus),
                "confidence": float(critic.get("confidence", 0.0)),
            }
            stage.detail["method"] = "fallback_template"
            caveats.append(
                "The answer model call failed; the answer was assembled from the consensus "
                "and claims."
            )
    return {"answer": answer, "status": "answered", "outcome_reason": None, "caveats": caveats}


# ----------------------------------------------------------------------- verify


def verify_node(state: RunState, ctx: PipelineContext) -> dict:
    answer = dict(state.get("answer") or {"answer": "", "confidence": 0.0})
    caveats: list[str] = []
    with ctx.stage("verify") as stage:
        valid_ids = {s["source_id"] for s in state.get("sources", [])}
        cleaned, result = verify_citations(answer.get("answer", ""), valid_ids)
        answer["answer"] = cleaned
        if result.invalid_removed:
            caveats.append(
                f"{len(result.invalid_removed)} citation(s) to sources not retrieved in this "
                "run were removed."
            )
        if state.get("status") == "answered" and not result.has_valid_citation:
            caveats.append("The answer contains no verifiable citation; treat it with care.")
            answer["confidence"] = round(float(answer.get("confidence", 0.0)) * 0.5, 3)
        all_caveats = list(state.get("caveats", [])) + caveats
        stage.detail.update({**result.model_dump(), "caveats": all_caveats})
    return {"answer": answer, "verification": result.model_dump(), "caveats": caveats}
