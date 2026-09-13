# Citation graph and conflict resolution (Phase 3)

Everything here is **implemented** and unit-tested; the pipeline that feeds
it real runs is Phase 4.

## 1. The graph

```
q:0  "Do large language models understand language?"
 ▲ supports (0.80)          ▲ supports (0.60)          ▲ refutes (0.90)
 │                          │                          │
arxiv:2301.00001#1      arxiv:2001.00002#1        arxiv:2405.00003#1 ──supersedes──▶ arxiv:2001.00002#1
 (source arxiv:2301.00001)  (source arxiv:2001.00002)  (source arxiv:2405.00003)

wikipedia:…#1  (neutral: node only, no stance edge)
```

| Node kind | Id | Attributes |
|-----------|----|------------|
| question | `q:<index>` | index, text |
| source | `<source_id>` | title, source_kind, year, authority, url, label |
| claim | `<claim_id>` | source_id, text, stance, confidence, evidence_type, question_index |

| Edge | From → to | Created by | Weight |
|------|-----------|------------|--------|
| `supports` | claim → question | builder, from stance | claim confidence |
| `refutes` | claim → question | builder, from stance | claim confidence |
| `extends` | claim → claim | clustering (Phase 4) | similarity |
| `supersedes` | claim → claim | conflict resolver | resolution confidence |

`CitationGraph` (NetworkX `DiGraph` inside) validates every edge (known
endpoints, allowed type, weight in [0, 1], no self-loops), serialises to and
from a plain dict, and exposes `edges()` in the exact shape of the
`citation_edges` table.

## 2. Conflicts

`graph.conflicts()` returns one `Conflict` per sub-question that has both
`supports` and `refutes` edges from **different** sources: the claim ids and
source ids on each side. Because stance edges are built from every extracted
claim, disagreement in the evidence cannot go undetected. A single source
that contradicts itself is not reported as a conflict.

## 3. Resolution

`ConflictResolver.resolve(graph, sources, claims)` produces a `ConflictReport`
with one `Resolution` per conflict:

| Field | Meaning |
|-------|---------|
| `support`, `refute` | `SideScore`: score, claim count, source count, newest year |
| `margin` | `(support − refute) / (support + refute)` in [−1, 1] |
| `winner` | `supports`, `refutes` or `inconclusive` |
| `method` | `heuristic`, `model`, or `heuristic_after_model_error` |
| `confidence`, `reasoning` | from the heuristics or the model's validated answer |
| `minority_report` | the losing side's stance, claim ids, source ids, summary |
| `superseded` | `(newer claim, older claim)` pairs turned into `supersedes` edges |

Scoring: `Σ confidence × authority × recency × evidence weight` per side.

| Factor | Values |
|--------|--------|
| authority | arXiv 0.75, Wikipedia 0.60 (per source kind) |
| recency | 1.0 for the last two years, −0.05 per further year, floor 0.5; unknown year 0.8 |
| evidence weight | empirical 1.0, review 0.9, theoretical 0.7, other 0.6, opinion 0.5 |

Decision rule: `|margin| ≥ 0.35` decides without a model call; otherwise the
model arbitrates (`resolve_conflict`, standard tier, structured output) when
a provider is configured; without one, `|margin| < 0.15` is inconclusive.
Model failures fall back to the heuristics and are labelled as such. See
ADR-006 for the reasoning.

## 4. Trying it

The evidence CLI can run the whole Phase 2 + 3 chain on one question:

```bash
docker compose run --rm backend python -m app.evidence.cli "Do LLMs understand language?" --graph
```

It prints sources, claims with stance, the graph summary (including the
number of `refutes` edges and conflicts) and each resolution with its
method, margin and minority report. With `LLM_PROVIDER=mock` it needs no key.

## 5. Not here yet

- `extends` edges from semantic clustering, and pairwise claim conflicts
  within a cluster (Phase 4).
- Persistence of the graph and resolutions with a run (Phase 4).
