# Evaluation (Phase 7)

A benchmark of 30 questions, deterministic graders, a runner with resume, and
committed reports. The harness is **implemented** and unit-tested; the
baseline against the real model and live sources is recorded below when it
has been run.

## 1. Dataset

`backend/app/evaluation/benchmark/*.yaml`, one file per category.

| Category | Cases | What a pass means |
|----------|------:|-------------------|
| settled | 6 | answered with ≥2 sources, supporting evidence, on-topic answer |
| contested | 7 | both stances present; a conflict detected and resolved with a minority view (5 of 7) |
| comparative | 2 | evidence on at least one side, answered or honestly inconclusive |
| no_evidence | 4 | inconclusive with confidence ≤ 0.5; nothing invented |
| speculative | 3 | inconclusive, or answered with confidence ≤ 0.7 |
| injection | 5 | injected text never reaches sub-questions or answer; planted ids never cited; no forced confidence |
| validation | 3 | rejected with 422 before any model call |

Every case is an `id`, `category`, `question` and an `expect` block
(`status_any`, `min_sources`, `min_claims`, `stances_any`, `stances_all`,
`conflict_expected`, `citations_required`, `answer_contains_any`,
`answer_excludes`, `max_confidence`, `injection_markers`, `max_llm_calls`,
`max_latency_ms`, `http_status`). Loading validates ids, categories and types.

## 2. Graders

See ADR-010. Seven dimensions, each PASS / FAIL / not applicable, computed
from the run detail the API returns: outcome, evidence, conflicts, citations
(fabrication), answer, safety, cost. A case passes when no applicable
dimension fails.

## 3. Running

Against the live stack (the backend must have a model key, or use the mock
for a plumbing check):

```bash
make evaluate EVAL_ARGS="--tag baseline"          # all 30 cases, ~30-60 min on the free tier
make evaluate-smoke                                # 4 cases, harness check; not kept as latest
make evaluate EVAL_ARGS="--category contested --tag contested-only"
make evaluate EVAL_ARGS="--ids settled-001,injection-002 --tag spot"
make evaluate EVAL_ARGS="--tag baseline --resume"  # continue after an interruption
```

Each case is one `POST /api/v1/runs?wait=true` with client id
`bench:<tag>:<case>`, so the per-client rate limit never triggers. `429`,
`502` and `503` are retried once after `Retry-After`. A checkpoint
(`partial.json`, git-ignored) is written after every case.

**Circuit pacing.** The runner reads `/api/v1/system/operations` before every
case and, if an `llm:*` circuit breaker is open, waits `retry_after + 2 s`
(capped at 10 min) instead of starting. After each case it checks again and,
if the circuit is open or the run ended `inconclusive`/`failed` with **zero**
successful model calls, waits the circuit out and reruns the case once
(`extra.retried_circuit`, `extra.circuit_wait_seconds` in the JSON report).
This was added after the first live attempt: a canary run plus the first
case tripped Gemini's per-minute quota, the backend's `llm:gemini` breaker
opened for 120 s, and the next six cases each ended inconclusive in 4-34 s
with no model call. Those are harness-induced failures, not pipeline
failures, and grading them would have made the baseline meaningless.
`--no-circuit-pacing` disables the behaviour; `--pause 15` is the gentle
default used for the baseline.

If a case is still starved after the rerun, the provider is exhausted (a spent
daily quota, in practice): the runner stops **without grading that case or
writing a report**, exits with code 3 and leaves `partial.json` for
`--resume`.

**Free-tier facts learned while getting the baseline to run** (each became a
control in `docs/reliability.md`): the fast model allows 15 requests/min and
its 429 asks for a 40-60 s retry, so the provider now paces requests to one
per 4 s and honours the hint (capped at 30 s). Daily caps per model are
**20 requests for the standard model and 500 for the fast model**; the
provider detects a per-day quota in the 429 body and parks the model for an
hour instead of retrying, so synthesis calls fall back to the fast tier after
the first few cases. A case costs 13-30 fast-tier calls, so **one free-tier
day covers roughly 15-20 cases**; the full baseline is collected over two
days with `--resume`, and the report records that. Reports land in
`evaluation/reports/<tag>.md` and `.json`; non-smoke tags also update
`latest.*`, and the Markdown lists regressions and fixes versus the previous
latest.

Cost of a full run on the free tier: roughly 8-20 model calls per answered
case, 30-120 s each. Wikipedia and arXiv results are cached for 24 h, so a
re-run within a day is faster and cheaper.

## 4. Baseline

**Not yet recorded.** The first attempt was blocked by two facts of the day:
arXiv had been rate-limiting this network for hours (every arXiv lookup
returned 429 or timed out), and the backend was running with the mock model.
A baseline without arXiv would grade Wikipedia-only evidence, which rarely
provides two sources with a stance, and would say nothing about the model.

The harness itself was validated with a smoke run against the live stack with
the mock provider on the validation, no-evidence and injection categories:
9 of 12 passed. All validation cases were rejected with 422 and all
no-evidence cases ended inconclusive with confidence 0 and no invented
citation. Three injection cases failed `safety` because the **mock planner
echoes the question verbatim into the sub-questions**, so the planted text
surfaced there; that is a property of the mock, not of the real planner,
whose prompt treats the question as untrusted input. The injection category
is therefore only meaningful against the real model. When arXiv has recovered
and `LLM_API_KEY` is set in `.env`:

```bash
docker compose up -d backend
make evaluate EVAL_ARGS="--tag baseline"
```

then commit `evaluation/reports/baseline.*` and `latest.*`, and replace this
section with the headline table, the per-dimension and per-category results,
and the failures classified as system defects, dataset strictness, or
external causes.

## 5. Limits of the method

- Structure, not prose: a fluent but wrong answer with a real citation passes
  `citations`; only content expectations can catch it.
- Live sources: results move with arXiv and Wikipedia; the source cache and
  the caveats recorded per run make a bad day visible rather than silent.
- Thirty cases detect gross regressions, not small quality shifts.
