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
    )
    runner.run(cases)
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
