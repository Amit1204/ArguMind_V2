import type { StageRecord } from "../api/client";
import { StageStatusDot, formatMs } from "./Badges";

export const STAGE_ORDER = [
  "plan",
  "gather",
  "extract",
  "build_graph",
  "resolve_conflicts",
  "cluster",
  "consensus",
  "critic",
  "answer",
  "verify",
];

const LABELS: Record<string, string> = {
  plan: "Plan sub-questions",
  gather: "Gather sources",
  extract: "Extract claims",
  build_graph: "Build citation graph",
  resolve_conflicts: "Resolve conflicts",
  cluster: "Cluster topics",
  consensus: "Form consensus",
  critic: "Critic gate",
  answer: "Write answer",
  verify: "Verify citations",
};

function summarise(stage: StageRecord): string {
  const d = stage.detail as Record<string, unknown>;
  switch (stage.name) {
    case "plan":
      return `${(d.sub_questions as string[] | undefined)?.length ?? 0} sub-questions`;
    case "gather": {
      const errors = (d.errors as string[] | undefined)?.length ?? 0;
      return `${String(d.new_sources ?? 0)} sources${d.broadened ? " (broadened)" : ""}${errors ? `, ${errors} source error(s)` : ""}`;
    }
    case "extract": {
      const by = (d.by_stance as Record<string, number> | undefined) ?? {};
      return `${String(d.new_claims ?? 0)} claims (${by.supports ?? 0} for, ${by.refutes ?? 0} against, ${by.neutral ?? 0} neutral)`;
    }
    case "build_graph":
      return `${String(d.claims ?? 0)} claims, ${String(d.refutes_edges ?? 0)} refutes edges, ${String(d.conflicts ?? 0)} conflicts`;
    case "resolve_conflicts":
      return `${String(d.conflicts ?? 0)} conflicts, ${String(d.resolved ?? 0)} resolved, ${String(d.model_arbitrations ?? 0)} by model`;
    case "cluster":
      return `${String(d.clusters ?? 0)} clusters, ${((d.contested as string[] | undefined) ?? []).length} contested`;
    case "consensus":
      return `${String(d.strength ?? "?")} (${String(d.method ?? "")})`;
    case "critic":
      return `${String(d.recommendation ?? "")}${(d.issues as string[] | undefined)?.length ? `: ${(d.issues as string[]).join("; ")}` : ""}`;
    case "answer":
      return String(d.method ?? "");
    case "verify":
      return `${((d.valid_citations as string[] | undefined) ?? []).length} valid citations, ${((d.invalid_removed as string[] | undefined) ?? []).length} removed`;
    default:
      return "";
  }
}

interface Props {
  stages: StageRecord[];
  running: boolean;
}

export default function StageTimeline({ stages, running }: Props) {
  const done = new Set(stages.map((s) => s.name));
  const lastName = stages[stages.length - 1]?.name;
  const nextIndex = lastName ? STAGE_ORDER.indexOf(lastName) + 1 : 0;
  const pending = running ? STAGE_ORDER.slice(nextIndex).filter((n) => !done.has(n) || n === "gather") : [];

  return (
    <table className="stages">
      <thead>
        <tr>
          <th></th>
          <th>Stage</th>
          <th>Result</th>
          <th className="num">Time</th>
          <th className="num">Model calls</th>
        </tr>
      </thead>
      <tbody>
        {stages.map((s, i) => (
          <tr key={`${s.name}-${s.attempt}-${i}`} className={s.status === "failed" ? "row-bad" : ""}>
            <td>
              <StageStatusDot status={s.status} />
            </td>
            <td>
              {LABELS[s.name] ?? s.name}
              {s.attempt > 1 && <span className="muted"> · attempt {s.attempt}</span>}
            </td>
            <td className="muted">
              {s.error ? <span className="error-text">{s.error}</span> : summarise(s)}
            </td>
            <td className="num">{formatMs(s.duration_ms)}</td>
            <td className="num">{s.llm_calls || ""}</td>
          </tr>
        ))}
        {pending.map((name, i) => (
          <tr key={`pending-${name}`} className="row-pending">
            <td>
              <StageStatusDot status={i === 0 ? "running" : "pending"} />
            </td>
            <td>{LABELS[name] ?? name}</td>
            <td className="muted">{i === 0 ? "running…" : "waiting"}</td>
            <td className="num">…</td>
            <td className="num"></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
