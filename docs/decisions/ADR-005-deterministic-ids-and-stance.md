# ADR-005: Deterministic identifiers, and stance as a first-class claim field

**Status:** Accepted (Phase 2)

## Problem

Two defects in the first ArguMind made its citation graph unreliable:

1. **Ids.** Web sources were identified by `hash(url) % 100000`, which changes
   every process (Python randomises string hashing), and the model was asked
   to name claims, so every paper produced `claim_1`, `claim_2`, ... and the
   graph overwrote one paper's claims with the next paper's.
2. **Stance.** A claim was only linked to its source with a `supports` edge.
   Nothing ever recorded that a claim argued *against* the question, so the
   graph could not contain disagreement and the conflict resolver never ran.

## Decision

Identifiers are pure functions of stable inputs, computed in code:

| Thing | Id | Example |
|-------|----|---------|
| arXiv paper | `arxiv:<id without version>` | `arxiv:2301.12345` |
| Wikipedia page | `wikipedia:<sha256(canonical url)[:16]>` | `wikipedia:3f9a1c…` |
| Claim | `<source_id>#<n>` numbered in extraction order | `arxiv:2301.12345#2` |
| Source lookup cache | `sha256(kind | limit | normalised query)` | — |

Canonicalisation lowercases scheme and host, drops fragments and tracking
parameters, and sorts the query string, so trivial URL variants share an id.
Python's `hash()` is not used anywhere for identity.

Every extracted claim carries a **stance** relative to the **main question's
proposition** (`supports`, `refutes` or `neutral`), chosen by the model as
part of the structured output and validated as an enum. *Amended after the
first baseline (2026-09-17):* stance was originally judged relative to the
sub-question that retrieved the source; when the planner phrased a
sub-question as "what evidence challenges X?", papers challenging X were
labelled `supports` and no conflict could form. The extractor now receives
both the main question (stance frame) and the sub-question (search focus). The database CHECK constraint
enforces the same three values. Phase 3 turns stance into `supports` and
`refutes` edges, which is what makes conflicts detectable by construction.

## Alternatives considered

- Random UUIDs per claim: unique, but two runs of the same question would
  not be comparable and cached results could not be joined.
- Letting the model assign ids with instructions to make them unique:
  cheaper, but exactly the failure mode being fixed.
- Deriving stance afterwards with a second classification call: doubles the
  model calls; asking for it in the same structured output costs nothing.

## Consequences

- A paper revised on arXiv (`v2`) keeps its id; the title and summary come
  from whichever version was retrieved in that run.
- Claim ids are unique within a run (enforced by `UNIQUE (run_id, claim_id)`),
  not globally; a claim is always read in the context of its run.
- Stance is the model's judgement; the benchmark in Phase 7 measures how
  often it agrees with the reference on contested questions.
