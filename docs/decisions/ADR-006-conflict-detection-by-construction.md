# ADR-006: Conflicts by construction; heuristics first, model arbitration when close

**Status:** Accepted (Phase 3)

## Problem

The first ArguMind's headline feature, "resolves contradictions and keeps a
minority report", never executed: its graph only ever received `supports`
edges from a source to its own claims, so the conflict finder had nothing to
find. When a conflict *could* have been found, resolution would have been one
opaque model call returning a "winner".

## Decision

1. **Stance edges to the sub-question.** Every claim carries a stance (ADR-005;
   judged against the main proposition, attached to the sub-question `q:i`
   that retrieved the source) and the graph builder turns it into
   `claim --supports--> q:i` or `claim --refutes--> q:i`. Neutral claims are nodes without a stance edge.
   Provenance (source → claim) is a node attribute, not an edge, so every
   edge is an argumentative relation. A **conflict** is therefore a
   sub-question with supporting and refuting edges whose sources differ. It
   cannot fail to be detected when the evidence disagrees, and one source
   contradicting itself is not counted as a conflict of evidence.

2. **Deterministic scoring first.** Each side is scored as
   `Σ confidence × source authority × recency × evidence-type weight`, and
   both scores, claim counts, source counts and newest years are recorded in
   the resolution. With margin `m = (S − R) / (S + R)`:
   - `|m| ≥ 0.35`: decided by the heuristics, no model call;
   - otherwise, if a model is available, it **arbitrates** with the claims as
     data and returns a validated `{winner, reasoning, confidence}`; its
     reasoning is stored verbatim;
   - if the model fails or answers invalidly, the heuristics decide and the
     method is labelled `heuristic_after_model_error`;
   - without a model, `|m| < 0.15` is `inconclusive`.

3. **The losing side is kept.** Every decided conflict carries a minority
   report (stance, claim ids, source ids, summary of the losing claims).

4. **Recency becomes structure.** When the newest winning claim is at least
   two years newer than a losing claim, a `supersedes` edge is added from the
   newer to the older claim with an explanation, so the graph shows *why* an
   older result lost.

## Alternatives considered

- Model-only resolution: cheaper to build, but non-deterministic,
  unexplainable, and every conflict costs a call. The heuristics resolve the
  clear cases for free and make the model's job narrower when it is used.
- Heuristics only: deterministic, but blind to whether two claims actually
  address the same proposition. The model is used exactly where that
  judgement matters.
- Pairwise claim conflicts instead of per-sub-question: finer, but needs the
  semantic clustering of Phase 4 to know which claims oppose each other. The
  sub-question is the proposition both sides were extracted against, so it
  is the right unit for Phase 3; clustering will refine it.

## Consequences

- Authority priors (arXiv 0.75, Wikipedia 0.60), the recency curve and the
  evidence-type weights are explicit constants that the benchmark in Phase 7
  can challenge.
- Model arbitration adds at most one standard-tier call per close conflict.
- Every resolution is serialisable and will be persisted with the run
  (Phase 4), so an answer's "why" can be audited later.
