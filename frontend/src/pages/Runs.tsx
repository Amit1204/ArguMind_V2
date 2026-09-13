import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, listRuns, type RunList } from "../api/client";
import { StatusBadge, formatMs, formatPct, formatWhen } from "../components/Badges";

const PAGE = 20;

export default function Runs() {
  const [data, setData] = useState<RunList | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listRuns(PAGE, offset)
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiError ? `${e.message} (HTTP ${e.status})` : "backend unreachable");
      });
    return () => {
      cancelled = true;
    };
  }, [offset]);

  return (
    <section>
      <h1>Runs</h1>
      <p className="muted">Every run is persisted with its evidence, graph, stages, tokens and latency.</p>
      {error && <div className="card error">Could not load runs: {error}</div>}
      <div className="card">
        {data && data.runs.length === 0 && (
          <p className="muted">
            No runs yet. <Link to="/ask">Ask a question</Link>.
          </p>
        )}
        {data && data.runs.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Question</th>
                <th>Status</th>
                <th className="num">Confidence</th>
                <th className="num">Time</th>
                <th className="num">Model calls</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {data.runs.map((r) => (
                <tr key={r.id}>
                  <td>
                    <Link to={`/runs/${r.id}`}>{r.question}</Link>
                  </td>
                  <td>
                    <StatusBadge status={r.status} />
                  </td>
                  <td className="num">{formatPct(r.confidence)}</td>
                  <td className="num">{formatMs(r.latency_ms)}</td>
                  <td className="num">{r.llm_calls}</td>
                  <td className="muted small">{formatWhen(r.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data && data.total > PAGE && (
          <div className="row-between pager">
            <button className="chip" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              newer
            </button>
            <span className="muted small">
              {offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}
            </span>
            <button className="chip" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>
              older
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
