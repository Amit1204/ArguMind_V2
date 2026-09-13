import { useEffect, useRef, useState } from "react";
import { isRunning, type RunDetail } from "../api/client";
import AnswerText from "./AnswerText";
import { StatusBadge, formatMs, formatPct } from "./Badges";
import CitationGraph from "./CitationGraph";
import EvidencePanel from "./EvidencePanel";
import StageTimeline from "./StageTimeline";

type Tab = "answer" | "evidence" | "graph" | "stages";

export default function RunView({ run }: { run: RunDetail }) {
  const running = isRunning(run.status);
  const finished = !running;
  const [tab, setTab] = useState<Tab>(running ? "stages" : "answer");
  const userPicked = useRef(false);
  const wasRunning = useRef(running);

  // A run that was being watched switches to its answer when it finishes, unless the
  // user has already chosen a tab.
  useEffect(() => {
    if (wasRunning.current && finished && !userPicked.current) setTab("answer");
    wasRunning.current = running;
  }, [running, finished]);

  const pick = (t: Tab) => {
    userPicked.current = true;
    setTab(t);
  };

  return (
    <div className="stack">
      <div className="card">
        <div className="row-between">
          <h2 className="question">{run.question}</h2>
          <StatusBadge status={run.status} />
        </div>
        <div className="metrics">
          <span>
            confidence <strong>{formatPct(run.confidence)}</strong>
          </span>
          <span>
            time <strong>{formatMs(run.latency_ms)}</strong>
          </span>
          <span>
            model calls <strong>{run.usage.llm_calls}</strong>
          </span>
          <span>
            tokens <strong>{run.usage.input_tokens.toLocaleString()}</strong> in /{" "}
            <strong>{run.usage.output_tokens.toLocaleString()}</strong> out
          </span>
          <span>
            iterations <strong>{run.iteration_count}</strong>
          </span>
          <span>
            sources <strong>{run.sources.length}</strong> · claims <strong>{run.claims.length}</strong> · conflicts{" "}
            <strong>{run.resolutions.length}</strong>
          </span>
        </div>
        {run.outcome_reason && finished && run.status !== "answered" && (
          <p className="muted">{run.outcome_reason}</p>
        )}
        {run.error && <div className="error-text">{run.error}</div>}
      </div>

      <nav className="tabs">
        {(["answer", "evidence", "graph", "stages"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "tab active" : "tab"} onClick={() => pick(t)} disabled={running && t !== "stages"}>
            {t}
          </button>
        ))}
      </nav>

      {tab === "answer" && finished && (
        <div className="stack">
          <div className="card">
            {run.answer ? <AnswerText text={run.answer} sources={run.sources} /> : <p className="muted">No answer.</p>}
          </div>
          {run.caveats.length > 0 && (
            <div className="card caveats">
              <h3>Caveats</h3>
              <ul>
                {run.caveats.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>
          )}
          {run.consensus && run.consensus.overall && (
            <div className="card">
              <h3>
                Consensus <span className="muted small">{run.consensus.strength} · {run.consensus.method}</span>
              </h3>
              <p>{run.consensus.overall}</p>
              {!!run.consensus.key_agreements?.length && (
                <>
                  <strong>Agreements</strong>
                  <ul>
                    {run.consensus.key_agreements.map((a, i) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                </>
              )}
              {!!run.consensus.key_disagreements?.length && (
                <>
                  <strong>Disagreements</strong>
                  <ul>
                    {run.consensus.key_disagreements.map((a, i) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                </>
              )}
              {!!run.consensus.research_gaps?.length && (
                <>
                  <strong>Open questions</strong>
                  <ul>
                    {run.consensus.research_gaps.map((a, i) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
          {run.critic && (
            <div className="card">
              <h3>
                Critic <span className="muted small">{run.critic.recommendation}</span>
              </h3>
              <p className="muted small">
                {run.critic.evidence_claims} claims with a stance from {run.critic.evidence_sources} sources ·{" "}
                {run.critic.unresolved_conflicts} unresolved conflicts
              </p>
              {run.critic.issues.length > 0 && (
                <ul>
                  {run.critic.issues.map((i, k) => (
                    <li key={k}>{i}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {run.verification && (
            <p className="muted small">
              Citations: {run.verification.valid_citations.length} verified
              {run.verification.invalid_removed.length > 0 && `, ${run.verification.invalid_removed.length} removed (${run.verification.invalid_removed.join(", ")})`}
              .
            </p>
          )}
        </div>
      )}
      {tab === "evidence" && finished && <EvidencePanel run={run} />}
      {tab === "graph" && finished && (
        <div className="card">
          <CitationGraph runId={run.id} />
        </div>
      )}
      {tab === "stages" && (
        <div className="card">
          <StageTimeline stages={run.stages} running={running} />
        </div>
      )}
    </div>
  );
}
