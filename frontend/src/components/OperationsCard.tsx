import { useEffect, useState } from "react";
import { getJson } from "../api/client";
import { formatMs } from "./Badges";

interface Operations {
  runs: {
    total: number;
    by_status: Record<string, number>;
    active: number;
    latency_ms_p50: number | null;
    latency_ms_p95: number | null;
  };
  stages: Record<string, { count: number; failed: number; skipped: number; latency_ms_p50: number | null }>;
  llm: {
    calls: number;
    by_outcome: Record<string, number>;
    latency_ms_p50: number | null;
    latency_ms_p95: number | null;
    input_tokens: number;
    output_tokens: number;
    fallbacks: number;
    estimated_cost_usd: number;
  };
  sources: Record<string, Record<string, number | null>>;
  circuits: Record<string, { state: string; consecutive_failures: number; retry_after_seconds: number }>;
  rate_limited: number;
  limits: Record<string, number>;
}

function circuitClass(state: string) {
  return state === "closed" ? "ok" : state === "half_open" ? "warn" : "bad";
}

export default function OperationsCard() {
  const [ops, setOps] = useState<Operations | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      getJson<Operations>("/api/v1/system/operations")
        .then((o) => {
          if (!cancelled) {
            setOps(o);
            setError(null);
          }
        })
        .catch((e: Error) => {
          if (!cancelled) setError(e.message);
        });
    void load();
    const timer = setInterval(load, 15000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  if (error) return <div className="card error">Could not load operations: {error}</div>;
  if (!ops) return <div className="card">Operations: loading…</div>;

  const byStatus = Object.entries(ops.runs.by_status)
    .map(([k, v]) => `${v} ${k}`)
    .join(", ");
  const llmOutcomes = Object.entries(ops.llm.by_outcome)
    .filter(([k]) => k !== "ok")
    .map(([k, v]) => `${v} ${k.replace("_", " ")}`)
    .join(", ");

  return (
    <div className="card">
      <h2>
        Operations <span className="muted small">since the backend started</span>
      </h2>
      <div className="ops-grid">
        <div>
          <div className="ops-label">runs</div>
          <div className="ops-value">{ops.runs.total}</div>
          <div className="muted small">
            {byStatus || "none yet"}
            {ops.runs.active > 0 ? ` · ${ops.runs.active} active` : ""}
          </div>
          <div className="muted small">
            p50 {formatMs(ops.runs.latency_ms_p50)} · p95 {formatMs(ops.runs.latency_ms_p95)}
          </div>
        </div>
        <div>
          <div className="ops-label">model calls</div>
          <div className="ops-value">{ops.llm.calls}</div>
          <div className="muted small">
            {ops.llm.input_tokens.toLocaleString()} in / {ops.llm.output_tokens.toLocaleString()} out
            {ops.llm.fallbacks ? ` · ${ops.llm.fallbacks} fallbacks` : ""}
          </div>
          <div className="muted small">
            p50 {formatMs(ops.llm.latency_ms_p50)}
            {llmOutcomes ? ` · ${llmOutcomes}` : ""}
          </div>
        </div>
        <div>
          <div className="ops-label">sources</div>
          {Object.keys(ops.sources).length === 0 && <div className="muted small">no lookups yet</div>}
          {Object.entries(ops.sources).map(([kind, o]) => (
            <div key={kind} className="muted small">
              <strong>{kind}</strong>: {o.ok ?? 0} ok, {o.cached ?? 0} cached, {o.error ?? 0} errors
              {o.circuit_open ? `, ${o.circuit_open} refused` : ""} · p50 {formatMs(o.latency_ms_p50)}
            </div>
          ))}
        </div>
        <div>
          <div className="ops-label">circuits</div>
          {Object.keys(ops.circuits).length === 0 && <div className="muted small">none created yet</div>}
          {Object.entries(ops.circuits).map(([name, c]) => (
            <div key={name} className="small">
              <span className={`dot ${circuitClass(c.state)}`} />
              {name} <span className="muted">{c.state.replace("_", " ")}</span>
              {c.retry_after_seconds > 0 ? <span className="muted"> · retry in {Math.round(c.retry_after_seconds)} s</span> : ""}
            </div>
          ))}
          <div className="muted small">
            rate limited: {ops.rate_limited} · limit {ops.limits.runs_per_minute_per_client}/min per client
          </div>
        </div>
      </div>
      {Object.keys(ops.stages).length > 0 && (
        <table className="ops-stages">
          <thead>
            <tr>
              <th>stage</th>
              <th className="num">runs</th>
              <th className="num">failed</th>
              <th className="num">skipped</th>
              <th className="num">p50</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(ops.stages).map(([name, s]) => (
              <tr key={name}>
                <td>{name}</td>
                <td className="num">{s.count}</td>
                <td className="num">{s.failed || ""}</td>
                <td className="num">{s.skipped || ""}</td>
                <td className="num">{formatMs(s.latency_ms_p50)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
