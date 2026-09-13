import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, createRun } from "../api/client";
import RunView from "../components/RunView";
import { useRun } from "../hooks/useRun";

const EXAMPLES = [
  "Do large language models understand language?",
  "Is the Chinese room argument a valid objection to strong AI?",
  "Does intermittent fasting improve longevity in humans?",
  "Do neural networks generalise compositionally?",
];

export default function Ask() {
  const [question, setQuestion] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const { run, error } = useRun(runId);

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    const q = question.trim();
    if (q.length < 10) {
      setSubmitError("Please ask a fuller question (at least 10 characters).");
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      const created = await createRun(q);
      setRunId(created.id);
    } catch (err) {
      setSubmitError(err instanceof ApiError ? `${err.message} (HTTP ${err.status})` : "backend unreachable");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section>
      <h1>Ask</h1>
      <p className="muted">
        A research question is split into sub-questions, evidence is gathered from arXiv and Wikipedia, claims are
        extracted with their stance, conflicts are resolved and the answer cites only sources retrieved in this run.
        Runs take one to three minutes on the free tier.
      </p>
      <form className="card ask-form" onSubmit={submit}>
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask a research question…"
          rows={3}
          maxLength={500}
          disabled={submitting}
        />
        <div className="row-between">
          <div className="chips">
            {EXAMPLES.map((ex) => (
              <button key={ex} type="button" className="chip" onClick={() => setQuestion(ex)} disabled={submitting}>
                {ex}
              </button>
            ))}
          </div>
          <button type="submit" className="primary" disabled={submitting || question.trim().length < 10}>
            {submitting ? "starting…" : "Ask"}
          </button>
        </div>
        {submitError && <div className="error-text">{submitError}</div>}
      </form>

      {runId && (
        <div className="stack">
          <div className="row-between muted small">
            <span>
              run <code className="id">{runId}</code>
            </span>
            <Link to={`/runs/${runId}`}>open run page</Link>
          </div>
          {error && !run && <div className="card error">Could not load the run: {error}</div>}
          {run && <RunView run={run} />}
        </div>
      )}
    </section>
  );
}
