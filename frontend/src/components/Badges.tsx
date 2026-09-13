import type { RunStatus, Stance } from "../api/client";

export function StatusBadge({ status }: { status: RunStatus | string }) {
  const cls =
    status === "answered"
      ? "ok"
      : status === "inconclusive"
        ? "warn"
        : status === "failed"
          ? "bad"
          : "neutral";
  return <span className={`badge ${cls}`}>{status}</span>;
}

export function StanceBadge({ stance }: { stance: Stance | string }) {
  const cls = stance === "supports" ? "ok" : stance === "refutes" ? "bad" : "neutral";
  return <span className={`badge ${cls}`}>{stance}</span>;
}

export function StageStatusDot({ status }: { status: string }) {
  const cls =
    status === "ok" ? "ok" : status === "failed" ? "bad" : status === "running" ? "run" : "warn";
  return <span className={`dot ${cls}`} title={status} />;
}

export function formatMs(ms: number | null | undefined) {
  if (ms === null || ms === undefined) return "…";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
}

export function formatPct(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}

export function formatWhen(iso: string) {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
