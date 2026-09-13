import { Link, useParams } from "react-router-dom";
import RunView from "../components/RunView";
import { useRun } from "../hooks/useRun";

export default function RunPage() {
  const { id } = useParams();
  const { run, error } = useRun(id ?? null);
  return (
    <section>
      <div className="row-between">
        <h1>Run</h1>
        <Link to="/runs" className="muted small">
          all runs
        </Link>
      </div>
      {error && !run && <div className="card error">Could not load the run: {error}</div>}
      {!run && !error && <p className="muted">loading…</p>}
      {run && <RunView run={run} />}
    </section>
  );
}
