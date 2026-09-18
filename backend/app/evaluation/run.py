"""Benchmark CLI: run the cases against a live backend and write reports.

    docker compose run --rm --no-deps -v "$PWD/evaluation:/app/evaluation_out" backend \
        python -m app.evaluation.run --base-url http://backend:8000 \
        --out /app/evaluation_out/reports --tag baseline

Each case is one POST /api/v1/runs?wait=true with its own client id (so the
per-client rate limit never triggers), graded from the returned run detail.
A partial checkpoint is written after every case; --resume continues from it.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import httpx

from app.evaluation.dataset import Case, load_benchmark
from app.evaluation.report import now, write_reports
from app.evaluation.runner import CaseResult, EvaluationRunner


def make_ask(base_url: str, tag: str, timeout: float) -> Any:
    client = httpx.Client(base_url=base_url, timeout=timeout)

    def ask(case: Case) -> tuple[int, dict[str, Any] | None, float | None]:
        response = client.post(
            "/api/v1/runs",
            params={"wait": "true"},
            json={"question": case.question, "client_id": f"bench:{tag}:{case.id}"},
        )
        retry_after = response.headers.get("Retry-After")
        try:
            body = response.json()
        except ValueError:
            body = {"detail": response.text[:500]}
        return response.status_code, body, float(retry_after) if retry_after else None

    return ask


def make_circuit_probe(
    base_url: str,
    timeout: float = 10.0,
    client: httpx.Client | None = None,
    prefixes: tuple[str, ...] = ("llm:", "source:"),
) -> Any:
    """Read the backend's operations summary and report how long any watched
    circuit breaker stays open. Running a case while the model circuit is open
    (free-tier quota burst) or a source circuit is open (arXiv 429/406 outage)
    grades an infrastructure condition as a pipeline failure. The pipeline's
    behaviour *under* those conditions is covered by unit tests, not by the
    benchmark, so the runner waits them out."""
    client = client or httpx.Client(base_url=base_url, timeout=timeout)

    def probe() -> float:
        response = client.get("/api/v1/system/operations")
        response.raise_for_status()
        circuits = response.json().get("circuits") or {}
        waits = [
            float(state.get("retry_after_seconds") or 0.0)
            for name, state in circuits.items()
            if name.startswith(prefixes) and state.get("state") == "open"
        ]
        return max(waits, default=0.0)

    return probe


# Backwards-compatible name used by the first version of the pacing.
make_model_circuit_probe = make_circuit_probe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ArguMind benchmark")
    parser.add_argument("--base-url", default="http://backend:8000")
    parser.add_argument("--out", default="evaluation/reports")
    parser.add_argument("--tag", default=now().strftime("%Y-%m-%d"))
    parser.add_argument("--category", action="append", help="run only these categories")
    parser.add_argument("--ids", help="comma-separated case ids")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between cases")
    parser.add_argument("--timeout", type=float, default=400.0, help="HTTP timeout per case")
    parser.add_argument("--resume", action="store_true", help="skip cases in partial.json")
    parser.add_argument(
        "--no-circuit-pacing",
        action="store_true",
        help="do not wait for the backend's model/source circuit breakers between cases",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    partial_path = out / "partial.json"

    cases = load_benchmark()
    if args.category:
        cases = [c for c in cases if c.category in set(args.category)]
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",") if i.strip()}
        cases = [c for c in cases if c.id in wanted]
    done: list[CaseResult] = []
    if args.resume and partial_path.exists():
        for raw in json.loads(partial_path.read_text()).get("results", []):
            done.append(CaseResult(**raw))
        done_ids = {r.id for r in done}
        cases = [c for c in cases if c.id not in done_ids]
        print(f"resuming: {len(done)} done, {len(cases)} remaining")
    if args.limit:
        cases = cases[: args.limit]
    if not cases and not done:
        print("no cases selected")
        return 2

    results = list(done)

    def checkpoint(result: CaseResult) -> None:
        results.append(result)
        partial_path.write_text(
            json.dumps({"tag": args.tag, "results": [r.to_dict() for r in results]}, default=str)
        )

    started = now()
    print(f"benchmark {args.tag}: {len(cases)} case(s) against {args.base_url}")
    runner = EvaluationRunner(
        make_ask(args.base_url, args.tag, args.timeout),
        pause_seconds=args.pause,
        on_result=checkpoint,
        model_circuit=None if args.no_circuit_pacing else make_circuit_probe(args.base_url),
    )
    runner.run(cases)
    if runner.aborted_at:
        print(
            f"\nstopped at {runner.aborted_at}: the model provider rejected every call. "
            f"{len(results)} result(s) kept in {partial_path}; rerun with --resume when the "
            "quota is back. No report written."
        )
        return 3
    finished = now()
    results.sort(key=lambda r: r.id)
    md_path, json_path = write_reports(out, args.tag, started, finished, args.base_url, results)
    partial_path.unlink(missing_ok=True)
    passed = sum(1 for r in results if r.passed)
    lat = sorted(r.latency_ms for r in results if r.status is not None)
    p50 = lat[len(lat) // 2] if lat else 0
    print(
        f"\n{passed}/{len(results)} passed ({100 * passed / len(results):.1f}%)  "
        f"p50 {p50} ms  model calls {sum(r.llm_calls for r in results)}"
    )
    print(f"reports: {md_path} {json_path}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
