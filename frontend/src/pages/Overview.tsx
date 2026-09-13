import { useEffect, useState } from "react";
import {
  ApiError,
  getReadiness,
  getSystemStatus,
  type ReadinessResponse,
  type SystemStatus,
} from "../api/client";

export default function Overview() {
  const [ready, setReady] = useState<ReadinessResponse | null>(null);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [r, s] = await Promise.all([getReadiness(), getSystemStatus()]);
        if (cancelled) return;
        setReady(r);
        setStatus(s);
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
        Readiness of every dependency and the state of the run store. Refreshes every 15 seconds.
      </p>
      {error && <div className="card error">Could not load status: {error}</div>}

      <div className="grid">
        <div className="card">
          <h2>
            Readiness{" "}
            {ready && (
              <span className={ready.status === "ready" ? "badge ok" : "badge bad"}>
                {ready.status}
              </span>
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
                  <td>{status.runs_total}</td>
                </tr>
              </tbody>
            </table>
          ) : (
            <p className="muted">loading…</p>
          )}
        </div>
      </div>
    </section>
  );
}
