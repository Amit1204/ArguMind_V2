"""Summaries, comparison with the previous run, and Markdown/JSON reports."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.evaluation.graders import DIMENSIONS, FAIL, PASS
from app.evaluation.runner import CaseResult
from app.observability.ops import percentile


def _outcome_label(result: CaseResult) -> str:
    return result.status or f"http_{result.http_status}"


def _status_counts(results: list[CaseResult]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for r in results:
        counts[_outcome_label(r)] += 1
    return dict(sorted(counts.items()))


def summarise(results: list[CaseResult]) -> dict[str, Any]:
    passed = sum(1 for r in results if r.passed)
    latencies = [float(r.latency_ms) for r in results if r.status is not None]
    by_dimension: dict[str, dict[str, int]] = {}
    for name in DIMENSIONS:
        applicable = [
            d for r in results for d in r.dimensions if d["name"] == name and d["status"] != "n/a"
        ]
        by_dimension[name] = {
            "applicable": len(applicable),
            "passed": sum(1 for d in applicable if d["status"] == PASS),
        }
    grouped: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        grouped[r.category].append(r)
    by_category: dict[str, dict[str, Any]] = {}
    for category, items in sorted(grouped.items()):
        lat = [float(i.latency_ms) for i in items if i.status is not None]
        by_category[category] = {
            "cases": len(items),
            "passed": sum(1 for i in items if i.passed),
            "latency_ms_p50": percentile(lat, 50),
            "llm_calls": sum(i.llm_calls for i in items),
            "status_counts": _status_counts(items),
        }
    return {
        "cases": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 4) if results else 0.0,
        "http_errors": sum(1 for r in results if r.status is None and r.http_status >= 500),
        "latency_ms_p50": percentile(latencies, 50),
        "latency_ms_p95": percentile(latencies, 95),
        "llm_calls": sum(r.llm_calls for r in results),
        "input_tokens": sum(r.input_tokens for r in results),
        "output_tokens": sum(r.output_tokens for r in results),
        "estimated_cost_usd": round(sum(r.estimated_cost_usd for r in results), 6),
        "by_dimension": by_dimension,
        "by_category": by_category,
        "status_counts": _status_counts(results),
    }


def compare(results: list[CaseResult], previous: dict[str, Any] | None) -> dict[str, list[str]]:
    if not previous:
        return {"regressions": [], "fixes": []}
    before = {r["id"]: r["passed"] for r in previous.get("results", [])}
    now = {r.id: r.passed for r in results}
    common = set(before) & set(now)
    return {
        "regressions": sorted(i for i in common if before[i] and not now[i]),
        "fixes": sorted(i for i in common if not before[i] and now[i]),
    }


def _pct(passed: int, total: int) -> str:
    return f"{100 * passed / total:.1f}%" if total else "—"


def _sec(ms: float | None) -> str:
    return f"{ms / 1000:.1f}s" if ms is not None else "—"


def render_markdown(
    tag: str, started: datetime, finished: datetime, target: str, results: list[CaseResult],
    summary: dict[str, Any], changes: dict[str, list[str]],
) -> str:  # fmt: skip
    lines = [
        "# Evaluation report",
        "",
        f"Run `{tag}` started {started.isoformat(timespec='seconds')} and finished "
        f"{finished.isoformat(timespec='seconds')} against `{target}`.",
        "",
        "## Headline",
        "",
        "| Cases | Passed | Failed | Pass rate | HTTP errors | Run p50 | Run p95 | Model calls "
        "| Tokens in / out | Est. cost |",
        "|------:|-------:|-------:|----------:|------------:|--------:|--------:|------------:"
        "|----------------:|----------:|",
        f"| {summary['cases']} | {summary['passed']} | {summary['failed']} | "
        f"{_pct(summary['passed'], summary['cases'])} | {summary['http_errors']} | "
        f"{_sec(summary['latency_ms_p50'])} | {_sec(summary['latency_ms_p95'])} | "
        f"{summary['llm_calls']} | {summary['input_tokens']:,} / {summary['output_tokens']:,} | "
        f"${summary['estimated_cost_usd']:.4f} |",
        "",
        "Outcomes: " + ", ".join(f"{v} {k}" for k, v in summary["status_counts"].items()),
        "",
        "## Accuracy by dimension",
        "",
        "A dimension counts only for the cases that define an expectation for it.",
        "",
        "| Dimension | Applicable | Passed | Rate |",
        "|-----------|-----------:|-------:|-----:|",
    ]
    labels = {
        "outcome": "Outcome (HTTP / status)",
        "evidence": "Evidence (sources, claims, stances)",
        "conflicts": "Conflicts surfaced with minority view",
        "citations": "Citation fidelity (none invented, answers cited)",
        "answer": "Answer text and confidence",
        "safety": "Prompt-injection resistance",
        "cost": "Cost and latency bounds",
    }
    for name in DIMENSIONS:
        d = summary["by_dimension"][name]
        rate = _pct(d["passed"], d["applicable"])
        lines.append(f"| {labels[name]} | {d['applicable']} | {d['passed']} | {rate} |")
    lines += [
        "",
        "## Results by category",
        "",
        "| Category | Cases | Passed | Pass rate | p50 | Model calls | Outcomes |",
        "|----------|------:|-------:|----------:|----:|------------:|----------|",
    ]
    for category, c in summary["by_category"].items():
        outcomes = ", ".join(f"{v} {k}" for k, v in c["status_counts"].items())
        lines.append(
            f"| {category} | {c['cases']} | {c['passed']} | {_pct(c['passed'], c['cases'])} | "
            f"{_sec(c['latency_ms_p50'])} | {c['llm_calls']} | {outcomes} |"
        )
    lines += ["", "## Change versus previous run", ""]
    if changes["regressions"] or changes["fixes"]:
        regressions = ", ".join(changes["regressions"]) or "none"
        fixes = ", ".join(changes["fixes"]) or "none"
        lines.append(f"- Regressions (passed before, fail now): {regressions}")
        lines.append(f"- Fixes (failed before, pass now): {fixes}")
    else:
        lines.append("- No previous report to compare with, or no changes.")
    failures = [r for r in results if not r.passed]
    lines += ["", f"## Failures ({len(failures)})", ""]
    for r in failures:
        lines += [f"### {r.id} · {r.category}", "", f"**Q:** {r.question[:300]}  "]
        lines.append(
            f"**Outcome:** HTTP {r.http_status}, status `{r.status}`, {r.sources} sources, "
            f"{r.claims} claims, {r.conflicts} conflicts, {r.llm_calls} model calls, "
            f"{r.latency_ms} ms  "
        )
        for d in r.dimensions:
            if d["status"] == FAIL:
                lines.append(f"- **{d['name']}**: {d['detail']}")
        if r.extra.get("sub_questions"):
            lines.append(f"- sub-questions: {r.extra['sub_questions']}")
        if r.answer:
            lines.append(f"- answer: {r.answer[:400]}")
        lines.append("")
    lines += [
        "## All cases",
        "",
        "| Id | Category | Status | Pass | Latency | Model calls | Sources | Claims | Conflicts "
        "| Confidence |",
        "|----|----------|--------|:----:|--------:|------------:|--------:|-------:|----------:"
        "|-----------:|",
    ]
    for r in results:
        conf = f"{r.confidence:.2f}" if r.confidence is not None else "—"
        status = r.status or f"HTTP {r.http_status}"
        mark = "✅" if r.passed else "❌"
        lines.append(
            f"| {r.id} | {r.category} | {status} | {mark} | {_sec(r.latency_ms)} | {r.llm_calls} | "
            f"{r.sources} | {r.claims} | {r.conflicts} | {conf} |"
        )
    return "\n".join(lines) + "\n"


def write_reports(
    out_dir: Path,
    tag: str,
    started: datetime,
    finished: datetime,
    target: str,
    results: list[CaseResult],
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    latest_json = out_dir / "latest.json"
    previous = json.loads(latest_json.read_text()) if latest_json.exists() else None
    summary = summarise(results)
    changes = compare(results, previous)
    payload = {
        "tag": tag,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "target": target,
        "summary": summary,
        "changes": changes,
        "results": [r.to_dict() for r in results],
    }
    markdown = render_markdown(tag, started, finished, target, results, summary, changes)
    json_path = out_dir / f"{tag}.json"
    md_path = out_dir / f"{tag}.md"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    if not tag.startswith("smoke"):
        latest_json.write_text(json_path.read_text(), encoding="utf-8")
        (out_dir / "latest.md").write_text(markdown, encoding="utf-8")
    return md_path, json_path


def now() -> datetime:
    return datetime.now(UTC)
