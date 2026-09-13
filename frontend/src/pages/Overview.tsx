import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApiError,
  getReadiness,
  getSystemStatus,
  listRuns,
  type ReadinessResponse,
  type RunList,
  type SystemStatus,
} from "../api/client";
import { StatusBadge, formatMs, formatPct } from "../components/Badges";
import OperationsCard from "../components/OperationsCard";

export default function Overview() {
  const [ready, setReady] = useState<ReadinessResponse | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [runs, setRuns] = useState<RunList | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [r, s, l] = await Promise.all([getReadiness(), getSystemStatus(), listRuns(5, 0)]);
        if (cancelled) return;
        setReady(r);
        setStatus(s);
        setRuns(l);
        setError(null);
      } catch (e) {
        if (cancelled) return;
        const message =
          e instanceof ApiError
            ? `${e.message} (HTTP ${e.status}${e.requestId ? `, request ${e.requestId}` : ""})`
            : "backend unreachable";
        setError(message);
      }
    }
    void load();
    const timer = setInterval(load, 15000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <section>
      <h1>Overview</h1>
      <p className="muted">
        Readiness of every dependency, the state of the run store and the latest runs. Refreshes every 15 seconds.
      </p>
      {error && <div className="card error">Could not load status: {error}</div>}

      <div className="grid">
        <div className="card">
          <h2>
            Readiness{" "}
            {ready && (
              <span className={ready.status === "ready" ? "badge ok" : "badge bad"}>{ready.status}</span>
            )}
          </h2>
          {ready ? (
            <table>
              <tbody>
                {ready.checks.map((c) => (
                  <tr key={c.name}>
                    <td>{c.name}</td>
                    <td>
                      <span className={c.ok ? "dot ok" : c.optional ? "dot warn" : "dot bad"} />
                      {c.ok ? "ok" : c.optional ? "not configured" : "failing"}
                    </td>
                    <td className="muted">{c.detail ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">loading…</p>
          )}
        </div>

        <div className="card">
          <h2>System</h2>
          {status ? (
            <table>
              <tbody>
                <tr>
                  <td>service</td>
                  <td>
                    {status.service} {status.version} ({status.environment})
                  </td>
                </tr>
                <tr>
                  <td>schema</td>
                  <td>{status.schema_version ?? "not migrated"}</td>
                </tr>
                <tr>
                  <td>model provider</td>
                  <td>
                    {status.llm_provider} · {status.llm_configured ? "configured" : "no key"}
                  </td>
                </tr>
                <tr>
                  <td>runs stored</td>
                  <td>
                    {status.runs_total}
                    {Object.keys(status.runs_by_status).length > 0 && (
                      <span className="muted small">
                        {" "}
                        (
                        {Object.entries(status.runs_by_status)
                          .map(([k, v]) => `${v} ${k}`)
                          .join(", ")}
                        )
                      </span>
                    )}
                  </td>
                </tr>
              </tbody>
            </table>
          ) : (
            <p className="muted">loading…</p>
          )}
        </div>
      </div>

      <div className="stack">
        <OperationsCard />
      </div>

      <div className="card" style={{ marginTop: "1rem" }}>
        <h2>
          Recent runs{" "}
          <Link to="/runs" className="muted small">
            all runs
          </Link>
        </h2>
        {runs && runs.runs.length === 0 && (
          <p className="muted">
            No runs yet. <Link to="/ask">Ask a question</Link>.
          </p>
        )}
        {runs && runs.runs.length > 0 && (
          <table>
            <tbody>
              {runs.runs.map((r) => (
                <tr key={r.id}>
                  <td className="wide">
                    <Link to={`/runs/${r.id}`}>{r.question}</Link>
                  </td>
                  <td>
                    <StatusBadge status={r.status} />
                  </td>
                  <td className="num">{formatPct(r.confidence)}</td>
                  <td className="num">{formatMs(r.latency_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
